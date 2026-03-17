// electron/main.js — Electron main process
// Spawns the Python backend, waits for /api/health, then opens the app.

const { app, BrowserWindow, dialog } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

let mainWindow = null;
let backendProcess = null;
const BACKEND_PORT = 8000;

function backendDir() {
  // In packaged app, resources are under process.resourcesPath
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "backend");
  }
  return path.join(__dirname, "..", "backend");
}

function findPython() {
  const bd = backendDir();
  // venv path differs between Unix and Windows
  const venvPython = process.platform === "win32"
    ? path.join(bd, "venv", "Scripts", "python.exe")
    : path.join(bd, "venv", "bin", "python");
  try {
    require("fs").accessSync(venvPython);
    return venvPython;
  } catch {
    return process.platform === "win32" ? "python" : "python3";
  }
}

function startBackend() {
  const bd = backendDir();
  const py = findPython();
  backendProcess = spawn(py, ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", String(BACKEND_PORT)], {
    cwd: bd,
    env: { ...process.env, BACKEND_HOST: "127.0.0.1", BACKEND_RELOAD: "false" },
    stdio: ["ignore", "pipe", "pipe"],
  });

  backendProcess.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  backendProcess.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  backendProcess.on("close", (code) => {
    console.log(`Backend exited with code ${code}`);
    backendProcess = null;
  });
}

function waitForBackend(retries = 30) {
  return new Promise((resolve, reject) => {
    const check = (n) => {
      if (n <= 0) return reject(new Error("Backend did not start in time"));
      const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/api/health`, (res) => {
        // Must consume body to release the underlying socket
        res.resume();
        if (res.statusCode === 200) return resolve();
        setTimeout(() => check(n - 1), 1000);
      });
      req.on("error", () => setTimeout(() => check(n - 1), 1000));
      // Destroy socket if server accepts connection but never sends headers
      req.setTimeout(2000, () => {
        req.destroy();
        setTimeout(() => check(n - 1), 500);
      });
    };
    check(retries);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadURL(`http://127.0.0.1:${BACKEND_PORT}`);
  mainWindow.on("closed", () => { mainWindow = null; });
}

app.whenReady().then(async () => {
  startBackend();
  try {
    await waitForBackend();
  } catch (err) {
    dialog.showErrorBox(
      "AI Companion — 启动失败",
      `后端服务未能在规定时间内启动。\n\n${err.message}\n\n请确保 Python 环境正确，然后重试。`
    );
    app.quit();
    return;
  }
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (backendProcess) {
    backendProcess.kill();
    backendProcess = null;
  }
});
