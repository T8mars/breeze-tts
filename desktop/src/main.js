const { app, autoUpdater, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

let mainWindow = null;
let backend = null;
let backendPort = null;
let backendReady = false;
let backendStartupError = null;
let backendErrorDialogShown = false;
let logStream = null;
let whisperInstallerProcess = null;
let whisperInstallerPromise = null;
let focusRequested = false;
let settingsCache = {};
let consoleErrorHandlersInstalled = false;
let logStreamFailure = null;
let logStreamFailureReported = false;
let shutdownPromise = null;
let shutdownComplete = false;
let quitRequestedAfterShutdown = false;
let updaterStatus = {
  configured: false,
  state: "disabled",
  message: "自动更新未配置；请使用发布包附带的 release-manifest.json 手动校验。"
};

const SETTINGS_FILE_NAME = "settings.json";

function settingsPath() {
  return path.join(app.getPath("userData"), SETTINGS_FILE_NAME);
}

function normalizeDirectory(value) {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed || !path.isAbsolute(trimmed) || trimmed.includes("\0")) return null;
  return path.resolve(trimmed);
}

function isFilesystemRoot(target) {
  return path.parse(target).root.toLowerCase() === target.toLowerCase();
}

function sanitizeSettings(value) {
  const result = {};
  const modelDirectory = normalizeDirectory(value?.modelDirectory);
  const outputDirectory = normalizeDirectory(value?.outputDirectory);
  if (modelDirectory) result.modelDirectory = modelDirectory;
  if (outputDirectory && !isFilesystemRoot(outputDirectory)) result.outputDirectory = outputDirectory;
  return result;
}

function loadSettings() {
  try {
    if (!fs.existsSync(settingsPath())) return {};
    return sanitizeSettings(JSON.parse(fs.readFileSync(settingsPath(), "utf8")));
  } catch (error) {
    appendLog(`无法读取桌面设置，将使用默认值：${error.message}`);
    return {};
  }
}

function saveSettings(patch) {
  const next = sanitizeSettings({ ...settingsCache, ...patch });
  const target = settingsPath();
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const temporary = path.join(path.dirname(target), `.${SETTINGS_FILE_NAME}.${process.pid}.${Date.now()}.tmp`);
  try {
    fs.writeFileSync(temporary, `${JSON.stringify(next, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    fs.renameSync(temporary, target);
  } catch (error) {
    try { fs.rmSync(temporary, { force: true }); } catch (_) { /* Best effort cleanup. */ }
    throw error;
  }
  settingsCache = next;
  return { ...settingsCache };
}

function startLog() {
  const logDirectory = path.join(app.getPath("userData"), "logs");
  fs.mkdirSync(logDirectory, { recursive: true });
  logStream = fs.createWriteStream(path.join(logDirectory, "desktop.log"), { flags: "a", encoding: "utf8" });
  logStream.on("error", (error) => reportLogStreamFailure(error));
  installConsoleErrorHandlers();
  appendLog(`Desktop ${app.getVersion()} 正在启动（packaged=${app.isPackaged}）`);
}

function isBrokenPipe(error) {
  return error?.code === "EPIPE" || error?.errno === "EPIPE";
}

function reportLogStreamFailure(error) {
  logStreamFailure = error instanceof Error ? error : new Error(String(error));
  if (isBrokenPipe(logStreamFailure) || logStreamFailureReported) return;
  logStreamFailureReported = true;
  const message = `无法写入 desktop.log：${logStreamFailure.message}`;
  if (app.isPackaged) {
    if (!app.isQuitting) dialog.showErrorBox("Breeze 日志写入失败", message);
  } else {
    safelyWriteConsole("stderr", `${message}\n`, false);
  }
}

function writeLogText(text) {
  if (!logStream || logStream.destroyed || logStream.writableEnded) return false;
  try {
    return logStream.write(text);
  } catch (error) {
    reportLogStreamFailure(error);
    return false;
  }
}

function appendLog(message) {
  const line = `[${new Date().toISOString()}] ${String(message).replace(/\r?\n$/, "")}\n`;
  writeLogText(line);
}

function reportConsoleFailure(streamName, error) {
  if (isBrokenPipe(error)) return;
  appendLog(`桌面 ${streamName} 写入错误：${error?.stack || error}`);
}

function safelyWriteConsole(streamName, text, recordError = true) {
  if (app.isPackaged) return false;
  const stream = streamName === "stdout" ? process.stdout : process.stderr;
  if (!stream || stream.destroyed || stream.writableEnded) return false;
  try {
    stream.write(text, (error) => {
      if (error && recordError) reportConsoleFailure(streamName, error);
    });
    return true;
  } catch (error) {
    if (recordError) reportConsoleFailure(streamName, error);
    return false;
  }
}

function installConsoleErrorHandlers() {
  if (consoleErrorHandlersInstalled || app.isPackaged) return;
  consoleErrorHandlersInstalled = true;
  process.stdout?.on?.("error", (error) => reportConsoleFailure("stdout", error));
  process.stderr?.on?.("error", (error) => reportConsoleFailure("stderr", error));
}

function appendBackendOutput(streamName, chunk) {
  const text = String(chunk);
  // Packaged Windows GUI processes commonly have closed stdout/stderr handles.
  // Persist first and never touch those handles outside development mode.
  writeLogText(`[${new Date().toISOString()}] [backend:${streamName}] ${text}`);
  const consoleName = streamName === "stderr" || streamName.endsWith(":stderr") ? "stderr" : "stdout";
  safelyWriteConsole(consoleName, text);
}

function attachProcessOutput(child, label) {
  for (const streamName of ["stdout", "stderr"]) {
    const stream = child?.[streamName];
    if (!stream) continue;
    const outputLabel = label ? `${label}:${streamName}` : streamName;
    stream.on("data", (chunk) => appendBackendOutput(outputLabel, chunk));
    stream.on("error", (error) => {
      if (!isBrokenPipe(error)) {
        appendLog(`${label || "backend"} ${streamName} 管道错误：${error?.stack || error}`);
      }
    });
  }
}

function processHasExited(child) {
  return Number.isInteger(child?.exitCode) || Boolean(child?.signalCode);
}

function forceKillProcessTree(child, label) {
  if (!child || processHasExited(child)) return;
  appendLog(`${label} 未在宽限期内退出，正在强制结束进程树（pid=${child.pid || "unknown"}）。`);
  if (process.platform === "win32" && Number.isInteger(child.pid) && child.pid > 0) {
    try {
      const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
        windowsHide: true,
        stdio: "ignore"
      });
      killer.on("error", (error) => appendLog(`无法强制结束 ${label}：${error.message}`));
      return;
    } catch (error) {
      appendLog(`无法启动 ${label} 的进程树清理：${error.message}`);
      return;
    }
  }
  try {
    child.kill("SIGKILL");
  } catch (error) {
    appendLog(`无法强制结束 ${label}：${error.message}`);
  }
}

function terminateManagedProcess(child, label, graceMilliseconds = 1500) {
  if (!child || processHasExited(child)) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    let forceTimer = null;
    let hardTimer = null;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (forceTimer) clearTimeout(forceTimer);
      if (hardTimer) clearTimeout(hardTimer);
      child.removeListener?.("close", finish);
      child.removeListener?.("error", finish);
      resolve();
    };
    child.once?.("close", finish);
    child.once?.("error", finish);
    try {
      child.kill();
    } catch (error) {
      appendLog(`无法停止 ${label}：${error.message}`);
      finish();
      return;
    }
    if (settled) return;
    forceTimer = setTimeout(() => forceKillProcessTree(child, label), graceMilliseconds);
    hardTimer = setTimeout(() => {
      appendLog(`${label} 退出等待超时；桌面进程将继续关闭。`);
      finish();
    }, graceMilliseconds + 1500);
  });
}

function closeLogStream() {
  const stream = logStream;
  logStream = null;
  if (!stream || stream.destroyed || stream.writableEnded) return Promise.resolve();
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(finish, 500);
    try {
      stream.end(finish);
    } catch (_) {
      finish();
    }
  });
}

function installWhisperComponent() {
  if (whisperInstallerPromise) return whisperInstallerPromise;
  const root = projectRoot();
  const requirements = path.join(root, "requirements-whisper.txt");
  if (!fs.existsSync(requirements)) {
    return Promise.reject(new Error("安装清单 requirements-whisper.txt 不存在，请重新下载完整整合包。"));
  }
  appendLog("开始联网安装可选 Whisper 组件。");
  whisperInstallerPromise = new Promise((resolve, reject) => {
    const child = spawn(
      pythonExecutable(),
      ["-m", "pip", "install", "--disable-pip-version-check", "-r", requirements],
      {
        cwd: root,
        env: { ...process.env, PYTHONUTF8: "1", PYTHONUNBUFFERED: "1" },
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"]
      }
    );
    whisperInstallerProcess = child;
    attachProcessOutput(child, "whisper-install");
    child.once("error", (error) => {
      appendLog(`Whisper 组件安装进程错误：${error.stack || error}`);
      reject(new Error(`无法启动 Whisper 安装程序：${error.message}`));
    });
    child.once("exit", (code, signal) => {
      appendLog(`Whisper 组件安装结束：code=${code ?? "unknown"}, signal=${signal ?? "none"}`);
      if (code === 0) resolve({ installed: true, restart_required: true });
      else reject(new Error(`Whisper 组件安装失败（退出代码：${code ?? "unknown"}），请查看 desktop.log。`));
    });
  }).finally(() => {
    whisperInstallerProcess = null;
    whisperInstallerPromise = null;
  });
  return whisperInstallerPromise;
}

function updateFeedUrl() {
  if (!app.isPackaged) return null;
  const configured = String(process.env.T8_BREEZE_UPDATE_URL || "").trim();
  if (!configured) return null;
  try {
    const parsed = new URL(configured);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null;
    return parsed.toString();
  } catch (_) {
    return null;
  }
}

function publishUpdaterStatus(patch) {
  updaterStatus = { ...updaterStatus, ...patch };
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("update-status", updaterStatus);
  return { ...updaterStatus };
}

function initializeAutoUpdater() {
  if (!app.isPackaged) {
    appendLog("自动更新未启用：开发模式不会设置更新源或发起更新请求。");
    return publishUpdaterStatus({
      configured: false,
      state: "development",
      message: "开发模式不启用自动更新，未联系任何更新服务器。"
    });
  }
  const configured = String(process.env.T8_BREEZE_UPDATE_URL || "").trim();
  const feedUrl = updateFeedUrl();
  if (!feedUrl) {
    const invalid = Boolean(configured);
    appendLog(invalid
      ? "自动更新未启用：T8_BREEZE_UPDATE_URL 不是无凭据的有效 HTTPS 地址，未进行网络请求。"
      : "自动更新未启用：未配置 T8_BREEZE_UPDATE_URL，未进行网络请求。");
    return publishUpdaterStatus({
      configured: false,
      state: invalid ? "invalid" : "disabled",
      message: invalid
        ? "自动更新地址无效，未联系任何服务器；请检查 HTTPS 配置或手动校验 release-manifest.json。"
        : "自动更新未配置；请使用发布包附带的 release-manifest.json 手动校验。"
    });
  }
  try {
    autoUpdater.setFeedURL({ url: feedUrl });
  } catch (error) {
    appendLog(`自动更新配置失败：${error.stack || error}`);
    return publishUpdaterStatus({ configured: false, state: "error", message: `自动更新配置失败：${error.message}` });
  }
  publishUpdaterStatus({ configured: true, state: "idle", message: "自动更新已配置，可检查新版本。" });
  autoUpdater.on("checking-for-update", () => publishUpdaterStatus({ state: "checking", message: "正在检查更新…" }));
  autoUpdater.on("update-available", () => publishUpdaterStatus({ state: "downloading", message: "发现新版本，正在安全下载…" }));
  autoUpdater.on("update-not-available", () => publishUpdaterStatus({ state: "current", message: "当前已是最新版本。" }));
  autoUpdater.on("download-progress", (progress) => publishUpdaterStatus({
    state: "downloading",
    percent: Number(progress?.percent || 0),
    message: `正在下载更新… ${Number(progress?.percent || 0).toFixed(1)}%`
  }));
  autoUpdater.on("update-downloaded", (_event, _notes, releaseName) => publishUpdaterStatus({
    state: "downloaded",
    releaseName: releaseName || "",
    message: `更新${releaseName ? ` ${releaseName}` : ""}已下载，可安装并重启。`
  }));
  autoUpdater.on("error", (error) => {
    appendLog(`自动更新错误：${error.stack || error}`);
    publishUpdaterStatus({ state: "error", message: `检查更新失败：${error.message}` });
  });
  return { ...updaterStatus };
}

function checkForUpdates() {
  if (!updaterStatus.configured) return Promise.resolve({ ...updaterStatus });
  if (["checking", "downloading"].includes(updaterStatus.state)) return Promise.resolve({ ...updaterStatus });
  publishUpdaterStatus({ state: "checking", message: "正在检查更新…" });
  try {
    return Promise.resolve(autoUpdater.checkForUpdates())
      .then(() => ({ ...updaterStatus }))
      .catch((error) => publishUpdaterStatus({ state: "error", message: `检查更新失败：${error.message}` }));
  } catch (error) {
    return Promise.resolve(publishUpdaterStatus({ state: "error", message: `检查更新失败：${error.message}` }));
  }
}

function installDownloadedUpdate() {
  if (!updaterStatus.configured || updaterStatus.state !== "downloaded") {
    throw new Error("当前没有已下载的更新。");
  }
  appendLog("用户确认安装已下载的更新并重启。");
  setImmediate(() => {
    prepareForShutdown().then(() => autoUpdater.quitAndInstall());
  });
  return true;
}

function projectRoot() {
  return app.isPackaged
    ? path.join(process.resourcesPath, "backend")
    : path.resolve(__dirname, "..", "..");
}

function pythonExecutable() {
  const configured = process.env.T8_BREEZE_PYTHON;
  const candidates = [
    configured,
    app.isPackaged && path.join(process.resourcesPath, "python", "python.exe"),
    path.join(projectRoot(), ".venv", "Scripts", "python.exe"),
    path.join(projectRoot(), ".runtime", "python", "python.exe")
  ].filter(Boolean);
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) {
    throw new Error("找不到 Breeze Studio 的隔离 Python。请先运行 packaging\\build_runtime.ps1。");
  }
  return found;
}

function outputRoot() {
  const configured = String(process.env.T8_BREEZE_OUTPUT_DIR || "").trim();
  if (configured && path.isAbsolute(configured)) return path.resolve(configured);
  if (settingsCache.outputDirectory) return settingsCache.outputDirectory;
  return path.join(app.getPath("documents"), "T8star-Aix Breeze TTS", "outputs");
}

function modelRoot() {
  const configured = String(process.env.T8_BREEZE_MODEL_DIR || "").trim();
  if (configured && path.isAbsolute(configured)) return path.resolve(configured);
  if (settingsCache.modelDirectory) return settingsCache.modelDirectory;
  return app.isPackaged
    ? path.join(app.getPath("userData"), "models", "Breeze-TTS-2")
    : path.join(projectRoot(), "models", "Breeze-TTS-2");
}

function realExistingPath(target) {
  try {
    return fs.realpathSync.native(path.resolve(target));
  } catch (_) {
    return null;
  }
}

function isInside(root, target) {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
}

function allowedOpenTarget(target) {
  if (typeof target !== "string" || !path.isAbsolute(target)) return null;
  const resolved = realExistingPath(target);
  if (!resolved) return null;
  const diagnostics = realExistingPath(path.join(app.getPath("userData"), "diagnostics"));
  const userData = realExistingPath(app.getPath("userData"));
  const logs = realExistingPath(path.join(app.getPath("userData"), "logs"));
  const exportsRoot = realExistingPath(path.join(app.getPath("userData"), "exports"));
  const outputs = realExistingPath(outputRoot());
  const stat = fs.statSync(resolved);
  if (diagnostics && isInside(diagnostics, resolved)) {
    return stat.isFile() && path.extname(resolved).toLowerCase() === ".json" ? resolved : null;
  }
  if (outputs && isInside(outputs, resolved)) {
    if (stat.isDirectory()) return resolved === outputs ? resolved : null;
    return stat.isFile() && [".wav", ".json"].includes(path.extname(resolved).toLowerCase())
      ? resolved
      : null;
  }
  if (userData && resolved === userData && stat.isDirectory()) return resolved;
  if (logs && resolved === logs && stat.isDirectory()) return resolved;
  if (exportsRoot && isInside(exportsRoot, resolved)) {
    if (stat.isDirectory()) return resolved === exportsRoot ? resolved : null;
    return stat.isFile() && [".zip", ".json", ".wav"].includes(path.extname(resolved).toLowerCase())
      ? resolved
      : null;
  }
  return null;
}

function findPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

async function waitForBackend(url, child, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (backend !== child || backendStartupError) {
      throw backendStartupError || new Error("本地服务在启动完成前停止。请查看 desktop.log。");
    }
    try {
      const response = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(1000) });
      if (response.ok) return;
    } catch (_) {
      // Backend may still be importing lightweight dependencies.
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error("本地服务启动超时。请查看日志目录中的 desktop.log。");
}

async function startBackend() {
  backendReady = false;
  backendStartupError = null;
  backendErrorDialogShown = false;
  backendPort = await findPort();
  const root = projectRoot();
  const env = {
    ...process.env,
    PYTHONUTF8: "1",
    PYTHONUNBUFFERED: "1",
    PYTHONPATH: root,
    T8_BREEZE_UI_DIR: path.join(root, "desktop", "src"),
    T8_BREEZE_DATA_DIR: app.getPath("userData"),
    T8_BREEZE_OUTPUT_DIR: outputRoot(),
    T8_BREEZE_MODEL_DIR: modelRoot()
  };
  appendLog(`启动本地服务：${pythonExecutable()}（port=${backendPort}）`);
  const child = spawn(
    pythonExecutable(),
    ["-m", "t8_runtime.server", "--host", "127.0.0.1", "--port", String(backendPort)],
    { cwd: root, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] }
  );
  backend = child;
  attachProcessOutput(child, "backend");
  child.on("error", (error) => {
    appendLog(`本地服务进程错误：${error.stack || error}`);
    if (!app.isQuitting) {
      backendStartupError = new Error(`无法启动本地服务：${error.message}\n请查看 desktop.log。`);
    }
    if (backend === child) backend = null;
    if (backendReady && !app.isQuitting && !backendErrorDialogShown) {
      backendErrorDialogShown = true;
      dialog.showErrorBox("Breeze 本地服务启动失败", backendStartupError.message);
    }
  });
  child.on("exit", (code, signal) => {
    appendLog(`本地服务退出：code=${code ?? "unknown"}, signal=${signal ?? "none"}`);
    if (backend === child) backend = null;
    if (!backendReady && !app.isQuitting) {
      backendStartupError ||= new Error(`本地服务启动时退出（代码：${code ?? "unknown"}）。请查看 desktop.log。`);
    } else if (!app.isQuitting && mainWindow && !mainWindow.isDestroyed() && !backendErrorDialogShown) {
      backendErrorDialogShown = true;
      dialog.showErrorBox("Breeze 本地服务已停止", `退出代码：${code ?? "unknown"}\n请查看 desktop.log。`);
    }
  });
  const baseUrl = `http://127.0.0.1:${backendPort}`;
  await waitForBackend(baseUrl, child);
  backendReady = true;
  appendLog(`本地服务已就绪：${baseUrl}`);
  return baseUrl;
}

function stopBackend() {
  const child = backend;
  backend = null;
  backendReady = false;
  return terminateManagedProcess(child, "Breeze 本地服务");
}

function prepareForShutdown() {
  app.isQuitting = true;
  if (shutdownPromise) return shutdownPromise;
  const installer = whisperInstallerProcess;
  whisperInstallerProcess = null;
  shutdownPromise = Promise.allSettled([
    stopBackend(),
    terminateManagedProcess(installer, "Whisper 安装程序")
  ]).then(() => closeLogStream()).finally(() => {
    shutdownComplete = true;
  });
  return shutdownPromise;
}

function restoreMainWindow() {
  if (!mainWindow || mainWindow.isDestroyed()) {
    focusRequested = true;
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  if (!mainWindow.isVisible()) mainWindow.show();
  mainWindow.moveTop();
  mainWindow.focus();
  focusRequested = false;
}

async function createWindow() {
  const baseUrl = await startBackend();
  const baseOrigin = new URL(baseUrl).origin;
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 940,
    minWidth: 640,
    minHeight: 600,
    show: false,
    backgroundColor: "#f7fafd",
    title: "T8star-Aix Voice Studio",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: !app.isPackaged
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\/(github\.com|huggingface\.co|breeze\.blue)\//i.test(url)) {
      shell.openExternal(url);
    }
    return { action: "deny" };
  });
  mainWindow.webContents.on("will-navigate", (event, url) => {
    let sameOrigin = false;
    try {
      sameOrigin = new URL(url).origin === baseOrigin;
    } catch (_) {
      sameOrigin = false;
    }
    if (!sameOrigin) event.preventDefault();
  });
  mainWindow.once("ready-to-show", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.show();
      if (focusRequested) restoreMainWindow();
    }
  });
  await mainWindow.loadURL(baseUrl);
  // `ready-to-show` can fire before loadURL's promise resolves. Keep this
  // fallback so a fast local backend can never leave the window hidden.
  if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) {
    mainWindow.show();
  }
  if (focusRequested) restoreMainWindow();
}

ipcMain.handle("choose-model-directory", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择 Breeze TTS 2 模型目录",
    properties: ["openDirectory", "createDirectory"]
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("choose-output-directory", async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: "选择语音输出目录",
    defaultPath: outputRoot(),
    properties: ["openDirectory", "createDirectory"]
  });
  if (result.canceled) return null;
  const selected = normalizeDirectory(result.filePaths[0]);
  if (!selected || isFilesystemRoot(selected)) throw new Error("输出目录不能是磁盘根目录。");
  return selected;
});

ipcMain.handle("choose-bundle-file", async (_event, kind) => {
  const filters = kind === "voice"
    ? [{ name: "T8 Voice Bundle", extensions: ["zip"] }]
    : [{ name: "T8 Project Bundle", extensions: ["zip"] }];
  const result = await dialog.showOpenDialog(mainWindow, {
    title: kind === "voice" ? "导入 T8 音色包" : "导入 T8 工程包",
    properties: ["openFile"],
    filters
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("save-directory-setting", (_event, key, value) => {
  if (!["modelDirectory", "outputDirectory"].includes(key)) throw new Error("不支持的设置项。");
  const normalized = normalizeDirectory(value);
  if (!normalized || (key === "outputDirectory" && isFilesystemRoot(normalized))) {
    throw new Error("目录无效；输出目录不能是磁盘根目录。");
  }
  return saveSettings({ [key]: normalized });
});

// Fixed, argument-free installer. The renderer cannot choose an executable,
// requirements file, index URL, or pip flags.
ipcMain.handle("install-whisper", () => installWhisperComponent());
ipcMain.handle("update-status", () => ({ ...updaterStatus }));
ipcMain.handle("check-for-updates", () => checkForUpdates());
ipcMain.handle("install-update", () => installDownloadedUpdate());

ipcMain.handle("open-path", async (_event, target) => {
  const allowed = allowedOpenTarget(target);
  if (!allowed) return "拒绝打开白名单以外的路径";
  return shell.openPath(allowed);
});

ipcMain.handle("open-external", async (_event, url) => {
  if (!/^https:\/\/(github\.com|huggingface\.co|breeze\.blue)\//i.test(String(url))) {
    throw new Error("外链不在允许列表中。");
  }
  await shell.openExternal(url);
  return true;
});

ipcMain.handle("app-info", () => ({
  version: app.getVersion(),
  packaged: app.isPackaged,
  backendPort,
  userData: app.getPath("userData"),
  logsDirectory: path.join(app.getPath("userData"), "logs"),
  modelDirectory: modelRoot(),
  outputDirectory: outputRoot(),
  logError: logStreamFailure?.message || null
}));

app.on("before-quit", (event) => {
  if (shutdownComplete) return;
  event.preventDefault();
  if (quitRequestedAfterShutdown) return;
  quitRequestedAfterShutdown = true;
  prepareForShutdown().finally(() => setImmediate(() => app.quit()));
});

const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => restoreMainWindow());
  app.whenReady().then(() => {
    startLog();
    settingsCache = loadSettings();
    initializeAutoUpdater();
    return createWindow();
  }).catch((error) => {
    appendLog(`桌面启动失败：${error.stack || error}`);
    if (app.isQuitting) return;
    dialog.showErrorBox("Breeze Studio 启动失败", error.stack || String(error));
    app.quit();
  });
  app.on("window-all-closed", () => app.quit());
}
