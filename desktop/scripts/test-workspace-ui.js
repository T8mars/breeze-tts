const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const renderer = fs.readFileSync(path.join(root, "src", "renderer.js"), "utf8");
const html = fs.readFileSync(path.join(root, "src", "index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "src", "styles.css"), "utf8");
const main = fs.readFileSync(path.join(root, "src", "main.js"), "utf8");

function sourceBetween(source, start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from + start.length);
  assert.notEqual(from, -1, `missing source marker: ${start}`);
  assert.notEqual(to, -1, `missing source marker: ${end}`);
  return source.slice(from, to);
}

test("renderer is valid JavaScript", () => {
  assert.doesNotThrow(() => new vm.Script(renderer, { filename: "renderer.js" }));
});

test("dialogue projects have an automatic draft and leave guard", () => {
  assert.match(renderer, /PROJECT_DRAFT_KEY/);
  assert.match(renderer, /localStorage\.setItem\(PROJECT_DRAFT_KEY/);
  assert.match(renderer, /function restoreProjectDraft\(/);
  assert.match(renderer, /addEventListener\("beforeunload"/);
  assert.match(renderer, /window\.confirm\("当前对白工程尚未正式保存/);
  assert.match(html, /id="projectSaveState"[^>]+aria-live="polite"/);
});

test("role syntax is detected for colon, bracket and pipe formats", () => {
  const roleSource = sourceBetween(renderer, "function normalizeRoleName", "function shouldShowRoleMappings");
  const context = { INLINE_VOCAL_EVENT_ROLES: new Set(["笑", "咳嗽", "清嗓子", "叹气"]) };
  vm.runInNewContext(`${roleSource}; this.detect = detectRoleNames;`, context);
  assert.deepEqual(Array.from(context.detect("旁白：开始\n[小蓝] 你好\nAlice | Hello | en | cheerful")), ["旁白", "小蓝", "Alice"]);
  assert.deepEqual(Array.from(context.detect("[笑] 这仍是普通台词\n[叹气] 第二句")), []);
  assert.match(renderer, /row\.className = "role-mapping-row"/);
  assert.match(renderer, /matchedVoice/);
  assert.match(renderer, /roleMappingPanel"\)\.addEventListener\("change"/);
});

test("timeline movement preserves duration at both boundaries", () => {
  const helperSource = sourceBetween(renderer, "function applyTimelineDelta", "function startTimelineDrag");
  const context = { MIN_LINE_DURATION_MS: 50, MAX_TIMELINE_MS: 1000 };
  vm.runInNewContext(`${helperSource}; this.apply = applyTimelineDelta;`, context);

  const left = { start_ms: 100, end_ms: 300 };
  context.apply(left, 100, 300, "move", -500);
  assert.deepEqual(left, { start_ms: 0, end_ms: 200 });

  const right = { start_ms: 800, end_ms: 950 };
  context.apply(right, 800, 950, "move", 500);
  assert.deepEqual(right, { start_ms: 850, end_ms: 1000 });

  const resized = { start_ms: 100, end_ms: 300 };
  context.apply(resized, 100, 300, "start", 1000);
  assert.equal(resized.start_ms, 250);
  context.apply(resized, resized.start_ms, resized.end_ms, "end", -1000);
  assert.equal(resized.end_ms - resized.start_ms, 50);
});

test("timeline drag is frame-throttled and does not rebuild during pointermove", () => {
  const dragSource = sourceBetween(renderer, "function startTimelineDrag", "function timelineKey");
  const moveSource = sourceBetween(dragSource, "const move =", "const finish =");
  assert.match(moveSource, /requestAnimationFrame/);
  assert.doesNotMatch(moveSource, /renderTimeline\(\)/);
  assert.match(renderer, /TIMELINE_TRACK_RENDER_LIMIT = 120/);
  assert.match(renderer, /timeline-handle start/);
  assert.match(renderer, /timeline-handle end/);
  assert.match(renderer, /event\.altKey \? 1 : timelineSnapMs\(\)/);
});

test("narrow screens retain usable controls and card-form timeline rows", () => {
  assert.doesNotMatch(css, /body\s*\{[^}]*min-width:\s*640px/);
  assert.match(css, /\.path-row button\s*\{[^}]*white-space:\s*nowrap/);
  assert.match(css, /@media \(max-width: 760px\)/);
  assert.match(css, /\.timeline-table td::before\s*\{\s*content:\s*attr\(data-label\)/);
  assert.match(css, /\.action-dock\s*\{\s*position:\s*static/);
  assert.doesNotMatch(css, /\.action-dock\s*\{[^}]*position:\s*fixed/);
  assert.match(css, /\.studio-toolbar\s*\{[^}]*display:\s*grid/);
  assert.match(css, /@media \(max-width: 760px\)[\s\S]*?\.workspace-tabs\s*\{[^}]*flex-wrap:\s*wrap/);
  assert.match(renderer, /cell\.dataset\.label = label/);
});

test("creation templates and direction recipes stay within Breeze capabilities", () => {
  assert.match(html, /data-template="narration"/);
  assert.match(html, /data-template="dialogue"/);
  assert.match(html, /data-template="subtitle"/);
  assert.match(html, /id="continueDraftTemplate"[^>]+disabled/);
  assert.match(html, /id="quickLaunchFeedback"[^>]+aria-live="polite"[^>]+hidden/);
  assert.doesNotMatch(html, /id="advancedLauncher"[^>]+open/);
  assert.match(renderer, /const CREATION_TEMPLATES =/);
  assert.match(renderer, /function applyCreationTemplate/);
  assert.match(renderer, /quickLaunchStatus/);
  assert.match(renderer, /const DIRECTION_RECIPES =/);
  assert.match(renderer, /演绎要求：\$\{direction\}/);
  assert.doesNotMatch(renderer, /emotion_vector|duration_factor/);
});

test("first-run model preparation stays on a clean launcher with concise guidance", () => {
  const launchSource = sourceBetween(renderer, "async function launchStudio", "async function returnToLauncher");
  assert.match(launchSource, /if \(!model\?\.valid \|\| !model\?\.license_accepted\)/);
  assert.match(launchSource, /modelPreparationMessage\(model\)/);
  assert.match(launchSource, /advanced\.open = true/);
  assert.ok(launchSource.indexOf("if (!model?.valid || !model?.license_accepted)") < launchSource.indexOf("state.launching = true"));
  assert.doesNotMatch(launchSource, /await activateCurrentModel\(\)/);
  assert.match(renderer, /message\.length > 240/);
  assert.match(css, /\.launcher-grid > \*\s*\{[^}]*min-width:\s*0/);
  assert.match(css, /\.launcher-grid\s*\{[^}]*minmax\(0, 1\.2fr\)/);
  assert.match(css, /\.quick-launch-feedback span\s*\{[^}]*overflow-wrap:\s*anywhere/);
});

test("successful model activation immediately updates the launch guard", () => {
  const rememberSource = sourceBetween(renderer, "function rememberActiveModel", "function renderDiagnostics");
  const activateSource = sourceBetween(renderer, "async function activateCurrentModel", "async function pollDownload");
  const chooseSource = sourceBetween(renderer, "async function chooseModelDirectory", "async function downloadModel");
  assert.match(rememberSource, /state\.diagnostics\.model = report/);
  assert.match(activateSource, /rememberActiveModel\(result\.model, result\.runtime\)/);
  assert.match(activateSource, /quickLaunchFeedback/);
  assert.match(chooseSource, /rememberActiveModel\(selectedReport\.model, selectedReport\.runtime\)/);
  assert.match(chooseSource, /await refresh\(\)\.catch/);
});

test("inline vocal events are visible in both Chinese and English", () => {
  for (const event of ["[笑]", "[咳嗽]", "[清嗓子]", "[叹气]", "(laugh)", "(cough)", "(clears throat)", "(sigh)"]) {
    assert.ok(html.includes(`<code>${event}</code>`), `missing inline vocal event: ${event}`);
  }
});

test("normal generation is the default and streaming playback is opt-in", () => {
  assert.match(html, /id="streamAudio" type="checkbox"/);
  assert.doesNotMatch(html, /id="streamAudio"[^>]*\schecked(?:\s|>)/);
  assert.match(html, /默认关闭：生成完整 WAV 后再提供试听与下载/);
  assert.match(renderer, /stream_audio:\s*streamAudio/);
  assert.match(renderer, /message\.stream_audio === true/);
  assert.match(renderer, /正在生成完整音频/);
  assert.match(fs.readFileSync(path.resolve(root, "..", "t8_runtime", "server.py"), "utf8"), /stream_audio = payload\.get\("stream_audio"\) is True/);
});

test("Fast All reports Triton readiness and safely falls back to Eager", () => {
  assert.match(html, /id="runtimeCompatibilityHint"/);
  assert.match(renderer, /packages\["triton-windows"\]/);
  assert.match(renderer, /Fast All \/ Triton/);
  assert.match(renderer, /fastOption\.disabled = !fastAllReady/);
  assert.match(renderer, /runtime\.fast_all_fallback_reason/);
});

test("FlashAttention readiness and active text-encoder backend are visible", () => {
  assert.match(renderer, /packages\["flash-attn"\]/);
  assert.match(renderer, /FlashAttention 2/);
  assert.match(renderer, /runtime\.flash_attention_active/);
  assert.match(renderer, /runtime\.text_encoder_attention/);
});

test("global task feedback and keyboard tabs remain available across pages", () => {
  assert.match(html, /id="globalTaskBar"[^>]+aria-live="polite"[^>]+hidden/);
  assert.match(renderer, /function setGlobalTask/);
  assert.match(renderer, /kind === "success" \? 7000 : 3500/);
  assert.match(renderer, /bar\.hidden = true/);
  assert.match(renderer, /function workspaceTabKeydown/);
  assert.match(renderer, /\["ArrowLeft", "ArrowRight", "Home", "End"\]/);
  assert.match(renderer, /button\.tabIndex = active \? 0 : -1/);
  assert.match(renderer, /globalTaskCancelButton/);
});

test("history and queue actions use explicit, consistent labels and retry states", () => {
  assert.match(html, /id="refreshHistoryButton"[^>]*>刷新历史</);
  assert.match(html, /id="refreshQueueButton"[^>]*>刷新队列</);
  assert.match(renderer, /试听 \/ 下载 WAV/);
  assert.match(renderer, /从断点继续/);
  assert.match(renderer, /\/api\/queue\/\$\{encodeURIComponent\(jobId\)\}\/resume/);
  assert.match(renderer, /resume_job_id: resumeJobId/);
  assert.doesNotMatch(renderer, /updates:\s*\{\s*status:\s*"pending"/);
  assert.match(renderer, /function renderListError/);
  assert.match(renderer, /建议：/);
});

test("launch, voice library, history and diagnostics expose actionable states", () => {
  assert.match(html, /id="launchProgress"[^>]+hidden/);
  assert.match(html, /id="launchElapsed"[^>]+hidden/);
  assert.match(renderer, /阶段 1\/2：正在校验模型与许可证/);
  assert.match(renderer, /阶段 2\/2：正在把模型加载到显存/);
  assert.match(html, /id="voiceEmptyState"[^>]+hidden/);
  assert.match(renderer, /selectedLibraryVoice/);
  assert.match(renderer, /\["applyVoiceButton", "updateVoiceButton", "deleteVoiceButton", "exportVoiceButton"\]/);
  assert.match(renderer, /api\("\/api\/history\?limit=500"\)/);
  assert.match(html, /id="historySearch"/);
  assert.match(html, /id="historyPagination"/);
  assert.match(renderer, /function reuseHistoryItem/);
  assert.match(html, /id="settingsDiagnosticsGrid"/);
  assert.match(html, /id="copyDiagnosticsButton"/);
});

test("launcher separates official links from T8star-Aix social links", () => {
  for (const url of ["https://github.com/T8mars", "https://huggingface.co/t8star", "https://space.bilibili.com/385085361", "https://www.youtube.com/@T8star-Aix/"]) {
    assert.ok(html.includes(`data-url="${url}"`), `missing creator link: ${url}`);
  }
  assert.match(html, /reference-links-label">官方项目/);
  assert.match(html, /reference-links-label">T8star-Aix/);
});

test("voice library owns its reference-audio upload, transcript and playback flow", () => {
  for (const id of ["newVoiceButton", "voiceMode", "voiceInstruction", "voiceReferenceAudio", "voiceReferenceText", "voiceReferencePreview", "voiceTranscribeButton", "voiceClearReferenceButton"]) {
    assert.ok(html.includes(`id="${id}"`), `missing voice-library control: ${id}`);
  }
  const selectSource = sourceBetween(renderer, "function selectLibraryVoice", "function applyLibraryVoiceToGeneration");
  const createSource = sourceBetween(renderer, "async function saveCurrentVoice", "async function updateSelectedVoice");
  const updateSource = sourceBetween(renderer, "async function updateSelectedVoice", "async function exportSelectedVoice");
  assert.match(renderer, /\/api\/voices\/\$\{encodeURIComponent\(voice\.id\)\}\/reference/);
  assert.match(selectSource, /renderVoiceReferenceEditor\(voice\)/);
  assert.match(createSource, /\$\("voiceReferenceAudio"\)\.files\[0\]/);
  assert.match(createSource, /\$\("voiceReferenceText"\)\.value\.trim\(\)/);
  assert.match(updateSource, /clear_reference: state\.voiceClearReference/);
  assert.match(renderer, /function beginNewVoice/);
  assert.match(renderer, /beginNewVoice\(`已删除音色/);
  assert.match(html, /id="saveVoiceButton"[^>]*>保存为新音色</);
  assert.match(css, /\.voice-reference-panel\s*\{[^}]*grid-template-columns/);
});

test("batch editor ships runnable examples and does not mistake vocal events for roles", () => {
  assert.match(html, /id="loadBatchExampleButton"/);
  assert.match(html, /id="clearBatchInputButton"/);
  assert.match(html, /This is a real English batch example/);
  assert.match(renderer, /const BATCH_EXAMPLES = \{/);
  assert.match(renderer, /function loadBatchExample/);
  assert.match(renderer, /function clearBatchInput/);
  assert.match(renderer, /INLINE_VOCAL_EVENT_ROLES/);
  assert.match(renderer, /if \(bracket\) return !INLINE_VOCAL_EVENT_ROLES\.has/);
  assert.match(renderer, /BATCH_EXAMPLES\[template\.batchKind\]/);
});

test("batch default role can be selected from the saved voice library", () => {
  assert.match(html, /id="defaultRolePreset"[^>]*aria-label="从已保存音色选择默认角色"/);
  assert.match(renderer, /function renderDefaultRolePresets/);
  assert.match(renderer, /function applyDefaultRolePreset/);
  assert.match(renderer, /解析时会自动绑定这个音色/);
  assert.match(renderer, /defaultRolePreset"\)\.addEventListener\("change"/);
});

test("Whisper Large-v3 is bundled and its draft cannot silently replace the transcript", () => {
  assert.match(html, /Whisper Large-v3（整合包内置）/);
  assert.match(html, /不会自动覆盖准确逐字稿/);
  assert.match(renderer, /whisper_large_bundled/);
  assert.match(renderer, /result\.audio_quality/);
  assert.match(renderer, /确认没有音乐、混响或回声/);
  assert.doesNotMatch(renderer, /referenceText"\)\.value = \(result\.segments/);
  assert.match(main, /"--break-system-packages"/);
});

test("reference transcripts show a prominent warning and are enforced end to end", () => {
  assert.ok((html.match(/class="critical-transcript-warning" role="alert"/g) || []).length >= 2);
  assert.match(html, /必须边听边逐字核对并修改正确，否则不要生成/);
  assert.match(html, /重复、拖音、回声样伪影和异常音色/);
  assert.match(html, /我已播放参考音频并逐字核对/);
  assert.match(css, /\.critical-transcript-warning\s*\{[^}]*border:\s*2px solid/);
  assert.match(renderer, /reference_transcript_verified:\s*usesSavedVoice \|\|/);
  assert.match(renderer, /reference_transcript_verified:\s*\$\("voiceTranscriptVerified"\)\.checked/);
  const server = fs.readFileSync(path.resolve(root, "..", "t8_runtime", "server.py"), "utf8");
  assert.match(server, /def _require_verified_reference_transcript/);
  assert.match(server, /回声样伪影和异常音色/);
});

test("creative presets are categorized and perform explicit one-click actions", () => {
  assert.match(html, /id="creativePresetPanel"/);
  for (const category of ["声音事件 · Breeze 原生", "角色声线 · 一键填写", "内容场景 · 一键填写", "后期音效 · T8 处理"]) {
    assert.ok(html.includes(category), `missing creative preset category: ${category}`);
  }
  for (const kind of ["event", "voice", "scene", "effect"]) {
    assert.match(html, new RegExp(`data-preset-kind="${kind}"`));
  }
  for (const effect of ["telephone", "walkie_talkie", "radio", "megaphone", "muffled", "dream", "robot"]) {
    assert.ok(html.includes(`value="${effect}"`) || html.includes(`data-preset-value="${effect}"`), `missing effect preset: ${effect}`);
  }
  assert.match(renderer, /const VOICE_PRESETS =/);
  assert.match(renderer, /const SCENE_PRESETS =/);
  assert.match(renderer, /const AUDIO_EFFECT_GROUPS =/);
  assert.match(renderer, /function insertAtCursor/);
  assert.match(renderer, /function applyCreativePreset/);
  assert.match(renderer, /data-preset-kind="effect"/);
  assert.match(css, /\.preset-groups\s*\{[^}]*grid-template-columns/);
});

test("long-form creation exposes voice lock, separate spoken text and spatial effects", () => {
  assert.match(html, /id="longFormVoiceLock"[^>]*checked/);
  assert.match(html, /id="pronunciationAliases"/);
  assert.match(html, /不修改原文、字幕或工程显示/);
  assert.match(html, /id="audioEffect"/);
  assert.match(html, /山间回音/);
  assert.match(renderer, /long_form_voice_lock/);
  assert.match(renderer, /spoken_text/);
  assert.match(renderer, /audio_effect/);
});

test("timeline controls have per-line accessible names and touch-sized handles", () => {
  assert.match(renderer, /第 \$\{line\.order\} 句音色/);
  assert.match(renderer, /第 \$\{line\.order\} 句语言/);
  assert.match(renderer, /第 \$\{line\.order\} 句演绎模式/);
  assert.match(renderer, /第 \$\{line\.order\} 句\$\{label\}/);
  assert.match(css, /\.timeline-handle\s*\{[^}]*width:\s*24px/);
});

test("single-line rerun saves first, synchronizes revisions and exposes honest line results", () => {
  const saveGuard = sourceBetween(renderer, "async function ensureDialogueProjectSaved", "function renderTimeline");
  assert.match(saveGuard, /state\.projectBaseRevision === null \|\| state\.projectDirty/);
  assert.match(saveGuard, /await saveDialogueProject\(\)/);

  const batchSource = sourceBetween(renderer, "async function runBatchPayload", "async function resumeBatchJob");
  const syncSource = sourceBetween(renderer, "async function syncDialogueProjectFromBatch", "async function ensureDialogueProjectSaved");
  assert.match(batchSource, /singleLineId/);
  assert.match(batchSource, /syncDialogueProjectFromBatch\(message\.project/);
  assert.match(syncSource, /state\.projectBaseRevision = project\.revision/);
  assert.match(batchSource, /message\.full_project_mix === true && message\.remix\?\.status === "completed"/);
  assert.match(batchSource, /message\.remix\?\.reason/);
  assert.match(batchSource, /整条时间轴（已自动重混）/);
  assert.match(batchSource, /仅得到当前句结果/);

  const clickSource = sourceBetween(renderer, "$('timelineBody').addEventListener('click'", '$("referenceAudio").addEventListener');
  assert.ok(clickSource.indexOf("await ensureDialogueProjectSaved()") < clickSource.indexOf("runBatchPayload({ defaults"));
  assert.match(clickSource, /\{ singleLineId: requestedLineId \}/);
  assert.match(clickSource, /project_revision: state\.projectBaseRevision/);
  assert.match(renderer, /document\.querySelectorAll\('#timelineBody input, #timelineBody select, #timelineBody textarea, #timelineBody button'\)/);
  assert.match(renderer, /function startTimelineDrag\(event\) \{\s*if \(state\.batching\) return/);
  assert.match(renderer, /function timelineKey\(event\) \{\s*if \(state\.batching\) return/);

  assert.match(html, />状态 \/ 最新结果<\/th>/);
  assert.match(renderer, /function lineResultView/);
  assert.match(renderer, /line\.status = "running"/);
  assert.match(renderer, /line\.status = "completed"/);
  assert.match(renderer, /line\.status = "failed"/);
  assert.match(renderer, /audio\.src = `\/api\/outputs\/\$\{encodeURIComponent\(line\.audio_file\)\}`/);
  assert.match(renderer, /上次成功音频（本次失败）/);
  assert.match(css, /\.line-result-state\.running/);
  assert.match(css, /\.line-result-state\.completed/);
  assert.match(css, /\.line-result-state\.failed/);
});

test("full-project generation saves first, uses CAS and skips clean completed lines", () => {
  const generationFilter = sourceBetween(renderer, "function lineNeedsGeneration", "async function remixCompletedDialogueProject");
  const context = { LINE_GENERATION_DIRTY_FIELDS: new Set(["text", "role", "voice_id", "language", "direction_mode", "direction_text", "cfg_scale", "seed", "instruction", "reference"]) };
  vm.runInNewContext(`${generationFilter}; this.needs = lineNeedsGeneration;`, context);
  assert.equal(context.needs({ status: "completed", audio_file: "line.wav", dirty_fields: ["timing"] }), false);
  assert.equal(context.needs({ status: "completed", audio_file: "line.wav", dirty_fields: ["text"] }), true);
  assert.equal(context.needs({ status: "failed", audio_file: "old.wav", dirty_fields: [] }), true);
  assert.equal(context.needs({ status: "completed", audio_file: "", dirty_fields: [] }), true);

  const startSource = sourceBetween(renderer, "async function startBatch", "function cancelBatch");
  assert.ok(startSource.indexOf("await ensureDialogueProjectSaved()") < startSource.indexOf("const project = state.dialogueProject"));
  assert.ok(startSource.indexOf("const project = state.dialogueProject") < startSource.indexOf("const items = linesToGenerate.map"));
  assert.match(startSource, /project_revision: state\.projectBaseRevision/);
  assert.match(startSource, /if \(!linesToGenerate\.length\) return remixCompletedDialogueProject\(project\)/);

  const remixSource = sourceBetween(renderer, "async function remixCompletedDialogueProject", "async function startBatch");
  assert.match(remixSource, /\/api\/projects\/\$\{encodeURIComponent\(project\.project_id\)\}\/remix/);
  assert.match(remixSource, /expected_revision: state\.projectBaseRevision/);
  assert.match(remixSource, /无需重生成/);
});
