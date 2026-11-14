# Public site on Vercel (free)

An always-on copy of the dashboard. While the Pi is pushing snapshots it shows the Pi's live data
(`source_mode: "live"`); when the Pi has been quiet for 60 s it shows the simulator demo
(`source_mode: "mock"`), so the site never goes dark. Both services below have free tiers that
need no credit card at the time of writing; check before relying on that.

```
Pi: ccm-gateway uplink ──HTTPS POST /api/uplink──▶ Vercel function ──▶ Supabase table
                                                         │ (reads it back on each GET)
Browser ──▶ Vercel CDN (dashboard/dist) + /api/* ────────┘   no fresh snapshot → simulator
```

Code: `cloud/ccm_cloud/` (the API), `api/index.py` (Vercel entry point), `vercel.json`,
`requirements.txt`, `.vercelignore`. Local checks: `make cloud-check`.

## 1. Supabase (stores the latest snapshot)

1. Create a free project at supabase.com.
2. In **SQL Editor**, run:

   ```sql
   create table ccm_snapshot (
     id int primary key,
     body jsonb not null,
     received_at timestamptz not null default now()
   );
   alter table ccm_snapshot enable row level security;
   ```

   Row level security with no policies means only the service key can read or write.
3. In **Project Settings > API**, copy the project URL and the `service_role` key. The service key
   bypasses row level security: put it only in Vercel's environment variables, never in the repo
   or the dashboard.

Without Supabase the site still runs, but snapshots live in one function instance's memory, so
live mode is unreliable. Use it only for a quick test.

## 2. Vercel (hosts the site)

1. Create a free Hobby account at vercel.com and import the GitHub repository.
2. Leave the framework preset as **Other**. `vercel.json` already sets the build command, the
   output folder (`dashboard/dist`) and the `/api/*` routing.
3. Set the production branch to the branch you deploy (for now `feat/pi-gateway`).
4. Add environment variables:

   | Name | Value |
   |---|---|
   | `SUPABASE_URL` | the project URL from step 1 |
   | `SUPABASE_SERVICE_KEY` | the `service_role` key from step 1 |
   | `CCM_UPLINK_KEY` | a long random string; generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
   | `CCM_ROTATION` (optional) | the demo's scenario rotation, default `scrubber_failure:60,individual_illness:120,solar_particle_event:60` |
   | `CCM_LIVE_MAX_AGE_S` (optional) | seconds before the site falls back to the simulator, default `60` |

5. Deploy, then check `https://<project>.vercel.app/api/_cloud/info`: it should say
   `"mode": "mock"` until the Pi starts pushing.

Or from a terminal with the Vercel CLI: `vercel link`, `vercel env add ...`, `vercel --prod`.

## 3. Point the Pi at it

In `/etc/ccm/ccm.env` on the Pi:

```bash
CCM_UPLINK_URL=https://<project>.vercel.app/api/uplink
CCM_UPLINK_KEY=<the same key as in Vercel>
```

Then rerun `sudo deploy/pi/install.sh`: it enables `ccm-uplink` once `CCM_UPLINK_URL` is set. The
Pi needs internet for this. Its WiFi is busy being the hotspot, so use Ethernet or a USB WiFi
adapter (see `deploy/pi/README.md`). Within about 10 s, `/api/_cloud/info` should show
`"mode": "live"`.

## How it behaves

- **Live**: every `GET /api/*` is answered from the Pi's last snapshot. History is available for
  the series the Pi includes (cabin CO2, temperature, VOC, radiation proxy; each crew member's
  pulse-rate variability, heart rate and skin temperature); others come back empty.
- **Read-only**: in live mode, approving an Earth-queue item, check-ins and settings answer 409.
  Make those on the Pi's own dashboard (`http://192.168.4.1:8000`).
- **Fallback**: with no snapshot newer than `CCM_LIVE_MAX_AGE_S`, everything comes from the
  simulator, which follows the wall clock through the scenario rotation.
- **Security**: `POST /api/uplink` needs `Authorization: Bearer <CCM_UPLINK_KEY>` and a snapshot
  that passes the contract schemas; anything else is refused.
