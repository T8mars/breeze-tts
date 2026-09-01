const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const sourcePath = path.resolve(__dirname, "..", "src", "main.js");
const indexPath = path.resolve(__dirname, "..", "src", "index.html");
const faviconPath = path.resolve(__dirname, "..", "src", "favicon.svg");
const source = `${fs.readFileSync(sourcePath, "utf8")}
globalThis.__mainTest = {
  appendBackendOutput,
  attachProcessOutput,
  safelyWriteConsole,
  installConsoleErrorHandlers,
  initializeAutoUpdater,
  checkForUpdates,
  reportLogStreamFailure,
  restoreMainWindow,
  terminateManagedProcess,
  stopBackend,
  prepareForShutdown,
  setMainWindow(value) { mainWindow = value; },
  setBackend(value, ready = false) { backend = value; backendReady = ready; },
  setLogStream(value) { logStream = value; },
  resetLogFailure() { logStreamFailure = null; logStreamFailureReported = false; },
  setUpdaterStatus(value) { updaterStatus = value; },
  getUpdaterStatus() { return { ...updaterStatus }; }
};`;

const writes = { stdout: [], stderr: [], dialogs: [], feedUrls: [], updateChecks: 0, quits: 0 };
const appHandlers = new Map();
function mockConsoleStream(target) {
  const handlers = new Map();
  return {
    destroyed: false,
    writableEnded: false,
    on(event, callback) {
      const callbacks = handlers.get(event) || [];
      callbacks.push(callback);
      handlers.set(event, callbacks);
    },
    emit(event, value) { for (const callback of handlers.get(event) || []) callback(value); },
    write(text, callback) { target.push(String(text)); callback?.(); return true; }
  };
}
const streams = {
  stdout: mockConsoleStream(writes.stdout),
  stderr: mockConsoleStream(writes.stderr)
};
const mockProcess = Object.create(process);
Object.defineProperties(mockProcess, {
  env: { value: {}, writable: true },
  stdout: { value: streams.stdout },
  stderr: { value: streams.stderr },
  resourcesPath: { value: "C:\\Program Files\\T8star-Aix\\resources" },
  pid: { value: 4242 }
});

const app = {
  isPackaged: true,
  isQuitting: false,
  getPath() { return "C:\\T8-Test"; },
  getVersion() { return "test"; },
  requestSingleInstanceLock() { return false; },
  quit() { writes.quits += 1; },
  on(event, callback) { appHandlers.set(event, callback); },
  whenReady() { return Promise.resolve(); }
};
const autoUpdater = {
  on() {},
  setFeedURL(value) { writes.feedUrls.push(value); },
  checkForUpdates() { writes.updateChecks += 1; return Promise.resolve(); },
  quitAndInstall() {}
};
const electron = {
  app,
  autoUpdater,
  BrowserWindow: function BrowserWindow() {},
  dialog: { showErrorBox(title, message) { writes.dialogs.push({ title, message }); } },
  ipcMain: { handle() {} },
  shell: {}
};
const context = {
  require(id) { return id === "electron" ? electron : require(id); },
  __dirname: path.dirname(sourcePath),
  process: mockProcess,
  console,
  URL,
  AbortSignal,
  fetch,
  Buffer,
  setTimeout,
  clearTimeout,
  setImmediate
};
vm.runInNewContext(source, context, { filename: sourcePath });
const main = context.__mainTest;

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function memoryLog() {
  return {
    destroyed: false,
    writableEnded: false,
    entries: [],
    write(text) { this.entries.push(String(text)); return true; },
    end(callback) { this.writableEnded = true; callback?.(); }
  };
}

async function run() {
const packagedLog = memoryLog();
main.setLogStream(packagedLog);
app.isPackaged = true;
main.appendBackendOutput("stdout", Buffer.from("backend-ready\n"));
assert(packagedLog.entries.join("").includes("backend-ready"), "packaged backend output was not persisted");
assert(writes.stdout.length === 0 && writes.stderr.length === 0, "packaged mode touched a GUI console stream");

app.isPackaged = false;
main.installConsoleErrorHandlers();
const emittedPipeError = new Error("async closed pipe");
emittedPipeError.code = "EPIPE";
streams.stdout.emit("error", emittedPipeError);
const emittedDiagnostic = new Error("async bad descriptor");
emittedDiagnostic.code = "EBADF";
streams.stderr.emit("error", emittedDiagnostic);
assert(packagedLog.entries.join("").includes("async bad descriptor"), "non-EPIPE stream event was swallowed");
streams.stdout.write = () => { const error = new Error("closed pipe"); error.code = "EPIPE"; throw error; };
main.appendBackendOutput("stdout", Buffer.from("development-output\n"));
assert(packagedLog.entries.join("").includes("development-output"), "EPIPE prevented file logging");

streams.stdout.write = () => { const error = new Error("bad descriptor"); error.code = "EBADF"; throw error; };
main.safelyWriteConsole("stdout", "diagnostic\n");
assert(packagedLog.entries.join("").includes("bad descriptor"), "non-EPIPE console error was swallowed");

app.isPackaged = true;
main.resetLogFailure();
const logError = new Error("disk unavailable");
logError.code = "ENOSPC";
main.reportLogStreamFailure(logError);
main.reportLogStreamFailure(logError);
assert(writes.dialogs.length === 1, "packaged log failure was hidden or reported repeatedly");

app.isPackaged = false;
main.setUpdaterStatus({ configured: false, state: "disabled", message: "" });
main.initializeAutoUpdater();
assert(main.getUpdaterStatus().state === "development", "development updater status is misleading");
assert(writes.feedUrls.length === 0, "development mode configured an update feed");

app.isPackaged = true;
delete mockProcess.env.T8_BREEZE_UPDATE_URL;
main.initializeAutoUpdater();
assert(main.getUpdaterStatus().state === "disabled", "missing update URL was not reported as disabled");
mockProcess.env.T8_BREEZE_UPDATE_URL = "http://updates.example.invalid/feed";
main.initializeAutoUpdater();
assert(main.getUpdaterStatus().state === "invalid", "insecure update URL was not rejected explicitly");
assert(writes.feedUrls.length === 0, "invalid update URL configured a feed");

mockProcess.env.T8_BREEZE_UPDATE_URL = "https://updates.example.invalid/feed";
main.initializeAutoUpdater();
assert(main.getUpdaterStatus().configured, "valid HTTPS update URL was rejected");
assert(writes.feedUrls.length === 1, "valid update feed was not configured exactly once");

main.setUpdaterStatus({ configured: false, state: "disabled", message: "manual" });
main.checkForUpdates();
assert(writes.updateChecks === 0, "unconfigured updater contacted the update service");

assert(source.includes("requestSingleInstanceLock()") && source.includes('app.on("second-instance"'), "single-instance restore guard missing");
const windowCalls = [];
main.setMainWindow({
  isDestroyed() { return false; },
  isMinimized() { return true; },
  isVisible() { return false; },
  restore() { windowCalls.push("restore"); },
  show() { windowCalls.push("show"); },
  moveTop() { windowCalls.push("moveTop"); },
  focus() { windowCalls.push("focus"); }
});
main.restoreMainWindow();
assert(["restore", "show", "moveTop", "focus"].every((item) => windowCalls.includes(item)), "second instance did not fully restore and focus the window");
assert(source.includes('child.on("exit"') && source.includes("backendErrorDialogShown"), "backend exit de-duplication guard missing");
assert(fs.existsSync(faviconPath), "favicon asset missing");
assert(fs.readFileSync(indexPath, "utf8").includes('rel="icon" href="favicon.svg"'), "favicon is not linked from index.html");

const childStdout = mockConsoleStream([]);
const childStderr = mockConsoleStream([]);
main.attachProcessOutput({ stdout: childStdout, stderr: childStderr }, "test-child");
childStdout.emit("data", Buffer.from("child-output\n"));
const childPipeError = new Error("child pipe closed");
childPipeError.code = "EPIPE";
childStdout.emit("error", childPipeError);
const childDescriptorError = new Error("child pipe descriptor failed");
childDescriptorError.code = "EBADF";
childStderr.emit("error", childDescriptorError);
assert(packagedLog.entries.join("").includes("child-output"), "child output was not drained into the desktop log");
assert(packagedLog.entries.join("").includes("child pipe descriptor failed"), "non-EPIPE child pipe errors were hidden");

const { EventEmitter } = require("node:events");
function managedChild() {
  const child = new EventEmitter();
  child.pid = 9001;
  child.exitCode = null;
  child.signalCode = null;
  child.stdout = mockConsoleStream([]);
  child.stderr = mockConsoleStream([]);
  child.kill = () => {
    child.killCalls = (child.killCalls || 0) + 1;
    setImmediate(() => {
      child.exitCode = 0;
      child.emit("close", 0, null);
    });
    return true;
  };
  return child;
}

const standaloneChild = managedChild();
await main.terminateManagedProcess(standaloneChild, "test process", 25);
assert(standaloneChild.killCalls === 1, "managed process was not terminated exactly once");

const shutdownLog = memoryLog();
const shutdownChild = managedChild();
const quitBaseline = writes.quits;
main.setLogStream(shutdownLog);
main.setBackend(shutdownChild, true);
let quitPrevented = false;
appHandlers.get("before-quit")({ preventDefault() { quitPrevented = true; } });
await new Promise((resolve) => setImmediate(resolve));
await new Promise((resolve) => setImmediate(resolve));
assert(quitPrevented, "application quit was not held until the backend closed");
assert(shutdownChild.killCalls === 1, "shutdown raced without stopping the backend exactly once");
assert(shutdownLog.writableEnded, "desktop log closed before coordinated process shutdown completed");
assert(writes.quits === quitBaseline + 1, "coordinated shutdown did not resume Electron quit exactly once");

console.log("Desktop main-process regressions: passed");
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
