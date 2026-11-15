---
name: ccm-dashboard
description: Use when changing the Crew Context Monitor dashboard (dashboard/, React + Vite) or its Electron wrapper (desktop/) - adding or editing a screen, card, chart or sentence, debugging data that doesn't refresh, regenerating API types after a contract change, or wiring the dashboard to the mock or real API.
---

# CCM dashboard (`dashboard/`) and desktop app (`desktop/`)

React 19 + Vite + React Router + Recharts + Tailwind. It is built once to `dashboard/dist` and
served as static files by whichever Python server owns `/api` (the mock or the gateway), so the
Pi needs no Node at runtime. It holds **no detection logic and no unit math**: every level,
sentence and number comes from the API.

## Screens (`src/App.tsx`, views in `src/views/`)

| Route | View | Shows |
|---|---|---|
| `/` | `Home` | Status bar, crew strip, four risk cards, fused timeline (CO2 over pulse-rate variability), Earth queue |
| `/domain/:name` | `Domain` | One card's raw series, baseline band, detector state, model |
| `/crew/:id` | `Crew` | One crew member |
| `/history` | `History` | 7-day trends with CUSUM |
| `/events` | `Events` | Every spike, drift, correlation, check-in and simulator event |
| `/settings` | `Settings` | Enroll crew, reset a baseline, Earth-queue auto-approve tiers |

Shared pieces are in `src/components/` (`StatusBar`, `CrewStrip`, `RiskCards`, `Timeline`,
`MetricChart`, `CusumChart`, `EarthQueuePanel`, `CheckinPrompt`, `Layout`...).

## Data flow

- `src/api/client.ts`: one function per route, all **relative** `/api/...` paths, so the page
  and the API must share an origin. In dev, Vite proxies `/api` to `127.0.0.1:8000`.
- `src/api/types.gen.ts`: TypeScript types **generated** from `contracts/api/*.schema.json`.
  Never edit by hand; run `npm run gen:types` (or `make types`). `npm run check:types-fresh`
  fails if they are stale.
- `src/api/validate.ts`: in dev builds, responses are checked against the schemas (ajv) and
  violations are reported to the console.
- `src/hooks/usePolling.ts`: polls every **5 s** (`POLL_MS`), aborts the in-flight request when
  the next starts, keeps the last good data on failure, and marks data **stale after 15 s**.
  `StatusContext` shares one `/api/status` poll across the app.

## Copy rules (tests enforce them)

One plain sentence per reason, with a number and a baseline comparison; no raw z-scores;
"pulse-rate variability", never "HRV"; radiation is always a "proxy"; "consistent with", never
"caused by"; the footer "Decision-support prototype, not a medical device" on every screen; a
Hardware or Simulator badge on every crew chip (from `crew[].src`). Most sentences come from the
API; any text written in the dashboard must follow the same rules (`test/copy-rules.test.tsx`).

## Commands (in `dashboard/`)

| Goal | Command |
|---|---|
| Hot reload against the mock | terminal 1: `ccm-sim serve-mock`; terminal 2: `npm run dev` (port 5173) |
| Production build (type check + bundle budget 400 kB gzip) | `npm run build` |
| Lint / format / unit tests | `npm run lint`, `npm run format`, `npm test` (Vitest, `test/`) |
| End-to-end against the mock (Playwright) | `npx playwright install chromium` once, then `make e2e` |

## Desktop app (`desktop/`)

Electron. `server.js` serves `dashboard/dist` and proxies `/api` to a backend (mirrors
`mockapi/static.py`). With no backend given, `backend.js` starts `ccm-sim serve-mock --scenario
demo` on a free port. Point it elsewhere with `CCM_API_URL`, `--api <url>`, or `config.json` in
the user-data folder. `make desktop` runs it; `make desktop-dist` builds installers.
