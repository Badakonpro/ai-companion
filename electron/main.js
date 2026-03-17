// electron/main.js — Electron main process
// Spawns the Python backend, waits for /api/health, then opens the app.

const { app, BrowserWindow } = require("electron");
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
  const venvPython = path.join(bd, "venv", "bin", "python");
  // Use venv python if it exists, otherwise system python3
  try {
    require("fs").accessSync(venvPython);
    return venvPython;
  } catch {
    return "python3";
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
        if (res.statusCode === 200) return resolve();
        setTimeout(() => check(n - 1), 1000);
      });
      req.on("error", () => setTimeout(() => check(n - 1), 1000));
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
    console.error(err.message);
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
