const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const renderer = fs.readFileSync(path.join(root, "src", "renderer.js"), "utf8");
const html = fs.readFileSync(path.join(root, "src", "index.html"), "utf8");
const css = fs.readFileSync(path.join(root, "src", "styles.css"), "utf8");

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
  const context = {};
  vm.runInNewContext(`${roleSource}; this.detect = detectRoleNames;`, context);
  assert.deepEqual(Array.from(context.detect("旁白：开始\n[小蓝] 你好\nAlice | Hello | en | cheerful")), ["旁白", "小蓝", "Alice"]);
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

test("voice library owns its reference-audio upload, transcript and playback flow", () => {
  for (const id of ["voiceMode", "voiceInstruction", "voiceReferenceAudio", "voiceReferenceText", "voiceReferencePreview", "voiceTranscribeButton", "voiceClearReferenceButton"]) {
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
  assert.match(css, /\.voice-reference-panel\s*\{[^}]*grid-template-columns/);
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
