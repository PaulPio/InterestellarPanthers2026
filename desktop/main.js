// Electron main process: one window showing the dashboard from a local server that proxies /api.
//
// The backend is, in order: --api <url> or CCM_API_URL, then "apiUrl" in <userData>/config.json,
// else a mock API started from ccm-sim (--scenario <name> or CCM_SCENARIO, default demo).
// --dashboard-url <url> or CCM_DASHBOARD_URL loads that URL instead, e.g. Vite on :5173.
import { app, BrowserWindow, dialog, Menu, nativeTheme, shell } from "electron";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { option, savedApiUrl, startMock } from "./backend.js";
import { startServer } from "./server.js";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = app.isPackaged ? undefined : join(here, "..");
const distDir = app.isPackaged
  ? join(process.resourcesPath, "dashboard")
  : join(here, "..", "dashboard", "dist");

let mainWindow;
let backend = { label: "none" };
const cleanups = [];

async function resolveBackend() {
  const given = option(process.argv, "api", "CCM_API_URL") ?? savedApiUrl(app.getPath("userData"));
  if (given) return { apiUrl: new URL(given).origin, label: given };
  const scenario = option(process.argv, "scenario", "CCM_SCENARIO") ?? "demo";
  const mock = await startMock({ repoRoot, scenario });
  cleanups.push(mock.stop);
  return {
    apiUrl: mock.apiUrl,
    label: `mock API (${scenario}) from ${mock.command}`,
  };
}

function errorPage(title, detail) {
  const escape = (s) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
  const html = `<!doctype html><meta charset="utf-8"><title>${escape(title)}</title>
<style>body{font:14px system-ui,sans-serif;margin:2rem;color:#d6dde6;background:#0b0f14}
pre{white-space:pre-wrap;background:#11161d;padding:1rem}code{color:#9fd3ff}</style>
<h1>${escape(title)}</h1><pre>${escape(detail)}</pre>
<p>Point the app at a running API with <code>--api http://host:port</code>, the
<code>CCM_API_URL</code> environment variable, or <code>{"apiUrl": "..."}</code> in
<code>${escape(join(app.getPath("userData"), "config.json"))}</code>. Without one, the app starts
<code>ccm-sim serve-mock</code>: run <code>make setup</code>, or put <code>ccm-sim</code> on the PATH
or in <code>CCM_SIM</code>.</p>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

function buildMenu() {
  const template = [
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { role: "fileMenu" },
    {
      label: "View",
      submenu: [
        { role: "reload" },
        { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    { role: "windowMenu" },
    {
      role: "help",
      submenu: [
        {
          label: "Backend",
          click: () =>
            dialog.showMessageBox(mainWindow, {
              type: "info",
              title: "Backend",
              message: "The dashboard is reading from",
              detail: backend.label,
            }),
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: "Crew Context Monitor",
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#0b0f14" : "#f4f6f8",
    show: false,
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });
  mainWindow.once("ready-to-show", () => mainWindow.show());

  // Links that leave the app open in the system browser; the window stays on the dashboard.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  let url;
  try {
    url = option(process.argv, "dashboard-url", "CCM_DASHBOARD_URL");
    if (url) {
      backend = { label: `${url} (its own /api proxy)` };
    } else {
      backend = await resolveBackend();
      const server = await startServer({ distDir, apiUrl: backend.apiUrl });
      cleanups.push(server.close);
      url = server.url;
    }
  } catch (err) {
    await mainWindow.loadURL(
      errorPage("The dashboard could not start", String(err?.message ?? err)),
    );
    return;
  }

  const origin = new URL(url).origin;
  mainWindow.webContents.on("will-navigate", (event, target) => {
    if (new URL(target).origin !== origin) {
      event.preventDefault();
      if (/^https?:/.test(target)) shell.openExternal(target);
    }
  });
  await mainWindow.loadURL(url);
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow?.isMinimized()) mainWindow.restore();
    mainWindow?.focus();
  });
  app.whenReady().then(() => {
    buildMenu();
    createWindow();
    app.on("activate", () => BrowserWindow.getAllWindows().length === 0 && createWindow());
  });
  app.on("window-all-closed", () => process.platform !== "darwin" && app.quit());
  app.on("will-quit", () => cleanups.splice(0).forEach((stop) => stop()));
}
