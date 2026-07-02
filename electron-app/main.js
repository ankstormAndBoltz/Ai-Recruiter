/**
 * main.js — Electron main process for AI Recruiter Desktop App.
 *
 * What this does:
 * 1. Shows a branded splash screen while the Python backend starts
 * 2. Spawns the Python backend (bundled venv when packaged, system Python
 *    in dev) running `-m uvicorn app.main:app` from the backend folder
 * 3. Polls /health until the server is ready
 * 4. Opens the frontend in a full BrowserWindow
 * 5. Kills the backend process cleanly when the app closes
 *
 * Works both in dev (`npm start`, files live next to this folder) and
 * packaged (`electron-builder` output, backend/frontend copied under
 * process.resourcesPath via `extraResources`).
 */

const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn }  = require('child_process');
const fs         = require('fs');
const path       = require('path');
const http       = require('http');

// ─── Paths ────────────────────────────────────────────────────────────────────
// In a packaged app, extraResources land in `process.resourcesPath`.
// In dev, everything lives one level up from this file.
const IS_PACKAGED = app.isPackaged;
const RESOURCES_DIR = IS_PACKAGED ? process.resourcesPath : path.join(__dirname, '..');

const BACKEND_DIR   = path.join(RESOURCES_DIR, 'backend');
const FRONTEND_FILE = path.join(RESOURCES_DIR, 'frontend', 'index.html');
const BACKEND_PORT  = 8000;
const BACKEND_URL   = `http://localhost:${BACKEND_PORT}`;

// Prefer the bundled venv's Python (fully offline, no system install needed);
// fall back to whatever `python`/`python3` is on PATH for dev machines that
// haven't created a venv yet.
function resolvePythonCommand() {
  const venvPython = process.platform === 'win32'
    ? path.join(BACKEND_DIR, 'venv', 'Scripts', 'python.exe')
    : path.join(BACKEND_DIR, 'venv', 'bin', 'python3');

  if (fs.existsSync(venvPython)) {
    return venvPython;
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

// ─── State ────────────────────────────────────────────────────────────────────
let mainWindow    = null;
let splashWindow  = null;
let backendProcess = null;

// ─── Splash screen HTML ───────────────────────────────────────────────────────
const SPLASH_HTML = `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background: #07071a;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    font-family: 'Segoe UI', system-ui, sans-serif;
    color: #eeeeff;
    overflow: hidden;
  }
  .logo {
    font-size: 3rem;
    margin-bottom: 1rem;
    animation: pulse 1.5s ease-in-out infinite;
  }
  @keyframes pulse {
    0%, 100% { transform: scale(1); }
    50%       { transform: scale(1.08); }
  }
  h1 {
    font-size: 1.5rem;
    font-weight: 700;
    background: linear-gradient(135deg, #6c63ff, #00d4ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 0.3rem;
  }
  .subtitle {
    font-size: 0.85rem;
    color: #5a5a80;
    margin-bottom: 2.5rem;
  }
  .spinner-track {
    width: 200px; height: 4px;
    background: rgba(255,255,255,0.08);
    border-radius: 99px;
    overflow: hidden;
  }
  .spinner-fill {
    height: 100%;
    width: 40%;
    background: linear-gradient(90deg, #6c63ff, #00d4ff);
    border-radius: 99px;
    animation: slide 1.2s ease-in-out infinite;
  }
  @keyframes slide {
    0%   { transform: translateX(-100%); }
    100% { transform: translateX(350%); }
  }
  .status {
    margin-top: 1.2rem;
    font-size: 0.78rem;
    color: #5a5a80;
    letter-spacing: 0.04em;
  }
  .version {
    position: fixed;
    bottom: 16px;
    right: 20px;
    font-size: 0.65rem;
    color: #2a2a4a;
  }
</style>
</head>
<body>
  <div class="logo">🎯</div>
  <h1>AI Recruiter</h1>
  <div class="subtitle">Candidate Ranking System v2.0</div>
  <div class="spinner-track"><div class="spinner-fill"></div></div>
  <div class="status" id="status">Starting backend server…</div>
  <div class="version">v2.0.0</div>
</body>
</html>`;

// ─── Backend management ───────────────────────────────────────────────────────

function startBackend() {
  return new Promise((resolve, reject) => {
    console.log('[backend] Starting from:', BACKEND_DIR);

    const pythonCmd = resolvePythonCommand();
    console.log('[backend] Using Python:', pythonCmd);

    backendProcess = spawn(
      pythonCmd,
      ['-m', 'uvicorn', 'app.main:app', '--port', String(BACKEND_PORT), '--log-level', 'warning'],
      {
        cwd: BACKEND_DIR,
        windowsHide: true, // Don't show a console window on Windows
      }
    );

    backendProcess.stderr.on('data', d => console.log('[backend]', d.toString().trim()));
    backendProcess.stdout.on('data', d => console.log('[backend]', d.toString().trim()));

    backendProcess.on('error', err => {
      reject(new Error(
        `Could not start the Python backend (${pythonCmd}).\n\n` +
        `Make sure Python is installed and on your PATH, and that ` +
        `backend dependencies are installed (pip install -r backend/requirements.txt).\n\nError: ${err.message}`
      ));
    });

    backendProcess.on('exit', (code) => {
      if (code !== 0 && code !== null) {
        console.error(`[backend] Exited with code ${code}`);
      }
    });

    // Poll /health until ready
    let attempts = 0;
    const maxAttempts = 60; // 30 seconds at 500ms intervals

    const poll = setInterval(() => {
      attempts++;
      if (attempts > maxAttempts) {
        clearInterval(poll);
        reject(new Error('Backend took too long to start (>30s). Check that port 8000 is free.'));
        return;
      }

      const req = http.get(`${BACKEND_URL}/health`, { timeout: 800 }, (res) => {
        if (res.statusCode === 200) {
          clearInterval(poll);
          console.log('[backend] Ready ✓');
          resolve();
        }
      });
      req.on('error', () => {}); // Expected while server is still starting
      req.end();
    }, 500);
  });
}

function killBackend() {
  if (backendProcess && !backendProcess.killed) {
    console.log('[backend] Shutting down…');
    if (process.platform === 'win32') {
      spawn('taskkill', ['/PID', String(backendProcess.pid), '/T', '/F'], { windowsHide: true });
    } else {
      backendProcess.kill('SIGTERM');
    }
  }
}

// ─── Windows ──────────────────────────────────────────────────────────────────

const APP_ICON_PATH = path.join(__dirname, 'build', 'icon.ico');
const APP_ICON = fs.existsSync(APP_ICON_PATH) ? APP_ICON_PATH : undefined;

function createSplash() {
  splashWindow = new BrowserWindow({
    width: 440,
    height: 280,
    frame: false,
    transparent: false,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    backgroundColor: '#07071a',
    icon: APP_ICON,
    webPreferences: { nodeIntegration: false },
  });
  splashWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(SPLASH_HTML));
  splashWindow.center();
}

function createMain() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    backgroundColor: '#07071a',
    title: 'AI Recruiter — Candidate Ranking System',
    icon: APP_ICON,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.loadFile(FRONTEND_FILE);

  mainWindow.once('ready-to-show', () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    mainWindow.show();
    mainWindow.focus();
  });

  // Open external links in the default browser, not Electron
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ─── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  createSplash();

  try {
    await startBackend();
    createMain();
  } catch (err) {
    console.error('[app] Fatal:', err.message);

    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.close();
    }

    await dialog.showMessageBox({
      type: 'error',
      title: 'AI Recruiter — Startup Failed',
      message: 'Could not start the backend server.',
      detail: err.message + '\n\nMake sure:\n• Python is installed\n• You ran: pip install -r backend/requirements.txt\n• Port 8000 is not in use by another app',
      buttons: ['Quit'],
    });

    app.quit();
  }
});

// Quit when all windows are closed (Windows/Linux behavior)
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    killBackend();
    app.quit();
  }
});

app.on('before-quit', () => {
  killBackend();
});

// macOS: re-open on dock click
app.on('activate', () => {
  if (mainWindow === null) createMain();
});
