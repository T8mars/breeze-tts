const state = {
  mode: "design",
  diagnostics: null,
  socket: null,
  downloadTimer: null,
  scheduledAudioTime: 0,
  scheduledSources: new Set(),
  audioContext: null,
  outputDirectory: null,
  referencePreviewUrl: null,
  voiceReferencePreviewUrl: null,
  voiceClearReference: false,
  generating: false,
  downloading: false,
  referenceDuration: null,
  referenceValidationId: 0,
  capabilities: {},
  voices: [],
  selectedVoiceId: "",
  batchSocket: null,
  batching: false,
  remixing: false,
  batchTotal: 0,
  batchCompleted: 0,
  batchRoles: [],
  transcribing: false,
  installingWhisper: false,
  whisperInstallComplete: false,
  updaterStatus: { configured: false, state: "disabled" },
  appInfo: {},
  activeTab: "generate",
  dialogueProject: null,
  projectBaseRevision: null,
  projectDirty: false,
  projectDraftTimer: null,
  globalTaskHideTimer: null,
  selectedLineId: "",
  launching: false,
  launchStartedAt: 0,
  launchTimer: null,
  historyItems: [],
  historyPage: 1,
  historyPageSize: 12,
  selectedRecipe: "",
  recipeBaseInstruction: "",
  recipeAppliedInstruction: ""
};

const $ = (id) => document.getElementById(id);
const MAX_REFERENCE_BYTES = 100 * 1024 * 1024;
const MAX_REFERENCE_SECONDS = 60;
const SUPPORTED_REFERENCE_EXTENSIONS = new Set(["wav", "flac", "ogg", "mp3"]);
const PROJECT_DRAFT_KEY = "t8-breeze-dialogue-project-draft-v1";
const PROJECT_DRAFT_DELAY_MS = 450;
const TIMELINE_TRACK_RENDER_LIMIT = 120;
const MIN_LINE_DURATION_MS = 50;
const MAX_TIMELINE_MS = 24 * 60 * 60 * 1000;
const LINE_GENERATION_DIRTY_FIELDS = new Set([
  "text", "role", "voice_id", "language", "direction_mode", "direction_text",
  "cfg_scale", "seed", "instruction", "reference"
]);
const GENERATION_CONFLICT_IDS = [
  "modelPath",
  "chooseModelButton",
  "acceptLicense",
  "activateModelButton",
  "verifyModelButton",
  "downloadModelButton",
  "cancelDownloadButton",
  "unloadButton",
  "chooseOutputButton"
];
const VOICE_CONTROL_IDS = ["voiceSelect", "libraryVoiceSelect", "voiceName", "voiceMode", "voiceLanguage", "voiceInstruction", "voiceReferenceAudio", "voiceReferenceText", "voiceTags", "voiceNotes", "voicePreviewText", "voiceFavorite", "newVoiceButton", "applyVoiceButton", "saveVoiceButton", "updateVoiceButton", "deleteVoiceButton", "refreshVoicesButton", "exportVoiceButton", "importVoiceButton"];
const BATCH_CONTROL_IDS = ["batchKind", "batchInput", "defaultRole", "defaultRolePreset", "analyzeRolesButton", "loadBatchExampleButton", "clearBatchInputButton"];
const CREATION_TEMPLATES = {
  blank: { tab: "generate", mode: "design" },
  narration: {
    tab: "generate",
    mode: "design",
    text: "欢迎来到今天的内容。接下来，让我们用清晰自然的节奏，一起了解重点。",
    instruction: "一位可靠、亲切的中文旁白，吐字清晰，语速自然，重点处适度停顿，适合短视频解说。"
  },
  audiobook: {
    tab: "generate",
    mode: "design",
    text: "夜色渐深，窗外只剩下风吹过树叶的声音。她停下脚步，回头望向来时的路。",
    instruction: "一位沉稳、有叙事感的中文讲述者，声音温暖，语速舒缓，句间留有自然呼吸和画面感。"
  },
  advertisement: {
    tab: "generate",
    mode: "design",
    text: "现在出发，让每一次灵感都更快变成作品。",
    instruction: "一位明快、自信、有感染力的中文广告配音，节奏紧凑，关键词有力度，结尾利落。"
  },
  dialogue: { tab: "dialogue", batchKind: "script" },
  subtitle: { tab: "dialogue", batchKind: "srt" },
  continue: { tab: "dialogue", draft: true }
};
const DIRECTION_RECIPES = {
  calm: "平静、克制，情绪稳定",
  warm: "温暖、亲切，带有自然的关怀感",
  excited: "兴奋、有感染力，重点词更有能量",
  tense: "紧张、警觉，保持适度压迫感",
  sad: "悲伤、内敛，保留克制的呼吸和停顿",
  whisper: "轻声、贴近耳语，但保持发音清楚",
  documentary: "沉稳、客观，具有纪录片旁白的可信感"
};
const RECIPE_INTENSITY = { subtle: "情绪克制", natural: "情绪自然", strong: "情绪鲜明" };
const RECIPE_PACE = { slow: "节奏舒缓", natural: "节奏自然", fast: "节奏紧凑" };
const INLINE_VOCAL_EVENT_ROLES = new Set(["笑", "咳嗽", "清嗓子", "叹气"]);
const BATCH_EXAMPLES = {
  items: "欢迎使用 Breeze TTS 2，这是普通批量的第一句示例。\n第二句可以继续编辑，解析后会进入可调整的时间轴。\nThis is a real English batch example.",
  script: "旁白：夜色渐深，故事从这里开始。\n小雨：[笑] 你终于来了！\n阿诚：(sigh) 对不起，让你久等了。",
  srt: "1\n00:00:00,000 --> 00:00:02,400\n欢迎使用 Breeze TTS 2。\n\n2\n00:00:02,700 --> 00:00:05,200\n这是可编辑时间轴的字幕示例。",
  txt: "旁白 | 欢迎使用逐句演绎 | zh | 平静、从容\n角色A | [笑] 很高兴见到你 | zh | 温暖、轻快\n角色B | This is a real English example. | en | confident",
  json: "{\n  \"name\": \"Breeze 示例工程\",\n  \"lines\": [\n    {\"role\": \"旁白\", \"text\": \"这是 JSON 工程示例。\", \"start_ms\": 0, \"end_ms\": 2400}\n  ]\n}"
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail ?? payload;
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.detail = detail;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function errorMessage(error) {
  const message = error instanceof Error ? error.message : String(error || "未知错误");
  if (/Failed to fetch|NetworkError|fetch/i.test(message)) return "无法连接本地服务";
  return message;
}

function actionableError(error, nextStep) {
  const message = errorMessage(error);
  return nextStep ? `${message}。建议：${nextStep}` : message;
}

function modelReportFromError(error) {
  const detail = error?.detail;
  if (detail && typeof detail === "object") {
    if (typeof detail.valid === "boolean") return detail;
    if (detail.model && typeof detail.model.valid === "boolean") return detail.model;
  }
  return null;
}

function modelPreparationMessage(report) {
  if (!report) return "仍在读取本地环境，请稍候再试。";
  if (!report.valid) {
    const missing = Array.isArray(report.missing) ? report.missing.length : 0;
    const damaged = (Array.isArray(report.size_mismatch) ? report.size_mismatch.length : 0)
      + (Array.isArray(report.hash_mismatch) ? report.hash_mismatch.length : 0);
    const details = [missing && `缺少 ${missing} 个文件`, damaged && `${damaged} 个文件需要修复`].filter(Boolean).join("，");
    return `模型尚未准备完整${details ? `（${details}）` : ""}。请展开下方设置，阅读许可证后点击“下载／修复模型”。`;
  }
  if (!report.license_accepted) return "模型文件已经完整，请阅读许可证、勾选同意，再点击“确认并启用”。";
  return "模型与许可证已经就绪。";
}

function conciseActionError(error, nextStep) {
  const report = modelReportFromError(error);
  if (report) return modelPreparationMessage(report);
  const message = errorMessage(error).replace(/\s+/g, " ").trim();
  const concise = message.length > 240 ? `${message.slice(0, 220)}…` : message;
  return nextStep ? `${concise}。建议：${nextStep}` : concise;
}

function setActionMessage(target, text, kind = "") {
  const element = typeof target === "string" ? $(target) : target;
  if (!element) return;
  element.textContent = text;
  element.classList.toggle("error-message", kind === "error");
  element.classList.toggle("success-message", kind === "success");
  if (kind === "error") element.setAttribute("role", "alert");
  else element.setAttribute("role", "status");
}

function setGlobalTask({ kind = "idle", kicker = "工作台", title = "准备创作", detail = "选择生成模式或导入脚本开始。", progress = null, target = "", cancellable = false } = {}) {
  const bar = $("globalTaskBar");
  if (!bar) return;
  window.clearTimeout(state.globalTaskHideTimer);
  state.globalTaskHideTimer = null;
  if (kind === "idle" && !target && title === "准备创作") {
    bar.hidden = true;
    return;
  }
  bar.hidden = false;
  bar.className = `global-task-bar ${kind}`;
  bar.dataset.kind = kind;
  bar.dataset.target = target;
  $("globalTaskKicker").textContent = kicker;
  $("globalTaskTitle").textContent = title;
  $("globalTaskDetail").textContent = detail;
  const progressElement = $("globalTaskProgress");
  progressElement.hidden = progress === null;
  if (progress === "indeterminate") progressElement.removeAttribute("value");
  else if (progress !== null) progressElement.value = Math.max(0, Math.min(100, Number(progress) || 0));
  $("globalTaskViewButton").hidden = !target;
  $("globalTaskCancelButton").hidden = !cancellable;
  if (kind === "idle" || kind === "success") {
    state.globalTaskHideTimer = window.setTimeout(() => {
      bar.hidden = true;
      state.globalTaskHideTimer = null;
    }, kind === "success" ? 7000 : 3500);
  }
}

function updateContinueDraftTemplate() {
  const button = $("continueDraftTemplate");
  if (!button) return;
  let draft = null;
  try {
    const raw = localStorage.getItem(PROJECT_DRAFT_KEY);
    if (raw) draft = JSON.parse(raw);
  } catch (_) { /* 本地存储不可用时只禁用恢复入口。 */ }
  const count = Array.isArray(draft?.project?.lines) ? draft.project.lines.length : 0;
  button.disabled = count === 0;
  const summary = button.querySelector("span");
  if (summary) summary.textContent = count ? `恢复“${draft.project.name || "未命名工程"}” · ${count} 句` : "暂无可恢复的自动草稿";
}

function updateRecipeSelection() {
  document.querySelectorAll(".recipe-chip").forEach((button) => {
    const active = button.dataset.recipe === state.selectedRecipe;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function clearDirectionRecipe({ preserveInstruction = false } = {}) {
  if (!preserveInstruction && state.recipeAppliedInstruction && $("instruction").value === state.recipeAppliedInstruction) {
    $("instruction").value = state.recipeBaseInstruction;
  }
  state.selectedRecipe = "";
  state.recipeBaseInstruction = "";
  state.recipeAppliedInstruction = "";
  updateRecipeSelection();
  setActionMessage("directionRecipeStatus", "选择一个配方即可应用，可继续手动修改。");
}

function applyDirectionRecipe(recipeId = state.selectedRecipe) {
  const recipe = DIRECTION_RECIPES[recipeId];
  if (!recipe) return clearDirectionRecipe();
  const current = $("instruction").value.trim();
  const base = current === state.recipeAppliedInstruction ? state.recipeBaseInstruction : current;
  state.selectedRecipe = recipeId;
  state.recipeBaseInstruction = base;
  const direction = `${recipe}，${RECIPE_INTENSITY[$("recipeIntensity").value]}，${RECIPE_PACE[$("recipePace").value]}，保持发音清晰并自然控制停顿。`;
  state.recipeAppliedInstruction = base ? `${base}\n演绎要求：${direction}` : direction;
  $("instruction").value = state.recipeAppliedInstruction;
  updateRecipeSelection();
  clearSelectedVoice("演绎配方已应用，当前切换为手动配置。");
  setActionMessage("directionRecipeStatus", `已应用“${document.querySelector(`.recipe-chip[data-recipe="${recipeId}"]`)?.textContent || recipeId}”配方，可直接生成或继续修改。`, "success");
}

function updateProjectSaveState(text) {
  const indicator = $("projectSaveState");
  if (!indicator) return;
  indicator.textContent = text || (state.projectDirty ? "有未保存修改 · 已自动保存草稿" : "工程修改已保存");
  indicator.classList.toggle("unsaved", state.projectDirty);
}

function persistProjectDraft() {
  if (!state.dialogueProject || !state.projectDirty) return;
  try {
    localStorage.setItem(PROJECT_DRAFT_KEY, JSON.stringify({
      saved_at: Date.now(),
      base_revision: state.projectBaseRevision,
      project: state.dialogueProject
    }));
    updateProjectSaveState("有未保存修改 · 草稿已自动保存");
    updateContinueDraftTemplate();
  } catch (error) {
    updateProjectSaveState(`有未保存修改 · 草稿保存失败：${errorMessage(error)}`);
  }
}

function scheduleProjectDraft() {
  window.clearTimeout(state.projectDraftTimer);
  state.projectDraftTimer = window.setTimeout(persistProjectDraft, PROJECT_DRAFT_DELAY_MS);
}

function markProjectDirty(message = "有未保存修改 · 正在保存草稿…") {
  state.projectDirty = true;
  updateProjectSaveState(message);
  scheduleProjectDraft();
}

function clearProjectDraft() {
  window.clearTimeout(state.projectDraftTimer);
  state.projectDraftTimer = null;
  try { localStorage.removeItem(PROJECT_DRAFT_KEY); } catch (_) { /* 存储不可用时仍允许继续使用工程。 */ }
  updateContinueDraftTemplate();
}

function markProjectSaved(message = "工程修改已保存") {
  state.projectDirty = false;
  clearProjectDraft();
  updateProjectSaveState(message);
}

function confirmReplaceDirtyProject(action) {
  if (!state.projectDirty) return true;
  persistProjectDraft();
  return window.confirm(`当前对白工程尚未正式保存。继续${action}会用新工程替换当前自动草稿，是否继续？`);
}

function restoreProjectDraft() {
  if (state.dialogueProject) return false;
  try {
    const raw = localStorage.getItem(PROJECT_DRAFT_KEY);
    if (!raw) return false;
    const draft = JSON.parse(raw);
    if (!draft?.project?.project_id || !Array.isArray(draft.project.lines)) throw new Error("草稿格式无效");
    state.dialogueProject = draft.project;
    state.projectBaseRevision = Number.isInteger(draft.base_revision) ? draft.base_revision : null;
    state.projectDirty = true;
    state.selectedLineId = draft.project.lines[0]?.line_id || "";
    $("timingPolicy").value = draft.project.timing?.policy || "preserve";
    renderRoleMappings(draft.project.lines.map((line) => line.role));
    renderTimeline();
    const time = draft.saved_at ? new Date(draft.saved_at).toLocaleString() : "上次退出前";
    updateProjectSaveState(`已恢复 ${time} 的自动草稿 · 尚未正式保存`);
    setActionMessage("batchStatus", `已恢复未保存工程“${draft.project.name || "未命名工程"}”，请确认后保存。`);
    updateContinueDraftTemplate();
    return true;
  } catch (error) {
    clearProjectDraft();
    setActionMessage("batchStatus", actionableError(error, "草稿已忽略，请重新导入或解析工程"), "error");
    return false;
  }
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value.toFixed(index > 2 ? 2 : 1)} ${units[index]}`;
}

function setStatus(kind, text) {
  $("statusDot").className = `status-dot ${kind || ""}`;
  $("statusText").textContent = text;
}

function updateControlState() {
  const generationBusy = state.generating || state.batching;
  for (const id of GENERATION_CONFLICT_IDS) {
    const element = $(id);
    if (element) element.disabled = generationBusy || (state.downloading && id !== "chooseOutputButton");
  }
  for (const id of VOICE_CONTROL_IDS) $(id).disabled = generationBusy;
  const selectedLibraryVoice = state.voices.some((voice) => voice.id === $("libraryVoiceSelect")?.value);
  for (const id of ["applyVoiceButton", "updateVoiceButton", "deleteVoiceButton", "exportVoiceButton"]) {
    const element = $(id);
    if (element) element.disabled = generationBusy || !selectedLibraryVoice;
  }
  for (const id of BATCH_CONTROL_IDS) {
    const element = $(id);
    if (element) element.disabled = state.batching || (id === "analyzeRolesButton" && !$("batchInput").value.trim());
  }
  $("generateButton").disabled = generationBusy || state.downloading;
  $("cancelGenerateButton").disabled = !state.generating;
  $("downloadModelButton").disabled = generationBusy || state.downloading;
  $("cancelDownloadButton").disabled = generationBusy || !state.downloading;
  $("batchStartButton").disabled = generationBusy || state.downloading || state.capabilities.batch === false;
  $("batchCancelButton").disabled = !state.batching || state.remixing;
  for (const id of ["parseDialogueButton", "scriptFileInput", "addDialogueLineButton", "saveProjectButton", "importProjectButton", "exportProjectButton", "exportSrtButton", "timingPolicy"]) {
    const element = $(id);
    if (element) element.disabled = state.batching;
  }
  for (const control of document.querySelectorAll('#timelineBody input, #timelineBody select, #timelineBody textarea, #timelineBody button')) control.disabled = state.batching;
  for (const select of document.querySelectorAll('#roleMappingPanel select[data-role]')) select.disabled = state.batching;
  for (const block of document.querySelectorAll('#timelineTrack .timeline-block')) block.tabIndex = state.batching ? -1 : 0;
  for (const handle of document.querySelectorAll('#timelineTrack .timeline-handle')) handle.disabled = state.batching;
  $("timelineTrack")?.setAttribute("aria-disabled", String(state.batching));
  $("timelineTrack")?.classList.toggle("busy", state.batching);
  $("transcribeButton").disabled = generationBusy || state.transcribing || !state.capabilities.whisper;
  $("voiceTranscribeButton").disabled = generationBusy || state.transcribing || !state.capabilities.whisper || !$("voiceReferenceAudio").files[0];
  const selectedEditorVoice = state.voices.find((voice) => voice.id === $("libraryVoiceSelect")?.value);
  $("voiceClearReferenceButton").disabled = generationBusy || $("voiceMode").value !== "design" || !selectedEditorVoice?.has_reference || state.voiceClearReference;
  $("installWhisperButton").disabled = generationBusy || state.installingWhisper || state.whisperInstallComplete;
  $("checkUpdateButton").disabled = !state.updaterStatus.configured || ["checking", "downloading"].includes(state.updaterStatus.state);
  $("installUpdateButton").disabled = state.updaterStatus.state !== "downloaded";
}

function transformersCompatible(version) {
  const match = String(version || "").match(/^(\d+)\.(\d+)/);
  if (!match) return false;
  const major = Number(match[1]);
  const minor = Number(match[2]);
  return (major === 4 && minor >= 57) || major === 5;
}

function renderModel(report) {
  const summary = $("modelSummary");
  summary.classList.remove("ready-text");
  if (report.valid) {
    const license = report.license_accepted ? "许可证已确认" : "等待许可证确认";
    summary.textContent = `模型完整 · ${formatBytes(report.total_size)} · ${license} · revision ${report.revision.slice(0, 12)}`;
    summary.classList.add("ready-text");
    setStatus(report.license_accepted ? "ready" : "", report.license_accepted ? "环境与模型已就绪" : "请确认模型许可证");
    $("advancedLauncherSummary").textContent = report.license_accepted ? "模型与许可证已就绪 · 需要时展开调整" : "模型完整 · 等待确认许可证";
  } else {
    const problems = [
      report.missing.length && `缺少 ${report.missing.length} 个文件`,
      report.size_mismatch.length && `${report.size_mismatch.length} 个大小错误`,
      report.hash_mismatch.length && `${report.hash_mismatch.length} 个哈希错误`
    ].filter(Boolean).join("；");
    summary.textContent = problems || "模型尚未安装。";
    setStatus("", "等待完整模型");
    $("advancedLauncherSummary").textContent = "模型尚未就绪 · 展开完成配置";
  }
}

function rememberActiveModel(report, runtime = null) {
  state.diagnostics = state.diagnostics || {};
  state.diagnostics.model = report;
  if (runtime) state.diagnostics.runtime = runtime;
}

function renderDiagnostics(data) {
  state.diagnostics = data;
  const gpu = data.gpu?.devices?.[0];
  const packages = data.packages || {};
  const entries = [
    ["GPU", gpu ? `${gpu.name} · ${formatBytes(gpu.memory_free_mib * 1024 ** 2)} 可用` : "未检测到 NVIDIA GPU", Boolean(gpu)],
    ["Python", `${data.python.version} · ${data.python.architecture}`, true],
    ["PyTorch", `${packages.torch || "缺失"} · CUDA ${gpu ? "可检测" : "不可用"}`, Boolean(packages.torch)],
    ["Transformers", packages.transformers || "缺失", transformersCompatible(packages.transformers)],
    ["Qwen TTS", packages["qwen-tts"] || "缺失", packages["qwen-tts"] === "0.1.1"],
    ["模型运行时", data.runtime.loaded ? "已加载" : "按需加载", true]
  ];
  const cards = () => entries.map(([name, value, ready]) => {
    const item = document.createElement("div");
    item.className = `diagnostic ${ready ? "ready" : "warning"}`;
    const heading = document.createElement("strong");
    heading.textContent = name;
    item.append(heading, document.createTextNode(value));
    return item;
  });
  $("diagnosticsGrid")?.replaceChildren(...cards());
  $("settingsDiagnosticsGrid")?.replaceChildren(...cards());
  if ($("settingsDiagnosticStatus")) {
    const runtime = data.runtime?.loaded ? "模型已加载" : "模型未占用显存";
    $("settingsDiagnosticStatus").textContent = `诊断已更新 · ${runtime} · ${new Date().toLocaleTimeString()}`;
  }
  $("buildMeta").textContent = `Desktop ${data.project_version} · Core ${data.core_revision.slice(0, 8)} · Model ${data.model_revision.slice(0, 8)}`;
}

function renderCapabilities(capabilities) {
  state.capabilities = capabilities || {};
  const labels = {
    long_text: "长文本",
    batch: "顺序批量",
    multi_role: "多角色",
    voice_library: "音色库",
    srt: "SRT",
    whisper: "Whisper",
    fast_24gb: "24GB 加速",
    editable_timeline: "可编辑时间轴",
    per_line_direction: "逐句演绎",
    projects: "工程包",
    persistent_queue: "任务恢复"
  };
  $("capabilitiesList").replaceChildren(...Object.entries(labels).map(([key, label]) => {
    const item = document.createElement("span");
    item.className = `capability ${capabilities?.[key] ? "" : "off"}`;
    item.textContent = `${label} ${capabilities?.[key] ? "可用" : "未启用"}`;
    return item;
  }));
  $("whisperStatus").textContent = capabilities?.whisper
    ? (capabilities?.whisper_small_bundled
      ? "Whisper small 已随整合包内置，可直接离线转写；其他规格首次使用时联网下载并缓存。"
      : "Whisper 引擎可用，但内置 small 模型缺失；可继续联网下载，建议重新下载完整整合包。")
    : "内置 faster-whisper 组件缺失；请重新下载完整整合包，或点击下方按钮联网修复。";
  $("installWhisperButton").hidden = Boolean(capabilities?.whisper) || state.whisperInstallComplete;
  updateControlState();
}

function voiceLabel(voice) {
  const modes = { design: "Design", clone: "Clone", direction: "Direction" };
  return `${voice.name} · ${modes[voice.mode] || voice.mode}${voice.has_reference ? " · 含参考音频" : ""}`;
}

function voiceOption(voice, selectedValue = "") {
  const option = document.createElement("option");
  option.value = voice.id;
  option.textContent = voiceLabel(voice);
  option.selected = voice.id === selectedValue;
  return option;
}

function renderDefaultRolePresets() {
  const preset = $("defaultRolePreset");
  const previous = preset.value;
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = state.voices.length ? "从音色库选择…" : "音色库暂无音色";
  preset.replaceChildren(placeholder, ...state.voices.map((voice) => voiceOption(voice, previous)));
  const matched = state.voices.find((voice) => normalizeRoleName(voice.name) === normalizeRoleName($("defaultRole").value));
  preset.value = state.voices.some((voice) => voice.id === previous) ? previous : (matched?.id || "");
}

function renderVoices(voices) {
  state.voices = Array.isArray(voices) ? voices : [];
  if (!state.voices.some((voice) => voice.id === state.selectedVoiceId)) state.selectedVoiceId = "";
  const manual = document.createElement("option");
  manual.value = "";
  manual.textContent = "手动配置（不使用音色库）";
  manual.selected = !state.selectedVoiceId;
  $("voiceSelect").replaceChildren(manual, ...state.voices.map((voice) => voiceOption(voice, state.selectedVoiceId)));
  renderDefaultRolePresets();
  renderLibraryVoices();
  if (state.batchRoles.length) renderRoleMappings(state.batchRoles);
}

function filteredVoices() {
  const query = $("voiceSearch").value.trim().toLowerCase();
  const favoriteOnly = $("favoriteOnly").checked;
  return state.voices.filter((voice) => {
    if (favoriteOnly && !voice.favorite) return false;
    if (!query) return true;
    return [voice.name, voice.notes, voice.instruction, ...(voice.tags || [])]
      .join(" ").toLowerCase().includes(query);
  });
}

function renderLibraryVoices() {
  const selected = $("libraryVoiceSelect").value || state.selectedVoiceId;
  const options = filteredVoices().map((voice) => voiceOption(voice, selected));
  $("libraryVoiceSelect").replaceChildren(...options);
  if (selected && options.some((option) => option.value === selected)) $("libraryVoiceSelect").value = selected;
  const empty = $("voiceEmptyState");
  $("libraryVoiceSelect").hidden = options.length === 0;
  empty.hidden = options.length > 0;
  if (!empty.hidden) {
    const hasAnyVoice = state.voices.length > 0;
    empty.querySelector("strong").textContent = hasAnyVoice ? "没有匹配的音色" : "还没有可用音色";
    empty.querySelector("span").textContent = hasAnyVoice
      ? "换一个关键词或关闭“只看收藏”后再试。"
      : "点击“开始新建”，上传参考音频并保存为可复用音色。";
  }
  updateControlState();
}

async function refreshVoices() {
  const result = await api("/api/voices");
  renderVoices(result.voices);
}

async function refresh() {
  try {
    const [data, appInfo, capabilities, voices] = await Promise.all([
      api("/api/diagnostics"),
      window.t8Desktop?.appInfo() || Promise.resolve({}),
      api("/api/capabilities").catch(() => ({})),
      api("/api/voices").catch(() => ({ voices: [] }))
    ]);
    $("modelPath").value = data.runtime.model_dir;
    state.outputDirectory = appInfo.outputDirectory || state.outputDirectory;
    $("outputPath").value = state.outputDirectory || "";
    renderDiagnostics(data);
    renderModel(data.model);
    renderCapabilities(capabilities);
    renderVoices(voices.voices);
    state.appInfo = appInfo || {};
    try {
      const settings = await api("/api/settings");
      const outputDirectory = settings.output_directory || settings.output_dir;
      if (outputDirectory) {
        state.outputDirectory = outputDirectory;
        $("outputPath").value = outputDirectory;
      }
    } catch (_) {
      // Compatibility fallback for older local backends.
    }
  } catch (error) {
    setStatus("error", "本地服务异常");
    $("modelSummary").textContent = actionableError(error, "确认桌面后端正在运行，或重启应用后再试");
  }
}

function selectMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-tab").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  $("referenceField").hidden = mode === "design";
  $("instructionField").hidden = mode === "clone";
  $("directionRecipePanel").hidden = mode === "clone";
  $("instructionField").querySelector("label").textContent = mode === "direction" ? "本句演绎指令" : "音色设计与演绎指令";
  $("cfgScale").value = mode === "clone" ? "1" : "4";
  if (mode === "clone") clearDirectionRecipe();
}

async function fileToBase64(file) {
  if (!file) return "";
  if (file.size > MAX_REFERENCE_BYTES) throw new Error("参考音频超过 100 MiB 限制。");
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(new Error("无法读取参考音频文件。"));
    reader.readAsDataURL(file);
  });
}

function referenceExtension(file) {
  const parts = String(file?.name || "").toLowerCase().split(".");
  return parts.length > 1 ? parts.pop() : "";
}

async function inspectReferenceAudio(file) {
  if (!file) return null;
  const extension = referenceExtension(file);
  if (!SUPPORTED_REFERENCE_EXTENSIONS.has(extension)) {
    throw new Error("参考音频仅支持 WAV、FLAC、OGG 或 MP3；M4A/AAC 当前后端无法可靠读取。");
  }
  if (file.size > MAX_REFERENCE_BYTES) throw new Error("参考音频超过 100 MiB 限制。");
  const objectUrl = URL.createObjectURL(file);
  try {
    const duration = await new Promise((resolve, reject) => {
      const audio = document.createElement("audio");
      let timer;
      const cleanup = () => {
        clearTimeout(timer);
        audio.onloadedmetadata = null;
        audio.onerror = null;
        audio.removeAttribute("src");
        audio.load();
      };
      timer = setTimeout(() => {
        cleanup();
        reject(new Error("无法在限定时间内读取参考音频信息。"));
      }, 10000);
      audio.preload = "metadata";
      audio.onloadedmetadata = () => {
        const value = audio.duration;
        cleanup();
        if (!Number.isFinite(value) || value <= 0) reject(new Error("无法读取参考音频时长，请检查文件是否损坏。"));
        else resolve(value);
      };
      audio.onerror = () => {
        cleanup();
        reject(new Error("参考音频无法解码，请转换为 WAV、FLAC、OGG 或 MP3。"));
      };
      audio.src = objectUrl;
    });
    if (duration > MAX_REFERENCE_SECONDS) {
      throw new Error(`参考音频为 ${duration.toFixed(1)} 秒，超过 ${MAX_REFERENCE_SECONDS} 秒限制；请先裁剪。`);
    }
    return duration;
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

function schedulePcm(arrayBuffer, sampleRate) {
  if (!state.audioContext) state.audioContext = new AudioContext({ sampleRate });
  const samples = new Int16Array(arrayBuffer);
  const audioBuffer = state.audioContext.createBuffer(1, samples.length, sampleRate);
  const channel = audioBuffer.getChannelData(0);
  for (let index = 0; index < samples.length; index += 1) channel[index] = samples[index] / 32768;
  const source = state.audioContext.createBufferSource();
  source.buffer = audioBuffer;
  source.connect(state.audioContext.destination);
  state.scheduledSources.add(source);
  source.onended = () => state.scheduledSources.delete(source);
  const now = state.audioContext.currentTime;
  state.scheduledAudioTime = Math.max(state.scheduledAudioTime, now + 0.04);
  source.start(state.scheduledAudioTime);
  state.scheduledAudioTime += audioBuffer.duration;
}

function stopScheduledAudio() {
  for (const source of state.scheduledSources) {
    try { source.stop(); } catch (_) { /* Already stopped. */ }
  }
  state.scheduledSources.clear();
  state.scheduledAudioTime = 0;
}

function cancelGeneration() {
  stopScheduledAudio();
  if (state.socket?.readyState === WebSocket.OPEN) state.socket.send(JSON.stringify({ type: "cancel" }));
  else if (state.socket?.readyState === WebSocket.CONNECTING) state.socket.close();
  $("generationStatus").textContent = "正在取消…";
  setGlobalTask({ kind: "working", kicker: "单句生成", title: "正在停止任务", detail: "已发送取消请求，正在等待模型安全结束。", progress: "indeterminate", target: "generate" });
}

async function generate() {
  if (state.socket || state.generating || state.batching) return;
  if (state.downloading) throw new Error("模型正在下载／修复，请等待完成后再生成。");
  state.generating = true;
  updateControlState();
  stopScheduledAudio();
  let socket;
  try {
    const reference = $("referenceAudio").files[0];
    const text = $("targetText").value.trim();
    const instruction = $("instruction").value.trim();
    const referenceText = $("referenceText").value.trim();
    const cfgScale = Number($("cfgScale").value);
    const seed = Number($("seed").value);
    const maxNewTokens = Number($("maxTokens").value);
    const usesSavedVoice = Boolean(state.selectedVoiceId);
    if (!text) throw new Error("目标文本不能为空。");
    if (!usesSavedVoice && ["clone", "direction"].includes(state.mode) && (!reference || !referenceText)) {
      throw new Error("Voice Clone/Direction 必须提供参考音频和准确逐字稿。");
    }
    if (!usesSavedVoice && ["design", "direction"].includes(state.mode) && !instruction) {
      throw new Error("Voice Design/Direction 的演绎指令不能为空。");
    }
    if (!Number.isFinite(cfgScale) || cfgScale <= 0) throw new Error("CFG 必须大于 0。");
    if (!Number.isInteger(seed) || seed < 0 || seed > 2147483647) throw new Error("Seed 超出有效范围。");
    if (!Number.isInteger(maxNewTokens) || maxNewTokens < 64 || maxNewTokens > 1500) {
      throw new Error("最大音频帧必须在 64 到 1500 之间。");
    }
    if (reference && !usesSavedVoice) {
      state.referenceDuration = await inspectReferenceAudio(reference);
      $("referenceAudioStatus").textContent = `格式可用 · ${state.referenceDuration.toFixed(1)} 秒`;
    }
    const payload = {
      mode: state.mode,
      voice_id: state.selectedVoiceId,
      text,
      instruction,
      reference_text: referenceText,
      reference_filename: !usesSavedVoice && reference?.name ? reference.name : "reference.wav",
      reference_audio_base64: usesSavedVoice ? "" : await fileToBase64(reference),
      cfg_scale: cfgScale,
      seed,
      fast_all: $("runtimeProfile").value === "fast",
      max_new_tokens: maxNewTokens
    };
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/generate`);
    state.socket = socket;
    socket.binaryType = "arraybuffer";
    state.scheduledAudioTime = 0;
    $("generationStatus").textContent = "正在准备模型…";
    $("generationMeta").textContent = "任务已提交；首次加载模型可能需要数十秒。";
    setGlobalTask({ kind: "working", kicker: "单句生成", title: "正在准备模型", detail: "任务已提交；首次加载模型可能需要数十秒。", progress: "indeterminate", target: "generate", cancellable: true });
    socket.onopen = () => socket.send(JSON.stringify(payload));
  } catch (error) {
    state.generating = false;
    updateControlState();
    setGlobalTask({ kind: "error", kicker: "单句生成", title: "无法开始生成", detail: errorMessage(error), target: "generate" });
    throw error;
  }
  socket.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      schedulePcm(event.data, 24000);
      $("generationStatus").textContent = "正在流式生成和播放…";
      setGlobalTask({ kind: "working", kicker: "单句生成", title: "正在生成并流式试听", detail: "可以切换分页查看其他内容，生成会继续进行。", progress: "indeterminate", target: "generate", cancellable: true });
      return;
    }
    const message = JSON.parse(event.data);
    if (message.type === "complete") {
      const url = `/api/outputs/${encodeURIComponent(message.output)}`;
      $("outputAudio").src = url;
      $("downloadOutput").href = url;
      $("downloadOutput").hidden = false;
      $("openOutputButton").hidden = false;
      state.outputDirectory = message.metadata.output.replace(/[\\/][^\\/]+$/, "");
      $("generationMeta").textContent = JSON.stringify(message.metadata, null, 2);
      $("generationStatus").textContent = "生成完成";
      setGlobalTask({ kind: "success", kicker: "单句生成", title: "生成完成", detail: `已保存 ${message.output}`, progress: 100, target: "generate" });
    } else if (message.type === "error") {
      $("generationStatus").textContent = `失败：${message.message}`;
      $("generationMeta").textContent = `${message.error_type}: ${message.message}`;
      setGlobalTask({ kind: "error", kicker: "单句生成", title: "生成失败", detail: message.message, target: "generate" });
    } else if (message.type === "cancelled") {
      stopScheduledAudio();
      $("generationStatus").textContent = "已取消";
      setGlobalTask({ kind: "idle", kicker: "单句生成", title: "任务已取消", detail: "可以修改文本或参数后重新生成。", target: "generate" });
    }
  };
  socket.onclose = () => {
    state.socket = null;
    state.generating = false;
    updateControlState();
    refresh();
  };
  socket.onerror = () => {
    stopScheduledAudio();
    $("generationStatus").textContent = "WebSocket 连接失败";
    setGlobalTask({ kind: "error", kicker: "单句生成", title: "连接失败", detail: "本地生成服务连接中断，请返回设置与诊断检查服务。", target: "generate" });
  };
}

async function chooseModelDirectory() {
  if (!window.t8Desktop) return;
  const selected = await window.t8Desktop.chooseModelDirectory();
  if (selected) {
    $("modelPath").value = selected;
    await window.t8Desktop.saveDirectorySetting("modelDirectory", selected);
    const report = await api("/api/models/validate", { method: "POST", body: JSON.stringify({ model_dir: selected }) });
    renderModel(report);
    if (report.valid && (report.license_accepted || $("acceptLicense").checked)) {
      try {
        const selectedReport = await api("/api/models/select", {
          method: "POST",
          body: JSON.stringify({ model_dir: selected, accept_model_license: $("acceptLicense").checked })
        });
        await window.t8Desktop.saveDirectorySetting("modelDirectory", selected);
        rememberActiveModel(selectedReport.model, selectedReport.runtime);
        renderModel(selectedReport.model);
      } catch (error) {
        await refresh().catch(() => {});
        throw error;
      }
    } else if (report.valid) {
      $("downloadStatus").textContent = "模型完整；勾选许可证后再次选择此目录以启用。";
    }
  }
}

async function downloadModel() {
  if (state.generating || state.batching) throw new Error("生成期间不能下载或修复模型。");
  state.downloading = true;
  updateControlState();
  const modelDir = $("modelPath").value.trim();
  try {
    await window.t8Desktop?.saveDirectorySetting("modelDirectory", modelDir);
    await api("/api/models/download", {
      method: "POST",
      body: JSON.stringify({ model_dir: modelDir, accept_model_license: $("acceptLicense").checked })
    });
  } catch (error) {
    state.downloading = false;
    updateControlState();
    throw error;
  }
  $("downloadProgress").hidden = false;
  $("cancelDownloadButton").hidden = false;
  if (state.downloadTimer) clearInterval(state.downloadTimer);
  state.downloadTimer = setInterval(pollDownloadSafely, 1000);
  pollDownloadSafely();
}

async function activateCurrentModel() {
  let result;
  try {
    result = await api("/api/models/select", {
      method: "POST",
      body: JSON.stringify({
        model_dir: $("modelPath").value.trim(),
        verify_hashes: false,
        accept_model_license: $("acceptLicense").checked
      })
    });
  } catch (error) {
    await refresh().catch(() => {});
    throw error;
  }
  await window.t8Desktop?.saveDirectorySetting("modelDirectory", $("modelPath").value.trim());
  rememberActiveModel(result.model, result.runtime);
  renderModel(result.model);
  $("quickLaunchFeedback").hidden = true;
  setActionMessage("launchStatus", "模型已就绪，可点击启动或选择创作模板。", "success");
  $("downloadStatus").textContent = "当前模型已启用；首次生成时按需加载到显存。";
}

async function pollDownload() {
  const status = await api("/api/models/download");
  const percent = status.total_bytes ? Math.min(100, status.downloaded_bytes / status.total_bytes * 100) : 0;
  $("downloadProgress").value = percent;
  const speed = formatBytes(status.bytes_per_second || 0);
  $("downloadStatus").textContent = `${status.message} ${percent.toFixed(1)}% · ${speed}/s`;
  if (["completed", "failed", "cancelled"].includes(status.status)) {
    clearInterval(state.downloadTimer);
    state.downloadTimer = null;
    state.downloading = false;
    $("cancelDownloadButton").hidden = true;
    updateControlState();
    if (status.error) $("downloadStatus").textContent += ` · ${status.error}`;
    if (status.status === "completed") {
      await api("/api/models/select", {
        method: "POST",
        body: JSON.stringify({ model_dir: status.model_dir, accept_model_license: true })
      });
      await window.t8Desktop?.saveDirectorySetting("modelDirectory", status.model_dir);
    }
    refresh();
  }
}

async function pollDownloadSafely() {
  try {
    await pollDownload();
  } catch (error) {
    if (state.downloadTimer) clearInterval(state.downloadTimer);
    state.downloadTimer = null;
    state.downloading = false;
    $("cancelDownloadButton").hidden = true;
    $("downloadStatus").textContent = `无法读取下载状态：${error.message}`;
    updateControlState();
  }
}

async function verifyCurrentModel() {
  $("downloadStatus").textContent = "正在执行完整 SHA-256 校验，请稍候…";
  const report = await api("/api/models/validate", {
    method: "POST",
    body: JSON.stringify({ model_dir: $("modelPath").value.trim(), verify_hashes: true })
  });
  renderModel(report);
  $("downloadStatus").textContent = report.valid
    ? "完整校验通过，模型文件与固定清单一致。"
    : "完整校验失败；请点击“下载／修复官方模型”。";
}

async function chooseOutputDirectory() {
  if (!window.t8Desktop || state.generating) return;
  const selected = await window.t8Desktop.chooseOutputDirectory();
  if (!selected) return;
  const settings = await api("/api/settings/output-directory", {
    method: "POST",
    body: JSON.stringify({ path: selected })
  });
  await window.t8Desktop.saveDirectorySetting("outputDirectory", selected);
  state.outputDirectory = settings.output_directory || settings.output_dir || selected;
  $("outputPath").value = state.outputDirectory;
  $("outputStatus").textContent = "输出目录已保存，新任务会写入此目录。";
}

function clearSelectedVoice(message = "已切换为手动配置。") {
  if (!state.selectedVoiceId) return;
  state.selectedVoiceId = "";
  $("voiceSelect").value = "";
  $("voiceStatus").textContent = message;
}

function applySelectedVoice() {
  const voice = state.voices.find((item) => item.id === $("voiceSelect").value);
  if (!voice) {
    state.selectedVoiceId = "";
    $("voiceStatus").textContent = "当前使用手动配置。";
    return;
  }
  state.selectedVoiceId = voice.id;
  selectMode(voice.mode);
  clearDirectionRecipe({ preserveInstruction: true });
  $("instruction").value = voice.instruction || "";
  $("referenceText").value = voice.reference_text || "";
  $("referenceAudio").value = "";
  if (state.referencePreviewUrl) URL.revokeObjectURL(state.referencePreviewUrl);
  state.referencePreviewUrl = null;
  $("referencePreview").src = "";
  $("referencePreview").hidden = true;
  $("referenceAudioStatus").textContent = voice.has_reference
    ? "将使用音色库中保存的私有参考音频。"
    : `支持 WAV / FLAC / OGG / MP3，最长 ${MAX_REFERENCE_SECONDS} 秒。`;
  $("voiceStatus").textContent = `正在使用：${voiceLabel(voice)}。修改模式或参考配置会切回手动配置。`;
}

function clearVoiceReferencePreview() {
  if (state.voiceReferencePreviewUrl) URL.revokeObjectURL(state.voiceReferencePreviewUrl);
  state.voiceReferencePreviewUrl = null;
  const preview = $("voiceReferencePreview");
  preview.pause();
  preview.removeAttribute("src");
  preview.load();
  preview.hidden = true;
}

function renderVoiceReferenceEditor(voice = null) {
  clearVoiceReferencePreview();
  $("voiceReferenceAudio").value = "";
  state.voiceClearReference = false;
  if (voice?.has_reference) {
    $("voiceReferencePreview").src = `/api/voices/${encodeURIComponent(voice.id)}/reference?v=${encodeURIComponent(voice.updated_at || "")}`;
    $("voiceReferencePreview").hidden = false;
    $("voiceReferenceStatus").textContent = "已载入音色库中的参考音频，可直接试听；重新上传会在保存时替换。";
  } else {
    $("voiceReferenceStatus").textContent = $("voiceMode").value === "design"
      ? "声音设计可不上传；上传后也会随音色一起保存。"
      : `请上传 WAV / FLAC / OGG / MP3（最长 ${MAX_REFERENCE_SECONDS} 秒），并填写准确逐字稿。`;
  }
  updateControlState();
}

async function previewVoiceReferenceFile() {
  const file = $("voiceReferenceAudio").files[0];
  if (!file) {
    const selected = state.voices.find((voice) => voice.id === $("libraryVoiceSelect").value);
    renderVoiceReferenceEditor(selected || null);
    return;
  }
  const duration = await inspectReferenceAudio(file);
  clearVoiceReferencePreview();
  state.voiceReferencePreviewUrl = URL.createObjectURL(file);
  $("voiceReferencePreview").src = state.voiceReferencePreviewUrl;
  $("voiceReferencePreview").hidden = false;
  state.voiceClearReference = false;
  $("voiceReferenceStatus").textContent = `待保存的新参考音频：${file.name} · ${duration.toFixed(1)} 秒。`;
  updateControlState();
}

function updateVoiceModeEditor() {
  const mode = $("voiceMode").value;
  const selected = state.voices.find((voice) => voice.id === $("libraryVoiceSelect").value);
  const hasPendingFile = Boolean($("voiceReferenceAudio").files[0]);
  if (!hasPendingFile && !(selected?.has_reference && !state.voiceClearReference)) {
    $("voiceReferenceStatus").textContent = mode === "design"
      ? "声音设计可只保存文字描述，也可以附带参考音频。"
      : "声音克隆／演绎导演必须上传参考音频并填写准确逐字稿。";
  }
  updateControlState();
}

function markVoiceReferenceForRemoval() {
  if ($("voiceMode").value !== "design") {
    $("voiceReferenceStatus").textContent = "克隆和导演音色必须保留参考音频；可先改为声音设计，或上传新音频替换。";
    return;
  }
  state.voiceClearReference = true;
  $("voiceReferenceAudio").value = "";
  clearVoiceReferencePreview();
  $("voiceReferenceStatus").textContent = "已标记移除；点击“保存修改”后才会真正删除库内参考音频。";
  updateControlState();
}

function beginNewVoice(message = "当前为新建模式：填写名称，上传参考音频并填写准确逐字稿。") {
  state.selectedVoiceId = "";
  $("voiceSelect").value = "";
  $("libraryVoiceSelect").value = "";
  $("voiceName").value = "";
  $("voiceMode").value = "clone";
  $("voiceLanguage").value = "auto";
  $("voiceInstruction").value = "";
  $("voiceReferenceText").value = "";
  $("voiceTags").value = "";
  $("voiceNotes").value = "";
  $("voicePreviewText").value = "你好，这是一段音色库试听。";
  $("voiceFavorite").checked = false;
  renderVoiceReferenceEditor();
  setActionMessage("voiceStatus", message, "success");
  window.requestAnimationFrame(() => $("voiceName")?.focus());
}

function selectLibraryVoice() {
  const voice = state.voices.find((item) => item.id === $("libraryVoiceSelect").value);
  if (!voice) {
    state.selectedVoiceId = "";
    updateControlState();
    return;
  }
  state.selectedVoiceId = voice.id;
  $("voiceSelect").value = voice.id;
  selectMode(voice.mode);
  clearDirectionRecipe({ preserveInstruction: true });
  $("instruction").value = voice.instruction || "";
  $("referenceText").value = voice.reference_text || "";
  $("voiceName").value = voice.name || "";
  $("voiceMode").value = voice.mode || "design";
  $("voiceLanguage").value = voice.language || "auto";
  $("voiceInstruction").value = voice.instruction || "";
  $("voiceReferenceText").value = voice.reference_text || "";
  $("voiceTags").value = (voice.tags || []).join(", ");
  $("voiceNotes").value = voice.notes || "";
  $("voicePreviewText").value = voice.preview_text || "你好，这是一段音色库试听。";
  $("voiceFavorite").checked = Boolean(voice.favorite);
  renderVoiceReferenceEditor(voice);
  $("voiceStatus").textContent = `${voiceLabel(voice)} · ${voice.language || "auto"}${voice.favorite ? " · 已收藏" : ""}`;
  updateControlState();
}

function applyLibraryVoiceToGeneration() {
  const voice = state.voices.find((item) => item.id === $("libraryVoiceSelect").value);
  if (!voice) {
    $("voiceStatus").textContent = "请先选择一个已保存音色。";
    return;
  }
  $("voiceSelect").value = voice.id;
  applySelectedVoice();
  if (voice.preview_text) $("targetText").value = voice.preview_text;
  showWorkspaceTab("generate");
  $("generationStatus").textContent = `已应用音色“${voice.name}”，可以直接生成。`;
}

function voiceMetadata() {
  return {
    language: $("voiceLanguage").value,
    tags: $("voiceTags").value.split(/[,，]/).map((value) => value.trim()).filter(Boolean),
    favorite: $("voiceFavorite").checked,
    notes: $("voiceNotes").value.trim(),
    preview_text: $("voicePreviewText").value.trim()
  };
}

async function saveCurrentVoice() {
  const name = $("voiceName").value.trim();
  if (!name) throw new Error("请先输入音色名称。");
  const mode = $("voiceMode").value;
  const reference = $("voiceReferenceAudio").files[0];
  const referenceText = $("voiceReferenceText").value.trim();
  if (["clone", "direction"].includes(mode) && (!reference || !referenceText)) {
    throw new Error("保存 Clone/Direction 音色必须选择参考音频并填写准确逐字稿。");
  }
  if (reference) await inspectReferenceAudio(reference);
  const created = await api("/api/voices", {
    method: "POST",
    body: JSON.stringify({
      name,
      mode,
      instruction: $("voiceInstruction").value.trim(),
      reference_text: referenceText,
      reference_filename: reference?.name || "reference.wav",
      reference_audio_base64: await fileToBase64(reference),
      ...voiceMetadata()
    })
  });
  await refreshVoices();
  state.selectedVoiceId = created.id;
  renderVoices(state.voices);
  $("libraryVoiceSelect").value = created.id;
  selectLibraryVoice();
  $("voiceStatus").textContent = `已保存音色“${created.name}”。`;
}

async function updateSelectedVoice() {
  const voice = state.voices.find((item) => item.id === $("libraryVoiceSelect").value);
  if (!voice) throw new Error("请先选择要修改的音色。");
  const name = $("voiceName").value.trim();
  if (!name) throw new Error("音色名称不能为空。");
  const mode = $("voiceMode").value;
  const reference = $("voiceReferenceAudio").files[0];
  const referenceText = $("voiceReferenceText").value.trim();
  const willHaveReference = Boolean(reference) || (voice.has_reference && !state.voiceClearReference);
  if (["clone", "direction"].includes(mode) && (!willHaveReference || !referenceText)) {
    throw new Error("Clone/Direction 音色必须保留参考音频并填写准确逐字稿。");
  }
  if (reference) await inspectReferenceAudio(reference);
  const updated = await api(`/api/voices/${encodeURIComponent(voice.id)}`, {
    method: "PATCH",
    body: JSON.stringify({
      name,
      mode,
      instruction: $("voiceInstruction").value.trim(),
      reference_text: referenceText,
      reference_filename: reference?.name || "reference.wav",
      reference_audio_base64: await fileToBase64(reference),
      clear_reference: state.voiceClearReference,
      ...voiceMetadata()
    })
  });
  state.selectedVoiceId = updated.id;
  await refreshVoices();
  $("libraryVoiceSelect").value = updated.id;
  selectLibraryVoice();
  $("voiceStatus").textContent = `已保存“${updated.name}”的修改，稳定 ID 未改变。`;
}

async function exportSelectedVoice() {
  const voice = state.voices.find((item) => item.id === $("libraryVoiceSelect").value);
  if (!voice) throw new Error("请先选择要导出的音色。");
  const result = await api(`/api/voices/${encodeURIComponent(voice.id)}/export`, { method: "POST" });
  await window.t8Desktop?.openPath(result.path);
  $("voiceStatus").textContent = `已导出安全音色包：${result.path}`;
}

async function importVoiceBundle() {
  const path = await window.t8Desktop?.chooseBundleFile("voice");
  if (!path) return;
  const imported = await api("/api/voices/import", { method: "POST", body: JSON.stringify({ path }) });
  state.selectedVoiceId = imported.id;
  await refreshVoices();
  $("libraryVoiceSelect").value = imported.id;
  selectLibraryVoice();
  $("voiceStatus").textContent = `已安全导入音色“${imported.name}”。`;
}

async function deleteSelectedVoice() {
  const voiceId = $("libraryVoiceSelect").value || $("voiceSelect").value;
  const voice = state.voices.find((item) => item.id === voiceId);
  if (!voice) throw new Error("请先选择要删除的音色。");
  if (!window.confirm(`确定删除音色“${voice.name}”吗？`)) return;
  await api(`/api/voices/${encodeURIComponent(voice.id)}`, { method: "DELETE" });
  state.selectedVoiceId = "";
  await refreshVoices();
  beginNewVoice(`已删除音色“${voice.name}”，现在可直接新建另一个音色。`);
}

async function transcribeReference() {
  if (!state.capabilities.whisper) {
    throw new Error("内置 faster-whisper 组件缺失；请重新下载完整整合包或使用修复按钮。");
  }
  const reference = $("referenceAudio").files[0];
  if (!reference) throw new Error("请先选择参考音频。");
  await inspectReferenceAudio(reference);
  state.transcribing = true;
  updateControlState();
  const bundled = $("whisperModel").value === "small" && state.capabilities.whisper_small_bundled;
  $("whisperStatus").textContent = bundled ? "正在使用整合包内置 Whisper small 转写…" : "正在下载或加载所选 Whisper 模型…";
  try {
    const result = await api("/api/tools/transcribe", {
      method: "POST",
      body: JSON.stringify({
        reference_filename: reference.name,
        reference_audio_base64: await fileToBase64(reference),
        model_size: $("whisperModel").value,
        language: $("whisperLanguage").value.trim() || null
      })
    });
    $("referenceText").value = (result.segments || []).map((item) => item.text).join(" ").trim();
    $("whisperStatus").textContent = `转写完成 · ${result.language || "自动识别"} · ${result.segments?.length || 0} 段 · ${result.device || "unknown"}`;
  } finally {
    state.transcribing = false;
    updateControlState();
  }
}

async function installWhisperComponent() {
  if (!window.t8Desktop?.installWhisper) throw new Error("当前桌面程序不支持组件安装，请更新整合包。");
  state.installingWhisper = true;
  updateControlState();
  $("whisperStatus").textContent = "正在联网安装 Whisper 组件，耗时取决于网络速度；请勿关闭应用…";
  try {
    const result = await window.t8Desktop.installWhisper();
    if (!result?.installed) throw new Error("Whisper 组件安装未完成。");
    state.whisperInstallComplete = true;
    $("installWhisperButton").hidden = true;
    $("whisperStatus").textContent = "Whisper 组件安装完成，请重启应用后使用转写。";
  } finally {
    state.installingWhisper = false;
    updateControlState();
  }
}

function renderUpdateStatus(status) {
  state.updaterStatus = status || { configured: false, state: "disabled" };
  $("updateStatus").textContent = state.updaterStatus.message || (
    state.updaterStatus.configured
      ? "自动更新状态未知。"
      : "自动更新未配置；请使用发布包附带的 release-manifest.json 手动校验。"
  );
  $("installUpdateButton").hidden = state.updaterStatus.state !== "downloaded";
  updateControlState();
}

async function checkForDesktopUpdates() {
  if (!window.t8Desktop?.checkForUpdates) throw new Error("当前桌面程序不支持自动更新。");
  renderUpdateStatus(await window.t8Desktop.checkForUpdates());
}

async function installDesktopUpdate() {
  if (!window.t8Desktop?.installUpdate) throw new Error("当前桌面程序不支持自动更新。");
  $("updateStatus").textContent = "正在安装更新并重启…";
  await window.t8Desktop.installUpdate();
}

function normalizeRoleName(value) {
  return String(value || "").trim().toLocaleLowerCase();
}

function detectRoleNames(text) {
  const roles = [];
  for (const rawLine of String(text || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || /^\d+$/.test(line) || /-->/.test(line)) continue;
    const match = line.match(/^\[([^\]]{1,80})\]\s*\S/) || line.match(/^([^：:|]{1,80})\s*[：:]\s*\S/) || line.match(/^([^|]{1,80})\s*\|\s*\S/);
    const role = match?.[1]?.trim();
    if (INLINE_VOCAL_EVENT_ROLES.has(String(role || "").toLocaleLowerCase())) continue;
    if (role && !roles.some((item) => normalizeRoleName(item) === normalizeRoleName(role))) roles.push(role);
  }
  return roles;
}

function looksMultiRoleScript(text) {
  return String(text || "").split(/\r?\n/).some((line) => {
    const bracket = line.match(/^\s*\[([^\]]+)\]\s*\S/);
    if (bracket) return !INLINE_VOCAL_EVENT_ROLES.has(bracket[1].trim().toLocaleLowerCase());
    return /^\s*[^：:]{1,40}[：:]\s*\S/.test(line);
  });
}

function shouldShowRoleMappings() {
  return state.batchRoles.length > 0 && ($("batchKind").value === "script" || detectRoleNames($("batchInput").value).length > 0 || Boolean(state.dialogueProject));
}

function updateDetectedRoles() {
  const roles = detectRoleNames($("batchInput").value);
  if (roles.length) renderRoleMappings(roles);
  else if (!state.dialogueProject) renderRoleMappings([]);
  updateControlState();
}

function updateBatchKind() {
  const kind = $("batchKind").value;
  const content = {
    items: ["批量文本", "每行一条待生成文本", "最多 100 项，按顺序逐条生成；默认使用当前音色与参数。"],
    script: ["多角色脚本", "旁白：故事开始了。\n角色A：你好！\n[角色B] 很高兴见到你。", "支持“角色：文本”或“[角色] 文本”；先分析角色并为每个角色选择音色。"],
    srt: ["SRT 字幕", "1\n00:00:00,000 --> 00:00:02,000\n第一句字幕", "粘贴标准 SRT；按字幕段落依次生成，并在结果元数据中保留时间轴。"],
    txt: ["角色化 TXT", "旁白 | 欢迎使用逐句演绎 | zh | 平静、从容\n角色A | This is an example. | en | cheerful", "四列依次为角色、台词、语言、自然语言演绎；不接受 Index emotion=vector。"],
    json: ["工程 JSON", "{\n  \"name\": \"我的工程\",\n  \"lines\": []\n}", "导入 versioned DialogueProject 或台词数组。"]
  }[kind];
  $("batchInputLabel").textContent = content[0];
  $("batchInput").placeholder = content[1];
  $("batchInputHint").textContent = content[2];
  $("roleMappingPanel").hidden = !shouldShowRoleMappings();
  updateControlState();
}

function applyDefaultRolePreset() {
  const voice = state.voices.find((item) => item.id === $("defaultRolePreset").value);
  if (!voice) return;
  $("defaultRole").value = voice.name;
  if (["zh", "en"].includes(voice.language)) $("defaultLanguage").value = voice.language;
  updateDetectedRoles();
  setActionMessage("batchStatus", `默认角色已选“${voice.name}”；解析时会自动绑定这个音色。`, "success");
}

function matchDefaultRolePreset() {
  const role = normalizeRoleName($("defaultRole").value);
  const voice = state.voices.find((item) => normalizeRoleName(item.name) === role);
  $("defaultRolePreset").value = voice?.id || "";
}

function loadBatchExample() {
  const kind = $("batchKind").value;
  $("batchInput").value = BATCH_EXAMPLES[kind] || BATCH_EXAMPLES.items;
  updateBatchKind();
  updateDetectedRoles();
  setActionMessage("batchStatus", `已载入${$("batchInputLabel").textContent}示例；可直接修改或点击“解析并检查”。`, "success");
  $("batchInput").focus();
}

function clearBatchInput() {
  $("batchInput").value = "";
  updateDetectedRoles();
  setActionMessage("batchStatus", "输入区已清空；已有时间轴不会被删除。", "success");
  $("batchInput").focus();
}

function renderRoleMappings(roles) {
  const panel = $("roleMappingPanel");
  const previous = new Map([...panel.querySelectorAll("select[data-role]")].map((select) => [select.dataset.role, select.value]));
  state.batchRoles = roles.filter(Boolean).filter((role, index, list) => list.findIndex((item) => normalizeRoleName(item) === normalizeRoleName(role)) === index);
  panel.replaceChildren(...state.batchRoles.map((role) => {
    const row = document.createElement("div");
    row.className = "role-mapping-row";
    const name = document.createElement("span");
    name.className = "role-name";
    name.textContent = role;
    const select = document.createElement("select");
    select.disabled = state.batching;
    select.dataset.role = role;
    const fallback = document.createElement("option");
    fallback.value = "";
    fallback.textContent = "使用当前默认音色";
    const projectVoice = state.dialogueProject?.lines?.find((line) => normalizeRoleName(line.role) === normalizeRoleName(role) && line.voice_id)?.voice_id || "";
    const matchedVoice = state.voices.find((voice) => normalizeRoleName(voice.name) === normalizeRoleName(role))?.id || "";
    const selectedVoice = previous.get(role) || projectVoice || matchedVoice || "";
    select.append(fallback, ...state.voices.map((voice) => voiceOption(voice, selectedVoice)));
    select.value = selectedVoice;
    select.setAttribute("aria-label", `${role} 的音色`);
    row.append(name, select);
    return row;
  }));
  panel.hidden = !shouldShowRoleMappings();
}

async function analyzeBatchRoles() {
  const script = $("batchInput").value.trim();
  if (!script) throw new Error("请先输入多角色脚本。");
  const result = await api("/api/tools/parse-multi-role", {
    method: "POST",
    body: JSON.stringify({ text: script })
  });
  renderRoleMappings((result.segments || []).map((item) => item.role));
  setActionMessage("batchStatus", `识别到 ${state.batchRoles.length} 个角色，请为角色选择音色。`);
}

async function currentBatchDefaults() {
  const cfgScale = Number($("cfgScale").value);
  const seed = Number($("seed").value);
  const maxNewTokens = Number($("maxTokens").value);
  if (!Number.isFinite(cfgScale) || cfgScale <= 0) throw new Error("CFG 必须大于 0。");
  if (!Number.isInteger(seed) || seed < 0 || seed > 2147483647) throw new Error("Seed 超出有效范围。");
  if (!Number.isInteger(maxNewTokens) || maxNewTokens < 64 || maxNewTokens > 1500) throw new Error("最大音频帧必须在 64 到 1500 之间。");
  const defaults = {
    mode: state.mode,
    instruction: $("instruction").value.trim(),
    reference_text: $("referenceText").value.trim(),
    cfg_scale: cfgScale,
    seed,
    fast_all: $("runtimeProfile").value === "fast",
    max_new_tokens: maxNewTokens
  };
  if (state.selectedVoiceId) return { ...defaults, voice_id: state.selectedVoiceId };
  const reference = $("referenceAudio").files[0];
  if (["clone", "direction"].includes(state.mode) && (!reference || !defaults.reference_text)) {
    throw new Error("当前批量默认配置需要参考音频和准确逐字稿，或选择一个已保存音色。");
  }
  if (["design", "direction"].includes(state.mode) && !defaults.instruction) throw new Error("演绎指令不能为空。");
  if (reference) await inspectReferenceAudio(reference);
  return {
    ...defaults,
    reference_filename: reference?.name || "reference.wav",
    reference_audio_base64: await fileToBase64(reference)
  };
}

function batchRoleVoices() {
  return Object.fromEntries(
    [...$("roleMappingPanel").querySelectorAll("select[data-role]")]
      .filter((select) => select.value)
      .map((select) => [select.dataset.role, select.value])
  );
}

function appendBatchResult(result) {
  const row = document.createElement("div");
  row.className = "batch-result";
  const label = document.createElement("span");
  const displayIndex = Number(result.subtitle?.index) || Number(result.index) + 1;
  label.textContent = `第 ${displayIndex} 句${result.role ? ` · ${result.role}` : ""} · 最新结果`;
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.src = `/api/outputs/${encodeURIComponent(result.output)}`;
  const download = document.createElement("a");
  download.className = "button-link";
  download.href = audio.src;
  download.download = "";
  download.textContent = "下载 WAV";
  row.append(label, audio, download);
  $("batchResults").append(row);
}

function formatDuration(seconds) {
  if (!Number.isFinite(Number(seconds))) return "未知";
  const total = Math.max(0, Math.round(Number(seconds)));
  const minutes = Math.floor(total / 60);
  const remainder = total % 60;
  return minutes ? `${minutes} 分 ${remainder} 秒` : `${remainder} 秒`;
}

function showMergedBatchOutput(message, titleText = "合并总音频") {
  if (!message.merged_output) return;
  const panel = document.createElement("section");
  panel.className = "batch-merged";
  const heading = document.createElement("div");
  heading.className = "batch-merged-heading";
  const title = document.createElement("strong");
  title.textContent = titleText;
  const meta = document.createElement("span");
  meta.textContent = `${message.merged_metadata?.item_count || state.batchCompleted} 项 · ${formatDuration(message.merged_metadata?.duration_seconds)}`;
  heading.append(title, meta);
  const audio = document.createElement("audio");
  audio.controls = true;
  audio.src = `/api/outputs/${encodeURIComponent(message.merged_output)}`;
  const download = document.createElement("a");
  download.className = "button-link";
  download.href = audio.src;
  download.download = "";
  download.textContent = "下载合并 WAV";
  panel.append(heading, audio, download);
  $("batchResults").prepend(panel);
  const output = message.merged_metadata?.output;
  if (typeof output === "string") state.outputDirectory = output.replace(/[\\/][^\\/]+$/, "");
}

async function runBatchPayload(payload, { resumeJobId = "", singleLineId = "" } = {}) {
  if (state.generating || state.batching || state.downloading) {
    setActionMessage("batchStatus", state.batching ? "已有批量或单句任务正在运行；请等待完成或先停止当前任务。" : "当前有其他生成或下载任务，完成后再开始。", "error");
    return;
  }
  const singleLineRun = Boolean(singleLineId);
  state.batching = true;
  state.batchTotal = 0;
  state.batchCompleted = 0;
  updateControlState();
  let queuedJob = resumeJobId ? { job_id: resumeJobId } : null;
  let queueWarning = "";
  if (!resumeJobId) {
    try {
      queuedJob = await api("/api/queue", {
        method: "POST",
        body: JSON.stringify({ payload: { kind: "dialogue", project_id: payload.project_id || "", total: payload.items?.length || 0 } })
      });
    } catch (error) {
      // Generation remains available for older backends, but persistence failures must be visible.
      queueWarning = `任务队列暂不可用：${errorMessage(error)}；本次仍可生成，但关闭应用后不能断点恢复。`;
    }
  }
  $("batchResults").replaceChildren();
  $("batchProgress").value = 0;
  setActionMessage("batchStatus", singleLineRun ? "工程已保存，正在准备重跑当前句…" : (resumeJobId ? "正在读取断点并继续任务…" : "正在整理批量任务…"));
  setGlobalTask({ kind: "working", kicker: singleLineRun ? "单句重跑" : "批量生成", title: singleLineRun ? "正在准备当前句" : (resumeJobId ? "正在恢复断点" : "正在整理任务"), detail: singleLineRun ? "当前句成功后会尝试自动重混整条时间轴。" : "任务会保存在队列中，可以切换到其他分页继续操作。", progress: "indeterminate", target: "dialogue", cancellable: true });
  let socket;
  try {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${scheme}://${location.host}/ws/batch`);
    state.batchSocket = socket;
    socket.onopen = () => socket.send(JSON.stringify(resumeJobId ? { resume_job_id: resumeJobId } : payload));
  } catch (error) {
    state.batching = false;
    updateControlState();
    setGlobalTask({ kind: "error", kicker: "批量生成", title: "无法开始任务", detail: errorMessage(error), target: "dialogue" });
    throw error;
  }
  socket.onmessage = async (event) => {
    let message;
    try { message = JSON.parse(event.data); }
    catch (_) {
      setActionMessage("batchStatus", "收到无法解析的批量进度消息；请从任务队列检查最终状态。", "error");
      return;
    }
    if (message.type === "batch_start") {
      state.batchTotal = message.total;
      setActionMessage("batchStatus", `${singleLineRun ? "当前句已进入生成队列" : (resumeJobId ? "断点任务已继续" : "批量任务已开始")}，共 ${message.total} 项。${queueWarning ? ` ${queueWarning}` : ""}`);
      setGlobalTask({ kind: "working", kicker: singleLineRun ? "单句重跑" : "批量生成", title: `正在生成 0/${message.total}`, detail: queueWarning || (singleLineRun ? "请等待当前句生成，完成后会检查整条时间轴是否可重混。" : "批量任务已开始，可在工作台中继续浏览。"), progress: 0, target: "dialogue", cancellable: true });
    } else if (message.type === "item_start") {
      const lineId = resolveBatchLineId(payload, message, singleLineId);
      const line = state.dialogueProject?.lines?.find((item) => item.line_id === lineId);
      if (line) { line.status = "running"; line.error = ""; refreshTimelineLineState(lineId); }
      setActionMessage("batchStatus", `正在生成 ${message.index + 1}/${message.total}${message.role ? ` · ${message.role}` : ""}`);
      setGlobalTask({ kind: "working", kicker: singleLineRun ? "单句重跑" : "批量生成", title: `正在生成 ${message.index + 1}/${message.total}`, detail: message.role ? `当前角色：${message.role}` : "正在处理当前台词。", progress: message.total ? state.batchCompleted / message.total * 100 : "indeterminate", target: "dialogue", cancellable: true });
    } else if (message.type === "item_complete") {
      state.batchCompleted += 1;
      $("batchProgress").value = state.batchTotal ? state.batchCompleted / state.batchTotal * 100 : 0;
      const lineId = resolveBatchLineId(payload, message, singleLineId);
      const line = state.dialogueProject?.lines?.find((item) => item.line_id === lineId);
      if (line) { line.status = "completed"; line.error = ""; line.audio_file = message.output || line.audio_file; refreshTimelineLineState(lineId); }
      appendBatchResult(message);
      setGlobalTask({ kind: "working", kicker: singleLineRun ? "单句重跑" : "批量生成", title: `已完成 ${state.batchCompleted}/${state.batchTotal || "?"}`, detail: singleLineRun ? "当前句结果已保存，正在检查整条时间轴能否自动重混。" : "结果已逐条保存，可随时查看已完成音频。", progress: state.batchTotal ? state.batchCompleted / state.batchTotal * 100 : "indeterminate", target: "dialogue", cancellable: true });
    } else if (message.type === "batch_complete") {
      $("batchProgress").value = 100;
      let syncedProject = null;
      let syncError = "";
      if (payload.project_id) {
        try {
          syncedProject = await syncDialogueProjectFromBatch(message.project, payload.project_id, singleLineId || state.selectedLineId);
        } catch (error) {
          syncError = errorMessage(error);
        }
      }
      const duration = message.merged_metadata?.duration_seconds;
      const fullProjectMix = message.full_project_mix === true && message.remix?.status === "completed";
      if (singleLineRun && fullProjectMix) {
        showMergedBatchOutput(message, "整条时间轴（已自动重混）");
        setActionMessage("batchStatus", `当前句结果已生成，整条时间轴已自动重混${Number.isFinite(Number(duration)) ? ` · 总时长 ${formatDuration(duration)}` : ""}。`, "success");
        setGlobalTask({ kind: "success", kicker: "单句重跑", title: "当前句完成 · 时间轴已重混", detail: "下方当前句结果与整条时间轴总音频均已更新。", progress: 100, target: "dialogue" });
      } else if (singleLineRun) {
        const missing = projectMissingAudioLines(syncedProject);
        const fallbackHint = missing.length
          ? `还缺第 ${missing.slice(0, 5).map((line) => line.order).join("、")} 句${missing.length > 5 ? `等 ${missing.length} 句` : ""}音频`
          : "仍有单句音频缺失或文件暂不可用";
        const missingHint = String(message.remix?.reason || "").trim() || fallbackHint;
        const syncHint = syncError ? ` 工程同步失败：${syncError}；请先点击“保存工程”后重试。` : "";
        setActionMessage("batchStatus", `当前句结果已生成，但仅得到当前句结果；${missingHint}，暂不能重混整条时间轴。请点击缺失行的“生成该句”。${syncHint}`, "error");
        setGlobalTask({ kind: "success", kicker: "单句重跑", title: "当前句已完成 · 时间轴尚未重混", detail: `${missingHint}；补齐后再次生成任一句即可自动重混。`, progress: 100, target: "dialogue" });
      } else {
        showMergedBatchOutput(message, payload.project_id && !fullProjectMix ? "本次任务合并结果（非完整工程重混）" : "合并总音频");
        const syncHint = syncError ? ` 工程同步失败：${syncError}；编辑前请重新导入或刷新工程。` : "";
        setActionMessage("batchStatus", `批量完成，共生成 ${message.results?.length || state.batchCompleted} 项${Number.isFinite(Number(duration)) ? ` · 合并总时长 ${formatDuration(duration)}` : ""}。${syncHint}`, syncError ? "error" : "success");
        setGlobalTask({ kind: syncError ? "error" : "success", kicker: "批量生成", title: `已完成 ${message.results?.length || state.batchCompleted} 项`, detail: syncError || (Number.isFinite(Number(duration)) ? `合并总时长 ${formatDuration(duration)}，结果已保存。` : "结果和合并音频已保存。"), progress: 100, target: "dialogue" });
      }
    } else if (message.type === "cancelled") {
      if (message.project) {
        try { await syncDialogueProjectFromBatch(message.project, payload.project_id, singleLineId || state.selectedLineId); } catch (_) { /* 保留取消提示和当前页面。 */ }
      }
      const line = state.dialogueProject?.lines?.find((item) => item.line_id === singleLineId);
      if (line) { line.status = "pending"; line.error = "本次重跑已取消，可再次生成。"; refreshTimelineLineState(singleLineId); }
      setActionMessage("batchStatus", `已取消；已完成 ${state.batchCompleted}/${state.batchTotal || "?"} 项。`);
      setGlobalTask({ kind: "idle", kicker: singleLineRun ? "单句重跑" : "批量生成", title: "任务已取消", detail: `已保留 ${state.batchCompleted}/${state.batchTotal || "?"} 项结果和队列断点。`, target: "dialogue" });
    } else if (message.type === "error") {
      const lineId = resolveBatchLineId(payload, message, singleLineId);
      const line = state.dialogueProject?.lines?.find((item) => item.line_id === lineId);
      if (line) { line.status = "failed"; line.error = message.message || "单句生成失败。"; refreshTimelineLineState(lineId); }
      if (singleLineRun && payload.project_id) {
        try { await syncDialogueProjectFromBatch(message.project, payload.project_id, singleLineId); } catch (_) { /* 保留当前明确失败状态。 */ }
      }
      setActionMessage("batchStatus", `批量失败：${actionableError(message.message, "保留当前断点，前往任务队列继续")}`, "error");
      setGlobalTask({ kind: "error", kicker: singleLineRun ? "单句重跑" : "批量生成", title: singleLineRun ? "当前句生成失败" : "任务中断", detail: `${message.message}；${singleLineRun ? "请检查该句音色与演绎设置后重试。" : "可从任务队列继续。"}`, target: singleLineRun ? "dialogue" : "queue" });
    }
  };
  socket.onerror = () => {
    if (singleLineRun) {
      const line = state.dialogueProject?.lines?.find((item) => item.line_id === singleLineId);
      if (line && line.status === "running") {
        line.status = "failed";
        line.error = "本地生成连接中断；请确认服务正常后重试。";
        refreshTimelineLineState(singleLineId);
      }
    }
    setActionMessage("batchStatus", "批量连接失败。建议：确认本地服务仍在运行，然后从任务队列继续", "error");
    setGlobalTask({ kind: "error", kicker: "批量生成", title: "连接失败", detail: "当前进度已尽量保留，请从任务队列检查并继续。", target: "queue" });
  };
  socket.onclose = () => {
    state.batchSocket = null;
    state.batching = false;
    updateControlState();
    refresh();
  };
}

async function resumeBatchJob(jobId) {
  if (!jobId) throw new Error("任务编号缺失。");
  await api(`/api/queue/${encodeURIComponent(jobId)}/resume`);
  showWorkspaceTab("dialogue");
  await runBatchPayload({}, { resumeJobId: jobId });
}

function lineNeedsGeneration(line) {
  const dirtyFields = Array.isArray(line?.dirty_fields) ? line.dirty_fields : [];
  return !String(line?.audio_file || "").trim()
    || line?.status !== "completed"
    || dirtyFields.some((field) => LINE_GENERATION_DIRTY_FIELDS.has(String(field)));
}

async function remixCompletedDialogueProject(project) {
  const selectedLineId = state.selectedLineId;
  state.batching = true;
  state.remixing = true;
  updateControlState();
  $("batchResults").replaceChildren();
  $("batchProgress").removeAttribute("value");
  setActionMessage("batchStatus", "所有台词已有最新语音，无需重新生成；正在按当前时间轴直接重混…");
  setGlobalTask({ kind: "working", kicker: "时间轴重混", title: "正在重混完整工程", detail: "不会重新推理语音，只按当前时间与间隔设置合成总音频。", progress: "indeterminate", target: "dialogue" });
  try {
    const result = await api(`/api/projects/${encodeURIComponent(project.project_id)}/remix`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: state.projectBaseRevision })
    });
    await syncDialogueProjectFromBatch(result.project, project.project_id, selectedLineId);
    $("batchProgress").value = 100;
    showMergedBatchOutput({ merged_output: result.output, merged_metadata: result.metadata }, "整条时间轴（已直接重混）");
    setActionMessage("batchStatus", "所有台词均为最新结果，无需重生成；整条时间轴已直接重混。", "success");
    setGlobalTask({ kind: "success", kicker: "时间轴重混", title: "完整工程重混完成", detail: "未重新生成单句，已更新总音频。", progress: 100, target: "dialogue" });
    return result;
  } catch (error) {
    setGlobalTask({ kind: "error", kicker: "时间轴重混", title: "无法重混完整工程", detail: errorMessage(error), target: "dialogue" });
    throw error;
  } finally {
    state.remixing = false;
    state.batching = false;
    updateControlState();
  }
}

async function transcribeVoiceReference() {
  if (!state.capabilities.whisper) {
    throw new Error("内置 faster-whisper 组件缺失；请重新下载完整整合包或使用修复按钮。");
  }
  const reference = $("voiceReferenceAudio").files[0];
  if (!reference) throw new Error("请先在音色库上传新的参考音频。");
  await inspectReferenceAudio(reference);
  state.transcribing = true;
  updateControlState();
  const bundled = $("whisperModel").value === "small" && state.capabilities.whisper_small_bundled;
  $("voiceReferenceStatus").textContent = bundled ? "正在使用整合包内置 Whisper small 识别参考音频…" : "正在下载或加载所选 Whisper 模型…";
  try {
    const result = await api("/api/tools/transcribe", {
      method: "POST",
      body: JSON.stringify({
        reference_filename: reference.name,
        reference_audio_base64: await fileToBase64(reference),
        model_size: $("whisperModel").value,
        language: $("voiceLanguage").value === "auto" ? null : $("voiceLanguage").value
      })
    });
    $("voiceReferenceText").value = (result.segments || []).map((item) => item.text).join(" ").trim();
    $("voiceReferenceStatus").textContent = `逐字稿已填写 · ${result.language || "自动识别"} · ${result.segments?.length || 0} 段；请核对后保存。`;
  } finally {
    state.transcribing = false;
    updateControlState();
  }
}

async function startBatch() {
  if (state.dialogueProject?.lines?.length) {
    await ensureDialogueProjectSaved();
    const project = state.dialogueProject;
    const linesToGenerate = project.lines.filter(lineNeedsGeneration);
    if (!linesToGenerate.length) return remixCompletedDialogueProject(project);
    const defaults = await currentBatchDefaults();
    const items = linesToGenerate.map((line) => ({
      text: line.text,
      role: line.role,
      voice_id: line.voice_id || defaults.voice_id,
      direction_mode: line.direction_mode,
      direction_text: line.direction_text,
      cfg_scale: line.cfg_scale ?? defaults.cfg_scale,
      seed: line.seed ?? defaults.seed,
      subtitle: { index: line.order, start_ms: line.start_ms, end_ms: line.end_ms, text: line.text },
      line_id: line.line_id
    }));
    return runBatchPayload({ defaults, items, timeline: true, timing_policy: project.timing?.policy || "preserve", project_id: project.project_id, project_revision: state.projectBaseRevision });
  }
  const defaults = await currentBatchDefaults();
  const text = $("batchInput").value.trim();
  if (!text) throw new Error("批量输入不能为空。");
  const selectedKind = $("batchKind").value;
  const kind = selectedKind === "items" && looksMultiRoleScript(text) ? "script" : selectedKind;
  if (kind === "script" && selectedKind !== "script") { $("batchKind").value = "script"; updateBatchKind(); }
  const payload = { defaults };
  if (["items", "txt", "json"].includes(kind)) payload.items = text.split(/\r?\n/).map((item) => item.trim()).filter(Boolean).map((item) => ({ text: item }));
  else if (kind === "script") {
    payload.script = text;
    payload.role_voices = batchRoleVoices();
  } else payload.srt_text = text;
  return runBatchPayload(payload);
}

function cancelBatch() {
  if (state.batchSocket?.readyState === WebSocket.OPEN) state.batchSocket.send(JSON.stringify({ type: "cancel" }));
  else if (state.batchSocket?.readyState === WebSocket.CONNECTING) state.batchSocket.close();
  setActionMessage("batchStatus", "正在取消批量任务…");
  setGlobalTask({ kind: "working", kicker: "批量生成", title: "正在停止任务", detail: "已完成结果与断点会保留。", progress: state.batchTotal ? state.batchCompleted / state.batchTotal * 100 : "indeterminate", target: "dialogue" });
}

function applyCreationTemplate(templateId) {
  const template = CREATION_TEMPLATES[templateId] || CREATION_TEMPLATES.blank;
  if (template.draft) {
    if (!state.dialogueProject) restoreProjectDraft();
    setActionMessage("batchStatus", state.dialogueProject?.lines?.length ? `已恢复“${state.dialogueProject.name || "未命名工程"}”，可从上次位置继续。` : "没有可恢复的自动草稿。");
  } else if (template.tab === "dialogue") {
    const currentBatchText = $("batchInput").value;
    $("batchKind").value = template.batchKind;
    if (!currentBatchText.trim() || Object.values(BATCH_EXAMPLES).includes(currentBatchText)) {
      $("batchInput").value = BATCH_EXAMPLES[template.batchKind] || BATCH_EXAMPLES.items;
    }
    updateBatchKind();
    updateDetectedRoles();
    setActionMessage("batchStatus", template.batchKind === "srt" ? "请导入或粘贴 SRT，解析后即可编辑时间轴。" : "请粘贴“角色：台词”脚本，解析后映射音色。", "success");
  } else if (templateId !== "blank") {
    clearDirectionRecipe({ preserveInstruction: true });
    selectMode(template.mode);
    $("targetText").value = template.text;
    $("instruction").value = template.instruction;
    clearSelectedVoice("创作模板已载入，当前使用手动配置。");
    setActionMessage("directionRecipeStatus", "模板已提供基础指令；可叠加演绎配方再生成。", "success");
  }
  showWorkspaceTab(template.tab);
  const focusTarget = template.tab === "dialogue" ? $("batchInput") : $("targetText");
  window.requestAnimationFrame(() => focusTarget?.focus());
  setGlobalTask({ kind: "idle", kicker: "创作模板", title: templateId === "blank" ? "工作台已就绪" : "模板已载入", detail: template.tab === "dialogue" ? "导入或粘贴内容后，按步骤解析和生成。" : "修改文本和演绎要求后即可生成。", target: template.tab });
}

function workspaceTabKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...document.querySelectorAll(".workspace-tab")];
  const current = tabs.indexOf(event.currentTarget);
  if (current < 0) return;
  event.preventDefault();
  const nextIndex = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  const next = tabs[nextIndex];
  showWorkspaceTab(next.dataset.tab);
  next.focus();
}

function showWorkspaceTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll(".workspace-tab").forEach((button) => {
    const active = button.dataset.tab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll(".tab-page").forEach((page) => {
    const active = page.dataset.page === tab;
    page.hidden = !active;
    page.classList.toggle("active", active);
    if (active) page.removeAttribute("aria-hidden");
    else page.setAttribute("aria-hidden", "true");
  });
  const activeButton = document.querySelector(`.workspace-tab[data-tab="${tab}"]`);
  activeButton?.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
  if (tab === "history") refreshHistory().catch((error) => renderListError("historyList", error, "重试刷新历史", refreshHistory));
  if (tab === "queue") refreshQueue().catch((error) => renderListError("queueList", error, "重试刷新队列", refreshQueue));
  if (tab === "dialogue") renderTimeline();
}

async function launchStudio(templateId = "blank") {
  if (state.launching) return;
  if (!state.diagnostics?.model) await refresh();
  const model = state.diagnostics?.model;
  if (!model?.valid || !model?.license_accepted) {
    const message = modelPreparationMessage(model);
    const feedback = $("quickLaunchFeedback");
    feedback.hidden = false;
    feedback.classList.remove("error");
    feedback.classList.add("needs-attention");
    $("quickLaunchProgress").hidden = true;
    $("quickLaunchHeading").textContent = "开始前还差一步";
    $("quickLaunchStatus").textContent = message;
    $("quickLaunchElapsed").hidden = true;
    setActionMessage("launchStatus", "完成模型准备后，再点击启动或创作模板。", "");
    const advanced = $("advancedLauncher");
    advanced.open = true;
    advanced.scrollIntoView({ behavior: "smooth", block: "start" });
    window.setTimeout(() => (model?.valid ? $("acceptLicense") : $("downloadModelButton"))?.focus(), 350);
    return;
  }
  state.launching = true;
  state.launchStartedAt = Date.now();
  $("startStudioButton").disabled = true;
  document.querySelectorAll(".template-card").forEach((button) => { button.disabled = true; });
  $("quickLaunchFeedback").hidden = false;
  $("quickLaunchFeedback").classList.remove("error", "needs-attention");
  $("quickLaunchProgress").hidden = false;
  $("quickLaunchProgress").removeAttribute("value");
  $("quickLaunchHeading").textContent = "正在启动工作台";
  $("quickLaunchStatus").textContent = "正在校验模型与许可证…";
  $("quickLaunchElapsed").textContent = "已等待 0 秒";
  $("quickLaunchElapsed").hidden = false;
  $("launchProgress").hidden = false;
  $("launchProgress").removeAttribute("value");
  $("launchElapsed").hidden = false;
  window.clearInterval(state.launchTimer);
  state.launchTimer = window.setInterval(() => {
    const seconds = Math.max(0, Math.floor((Date.now() - state.launchStartedAt) / 1000));
    $("launchElapsed").textContent = `已等待 ${seconds} 秒 · 正在加载模型，请不要关闭窗口`;
    $("quickLaunchElapsed").textContent = `已等待 ${seconds} 秒 · 请不要关闭窗口`;
  }, 1000);
  $("launchStatus").textContent = "阶段 1/2：正在校验模型与许可证…";
  try {
    $("launchStatus").textContent = "阶段 2/2：正在把模型加载到显存，首次启动可能需要几分钟…";
    $("quickLaunchStatus").textContent = "正在把模型加载到显存，首次启动可能需要几分钟…";
    const runtime = await api("/api/runtime/load", {
      method: "POST",
      body: JSON.stringify({ fast_all: $("runtimeProfile").value === "fast", max_new_tokens: Number($("maxTokens").value) })
    });
    $("launcherView").hidden = true;
    $("studioView").hidden = false;
    $("runtimeSummary").textContent = `${runtime.fast_all ? "Fast All" : "Eager"} · 24 kHz · 模型已加载`;
    applyCreationTemplate(templateId);
    $("quickLaunchFeedback").hidden = true;
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (error) {
    const message = `启动失败：${conciseActionError(error, "展开“环境诊断、扩展组件与更新”查看详情后重试")}`;
    setActionMessage("launchStatus", message, "error");
    $("quickLaunchFeedback").classList.add("error");
    $("quickLaunchProgress").hidden = true;
    $("quickLaunchStatus").textContent = message;
    $("quickLaunchElapsed").hidden = true;
  } finally {
    window.clearInterval(state.launchTimer);
    state.launchTimer = null;
    $("launchProgress").hidden = true;
    $("launchProgress").value = 0;
    $("launchElapsed").hidden = true;
    state.launching = false;
    $("startStudioButton").disabled = false;
    document.querySelectorAll(".template-card:not(.template-continue)").forEach((button) => { button.disabled = false; });
    updateContinueDraftTemplate();
  }
}

async function returnToLauncher() {
  if (state.generating || state.batching) throw new Error("请先停止当前生成任务。");
  if (state.projectDirty) {
    persistProjectDraft();
    const leave = window.confirm("当前对白工程尚未正式保存，自动草稿已保留。仍要返回启动首页并释放模型吗？");
    if (!leave) return;
  }
  await api("/api/runtime/unload", { method: "POST" });
  $("studioView").hidden = true;
  $("launcherView").hidden = false;
  setGlobalTask();
  $("launchStatus").textContent = "模型显存已释放，可调整配置后重新启动。";
  await refresh();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function openKnownPath(kind) {
  const paths = {
    output: state.outputDirectory,
    logs: state.appInfo.logsDirectory,
    data: state.appInfo.userData
  };
  if (paths[kind]) window.t8Desktop?.openPath(paths[kind]);
}

function lineVoiceSelect(line) {
  const select = document.createElement("select");
  select.disabled = state.batching;
  select.dataset.field = "voice_id";
  select.setAttribute("aria-label", `第 ${line.order} 句音色`);
  const manual = document.createElement("option");
  manual.value = "";
  manual.textContent = "继承默认";
  select.append(manual, ...state.voices.map((voice) => voiceOption(voice, line.voice_id)));
  select.value = line.voice_id || "";
  return select;
}

function fieldInput(line, field, type = "text") {
  const input = document.createElement(type === "textarea" ? "textarea" : "input");
  if (input instanceof HTMLInputElement) input.type = type;
  input.dataset.field = field;
  input.disabled = state.batching;
  input.value = line[field] ?? "";
  const labels = { role: "角色", start_ms: "开始时间", end_ms: "结束时间", text: "台词", direction_text: "演绎指令" };
  input.setAttribute("aria-label", `第 ${line.order} 句${labels[field] || field}`);
  return input;
}

function timelineSnapMs() {
  return Math.max(1, Number(state.dialogueProject?.timing?.snap_ms) || 50);
}

function normalizeLineTiming(line) {
  line.start_ms = Math.min(MAX_TIMELINE_MS - MIN_LINE_DURATION_MS, Math.max(0, Math.round(Number(line.start_ms) || 0)));
  line.end_ms = Math.min(MAX_TIMELINE_MS, Math.max(line.start_ms + MIN_LINE_DURATION_MS, Math.round(Number(line.end_ms) || 0)));
}

function timelineScaleMs(project = state.dialogueProject) {
  const maxEnd = Math.max(...(project?.lines || []).map((line) => Number(line.end_ms) || 0), 1000);
  return Math.min(MAX_TIMELINE_MS, Math.max(1000, Math.ceil(maxEnd * 1.08 / 100) * 100));
}

function updateTimelineSummary() {
  const project = state.dialogueProject;
  if (!project?.lines?.length) {
    $("timelineSummary").textContent = "尚未解析台词。";
    return;
  }
  const maxEnd = Math.max(...project.lines.map((line) => Number(line.end_ms) || 0), 0);
  const trackNote = project.lines.length > TIMELINE_TRACK_RENDER_LIMIT ? ` · 轨道显示 ${TIMELINE_TRACK_RENDER_LIMIT} 句，表格保留全部` : "";
  $("timelineSummary").textContent = `${project.lines.length} 句 · revision ${project.revision} · ${(maxEnd / 1000).toFixed(2)} 秒 · 修改时间只重混音${trackNote}`;
}

function updateTimelineBlock(block, line, scaleMs = timelineScaleMs()) {
  if (!block) return;
  block.style.left = `${line.start_ms / scaleMs * 100}%`;
  block.style.width = `${Math.max(0.35, (line.end_ms - line.start_ms) / scaleMs * 100)}%`;
  const label = block.querySelector(".timeline-block-label");
  if (label) label.textContent = `${line.order}. ${line.role} · ${line.text}`;
  block.setAttribute("aria-label", `第 ${line.order} 句，${line.start_ms} 到 ${line.end_ms} 毫秒。方向键移动；左右边缘手柄可调整开始或结束时间。`);
}

function updateTimelineRowTimes(line) {
  const row = [...$("timelineBody").querySelectorAll("tr")].find((item) => item.dataset.lineId === line.line_id);
  if (!row) return;
  for (const field of ["start_ms", "end_ms"]) {
    const input = row.querySelector(`[data-field="${field}"]`);
    if (input && document.activeElement !== input) input.value = line[field];
  }
}

function tableCell(label, ...children) {
  const cell = document.createElement("td");
  cell.dataset.label = label;
  cell.append(...children);
  return cell;
}

function lineStatusLabel(status) {
  return ({ pending: "等待生成", running: "正在生成", completed: "生成完成", failed: "生成失败" })[status] || "等待生成";
}

function lineResultView(line) {
  const wrapper = document.createElement("div");
  const status = ["pending", "running", "completed", "failed"].includes(line.status) ? line.status : "pending";
  wrapper.className = `line-result-state ${status}`;
  const badge = document.createElement("span");
  badge.className = "line-status-badge";
  badge.textContent = lineStatusLabel(status);
  wrapper.append(badge);
  if (line.error) {
    const error = document.createElement("span");
    error.className = "line-result-error";
    error.textContent = line.error;
    error.title = line.error;
    wrapper.append(error);
  }
  if (line.audio_file) {
    const audioLabel = document.createElement("span");
    audioLabel.className = "line-audio-label";
    audioLabel.textContent = status === "completed"
      ? "最新音频"
      : (status === "failed" ? "上次成功音频（本次失败）" : "上次成功音频（等待本次结果）");
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.setAttribute("aria-label", `第 ${line.order} 句${audioLabel.textContent}`);
    audio.src = `/api/outputs/${encodeURIComponent(line.audio_file)}`;
    const download = document.createElement("a");
    download.className = "line-audio-download";
    download.href = audio.src;
    download.download = "";
    download.textContent = "下载 WAV";
    wrapper.append(audioLabel, audio, download);
  }
  return wrapper;
}

function refreshTimelineLineState(lineId) {
  const line = state.dialogueProject?.lines?.find((item) => item.line_id === lineId);
  if (!line) return;
  const row = [...$("timelineBody").querySelectorAll("tr")].find((item) => item.dataset.lineId === lineId);
  if (row) {
    row.dataset.status = line.status || "pending";
    const cell = row.querySelector(".line-result-cell");
    if (cell) cell.replaceChildren(lineResultView(line));
  }
  const block = $("timelineTrack").querySelector(`.timeline-block[data-line-id="${lineId}"]`);
  if (block) block.dataset.status = line.status || "pending";
}

function resolveBatchLineId(payload, message, singleLineId = "") {
  if (message?.line_id) return String(message.line_id);
  if (singleLineId) return singleLineId;
  const index = Number(message?.index);
  return Number.isInteger(index) ? String(payload?.items?.[index]?.line_id || "") : "";
}

function projectMissingAudioLines(project) {
  return (project?.lines || []).filter((line) => !String(line.audio_file || "").trim());
}

async function syncDialogueProjectFromBatch(messageProject, projectId, selectedLineId) {
  let project = messageProject;
  if (!project && projectId) project = await api(`/api/projects/${encodeURIComponent(projectId)}`);
  if (!project?.project_id || !Array.isArray(project.lines)) return null;
  state.dialogueProject = project;
  state.projectBaseRevision = project.revision;
  state.selectedLineId = project.lines.some((line) => line.line_id === selectedLineId)
    ? selectedLineId
    : (project.lines[0]?.line_id || "");
  $("timingPolicy").value = project.timing?.policy || "preserve";
  renderRoleMappings(project.lines.map((line) => line.role));
  markProjectSaved(`生成结果已同步 · revision ${project.revision}`);
  renderTimeline();
  return project;
}

async function ensureDialogueProjectSaved() {
  if (!state.dialogueProject) throw new Error("当前没有可保存的工程。");
  if (state.projectBaseRevision === null || state.projectDirty) {
    setActionMessage("batchStatus", "单句重跑前正在可靠保存工程与未保存修改…");
    await saveDialogueProject();
  }
  return state.dialogueProject;
}

function renderTimeline() {
  const project = state.dialogueProject;
  const body = $("timelineBody");
  const track = $("timelineTrack");
  body.replaceChildren();
  track.replaceChildren();
  updateTimelineSummary();
  if (!project?.lines?.length) return;

  project.lines.forEach(normalizeLineTiming);
  const scaleMs = timelineScaleMs(project);
  track.dataset.scaleMs = String(scaleMs);
  const tableFragment = document.createDocumentFragment();
  for (const line of project.lines) {
    const row = document.createElement("tr");
    row.dataset.lineId = line.line_id;
    row.dataset.status = line.status || "pending";
    row.classList.toggle("selected", state.selectedLineId === line.line_id);
    const indexCell = tableCell("序号", document.createTextNode(String(line.order)));
    const roleCell = tableCell("角色", fieldInput(line, "role"));
    const voiceCell = tableCell("音色", lineVoiceSelect(line));
    const language = document.createElement("select"); language.dataset.field = "language"; language.disabled = state.batching; language.setAttribute("aria-label", `第 ${line.order} 句语言`);
    for (const [value, label] of [["auto", "自动"], ["zh", "中文"], ["en", "EN"]]) { const option = document.createElement("option"); option.value = value; option.textContent = label; option.selected = line.language === value; language.append(option); }
    const languageCell = tableCell("语言", language);
    const startInput = fieldInput(line, "start_ms", "number"); startInput.min = "0"; startInput.max = String(MAX_TIMELINE_MS - MIN_LINE_DURATION_MS); startInput.step = String(timelineSnapMs());
    const endInput = fieldInput(line, "end_ms", "number"); endInput.min = String(MIN_LINE_DURATION_MS); endInput.max = String(MAX_TIMELINE_MS); endInput.step = String(timelineSnapMs());
    const startCell = tableCell("开始 ms", startInput);
    const endCell = tableCell("结束 ms", endInput);
    const textCell = tableCell("台词", fieldInput(line, "text", "textarea"));
    const directionMode = document.createElement("select"); directionMode.dataset.field = "direction_mode"; directionMode.disabled = state.batching; directionMode.setAttribute("aria-label", `第 ${line.order} 句演绎模式`);
    for (const [value, label] of [["inherit", "继承音色"], ["override", "逐句覆盖"], ["neutral", "中性"]]) { const option = document.createElement("option"); option.value = value; option.textContent = label; option.selected = line.direction_mode === value; directionMode.append(option); }
    const directionText = fieldInput(line, "direction_text"); directionText.placeholder = "例如：压低声音，克制而悲伤";
    const directionCell = tableCell("逐句演绎", directionMode, directionText);
    const resultCell = tableCell("状态 / 最新结果", lineResultView(line)); resultCell.classList.add("line-result-cell");
    const actions = tableCell("操作"); actions.classList.add("line-controls");
    for (const [action, label] of [["up", "上移"], ["down", "下移"], ["single", "生成该句"], ["delete", "删除"]]) { const button = document.createElement("button"); button.type = "button"; button.className = action === "delete" ? "danger" : "secondary"; button.dataset.action = action; button.textContent = label; button.disabled = state.batching; button.setAttribute("aria-label", `第 ${line.order} 句${label}`); actions.append(button); }
    row.append(indexCell, roleCell, voiceCell, languageCell, startCell, endCell, textCell, directionCell, resultCell, actions);
    tableFragment.append(row);
  }
  body.append(tableFragment);

  const visibleLines = project.lines.slice(0, TIMELINE_TRACK_RENDER_LIMIT);
  const selected = project.lines.find((line) => line.line_id === state.selectedLineId);
  if (selected && !visibleLines.includes(selected) && visibleLines.length) visibleLines[visibleLines.length - 1] = selected;
  const trackFragment = document.createDocumentFragment();
  for (const line of visibleLines) {
    const lane = document.createElement("div"); lane.className = "timeline-lane"; lane.dataset.lineId = line.line_id;
    const block = document.createElement("div"); block.className = `timeline-block${state.selectedLineId === line.line_id ? " selected" : ""}`; block.tabIndex = state.batching ? -1 : 0; block.dataset.lineId = line.line_id; block.dataset.status = line.status || "pending"; block.setAttribute("aria-describedby", "timelineHelp");
    const startHandle = document.createElement("button"); startHandle.type = "button"; startHandle.disabled = state.batching; startHandle.className = "timeline-handle start"; startHandle.dataset.resize = "start"; startHandle.setAttribute("aria-label", `调整第 ${line.order} 句开始时间`);
    const label = document.createElement("span"); label.className = "timeline-block-label";
    const endHandle = document.createElement("button"); endHandle.type = "button"; endHandle.disabled = state.batching; endHandle.className = "timeline-handle end"; endHandle.dataset.resize = "end"; endHandle.setAttribute("aria-label", `调整第 ${line.order} 句结束时间`);
    block.append(startHandle, label, endHandle);
    updateTimelineBlock(block, line, scaleMs);
    lane.append(block); trackFragment.append(lane);
  }
  track.append(trackFragment);
}

function markLineChanged(line, field) {
  const timing = ["start_ms", "end_ms"].includes(field);
  const dirty = new Set(line.dirty_fields || []);
  dirty.add(timing ? "timing" : field);
  line.dirty_fields = [...dirty];
  line.status = timing ? line.status : "pending";
  state.dialogueProject.revision += 1;
  markProjectDirty();
  updateTimelineSummary();
}

async function parseDialogueEditor() {
  if (state.batching) throw new Error("当前生成期间已锁定时间轴；请等待完成或先停止任务。");
  const text = $("batchInput").value.trim();
  if (!text) throw new Error("请先粘贴脚本、SRT、TXT 或 JSON。");
  if (!confirmReplaceDirtyProject("重新解析")) return;
  const selected = $("batchKind").value;
  const looksRoleBased = looksMultiRoleScript(text);
  const kind = selected === "items" ? (looksRoleBased ? "script" : "txt") : selected;
  if (kind === "script" && selected !== "script") {
    $("batchKind").value = "script";
    updateBatchKind();
  }
  const result = await api("/api/dialogue/parse", {
    method: "POST",
    body: JSON.stringify({
      kind,
      text,
      default_role: $("defaultRole").value.trim() || "旁白",
    }),
  });
  const defaultLanguage = $("defaultLanguage").value;
  if (defaultLanguage !== "auto") {
    result.project.lines.forEach((line) => {
      if (!line.language || line.language === "auto") line.language = defaultLanguage;
    });
  }
  state.dialogueProject = result.project;
  state.dialogueProject.timing.policy = $("timingPolicy").value;
  state.projectBaseRevision = null;
  state.selectedLineId = result.project.lines[0]?.line_id || "";
  renderRoleMappings(result.project.lines.map((line) => line.role));
  const mappings = batchRoleVoices();
  result.project.lines.forEach((line) => {
    const match = Object.entries(mappings).find(([role]) => normalizeRoleName(role) === normalizeRoleName(line.role));
    if (match) line.voice_id = match[1];
  });
  renderTimeline();
  markProjectDirty("新解析工程尚未保存 · 正在保存草稿…");
  setActionMessage("batchStatus", result.warnings?.length ? result.warnings.join("；") : `已解析 ${result.project.lines.length} 句，可编辑时间轴。`);
}

function addDialogueLine() {
  if (state.batching) {
    setActionMessage("batchStatus", "当前生成期间已锁定时间轴；请等待完成或先停止任务。", "error");
    return;
  }
  if (!state.dialogueProject) state.dialogueProject = { schema_version: 2, project_id: crypto.randomUUID().replaceAll("-", ""), revision: 0, name: "未命名对白工程", timing: { policy: "preserve", gap_ms: 200, snap_ms: 50 }, lines: [] };
  const last = state.dialogueProject.lines.at(-1);
  const start = last ? last.end_ms + 200 : 0;
  state.dialogueProject.lines.push({ line_id: crypto.randomUUID().replaceAll("-", ""), order: state.dialogueProject.lines.length + 1, role: "旁白", voice_id: "", language: "auto", text: "新台词", start_ms: start, end_ms: start + 1200, direction_mode: "inherit", direction_text: "", cfg_scale: null, seed: null, audio_file: "", dirty_fields: ["text"], status: "pending", error: "" });
  state.dialogueProject.revision += 1;
  state.selectedLineId = state.dialogueProject.lines.at(-1).line_id;
  renderRoleMappings(state.dialogueProject.lines.map((line) => line.role));
  markProjectDirty();
  renderTimeline();
}

async function saveDialogueProject() {
  if (state.batching) throw new Error("当前生成期间不能再次保存工程；完成后会自动同步最新 revision。");
  if (!state.dialogueProject) throw new Error("当前没有可保存的工程。");
  const expected = state.projectBaseRevision;
  state.dialogueProject = await api(`/api/projects/${encodeURIComponent(state.dialogueProject.project_id)}`, { method: "PUT", body: JSON.stringify({ project: state.dialogueProject, expected_revision: expected }) });
  state.projectBaseRevision = state.dialogueProject.revision;
  markProjectSaved(`工程已保存 · revision ${state.dialogueProject.revision}`);
  renderTimeline();
  setActionMessage("batchStatus", `工程已保存 · revision ${state.dialogueProject.revision}`, "success");
}

async function exportDialogueProject() {
  if (state.batching) throw new Error("当前生成期间不能导出工程；请等待结果同步完成。");
  if (!state.dialogueProject) throw new Error("当前没有可导出的工程。");
  const result = await api("/api/projects/export", { method: "POST", body: JSON.stringify({ project: state.dialogueProject }) });
  await window.t8Desktop?.openPath(result.path);
  setActionMessage("batchStatus", `工程包已导出：${result.path}`, "success");
}

async function importDialogueProject() {
  if (state.batching) throw new Error("当前生成期间不能替换工程；请等待完成或先停止任务。");
  if (!confirmReplaceDirtyProject("导入工程包")) return;
  const path = await window.t8Desktop?.chooseBundleFile("project");
  if (!path) return;
  state.dialogueProject = await api("/api/projects/import", { method: "POST", body: JSON.stringify({ path }) });
  state.projectBaseRevision = state.dialogueProject.revision;
  $("timingPolicy").value = state.dialogueProject.timing?.policy || "preserve";
  state.selectedLineId = state.dialogueProject.lines?.[0]?.line_id || "";
  renderRoleMappings((state.dialogueProject.lines || []).map((line) => line.role));
  markProjectSaved("已导入并保存工程包");
  renderTimeline();
  setActionMessage("batchStatus", `已安全导入工程“${state.dialogueProject.name}”。`, "success");
}

async function exportDialogueSrt() {
  if (state.batching) throw new Error("当前生成期间不能回写 SRT；请等待结果同步完成。");
  if (!state.dialogueProject) throw new Error("当前没有可回写的时间轴。");
  const result = await api("/api/dialogue/srt", { method: "POST", body: JSON.stringify({ project: state.dialogueProject }) });
  $("batchKind").value = "srt";
  $("batchInput").value = result.srt;
  updateBatchKind();
  setActionMessage("batchStatus", "已按当前毫秒时间轴回写 SRT，可继续编辑或复制。", "success");
}

function applyTimelineDelta(line, originStart, originEnd, mode, delta) {
  if (mode === "move") {
    const duration = Math.max(MIN_LINE_DURATION_MS, originEnd - originStart);
    line.start_ms = Math.min(MAX_TIMELINE_MS - duration, Math.max(0, originStart + delta));
    line.end_ms = line.start_ms + duration;
  } else if (mode === "start") {
    line.start_ms = Math.max(0, Math.min(originEnd - MIN_LINE_DURATION_MS, originStart + delta));
    line.end_ms = originEnd;
  } else {
    line.start_ms = originStart;
    line.end_ms = Math.min(MAX_TIMELINE_MS, Math.max(originStart + MIN_LINE_DURATION_MS, originEnd + delta));
  }
}

function startTimelineDrag(event) {
  if (state.batching) return;
  if (event.button !== 0) return;
  const block = event.target.closest(".timeline-block");
  if (!block || !state.dialogueProject) return;
  const line = state.dialogueProject.lines.find((item) => item.line_id === block.dataset.lineId);
  if (!line) return;
  event.preventDefault();
  state.selectedLineId = line.line_id;
  $("timelineTrack").querySelectorAll(".timeline-block.selected").forEach((item) => item.classList.toggle("selected", item === block));
  const mode = event.target.closest(".timeline-handle")?.dataset.resize || "move";
  const originX = event.clientX, originStart = line.start_ms, originEnd = line.end_ms;
  const scaleMs = Number($("timelineTrack").dataset.scaleMs) || timelineScaleMs();
  const pixels = Math.max(1, $("timelineTrack").querySelector(".timeline-lane")?.clientWidth || $("timelineTrack").clientWidth);
  const pointerId = event.pointerId;
  let changed = false, frame = 0, pendingEvent = null;
  const applyMove = (moveEvent) => {
    const raw = (moveEvent.clientX - originX) / pixels * scaleMs;
    const snap = moveEvent.altKey ? 1 : timelineSnapMs();
    const delta = Math.round(raw / snap) * snap;
    applyTimelineDelta(line, originStart, originEnd, mode, delta);
    changed = line.start_ms !== originStart || line.end_ms !== originEnd;
    updateTimelineBlock(block, line, scaleMs);
    updateTimelineRowTimes(line);
  };
  const move = (moveEvent) => {
    if (moveEvent.pointerId !== pointerId) return;
    pendingEvent = moveEvent;
    if (!frame) frame = requestAnimationFrame(() => { frame = 0; if (pendingEvent) applyMove(pendingEvent); });
  };
  const finish = (finishEvent) => {
    if (finishEvent.pointerId !== pointerId) return;
    if (frame) cancelAnimationFrame(frame);
    if (pendingEvent) applyMove(pendingEvent);
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", finish);
    document.removeEventListener("pointercancel", finish);
    if (changed) markLineChanged(line, "start_ms");
    renderTimeline();
  };
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", finish);
  document.addEventListener("pointercancel", finish);
}

function timelineKey(event) {
  if (state.batching) return;
  const block = event.target.closest(".timeline-block");
  if (!block || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
  const line = state.dialogueProject?.lines.find((item) => item.line_id === block.dataset.lineId);
  if (!line) return;
  event.preventDefault();
  const step = event.altKey ? 1 : timelineSnapMs();
  const delta = event.key === "ArrowLeft" ? -step : step;
  const mode = event.target.closest(".timeline-handle")?.dataset.resize || (event.shiftKey ? "end" : "move");
  const originStart = line.start_ms, originEnd = line.end_ms;
  applyTimelineDelta(line, originStart, originEnd, mode, delta);
  if (line.start_ms !== originStart || line.end_ms !== originEnd) markLineChanged(line, mode === "end" ? "end_ms" : "start_ms");
  const scaleMs = Number($("timelineTrack").dataset.scaleMs) || timelineScaleMs();
  if (line.end_ms > scaleMs) {
    renderTimeline();
    const nextFocus = $("timelineTrack").querySelector(`.timeline-block[data-line-id="${line.line_id}"]${mode === "move" ? "" : ` .timeline-handle.${mode}`}`);
    nextFocus?.focus();
  } else {
    updateTimelineBlock(block, line, scaleMs);
    updateTimelineRowTimes(line);
  }
}

function reuseHistoryItem(item) {
  if (item.kind !== "single") return;
  selectMode(["design", "clone", "direction"].includes(item.mode) ? item.mode : "design");
  $("targetText").value = item.text || "";
  $("instruction").value = item.instruction || "";
  const matchingVoice = state.voices.find((voice) => voice.id === item.voice_id);
  state.selectedVoiceId = matchingVoice?.id || "";
  $("voiceSelect").value = state.selectedVoiceId;
  showWorkspaceTab("generate");
  setActionMessage("generationStatus", matchingVoice
    ? `已复用历史参数与音色“${matchingVoice.name}”，确认后可重新生成。`
    : "已复用历史文本和演绎参数；原音色不可用时已切换为手动配置。", "success");
}

function renderHistory() {
  const query = $("historySearch").value.trim().toLowerCase();
  const kind = $("historyKind").value;
  const filtered = state.historyItems.filter((item) => {
    if (kind !== "all" && item.kind !== kind) return false;
    if (!query) return true;
    return [item.text, item.output, item.mode, item.instruction, item.voice_id]
      .join(" ").toLowerCase().includes(query);
  });
  const pageCount = Math.max(1, Math.ceil(filtered.length / state.historyPageSize));
  state.historyPage = Math.min(Math.max(1, state.historyPage), pageCount);
  const start = (state.historyPage - 1) * state.historyPageSize;
  const visible = filtered.slice(start, start + state.historyPageSize);
  const cards = visible.map((item) => {
    const card = document.createElement("article");
    card.className = "record-card";
    const copy = document.createElement("div");
    const title = document.createElement("h3");
    title.textContent = item.text || (item.kind === "batch" ? `批量生成 · ${item.count || 0} 项` : "语音生成");
    const details = [
      new Date((item.created_at || 0) * 1000).toLocaleString(),
      item.kind === "batch" ? "批量 / 多角色" : (item.mode || "单句"),
      Number.isFinite(Number(item.metadata?.duration_seconds)) ? formatDuration(item.metadata.duration_seconds) : "",
      item.output || ""
    ].filter(Boolean);
    const meta = document.createElement("p");
    meta.textContent = details.join(" · ");
    copy.append(title, meta);
    const actions = document.createElement("div");
    actions.className = "row-actions record-actions";
    if (item.output) {
      const link = document.createElement("a");
      link.className = "button-link";
      link.href = `/api/outputs/${encodeURIComponent(item.output)}`;
      link.textContent = "试听 / 下载 WAV";
      actions.append(link);
    }
    if (item.kind === "single") {
      const reuse = document.createElement("button");
      reuse.className = "secondary";
      reuse.textContent = "复用参数";
      reuse.addEventListener("click", () => reuseHistoryItem(item));
      actions.append(reuse);
    }
    const reveal = document.createElement("button");
    reveal.className = "text-button";
    reveal.textContent = "打开输出目录";
    reveal.addEventListener("click", () => openKnownPath("output"));
    actions.append(reveal);
    card.append(copy, actions);
    return card;
  });
  if (!cards.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const message = document.createElement("p");
    message.textContent = state.historyItems.length ? "没有匹配的生成记录。" : "尚无生成历史。";
    empty.append(message);
    cards.push(empty);
  }
  $("historyList").replaceChildren(...cards);
  $("historySummary").textContent = `共 ${filtered.length} 条${state.historyItems.length !== filtered.length ? `（全部 ${state.historyItems.length} 条）` : ""}`;
  $("historyPagination").hidden = filtered.length <= state.historyPageSize;
  $("historyPageStatus").textContent = `第 ${state.historyPage} / ${pageCount} 页`;
  $("historyPrevious").disabled = state.historyPage <= 1;
  $("historyNext").disabled = state.historyPage >= pageCount;
}

async function refreshHistory() {
  const result = await api("/api/history?limit=500");
  state.historyItems = Array.isArray(result.history) ? result.history : [];
  state.historyPage = 1;
  renderHistory();
}

async function refreshQueue() {
  const result = await api("/api/queue");
  const statusLabels = { pending: "等待中", running: "生成中", completed: "已完成", failed: "失败", paused: "已暂停", cancelled: "已取消" };
  const cards = (result.jobs || []).map((item) => {
    const card = document.createElement("article"); card.className = "record-card";
    const copy = document.createElement("div"); const title = document.createElement("h3"); title.textContent = `对白任务 · ${item.total || 0} 句`; const meta = document.createElement("p"); meta.textContent = `${statusLabels[item.status] || item.status} · ${new Date((item.updated_at || 0) * 1000).toLocaleString()}${item.error ? ` · 原因：${item.error}` : ""}`; copy.append(title, meta); card.append(copy);
    if (["failed", "paused"].includes(item.status)) {
      const retry = document.createElement("button"); retry.className = "secondary"; retry.textContent = "从断点继续";
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        retry.textContent = "正在继续…";
        try { await resumeBatchJob(item.job_id); }
        catch (error) {
          retry.disabled = false;
          retry.textContent = "从断点继续";
          renderListError("queueList", error, "重试刷新队列", refreshQueue);
        }
      });
      card.append(retry);
    }
    return card;
  });
  if (!cards.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const message = document.createElement("p");
    message.textContent = "任务队列为空；批量或多角色任务开始后会自动保存在这里。";
    const action = document.createElement("button");
    action.className = "primary";
    action.textContent = "前往多角色 / 批量";
    action.addEventListener("click", () => showWorkspaceTab("dialogue"));
    empty.append(message, action);
    cards.push(empty);
  }
  $("queueList").replaceChildren(...cards);
}

function renderListError(containerId, error, retryLabel, retryAction) {
  const card = document.createElement("div");
  card.className = "empty-state error-state";
  const message = document.createElement("p");
  message.setAttribute("role", "alert");
  message.textContent = actionableError(error, "确认本地服务仍在运行，然后重试");
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "secondary";
  retry.textContent = retryLabel;
  retry.addEventListener("click", () => retryAction().catch((nextError) => renderListError(containerId, nextError, retryLabel, retryAction)));
  card.append(message, retry);
  $(containerId).replaceChildren(card);
}

document.querySelectorAll(".mode-tab").forEach((button) => button.addEventListener("click", () => {
  clearSelectedVoice();
  selectMode(button.dataset.mode);
}));
document.querySelectorAll(".workspace-tab").forEach((button) => {
  button.addEventListener("click", () => showWorkspaceTab(button.dataset.tab));
  button.addEventListener("keydown", workspaceTabKeydown);
});
document.querySelectorAll(".template-card").forEach((button) => button.addEventListener("click", () => launchStudio(button.dataset.template)));
document.querySelectorAll(".link-button").forEach((button) => button.addEventListener("click", () => window.t8Desktop?.openExternal(button.dataset.url)));
$('startStudioButton').addEventListener('click', () => launchStudio());
$('returnLauncherButton').addEventListener('click', () => returnToLauncher().catch((error) => { setActionMessage("runtimeSummary", actionableError(error, "停止当前生成或批量任务后重试"), "error"); }));
for (const [id, kind] of [["openOutputButtonTop", "output"], ["openLogsButton", "logs"], ["openDataButton", "data"], ["settingsOpenOutputButton", "output"], ["settingsOpenLogsButton", "logs"], ["settingsOpenDataButton", "data"]]) $(id).addEventListener("click", () => openKnownPath(kind));
$("refreshButton").addEventListener("click", refresh);
$("settingsRefreshDiagnosticsButton").addEventListener("click", () => refresh().catch((error) => setActionMessage("settingsDiagnosticStatus", errorMessage(error), "error")));
$("copyDiagnosticsButton").addEventListener("click", async () => {
  try {
    const data = state.diagnostics || await api("/api/diagnostics");
    const gpu = data.gpu?.devices?.[0];
    const packages = data.packages || {};
    const lines = [
      `Breeze TTS Desktop ${data.project_version || "未知"}`,
      `GPU: ${gpu ? `${gpu.name} / ${gpu.memory_free_mib || "?"} MiB free` : "未检测到"}`,
      `Python: ${data.python?.version || "未知"} ${data.python?.architecture || ""}`,
      `PyTorch: ${packages.torch || "缺失"}`,
      `Transformers: ${packages.transformers || "缺失"}`,
      `Qwen TTS: ${packages["qwen-tts"] || "缺失"}`,
      `Runtime: ${data.runtime?.loaded ? "loaded" : "unloaded"}`,
      `Model valid: ${Boolean(data.model?.valid)}`
    ];
    await navigator.clipboard.writeText(lines.join("\n"));
    setActionMessage("settingsDiagnosticStatus", "诊断摘要已复制到剪贴板。", "success");
  } catch (error) {
    setActionMessage("settingsDiagnosticStatus", actionableError(error, "可使用启动首页的“导出诊断”生成文件"), "error");
  }
});
$("checkUpdateButton").addEventListener("click", () => checkForDesktopUpdates().catch((error) => { $("updateStatus").textContent = error.message; }));
$("installUpdateButton").addEventListener("click", () => installDesktopUpdate().catch((error) => { $("updateStatus").textContent = error.message; }));
$("refreshVoicesButton").addEventListener("click", () => refreshVoices().catch((error) => { $("voiceStatus").textContent = error.message; }));
$("newVoiceButton").addEventListener("click", () => beginNewVoice());
$("voiceSelect").addEventListener("change", applySelectedVoice);
$('libraryVoiceSelect').addEventListener('change', selectLibraryVoice);
$('voiceSearch').addEventListener('input', renderLibraryVoices);
$('favoriteOnly').addEventListener('change', renderLibraryVoices);
$("voiceMode").addEventListener("change", updateVoiceModeEditor);
$("voiceReferenceAudio").addEventListener("change", () => previewVoiceReferenceFile().catch((error) => {
  clearVoiceReferencePreview();
  $("voiceReferenceStatus").textContent = error.message;
  updateControlState();
}));
$("voiceClearReferenceButton").addEventListener("click", markVoiceReferenceForRemoval);
$("voiceTranscribeButton").addEventListener("click", () => transcribeVoiceReference().catch((error) => { $("voiceReferenceStatus").textContent = error.message; }));
$("historySearch").addEventListener("input", () => { state.historyPage = 1; renderHistory(); });
$("historyKind").addEventListener("change", () => { state.historyPage = 1; renderHistory(); });
$("historyPrevious").addEventListener("click", () => { state.historyPage -= 1; renderHistory(); });
$("historyNext").addEventListener("click", () => { state.historyPage += 1; renderHistory(); });
$("applyVoiceButton").addEventListener("click", applyLibraryVoiceToGeneration);
$("saveVoiceButton").addEventListener("click", () => saveCurrentVoice().catch((error) => { setActionMessage("voiceStatus", actionableError(error, "确认名称、音色类型、参考音频与逐字稿后重试"), "error"); }));
$('updateVoiceButton').addEventListener('click', () => updateSelectedVoice().catch((error) => { setActionMessage("voiceStatus", actionableError(error, "确认参考音频与逐字稿仍完整"), "error"); }));
$('exportVoiceButton').addEventListener('click', () => exportSelectedVoice().catch((error) => { $("voiceStatus").textContent = error.message; }));
$('importVoiceButton').addEventListener('click', () => importVoiceBundle().catch((error) => { $("voiceStatus").textContent = error.message; }));
$("deleteVoiceButton").addEventListener("click", () => deleteSelectedVoice().catch((error) => { $("voiceStatus").textContent = error.message; }));
$("chooseModelButton").addEventListener("click", () => chooseModelDirectory().catch((error) => { $("downloadStatus").textContent = error.message; }));
$("chooseOutputButton").addEventListener("click", () => chooseOutputDirectory().catch((error) => { $("outputStatus").textContent = error.message; }));
$("downloadModelButton").addEventListener("click", () => downloadModel().catch((error) => { setActionMessage("downloadStatus", conciseActionError(error, "检查模型目录是否可写和网络是否可用"), "error"); }));
$("activateModelButton").addEventListener("click", () => activateCurrentModel().catch((error) => { setActionMessage("downloadStatus", conciseActionError(error, "若模型不完整，请先点击“下载／修复模型”"), "error"); }));
$("verifyModelButton").addEventListener("click", () => verifyCurrentModel().catch((error) => { setActionMessage("downloadStatus", conciseActionError(error, "若校验失败，请点击“下载／修复模型”"), "error"); }));
$("cancelDownloadButton").addEventListener("click", () => api("/api/models/download/cancel", { method: "POST" }).catch((error) => { $("downloadStatus").textContent = error.message; }));
$("generateButton").addEventListener("click", () => generate().catch((error) => { $("generationStatus").textContent = error.message; }));
$("cancelGenerateButton").addEventListener("click", cancelGeneration);
$("unloadButton").addEventListener("click", async () => {
  if (state.generating || state.batching) return;
  try {
    await api("/api/runtime/unload", { method: "POST" });
    refresh();
  } catch (error) {
    $("generationStatus").textContent = error.message;
  }
});
$("exportDiagnosticsButton").addEventListener("click", async () => {
  try {
    const result = await api("/api/diagnostics/export", { method: "POST" });
    await window.t8Desktop?.openPath(result.path);
  } catch (error) {
    $("modelSummary").textContent = error.message;
  }
});
$("openOutputButton").addEventListener("click", () => state.outputDirectory && window.t8Desktop?.openPath(state.outputDirectory));
$("viewLicenseButton").addEventListener("click", async () => { $("licenseText").textContent = await fetch("/api/license").then((response) => response.text()); $("licenseDialog").showModal(); });
$("instruction").addEventListener("input", () => {
  clearSelectedVoice();
  if ($("instruction").value !== state.recipeAppliedInstruction && state.selectedRecipe) clearDirectionRecipe({ preserveInstruction: true });
});
document.querySelectorAll(".recipe-chip").forEach((button) => button.addEventListener("click", () => applyDirectionRecipe(button.dataset.recipe)));
$("recipeIntensity").addEventListener("change", () => state.selectedRecipe && applyDirectionRecipe());
$("recipePace").addEventListener("change", () => state.selectedRecipe && applyDirectionRecipe());
$("clearDirectionRecipeButton").addEventListener("click", () => clearDirectionRecipe());
$("globalTaskViewButton").addEventListener("click", () => {
  const target = $("globalTaskBar").dataset.target;
  if (target) showWorkspaceTab(target);
});
$("globalTaskCancelButton").addEventListener("click", () => {
  if (state.batching) cancelBatch();
  else if (state.generating) cancelGeneration();
});
$("referenceText").addEventListener("input", () => clearSelectedVoice());
$("transcribeButton").addEventListener("click", () => transcribeReference().catch((error) => { $("whisperStatus").textContent = error.message; }));
$("installWhisperButton").addEventListener("click", () => installWhisperComponent().catch((error) => { $("whisperStatus").textContent = error.message; }));
$("batchKind").addEventListener("change", updateBatchKind);
$("batchInput").addEventListener("input", updateDetectedRoles);
$("defaultRolePreset").addEventListener("change", applyDefaultRolePreset);
$("defaultRole").addEventListener("input", matchDefaultRolePreset);
$("loadBatchExampleButton").addEventListener("click", loadBatchExample);
$("clearBatchInputButton").addEventListener("click", clearBatchInput);
$("roleMappingPanel").addEventListener("change", (event) => {
  if (state.batching) return;
  const role = event.target.dataset.role;
  if (!role || !state.dialogueProject) return;
  let changed = false;
  state.dialogueProject.lines.forEach((line) => {
    if (normalizeRoleName(line.role) === normalizeRoleName(role) && line.voice_id !== event.target.value) {
      line.voice_id = event.target.value;
      const dirty = new Set(line.dirty_fields || []);
      dirty.add("voice_id");
      line.dirty_fields = [...dirty];
      line.status = "pending";
      changed = true;
    }
  });
  if (changed) {
    state.dialogueProject.revision += 1;
    markProjectDirty();
    renderTimeline();
  }
});
$("scriptFileInput").addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error("脚本文件不能超过 2 MiB。");
    const extension = file.name.split(".").pop()?.toLowerCase();
    $("batchKind").value = extension === "srt" ? "srt" : extension === "json" ? "json" : "txt";
    $("batchInput").value = await file.text();
    updateBatchKind();
    updateDetectedRoles();
    setActionMessage("batchStatus", `已读取 ${file.name}，请点击“解析并检查”。`);
  } catch (error) {
    setActionMessage("batchStatus", actionableError(error, "请选择小于 2 MiB 的 UTF-8 SRT、TXT 或 JSON 文件"), "error");
  } finally {
    event.target.value = "";
  }
});
$('timingPolicy').addEventListener('change', () => { if (state.batching) return; if (state.dialogueProject) { state.dialogueProject.timing.policy = $("timingPolicy").value; state.dialogueProject.revision += 1; markProjectDirty(); renderTimeline(); } });
$("analyzeRolesButton").addEventListener("click", () => analyzeBatchRoles().catch((error) => { setActionMessage("batchStatus", actionableError(error, "检查角色行是否使用“角色：台词”或“[角色] 台词”格式"), "error"); }));
$("batchStartButton").addEventListener("click", () => startBatch().catch((error) => { setActionMessage("batchStatus", actionableError(error, "检查模型、音色映射和台词内容后重试"), "error"); }));
$("batchCancelButton").addEventListener("click", cancelBatch);
$('parseDialogueButton').addEventListener('click', () => parseDialogueEditor().catch((error) => { setActionMessage("batchStatus", actionableError(error, "检查所选格式和输入内容后再次解析"), "error"); }));
$('addDialogueLineButton').addEventListener('click', addDialogueLine);
$('saveProjectButton').addEventListener('click', () => saveDialogueProject().catch((error) => { setActionMessage("batchStatus", actionableError(error, "若工程已被其他窗口修改，请重新导入最新版后再保存"), "error"); }));
$('importProjectButton').addEventListener('click', () => importDialogueProject().catch((error) => { setActionMessage("batchStatus", actionableError(error, "确认工程包来自可信来源且结构完整"), "error"); }));
$('exportProjectButton').addEventListener('click', () => exportDialogueProject().catch((error) => { setActionMessage("batchStatus", actionableError(error, "确认输出目录可写后重试"), "error"); }));
$('exportSrtButton').addEventListener('click', () => exportDialogueSrt().catch((error) => { setActionMessage("batchStatus", actionableError(error, "先修正无效的开始和结束时间"), "error"); }));
$('refreshHistoryButton').addEventListener('click', () => refreshHistory().catch((error) => renderListError("historyList", error, "重试刷新历史", refreshHistory)));
$('refreshQueueButton').addEventListener('click', () => refreshQueue().catch((error) => renderListError("queueList", error, "重试刷新队列", refreshQueue)));
$('timelineTrack').addEventListener('pointerdown', startTimelineDrag);
$('timelineTrack').addEventListener('keydown', timelineKey);
$('timelineBody').addEventListener('input', (event) => {
  if (state.batching) return;
  const field = event.target.dataset.field;
  const row = event.target.closest('tr');
  const line = state.dialogueProject?.lines.find((item) => item.line_id === row?.dataset.lineId);
  if (!field || !line) return;
  const timingField = ['start_ms', 'end_ms'].includes(field);
  line[field] = timingField ? Math.max(0, Number(event.target.value) || 0) : event.target.value;
  markLineChanged(line, field);
  const block = $("timelineTrack").querySelector(`.timeline-block[data-line-id="${line.line_id}"]`);
  if (timingField) {
    const valid = line.end_ms - line.start_ms >= MIN_LINE_DURATION_MS;
    event.target.classList.toggle("invalid", !valid);
    if (valid) updateTimelineBlock(block, line, Number($("timelineTrack").dataset.scaleMs) || timelineScaleMs());
  } else {
    updateTimelineBlock(block, line, Number($("timelineTrack").dataset.scaleMs) || timelineScaleMs());
  }
});
$('timelineBody').addEventListener('change', (event) => {
  if (state.batching) return;
  const field = event.target.dataset.field;
  const row = event.target.closest('tr');
  const line = state.dialogueProject?.lines.find((item) => item.line_id === row?.dataset.lineId);
  if (!field || !line) return;
  const timingField = ['start_ms', 'end_ms'].includes(field);
  const value = timingField ? Math.max(0, Number(event.target.value) || 0) : event.target.value;
  if (line[field] !== value) { line[field] = value; markLineChanged(line, field); }
  if (timingField) normalizeLineTiming(line);
  event.target.classList.remove("invalid");
  if (field === "role") renderRoleMappings(state.dialogueProject.lines.map((item) => item.role));
  renderTimeline();
});
$('timelineBody').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button || !state.dialogueProject) return;
  if (state.batching) {
    setActionMessage("batchStatus", "已有任务正在运行；时间轴已锁定，请等待完成或先停止当前任务。", "error");
    return;
  }
  const row = button.closest('tr');
  const index = state.dialogueProject.lines.findIndex((item) => item.line_id === row.dataset.lineId);
  if (index < 0) return;
  const action = button.dataset.action;
  let changed = false;
  if (action === 'delete') { state.dialogueProject.lines.splice(index, 1); changed = true; }
  if (action === 'up' && index > 0) { [state.dialogueProject.lines[index - 1], state.dialogueProject.lines[index]] = [state.dialogueProject.lines[index], state.dialogueProject.lines[index - 1]]; changed = true; }
  if (action === 'down' && index < state.dialogueProject.lines.length - 1) { [state.dialogueProject.lines[index + 1], state.dialogueProject.lines[index]] = [state.dialogueProject.lines[index], state.dialogueProject.lines[index + 1]]; changed = true; }
  if (action === 'single') {
    try {
      const requestedLineId = state.dialogueProject.lines[index].line_id;
      state.selectedLineId = requestedLineId;
      renderTimeline();
      await ensureDialogueProjectSaved();
      const line = state.dialogueProject.lines.find((item) => item.line_id === requestedLineId);
      if (!line) throw new Error("保存工程后找不到当前句，请重新选择后重试。");
      line.status = "pending";
      line.error = "";
      refreshTimelineLineState(requestedLineId);
      const defaults = await currentBatchDefaults();
      await runBatchPayload({ defaults, items: [{ ...line, subtitle: { index: line.order, start_ms: line.start_ms, end_ms: line.end_ms, text: line.text } }], timeline: true, timing_policy: state.dialogueProject.timing?.policy || "preserve", project_id: state.dialogueProject.project_id, project_revision: state.projectBaseRevision }, { singleLineId: requestedLineId });
    } catch (error) {
      setActionMessage("batchStatus", actionableError(error, "检查该句音色、演绎指令和模型状态后重试"), "error");
    }
    return;
  }
  if (!changed) return;
  state.dialogueProject.lines.forEach((line, order) => { line.order = order + 1; });
  state.dialogueProject.revision += 1;
  if (!state.dialogueProject.lines.some((line) => line.line_id === state.selectedLineId)) state.selectedLineId = state.dialogueProject.lines[0]?.line_id || "";
  renderRoleMappings(state.dialogueProject.lines.map((line) => line.role));
  markProjectDirty();
  renderTimeline();
});
$("referenceAudio").addEventListener("change", async () => {
  clearSelectedVoice();
  const validationId = ++state.referenceValidationId;
  if (state.referencePreviewUrl) URL.revokeObjectURL(state.referencePreviewUrl);
  const file = $("referenceAudio").files[0];
  state.referenceDuration = null;
  if (!file) {
    state.referencePreviewUrl = null;
    $("referencePreview").src = "";
    $("referencePreview").hidden = true;
    $("referenceAudioStatus").textContent = `支持 WAV / FLAC / OGG / MP3，最长 ${MAX_REFERENCE_SECONDS} 秒。`;
    return;
  }
  try {
    const duration = await inspectReferenceAudio(file);
    if (validationId !== state.referenceValidationId) return;
    state.referenceDuration = duration;
    state.referencePreviewUrl = URL.createObjectURL(file);
    $("referencePreview").src = state.referencePreviewUrl;
    $("referencePreview").hidden = false;
    $("referenceAudioStatus").textContent = `格式可用 · ${state.referenceDuration.toFixed(1)} 秒`;
  } catch (error) {
    if (validationId !== state.referenceValidationId) return;
    $("referenceAudio").value = "";
    state.referencePreviewUrl = null;
    $("referencePreview").src = "";
    $("referencePreview").hidden = true;
    $("referenceAudioStatus").textContent = error.message;
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!state.projectDirty) return;
  persistProjectDraft();
  event.preventDefault();
  event.returnValue = "";
});
document.addEventListener("visibilitychange", () => { if (document.hidden) persistProjectDraft(); });

refresh().finally(() => {
  restoreProjectDraft();
  updateContinueDraftTemplate();
});
window.t8Desktop?.onUpdateStatus(renderUpdateStatus);
window.t8Desktop?.updateStatus().then(renderUpdateStatus).catch((error) => { $("updateStatus").textContent = error.message; });
selectMode("design");
updateBatchKind();
updateControlState();
