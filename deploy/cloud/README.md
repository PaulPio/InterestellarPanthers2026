# Public site on Vercel (free)

An always-on copy of the dashboard. The Raspberry Pi keeps the only database; the site reads it
live while the Pi is reachable (`source_mode: "live"`) and shows the simulator demo when it isn't
(`source_mode: "mock"`), so the site never goes dark.

```
Browser ──▶ Vercel CDN (dashboard/dist)
        └─▶ Vercel function /api/* ──GET + key──▶ Tailscale Funnel ──▶ Pi: ccm-gateway public (:8001)
                     │                                                      (read-only, SQLite)
                     └── Pi off or unreachable → simulator demo (wall clock, scenario rotation)
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
dashboard). With no environment variables the site runs the simulator demo. Check it at
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
- **Fallback**: if the Pi doesn't answer within 3 s, or answers with an error, the request is
  served by the simulator and the Pi is skipped for 15 s before it is tried again.
