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
  assert.match(renderer, /const CREATION_TEMPLATES =/);
  assert.match(renderer, /function applyCreationTemplate/);
  assert.match(renderer, /quickLaunchStatus/);
  assert.match(renderer, /const DIRECTION_RECIPES =/);
  assert.match(renderer, /演绎要求：\$\{direction\}/);
  assert.doesNotMatch(renderer, /emotion_vector|duration_factor/);
});

test("global task feedback and keyboard tabs remain available across pages", () => {
  assert.match(html, /id="globalTaskBar"[^>]+aria-live="polite"/);
  assert.match(renderer, /function setGlobalTask/);
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

test("timeline controls have per-line accessible names and touch-sized handles", () => {
  assert.match(renderer, /第 \$\{line\.order\} 句音色/);
  assert.match(renderer, /第 \$\{line\.order\} 句语言/);
  assert.match(renderer, /第 \$\{line\.order\} 句演绎模式/);
  assert.match(renderer, /第 \$\{line\.order\} 句\$\{label\}/);
  assert.match(css, /\.timeline-handle\s*\{[^}]*width:\s*24px/);
});
