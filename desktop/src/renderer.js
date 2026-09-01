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
  generating: false,
  downloading: false,
  referenceDuration: null,
  referenceValidationId: 0,
  capabilities: {},
  voices: [],
  selectedVoiceId: "",
  batchSocket: null,
  batching: false,
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
const VOICE_CONTROL_IDS = ["voiceSelect", "libraryVoiceSelect", "voiceName", "applyVoiceButton", "saveVoiceButton", "updateVoiceButton", "deleteVoiceButton", "refreshVoicesButton", "exportVoiceButton", "importVoiceButton"];
const BATCH_CONTROL_IDS = ["batchKind", "batchInput", "analyzeRolesButton"];
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

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail || payload));
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
  $("batchCancelButton").disabled = !state.batching;
  $("transcribeButton").disabled = generationBusy || state.transcribing || !state.capabilities.whisper;
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
  const advanced = $("advancedLauncher");
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
  if (advanced && !advanced.dataset.autoSet) {
    advanced.open = !(report.valid && report.license_accepted);
    advanced.dataset.autoSet = "true";
  }
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
    ? "Whisper 已启用；首次转写会加载所选模型。"
    : "未安装 faster-whisper；可点击下方按钮联网安装，完成后需重启应用。";
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

function renderVoices(voices) {
  state.voices = Array.isArray(voices) ? voices : [];
  if (!state.voices.some((voice) => voice.id === state.selectedVoiceId)) state.selectedVoiceId = "";
  const manual = document.createElement("option");
  manual.value = "";
  manual.textContent = "手动配置（不使用音色库）";
  manual.selected = !state.selectedVoiceId;
  $("voiceSelect").replaceChildren(manual, ...state.voices.map((voice) => voiceOption(voice, state.selectedVoiceId)));
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
      : "填写右侧信息并点击“新建音色”，以后即可一键复用。";
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
      const selectedReport = await api("/api/models/select", {
        method: "POST",
        body: JSON.stringify({ model_dir: selected, accept_model_license: $("acceptLicense").checked })
      });
      await window.t8Desktop.saveDirectorySetting("modelDirectory", selected);
      renderModel(selectedReport.model);
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
  const result = await api("/api/models/select", {
    method: "POST",
    body: JSON.stringify({
      model_dir: $("modelPath").value.trim(),
      verify_hashes: false,
      accept_model_license: $("acceptLicense").checked
    })
  });
  await window.t8Desktop?.saveDirectorySetting("modelDirectory", $("modelPath").value.trim());
  renderModel(result.model);
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
  $("voiceLanguage").value = voice.language || "auto";
  $("voiceTags").value = (voice.tags || []).join(", ");
  $("voiceNotes").value = voice.notes || "";
  $("voicePreviewText").value = voice.preview_text || "你好，这是一段音色库试听。";
  $("voiceFavorite").checked = Boolean(voice.favorite);
  $("voiceStatus").textContent = `${voiceLabel(voice)} · ${voice.language || "auto"}${voice.favorite ? " · 已收藏" : ""}`;
  updateControlState();
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
  const reference = $("referenceAudio").files[0];
  const referenceText = $("referenceText").value.trim();
  if (["clone", "direction"].includes(state.mode) && (!reference || !referenceText)) {
    throw new Error("保存 Clone/Direction 音色必须选择参考音频并填写准确逐字稿。");
  }
  if (reference) await inspectReferenceAudio(reference);
  const created = await api("/api/voices", {
    method: "POST",
    body: JSON.stringify({
      name,
      mode: state.mode,
      instruction: $("instruction").value.trim(),
      reference_text: referenceText,
      reference_filename: reference?.name || "reference.wav",
      reference_audio_base64: await fileToBase64(reference),
      ...voiceMetadata()
    })
  });
  await refreshVoices();
  state.selectedVoiceId = created.id;
  renderVoices(state.voices);
  $("voiceName").value = "";
  $("voiceStatus").textContent = `已保存音色“${created.name}”。`;
}

async function updateSelectedVoice() {
  const voice = state.voices.find((item) => item.id === $("libraryVoiceSelect").value);
  if (!voice) throw new Error("请先选择要修改的音色。");
  const reference = $("referenceAudio").files[0];
  if (reference) await inspectReferenceAudio(reference);
  const updated = await api(`/api/voices/${encodeURIComponent(voice.id)}`, {
    method: "PATCH",
    body: JSON.stringify({
      name: $("voiceName").value.trim(),
      instruction: $("instruction").value.trim(),
      reference_text: $("referenceText").value.trim(),
      reference_filename: reference?.name || "reference.wav",
      reference_audio_base64: await fileToBase64(reference),
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
  $("voiceStatus").textContent = `已删除音色“${voice.name}”。`;
}

async function transcribeReference() {
  if (!state.capabilities.whisper) {
    throw new Error("未安装 faster-whisper；请先联网安装 Whisper 组件并重启应用。");
  }
  const reference = $("referenceAudio").files[0];
  if (!reference) throw new Error("请先选择参考音频。");
  await inspectReferenceAudio(reference);
  state.transcribing = true;
  updateControlState();
  $("whisperStatus").textContent = "正在转写；首次使用可能需要下载并加载 Whisper 模型…";
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
    if (role && !roles.some((item) => normalizeRoleName(item) === normalizeRoleName(role))) roles.push(role);
  }
  return roles;
}

function looksMultiRoleScript(text) {
  return String(text || "").split(/\r?\n/).some((line) => /^\s*(?:\[[^\]]+\]|[^：:]{1,40}[：:])\s*\S/.test(line));
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
  label.textContent = `#${Number(result.index) + 1}${result.role ? ` · ${result.role}` : ""}`;
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

function showMergedBatchOutput(message) {
  if (!message.merged_output) return;
  const panel = document.createElement("section");
  panel.className = "batch-merged";
  const heading = document.createElement("div");
  heading.className = "batch-merged-heading";
  const title = document.createElement("strong");
  title.textContent = "合并总音频";
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

async function runBatchPayload(payload, { resumeJobId = "" } = {}) {
  if (state.generating || state.batching || state.downloading) return;
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
  state.batching = true;
  state.batchTotal = 0;
  state.batchCompleted = 0;
  updateControlState();
  $("batchResults").replaceChildren();
  $("batchProgress").value = 0;
  setActionMessage("batchStatus", resumeJobId ? "正在读取断点并继续任务…" : "正在整理批量任务…");
  setGlobalTask({ kind: "working", kicker: "批量生成", title: resumeJobId ? "正在恢复断点" : "正在整理任务", detail: "任务会保存在队列中，可以切换到其他分页继续操作。", progress: "indeterminate", target: "dialogue", cancellable: true });
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
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); }
    catch (_) {
      setActionMessage("batchStatus", "收到无法解析的批量进度消息；请从任务队列检查最终状态。", "error");
      return;
    }
    if (message.type === "batch_start") {
      state.batchTotal = message.total;
      setActionMessage("batchStatus", `${resumeJobId ? "断点任务已继续" : "批量任务已开始"}，共 ${message.total} 项。${queueWarning ? ` ${queueWarning}` : ""}`);
      setGlobalTask({ kind: "working", kicker: "批量生成", title: `正在生成 0/${message.total}`, detail: queueWarning || "批量任务已开始，可在工作台中继续浏览。", progress: 0, target: "dialogue", cancellable: true });
    } else if (message.type === "item_start") {
      setActionMessage("batchStatus", `正在生成 ${message.index + 1}/${message.total}${message.role ? ` · ${message.role}` : ""}`);
      setGlobalTask({ kind: "working", kicker: "批量生成", title: `正在生成 ${message.index + 1}/${message.total}`, detail: message.role ? `当前角色：${message.role}` : "正在处理当前台词。", progress: message.total ? state.batchCompleted / message.total * 100 : "indeterminate", target: "dialogue", cancellable: true });
    } else if (message.type === "item_complete") {
      state.batchCompleted += 1;
      $("batchProgress").value = state.batchTotal ? state.batchCompleted / state.batchTotal * 100 : 0;
      appendBatchResult(message);
      setGlobalTask({ kind: "working", kicker: "批量生成", title: `已完成 ${state.batchCompleted}/${state.batchTotal || "?"}`, detail: "结果已逐条保存，可随时查看已完成音频。", progress: state.batchTotal ? state.batchCompleted / state.batchTotal * 100 : "indeterminate", target: "dialogue", cancellable: true });
    } else if (message.type === "batch_complete") {
      $("batchProgress").value = 100;
      showMergedBatchOutput(message);
      const duration = message.merged_metadata?.duration_seconds;
      setActionMessage("batchStatus", `批量完成，共生成 ${message.results?.length || state.batchCompleted} 项${Number.isFinite(Number(duration)) ? ` · 合并总时长 ${formatDuration(duration)}` : ""}。`, "success");
      setGlobalTask({ kind: "success", kicker: "批量生成", title: `已完成 ${message.results?.length || state.batchCompleted} 项`, detail: Number.isFinite(Number(duration)) ? `合并总时长 ${formatDuration(duration)}，结果已保存。` : "结果和合并音频已保存。", progress: 100, target: "dialogue" });
    } else if (message.type === "cancelled") {
      setActionMessage("batchStatus", `已取消；已完成 ${state.batchCompleted}/${state.batchTotal || "?"} 项。`);
      setGlobalTask({ kind: "idle", kicker: "批量生成", title: "任务已取消", detail: `已保留 ${state.batchCompleted}/${state.batchTotal || "?"} 项结果和队列断点。`, target: "dialogue" });
    } else if (message.type === "error") {
      setActionMessage("batchStatus", `批量失败：${actionableError(message.message, "保留当前断点，前往任务队列继续")}`, "error");
      setGlobalTask({ kind: "error", kicker: "批量生成", title: "任务中断", detail: `${message.message}；可从任务队列继续。`, target: "queue" });
    }
  };
  socket.onerror = () => {
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

async function startBatch() {
  const defaults = await currentBatchDefaults();
  if (state.dialogueProject?.lines?.length) {
    const items = state.dialogueProject.lines.map((line) => ({
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
    return runBatchPayload({ defaults, items, timeline: true, timing_policy: state.dialogueProject.timing?.policy || "preserve", project_id: state.dialogueProject.project_id });
  }
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
    $("batchKind").value = template.batchKind;
    updateBatchKind();
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
  state.launching = true;
  state.launchStartedAt = Date.now();
  $("startStudioButton").disabled = true;
  document.querySelectorAll(".template-card").forEach((button) => { button.disabled = true; });
  $("quickLaunchFeedback").hidden = false;
  $("quickLaunchFeedback").classList.remove("error");
  $("quickLaunchProgress").hidden = false;
  $("quickLaunchProgress").removeAttribute("value");
  $("quickLaunchStatus").textContent = "正在校验模型与许可证…";
  $("quickLaunchElapsed").textContent = "已等待 0 秒";
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
    const model = state.diagnostics?.model;
    if (!model?.valid || !model?.license_accepted) await activateCurrentModel();
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
    const message = `启动失败：${actionableError(error, "返回首页检查模型完整性、许可证和环境诊断后重试")}`;
    setActionMessage("launchStatus", message, "error");
    $("quickLaunchFeedback").classList.add("error");
    $("quickLaunchProgress").hidden = true;
    $("quickLaunchStatus").textContent = message;
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
    const indexCell = tableCell("序号", document.createTextNode(String(line.order)));
    const roleCell = tableCell("角色", fieldInput(line, "role"));
    const voiceCell = tableCell("音色", lineVoiceSelect(line));
    const language = document.createElement("select"); language.dataset.field = "language"; language.setAttribute("aria-label", `第 ${line.order} 句语言`);
    for (const [value, label] of [["auto", "自动"], ["zh", "中文"], ["en", "EN"]]) { const option = document.createElement("option"); option.value = value; option.textContent = label; option.selected = line.language === value; language.append(option); }
    const languageCell = tableCell("语言", language);
    const startInput = fieldInput(line, "start_ms", "number"); startInput.min = "0"; startInput.max = String(MAX_TIMELINE_MS - MIN_LINE_DURATION_MS); startInput.step = String(timelineSnapMs());
    const endInput = fieldInput(line, "end_ms", "number"); endInput.min = String(MIN_LINE_DURATION_MS); endInput.max = String(MAX_TIMELINE_MS); endInput.step = String(timelineSnapMs());
    const startCell = tableCell("开始 ms", startInput);
    const endCell = tableCell("结束 ms", endInput);
    const textCell = tableCell("台词", fieldInput(line, "text", "textarea"));
    const directionMode = document.createElement("select"); directionMode.dataset.field = "direction_mode"; directionMode.setAttribute("aria-label", `第 ${line.order} 句演绎模式`);
    for (const [value, label] of [["inherit", "继承音色"], ["override", "逐句覆盖"], ["neutral", "中性"]]) { const option = document.createElement("option"); option.value = value; option.textContent = label; option.selected = line.direction_mode === value; directionMode.append(option); }
    const directionText = fieldInput(line, "direction_text"); directionText.placeholder = "例如：压低声音，克制而悲伤";
    const directionCell = tableCell("逐句演绎", directionMode, directionText);
    const actions = tableCell("操作"); actions.classList.add("line-controls");
    for (const [action, label] of [["up", "上移"], ["down", "下移"], ["single", "单句"], ["delete", "删除"]]) { const button = document.createElement("button"); button.type = "button"; button.className = action === "delete" ? "danger" : "secondary"; button.dataset.action = action; button.textContent = label; button.setAttribute("aria-label", `第 ${line.order} 句${label}`); actions.append(button); }
    row.append(indexCell, roleCell, voiceCell, languageCell, startCell, endCell, textCell, directionCell, actions);
    tableFragment.append(row);
  }
  body.append(tableFragment);

  const visibleLines = project.lines.slice(0, TIMELINE_TRACK_RENDER_LIMIT);
  const selected = project.lines.find((line) => line.line_id === state.selectedLineId);
  if (selected && !visibleLines.includes(selected) && visibleLines.length) visibleLines[visibleLines.length - 1] = selected;
  const trackFragment = document.createDocumentFragment();
  for (const line of visibleLines) {
    const lane = document.createElement("div"); lane.className = "timeline-lane"; lane.dataset.lineId = line.line_id;
    const block = document.createElement("div"); block.className = `timeline-block${state.selectedLineId === line.line_id ? " selected" : ""}`; block.tabIndex = 0; block.dataset.lineId = line.line_id; block.setAttribute("aria-describedby", "timelineHelp");
    const startHandle = document.createElement("button"); startHandle.type = "button"; startHandle.className = "timeline-handle start"; startHandle.dataset.resize = "start"; startHandle.setAttribute("aria-label", `调整第 ${line.order} 句开始时间`);
    const label = document.createElement("span"); label.className = "timeline-block-label";
    const endHandle = document.createElement("button"); endHandle.type = "button"; endHandle.className = "timeline-handle end"; endHandle.dataset.resize = "end"; endHandle.setAttribute("aria-label", `调整第 ${line.order} 句结束时间`);
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
  if (!state.dialogueProject) throw new Error("当前没有可保存的工程。");
  const expected = state.projectBaseRevision;
  state.dialogueProject = await api(`/api/projects/${encodeURIComponent(state.dialogueProject.project_id)}`, { method: "PUT", body: JSON.stringify({ project: state.dialogueProject, expected_revision: expected }) });
  state.projectBaseRevision = state.dialogueProject.revision;
  markProjectSaved(`工程已保存 · revision ${state.dialogueProject.revision}`);
  renderTimeline();
  setActionMessage("batchStatus", `工程已保存 · revision ${state.dialogueProject.revision}`, "success");
}

async function exportDialogueProject() {
  if (!state.dialogueProject) throw new Error("当前没有可导出的工程。");
  const result = await api("/api/projects/export", { method: "POST", body: JSON.stringify({ project: state.dialogueProject }) });
  await window.t8Desktop?.openPath(result.path);
  setActionMessage("batchStatus", `工程包已导出：${result.path}`, "success");
}

async function importDialogueProject() {
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
$("voiceSelect").addEventListener("change", applySelectedVoice);
$('libraryVoiceSelect').addEventListener('change', selectLibraryVoice);
$('voiceSearch').addEventListener('input', renderLibraryVoices);
$('favoriteOnly').addEventListener('change', renderLibraryVoices);
$("historySearch").addEventListener("input", () => { state.historyPage = 1; renderHistory(); });
$("historyKind").addEventListener("change", () => { state.historyPage = 1; renderHistory(); });
$("historyPrevious").addEventListener("click", () => { state.historyPage -= 1; renderHistory(); });
$("historyNext").addEventListener("click", () => { state.historyPage += 1; renderHistory(); });
$("applyVoiceButton").addEventListener("click", applySelectedVoice);
$("saveVoiceButton").addEventListener("click", () => saveCurrentVoice().catch((error) => { $("voiceStatus").textContent = error.message; }));
$('updateVoiceButton').addEventListener('click', () => updateSelectedVoice().catch((error) => { $("voiceStatus").textContent = error.message; }));
$('exportVoiceButton').addEventListener('click', () => exportSelectedVoice().catch((error) => { $("voiceStatus").textContent = error.message; }));
$('importVoiceButton').addEventListener('click', () => importVoiceBundle().catch((error) => { $("voiceStatus").textContent = error.message; }));
$("deleteVoiceButton").addEventListener("click", () => deleteSelectedVoice().catch((error) => { $("voiceStatus").textContent = error.message; }));
$("chooseModelButton").addEventListener("click", () => chooseModelDirectory().catch((error) => { $("downloadStatus").textContent = error.message; }));
$("chooseOutputButton").addEventListener("click", () => chooseOutputDirectory().catch((error) => { $("outputStatus").textContent = error.message; }));
$("downloadModelButton").addEventListener("click", () => downloadModel().catch((error) => { $("downloadStatus").textContent = error.message; }));
$("activateModelButton").addEventListener("click", () => activateCurrentModel().catch((error) => { $("downloadStatus").textContent = error.message; }));
$("verifyModelButton").addEventListener("click", () => verifyCurrentModel().catch((error) => { $("downloadStatus").textContent = error.message; }));
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
$("roleMappingPanel").addEventListener("change", (event) => {
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
$('timingPolicy').addEventListener('change', () => { if (state.dialogueProject) { state.dialogueProject.timing.policy = $("timingPolicy").value; state.dialogueProject.revision += 1; markProjectDirty(); renderTimeline(); } });
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
      const line = state.dialogueProject.lines[index];
      const defaults = await currentBatchDefaults();
      await runBatchPayload({ defaults, items: [{ ...line, subtitle: { index: line.order, start_ms: line.start_ms, end_ms: line.end_ms, text: line.text } }], timeline: true, project_id: state.dialogueProject.project_id });
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
