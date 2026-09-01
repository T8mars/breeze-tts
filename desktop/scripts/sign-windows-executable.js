"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

async function main() {
  const target = process.argv[2] ? path.resolve(process.argv[2]) : null;
  const certificateFile = process.env.T8_WINDOWS_CERTIFICATE_PATH
    ? path.resolve(process.env.T8_WINDOWS_CERTIFICATE_PATH)
    : null;
  const certificatePassword = process.env.T8_WINDOWS_CERTIFICATE_PASSWORD || null;
  const timestampServer =
    process.env.T8_WINDOWS_TIMESTAMP_SERVER || "http://timestamp.digicert.com";

  if (!target || path.extname(target).toLowerCase() !== ".exe" || !fs.statSync(target).isFile()) {
    throw new Error("A real .exe path is required.");
  }
  if (!certificateFile || !certificatePassword) {
    throw new Error("T8 Windows certificate path and password are both required.");
  }
  if (!fs.existsSync(certificateFile)) throw new Error("The signing certificate does not exist.");

  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "t8-sign-"));
  const temporaryExe = path.join(temporaryRoot, path.basename(target));
  try {
    fs.copyFileSync(target, temporaryExe, fs.constants.COPYFILE_EXCL);
    const { sign } = await import("@electron/windows-sign");
    await sign({
      appDirectory: temporaryRoot,
      certificateFile,
      certificatePassword,
      timestampServer,
      description: "T8star-Aix Voice Studio",
      continueOnError: false
    });
    const temporarySignature = require("node:child_process").spawnSync(
      "powershell.exe",
      [
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "(Get-AuthenticodeSignature -LiteralPath $args[0]).Status.ToString()",
        temporaryExe
      ],
      { encoding: "utf8", windowsHide: true }
    );
    if (temporarySignature.status !== 0 || temporarySignature.stdout.trim() !== "Valid") {
      throw new Error("The signed executable did not pass Authenticode verification.");
    }
    fs.copyFileSync(temporaryExe, target);
  } finally {
    fs.rmSync(temporaryRoot, { recursive: true, force: true });
  }
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
