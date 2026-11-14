import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { option } from "../backend.js";
import { startServer } from "../server.js";

let api;
let site;
let dist;

before(async () => {
  dist = mkdtempSync(join(tmpdir(), "ccm-dist-"));
  mkdirSync(join(dist, "assets"));
  writeFileSync(join(dist, "index.html"), "<html>index</html>");
  writeFileSync(join(dist, "assets", "app.js"), "console.log(1)");
  writeFileSync(join(tmpdir(), "outside.txt"), "secret");

  api = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ method: req.method, url: req.url, body }));
    });
  });
  await new Promise((ok) => api.listen(0, "127.0.0.1", ok));
  site = await startServer({
    distDir: dist,
    apiUrl: `http://127.0.0.1:${api.address().port}`,
  });
});

after(() => {
  site.close();
  api.close();
});

test("serves real files with their type", async () => {
  const response = await fetch(`${site.url}/assets/app.js`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /javascript/);
  assert.equal(await response.text(), "console.log(1)");
});

test("falls back to index.html for client-side routes", async () => {
  for (const path of ["/", "/history", "/domain/air", "/assets/missing.js"]) {
    const response = await fetch(`${site.url}${path}`);
    assert.equal(await response.text(), "<html>index</html>", path);
    assert.equal(response.headers.get("cache-control"), "no-cache");
  }
});

test("does not serve files outside the build", async () => {
  const response = await fetch(`${site.url}/..%2Foutside.txt`);
  assert.equal(await response.text(), "<html>index</html>");
});

test("proxies /api with method, query and body", async () => {
  const response = await fetch(`${site.url}/api/checkin?x=1`, {
    method: "POST",
    body: "{}",
  });
  assert.deepEqual(await response.json(), {
    method: "POST",
    url: "/api/checkin?x=1",
    body: "{}",
  });
});

test("answers 502 with a detail when the backend is down", async () => {
  const lonely = await startServer({
    distDir: dist,
    apiUrl: "http://127.0.0.1:1",
  });
  try {
    const response = await fetch(`${lonely.url}/api/status`);
    assert.equal(response.status, 502);
    assert.match((await response.json()).detail, /backend unreachable/);
  } finally {
    lonely.close();
  }
});

test("refuses to start without a build", async () => {
  const empty = mkdtempSync(join(tmpdir(), "ccm-empty-"));
  await assert.rejects(startServer({ distDir: empty, apiUrl: "http://x" }), /no dashboard build/);
});

test("reads options from argv before the environment", () => {
  process.env.CCM_TEST_OPT = "env";
  assert.equal(option(["--api", "http://a"], "api", "CCM_TEST_OPT"), "http://a");
  assert.equal(option(["--api=http://b"], "api", "CCM_TEST_OPT"), "http://b");
  assert.equal(option([], "api", "CCM_TEST_OPT"), "env");
  delete process.env.CCM_TEST_OPT;
  assert.equal(option([], "api", "CCM_TEST_OPT"), undefined);
});
