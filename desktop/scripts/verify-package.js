const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const asar = require("@electron/asar");
const { version } = require("../package.json");

const out = path.resolve(__dirname, "..", "out");
if (!fs.existsSync(out)) throw new Error("desktop/out does not exist; run npm run package first.");
const currentPackageSuffix = `-v${version}-win32-x64`;
const appDirs = fs.readdirSync(out).filter((name) => name.endsWith(currentPackageSuffix));
if (appDirs.length !== 1) {
  throw new Error(
    `Expected one ${version} win32-x64 package ending in ${currentPackageSuffix}, found ${appDirs.length}.`
  );
}
const root = path.join(out, appDirs[0]);
const required = [
  "T8star-Aix-Voice-Studio.exe",
  "resources/app.asar",
  "resources/python/python.exe",
  "resources/backend/t8_runtime/server.py",
  "resources/backend/desktop/src/index.html",
  "resources/backend/desktop/src/favicon.svg",
  "resources/backend/MODEL_LICENSE",
  "resources/backend/NOTICE",
  "resources/backend/requirements-desktop.lock.txt",
  "resources/backend/WHISPER_NOTICE.md",
  "resources/backend/models/faster-whisper-large-v3/config.json",
  "resources/backend/models/faster-whisper-large-v3/model.bin",
  "resources/backend/models/faster-whisper-large-v3/preprocessor_config.json",
  "resources/backend/models/faster-whisper-large-v3/tokenizer.json",
  "resources/backend/models/faster-whisper-large-v3/vocabulary.json",
  "resources/backend/T8_DISTRIBUTION.md"
];
for (const relative of required) {
  if (!fs.existsSync(path.join(root, relative))) throw new Error(`Missing packaged file: ${relative}`);
}
if (fs.existsSync(path.join(root, "resources/backend/models/Breeze-TTS-2"))) {
  throw new Error("Official model weights leaked into the public portable package.");
}
const css = fs.readFileSync(path.join(root, "resources/backend/desktop/src/styles.css"), "utf8");
if (!css.includes("[hidden] { display: none !important; }")) {
  throw new Error("Packaged UI is missing the verified hidden-state CSS rule.");
}
const stagedMainJs = fs.readFileSync(path.join(root, "resources/backend/desktop/src/main.js"), "utf8");
const mainJs = asar.extractFile(path.join(root, "resources/app.asar"), "src/main.js").toString("utf8");
if (mainJs !== stagedMainJs) {
  throw new Error("Packaged Electron main process does not match the staged, inspected source.");
}
for (const requiredSecurityControl of [
  "contextIsolation: true",
  "nodeIntegration: false",
  "sandbox: true",
  "allowedOpenTarget",
  "requestSingleInstanceLock",
  "safelyWriteConsole",
  "isBrokenPipe",
  "attachProcessOutput",
  "prepareForShutdown",
  "event.preventDefault()",
  'webContents.on("will-navigate"',
  "T8_BREEZE_OUTPUT_DIR"
]) {
  if (!mainJs.includes(requiredSecurityControl)) {
    throw new Error(`Packaged Electron security control missing: ${requiredSecurityControl}`);
  }
}
if (/appendBackendOutput[\s\S]{0,800}process\.(stdout|stderr)\.write/.test(mainJs)) {
  throw new Error("Packaged backend output path writes directly to a possibly closed GUI console pipe.");
}
const indexHtml = fs.readFileSync(path.join(root, "resources/backend/desktop/src/index.html"), "utf8");
if (!indexHtml.includes('rel="icon" href="favicon.svg"')) {
  throw new Error("Packaged UI is missing its local favicon and may generate a misleading 404.");
}
const readyHandlerIndex = mainJs.indexOf('mainWindow.once("ready-to-show"');
const loadUrlIndex = mainJs.indexOf("await mainWindow.loadURL(baseUrl)");
if (readyHandlerIndex < 0 || loadUrlIndex < 0 || readyHandlerIndex > loadUrlIndex) {
  throw new Error("Packaged Electron app registers ready-to-show too late and may remain hidden.");
}
if (!mainJs.includes("!mainWindow.isVisible()")) {
  throw new Error("Packaged Electron app is missing the post-load visibility fallback.");
}
const serverPy = fs.readFileSync(path.join(root, "resources/backend/t8_runtime/server.py"), "utf8");
for (const requiredServerControl of ["TrustedHostMiddleware", "Origin not allowed", 'host="127.0.0.1"']) {
  if (!serverPy.includes(requiredServerControl)) {
    throw new Error(`Packaged loopback security control missing: ${requiredServerControl}`);
  }
}
const runtimeManagerPy = fs.readFileSync(path.join(root, "resources/backend/t8_runtime/runtime_manager.py"), "utf8");
if (!runtimeManagerPy.includes('TORCHINDUCTOR_USE_STATIC_CUDA_LAUNCHER"] = "0"')) {
  throw new Error("Packaged runtime is missing the Windows Triton pointer-width compatibility fix.");
}
const python = path.join(root, "resources/python/python.exe");
const backend = path.join(root, "resources/backend");
const smoke = spawnSync(
  python,
  ["-c", "import importlib.metadata,torch,transformers,triton,qwen_tts,faster_whisper,t8_runtime.server; from torch.utils._triton import has_triton_package; from t8_runtime.transcription import bundled_whisper_large_available; assert bundled_whisper_large_available(); assert has_triton_package(); assert importlib.metadata.version('triton-windows') == '3.5.1.post24'; print(torch.__version__,transformers.__version__,triton.__version__)"],
  {
    cwd: backend,
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: backend, PYTHONUTF8: "1" },
    windowsHide: true,
    timeout: 180000
  }
);
if (smoke.status !== 0) {
  throw new Error(`Packaged Python smoke test failed: ${smoke.stderr || smoke.stdout}`);
}
console.log(`Verified package: ${root}`);
console.log(`Runtime smoke: ${smoke.stdout.trim()}`);
