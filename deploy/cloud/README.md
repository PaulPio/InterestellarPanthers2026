# Public site on Vercel (free)

An always-on copy of the dashboard. The Raspberry Pi keeps the only database. Each browser
chooses its data source on the Settings page:

- **Live** (default): the Pi's real data. While the Pi is not answering, Live shows **no data**,
  never simulated data.
- **Demo**: the scripted simulator, for that browser only.

```
Browser ──▶ Vercel CDN (dashboard/dist)
        └─▶ Vercel function /api/* ──GET + key──▶ Tailscale Funnel ──▶ Pi: ccm-gateway public (:8001)
                     │                                                      (read-only, SQLite)
                     ├── Live, Pi not answering → last real answer for up to 20 s, then "no data"
                     └── Demo (ccm-source: demo) → simulator (wall clock, scenario rotation)
```

Code: `cloud/ccm_cloud/` (the API), `api/index.py` (Vercel entry point), `vercel.json`,
`requirements.txt`, `.vercelignore`. Local checks: `make cloud-check`.

## Deploy

From the repository root, with the Vercel CLI logged in:

```bash
vercel deploy            # first run creates and links the project; prints a preview URL
vercel deploy --prod     # the public URL
```

`vercel.json` sets the build command (`cd dashboard && npm ci && npm run build`), the output
folder (`dashboard/dist`) and the routing (`/api/*` to the function, everything else to the
dashboard). With no environment variables Live shows no data and Demo still works. Check it at
`https://<project>.vercel.app/api/_cloud/info`.

## Connect the Pi

1. Set up the Pi's public API and Tailscale Funnel: `deploy/pi/README.md`, section 9. You end up
   with a key and an address like `https://<pi-name>.<tailnet>.ts.net`.
2. Give both to Vercel and redeploy:

   ```bash
   vercel env add CCM_PI_URL production     # the https://...ts.net address
   vercel env add CCM_PI_KEY production     # the Pi's CCM_PUBLIC_KEY
   vercel deploy --prod
   ```

3. `/api/_cloud/info` should now say `"mode": "live"`. If it says `mock`, `pi_error` tells you why
   (`HTTP 401`: the keys differ; `ConnectError`/`ConnectTimeout`: the Pi or Funnel is down).

Optional variables: `CCM_ROTATION` (the demo's scenarios, default
`scrubber_failure:60,individual_illness:120,solar_particle_event:60`) and `CCM_SEED`.

## How it behaves

- **Live**: each `GET /api/*` is forwarded to the Pi, query string included, and its answer
  returned as is. Each dashboard viewer polls every 5 s, so the Pi sees one small request per
  viewer per 5 s.
- **Read-only**: while the Pi is live, approving an Earth-queue item, check-ins and settings
  answer 409. Make those on the Pi's own dashboard (`http://192.168.4.1:8000`). The Pi's public
  API refuses them too.
- **No data, never simulated**: if the Pi doesn't answer within 3 s, or answers with an error,
  the last real answer for that request (at most 20 s old) covers the gap. After that, Live
  gets contract-valid "no data" answers: every card says "No data from the Pi since HH:MM UTC",
  no crew, nodes shown offline, empty history and events. The Pi is tried again every 5 s.
  `/api/_cloud/info` reports `"mode": "no_data"` and the reason in `pi_error`.
- **Demo**: a GET carrying the header `ccm-source: demo` skips the Pi and is served by the
  simulator. The public dashboard's Settings page sends that header for the browser that chose
  Demo. Other browsers, and every write, are unchanged. Writes stay refused while the Pi is live.
