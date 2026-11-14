// Find the API the dashboard talks to: a URL the user gave, or a mock API started from ccm-sim.
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import net from "node:net";
import { join } from "node:path";

/** `--name value` or `--name=value` from argv, else the environment variable, else undefined. */
export function option(argv, name, envName) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === `--${name}`) return argv[i + 1];
    if (argv[i].startsWith(`--${name}=`)) return argv[i].slice(name.length + 3);
  }
  return process.env[envName] || undefined;
}

/** The `apiUrl` saved in `<userData>/config.json`, if there is one. */
export function savedApiUrl(userData) {
  try {
    const config = JSON.parse(readFileSync(join(userData, "config.json"), "utf8"));
    return typeof config.apiUrl === "string" && config.apiUrl ? config.apiUrl : undefined;
  } catch {
    return undefined;
  }
}

/** Where to look for ccm-sim: CCM_SIM, the repository's .venv (when run from a checkout), PATH. */
export function simCandidates(repoRoot) {
  const found = [];
  if (process.env.CCM_SIM) found.push(process.env.CCM_SIM);
  if (repoRoot) {
    for (const rel of [".venv/Scripts/ccm-sim.exe", ".venv/bin/ccm-sim"]) {
      const path = join(repoRoot, rel);
      if (existsSync(path)) found.push(path);
    }
  }
  found.push("ccm-sim");
  return found;
}

function freePort() {
  return new Promise((ok, fail) => {
    const probe = net.createServer();
    probe.once("error", fail);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => ok(port));
    });
  });
}

async function waitForStatus(apiUrl, child, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`ccm-sim exited with code ${child.exitCode}`);
    try {
      const response = await fetch(`${apiUrl}/api/status`);
      if (response.ok) return;
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`the mock API did not answer within ${timeoutMs / 1000} s`);
}

function spawnSim(command, args) {
  return new Promise((ok, fail) => {
    const child = spawn(command, args, {
      stdio: ["ignore", "ignore", "pipe"],
      windowsHide: true,
    });
    child.once("spawn", () => ok(child));
    child.once("error", fail);
  });
}

/**
 * Start `ccm-sim serve-mock` on a free port and resolve to `{ apiUrl, stop, command }` once
 * /api/status answers. Tries each candidate in turn; rejects with the last error and stderr.
 */
export async function startMock({ repoRoot, scenario = "demo", timeoutMs = 30000 }) {
  let lastError = new Error("no ccm-sim found");
  for (const command of simCandidates(repoRoot)) {
    const port = await freePort();
    const apiUrl = `http://127.0.0.1:${port}`;
    let child;
    try {
      child = await spawnSim(command, [
        "serve-mock",
        "--scenario",
        scenario,
        "--host",
        "127.0.0.1",
        "--port",
        String(port),
      ]);
    } catch (err) {
      lastError = err;
      continue;
    }
    let stderr = "";
    child.stderr.on("data", (chunk) => (stderr = (stderr + chunk).slice(-2000)));
    try {
      await waitForStatus(apiUrl, child, timeoutMs);
    } catch (err) {
      child.kill();
      lastError = new Error(`${err.message}${stderr ? `\n\n${stderr.trim()}` : ""}`);
      continue;
    }
    return {
      apiUrl,
      command,
      stop: () => child.exitCode === null && child.kill(),
    };
  }
  throw lastError;
}
