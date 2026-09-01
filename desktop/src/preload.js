const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("t8Desktop", {
  chooseModelDirectory: () => ipcRenderer.invoke("choose-model-directory"),
  chooseOutputDirectory: () => ipcRenderer.invoke("choose-output-directory"),
  chooseBundleFile: (kind) => ipcRenderer.invoke("choose-bundle-file", kind),
  saveDirectorySetting: (key, value) => ipcRenderer.invoke("save-directory-setting", key, value),
  installWhisper: () => ipcRenderer.invoke("install-whisper"),
  updateStatus: () => ipcRenderer.invoke("update-status"),
  checkForUpdates: () => ipcRenderer.invoke("check-for-updates"),
  installUpdate: () => ipcRenderer.invoke("install-update"),
  onUpdateStatus: (callback) => {
    if (typeof callback !== "function") return;
    ipcRenderer.on("update-status", (_event, status) => callback(status));
  },
  openPath: (target) => ipcRenderer.invoke("open-path", target),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  appInfo: () => ipcRenderer.invoke("app-info")
});
