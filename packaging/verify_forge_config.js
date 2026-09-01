"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const projectRoot = path.resolve(__dirname, "..");
const configPath = path.join(projectRoot, "desktop", "forge.config.js");
const desktopPackage = require(path.join(projectRoot, "desktop", "package.json"));
const electronChecksums = require(path.join(__dirname, "electron-checksums.json"));
const testRoot = fs.mkdtempSync(path.join(os.tmpdir(), "t8-forge-config-"));
const runtimeRoot = path.join(testRoot, "runtime");
const backendRoot = path.join(testRoot, "backend");

function loadConfig(environment) {
  const previous = {};
  for (const [name, value] of Object.entries(environment)) {
    previous[name] = process.env[name];
    if (value === null) {
      delete process.env[name];
    } else {
      process.env[name] = value;
    }
  }
  try {
    delete require.cache[require.resolve(configPath)];
    return require(configPath);
  } finally {
    for (const [name, value] of Object.entries(previous)) {
      if (value === undefined) {
        delete process.env[name];
      } else {
        process.env[name] = value;
      }
    }
  }
}

try {
  const checksum = electronChecksums[desktopPackage.devDependencies.electron]?.["win32-x64"];
  assert.ok(checksum, "The locked Electron version must have a win32-x64 checksum.");
  assert.match(checksum.sha256, /^[0-9a-f]{64}$/);
  assert.equal(
    checksum.file,
    `electron-v${desktopPackage.devDependencies.electron}-win32-x64.zip`
  );
  fs.mkdirSync(runtimeRoot, { recursive: true });
  fs.mkdirSync(path.join(backendRoot, "t8_runtime"), { recursive: true });
  fs.writeFileSync(path.join(runtimeRoot, "python.exe"), "config-test");
  fs.writeFileSync(path.join(backendRoot, "t8_runtime", "server.py"), "# config-test\n");

  const baseEnvironment = {
    T8_BREEZE_RUNTIME: runtimeRoot,
    T8_BREEZE_BACKEND_STAGE: backendRoot,
    T8_WINDOWS_CERTIFICATE_PATH: null,
    T8_WINDOWS_CERTIFICATE_PASSWORD: null
  };
  const unsignedConfig = loadConfig(baseEnvironment);
  const makerNames = unsignedConfig.makers.map((maker) => maker.name).sort();
  assert.deepEqual(makerNames, ["@electron-forge/maker-zip"]);
  assert.equal(unsignedConfig.packagerConfig.asar, true);
  assert.equal(unsignedConfig.packagerConfig.windowsSign, undefined);

  const certificatePath = path.join(testRoot, "test-only.pfx");
  fs.writeFileSync(certificatePath, "not-a-real-certificate");
  assert.throws(
    () =>
      loadConfig({
        ...baseEnvironment,
        T8_WINDOWS_CERTIFICATE_PATH: certificatePath,
        T8_WINDOWS_CERTIFICATE_PASSWORD: null
      }),
    /requires both T8_WINDOWS_CERTIFICATE_PATH and T8_WINDOWS_CERTIFICATE_PASSWORD/
  );
  const signedConfig = loadConfig({
    ...baseEnvironment,
    T8_WINDOWS_CERTIFICATE_PATH: certificatePath,
    T8_WINDOWS_CERTIFICATE_PASSWORD: "test-only-password"
  });
  assert.equal(signedConfig.packagerConfig.windowsSign.certificateFile, certificatePath);
  assert.equal(signedConfig.packagerConfig.windowsSign.hashes[0], "sha256");

  console.log("Forge distribution configuration verified (portable ZIP and optional signing).");
} finally {
  fs.rmSync(testRoot, { recursive: true, force: true });
}
