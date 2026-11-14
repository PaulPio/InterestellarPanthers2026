// The desktop app's local web server: the built dashboard as static files, and /api proxied to the
// backend. Mirrors sim/src/ccm_sim/mockapi/static.py, so client-side routes such as /history and
// /domain/air work on reload. Plain Node with no Electron import, so the tests can run it.
import { createReadStream, statSync } from "node:fs";
import http from "node:http";
import https from "node:https";
import { extname, join, resolve, sep } from "node:path";

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

function isFile(path) {
  try {
    return statSync(path).isFile();
  } catch {
    return false;
  }
}

function sendFile(res, path, headers = {}) {
  res.writeHead(200, {
    "Content-Type": TYPES[extname(path).toLowerCase()] ?? "application/octet-stream",
    ...headers,
  });
  createReadStream(path).pipe(res);
}

function proxy(req, res, apiUrl) {
  const target = new URL(req.url, apiUrl);
  const client = target.protocol === "https:" ? https : http;
  const upstream = client.request(
    target,
    { method: req.method, headers: { ...req.headers, host: target.host } },
    (answer) => {
      res.writeHead(answer.statusCode ?? 502, answer.headers);
      answer.pipe(res);
    },
  );
  upstream.on("error", (err) => {
    if (res.headersSent) return res.destroy();
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(
      JSON.stringify({
        detail: `backend unreachable at ${apiUrl}: ${err.message}`,
      }),
    );
  });
  req.pipe(upstream);
}

/**
 * Start the server on 127.0.0.1 and resolve to `{ url, close }`.
 * `distDir` holds index.html and assets/; `apiUrl` is the backend origin, e.g. http://127.0.0.1:8000.
 */
export function startServer({ distDir, apiUrl, port = 0 }) {
  const root = resolve(distDir);
  const index = join(root, "index.html");
  if (!isFile(index)) {
    return Promise.reject(new Error(`no dashboard build at ${root} (run make build)`));
  }
  const server = http.createServer((req, res) => {
    const path = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    if (path === "/api" || path.startsWith("/api/")) return proxy(req, res, apiUrl);
    if (req.method !== "GET" && req.method !== "HEAD") {
      res.writeHead(405).end();
      return;
    }
    const candidate = resolve(root, `.${path}`);
    if (path !== "/" && candidate.startsWith(root + sep) && isFile(candidate)) {
      return sendFile(res, candidate);
    }
    sendFile(res, index, { "Cache-Control": "no-cache" });
  });
  return new Promise((ok, fail) => {
    server.once("error", fail);
    server.listen(port, "127.0.0.1", () => {
      const { port: bound } = server.address();
      ok({ url: `http://127.0.0.1:${bound}`, close: () => server.close() });
    });
  });
}
