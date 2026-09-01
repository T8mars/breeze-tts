const fs = require("node:fs");
const path = require("node:path");
const desktopPackage = require("./package.json");

const projectRoot = path.resolve(__dirname, "..");
const runtimeRoot = process.env.T8_BREEZE_RUNTIME
  ? path.resolve(process.env.T8_BREEZE_RUNTIME)
  : path.join(projectRoot, ".runtime", "python");
const backendStage = process.env.T8_BREEZE_BACKEND_STAGE
  ? path.resolve(process.env.T8_BREEZE_BACKEND_STAGE)
  : path.join(projectRoot, ".package", "backend");
const electronZipDir = process.env.T8_ELECTRON_ZIP_DIR
  ? path.resolve(process.env.T8_ELECTRON_ZIP_DIR)
  : null;
const certificatePath = process.env.T8_WINDOWS_CERTIFICATE_PATH
  ? path.resolve(process.env.T8_WINDOWS_CERTIFICATE_PATH)
  : null;
const certificatePassword = process.env.T8_WINDOWS_CERTIFICATE_PASSWORD || null;
const timestampServer =
  process.env.T8_WINDOWS_TIMESTAMP_SERVER || "http://timestamp.digicert.com";

if (!fs.existsSync(path.join(runtimeRoot, "python.exe"))) {
  throw new Error(
    `Bundled Python runtime missing at ${runtimeRoot}. Run packaging\\build_runtime.ps1 first.`
  );
}
if (!fs.existsSync(path.join(backendStage, "t8_runtime", "server.py"))) {
  throw new Error(`Backend staging directory missing at ${backendStage}. Run packaging\\build_portable.ps1.`);
}
if ((certificatePath && !certificatePassword) || (!certificatePath && certificatePassword)) {
  throw new Error(
    "Code signing requires both T8_WINDOWS_CERTIFICATE_PATH and T8_WINDOWS_CERTIFICATE_PASSWORD."
  );
}
if (certificatePath && !fs.existsSync(certificatePath)) {
  throw new Error(`Code-signing certificate does not exist: ${certificatePath}`);
}

const windowsSign = certificatePath
  ? {
      certificateFile: certificatePath,
      certificatePassword,
      timestampServer,
      hashes: ["sha256"],
      description: desktopPackage.productName
    }
  : null;

if (windowsSign) {
  console.log(`[distribution] Windows code signing enabled (${path.basename(certificatePath)}).`);
} else {
  console.log(
    "[distribution] Windows code signing skipped: T8_WINDOWS_CERTIFICATE_PATH/PASSWORD are not set."
  );
}

module.exports = {
  packagerConfig: {
    name: `T8star-Aix-Voice-Studio-v${desktopPackage.version}`,
    executableName: "T8star-Aix-Voice-Studio",
    asar: true,
    extraResource: [runtimeRoot, backendStage],
    ...(electronZipDir ? { electronZipDir } : {}),
    ...(windowsSign ? { windowsSign } : {}),
    ignore: [/^\/out($|\/)/, /^\/node_modules\/.cache($|\/)/]
  },
  rebuildConfig: {},
  // The CUDA/PyTorch payload is larger than Squirrel's reliable embedded
  // Setup limit. Keep Forge responsible for the portable ZIP only; the release
  // pipeline creates and verifies a clearly-labelled 7-Zip self-extractor.
  makers: [{ name: "@electron-forge/maker-zip", platforms: ["win32"] }]
};
