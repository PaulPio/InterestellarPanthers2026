# Raspberry Pi deployment

Gateway, MQTT broker, time server and WiFi hotspot for the Crew Context Monitor, on a Raspberry
Pi 4 Model B running Raspberry Pi OS Bookworm (64-bit). The dashboard is built elsewhere and served
by the gateway at `http://192.168.4.1:8000/`.

The ESP32 cabin node expects the Pi at `192.168.4.1`, an MQTT broker on port 1883 without
authentication, and an NTP server on the same address (`firmware/ccm_cabin_node/config.h`).

## What is in this folder

| File | Purpose |
|---|---|
| `install.sh` | Installs the OS packages, the Python environment, the configs and the systemd units; safe to run twice |
| `mosquitto-ccm.conf` | Broker listener on 1883, anonymous (demo LAN only) |
| `hotspot.sh` | Creates the `wlan0` access point with NetworkManager |
| `hotspot.env.example` | Template for `/etc/ccm/hotspot.env` (SSID and password) |
| `chrony-ccm.conf` | Serves time to `192.168.4.0/24` |
| `ccm.env.example` | Template for `/etc/ccm/ccm.env` (every `CCM_*` setting) |
| `ccm-*.service` | Five systemd units: ingest, analytics, API, public read-only API, simulated crew |
| `README.md` | This runbook |

## 1. Before you start

- Raspberry Pi 4 Model B with a fresh Raspberry Pi OS Bookworm 64-bit image.
- The Pi on a network with internet access for the install (Ethernet is easiest; after step 6 the
  WiFi adapter is the hotspot).
- A machine with Node 20 and npm to build the dashboard. The Pi never gets Node.
- Read access to this repository on the Pi.

## 2. First boot

Flash the image with Raspberry Pi Imager, set the hostname, user, SSH and (for the first boot) the
WiFi network in the Imager settings, then boot and log in. Update the base image:

```bash
sudo apt-get update && sudo apt-get upgrade -y
```

## 3. Clone the repository

```bash
git clone https://github.com/PaulPio/InterestellarPanthers2026.git
cd InterestellarPanthers2026
```

If the repository is private, use a deploy key or another credential that is not committed to the
repository.

## 4. Build the dashboard elsewhere and copy it

On the development machine, from the repository root:

```bash
make setup
make build
scp -r dashboard/dist pi@<pi-address>:~/InterestellarPanthers2026/dashboard/
```

`install.sh` copies `dashboard/dist` from the checkout to `/opt/ccm/dashboard/dist`. Node is not
installed on the Pi.

## 5. Run the installer

```bash
sudo deploy/pi/install.sh
```

The installer:

- installs `mosquitto`, `mosquitto-clients`, `chrony`, `python3-venv` and `git`;
- creates the system user `ccm` and `/var/lib/ccm` for the database;
- copies the code to `/opt/ccm/src` (the `ccm` service user cannot read a private home folder),
  creates `/opt/ccm/venv` and installs `sim` and `gateway` from that copy;
- copies `dashboard/dist` to `/opt/ccm/dashboard/dist` when it exists;
- installs the broker and chrony configs, and the systemd units;
- creates `/etc/ccm/ccm.env` and `/etc/ccm/hotspot.env` from the examples when they do not exist;
- enables and (re)starts mosquitto, chrony and the CCM services. The public API is enabled only
  once `CCM_PUBLIC_KEY` is set in `/etc/ccm/ccm.env` (section 9).

Rerun it after every `git pull`: that is what copies the new code to `/opt/ccm/src`. The packages
are installed in editable mode from that copy because the simulator finds its scenarios, parameters
and the `contracts/` directory relative to its source tree.

Simulated crew are **off** by default, so the dashboard shows only real sensors: until a wearable
sends data, the cardiovascular card says there is no wearable data. Set `CCM_SIM_CREW=1` in
`/etc/ccm/ccm.env` and rerun the installer to run `ccm-sim-crew`, which publishes simulated
crew W1 to W3 with `--no-cabin` (the real cabin node stays the only source of cabin data). The
public site's Demo mode has simulated crew either way.

## 6. Configure the hotspot

Edit the file the installer created and set your own SSID and password (at least 8 characters):

```bash
sudo nano /etc/ccm/hotspot.env
sudo deploy/pi/hotspot.sh
```

The hotspot uses `wlan0` as an access point with address `192.168.4.1/24` and NetworkManager's
shared IPv4 (DHCP for clients). Because `wlan0` is busy being the access point, use Ethernet or a
USB WiFi adapter if the Pi itself needs internet.

## 7. Configure the ESP32 cabin node

In `firmware/ccm_cabin_node/`:

1. Copy `secrets.example.h` to `secrets.h` and enter the hotspot SSID and password from
   `/etc/ccm/hotspot.env`.
2. In `config.h`, set `MQTT_ENABLED` to `1`. The default `MQTT_HOST` is already `192.168.4.1`.
3. Flash the board. Build and flash steps are in the firmware README
   (`firmware/README.md`).

## 8. Verify

Services:

```bash
systemctl status ccm-ingest ccm-analytics ccm-api ccm-public
journalctl -u ccm-api -n 50 --no-pager
```

Broker traffic (the cabin node once it is flashed; simulated crew only with `CCM_SIM_CREW=1`):

```bash
mosquitto_sub -h 127.0.0.1 -t 'ccm/#' -v
```

From a device on the hotspot, use `-h 192.168.4.1` instead.

Time service (clients appear after the ESP32 asks for the time):

```bash
chronyc clients
```

Dashboard and API:

```bash
curl -s http://127.0.0.1:8000/api/status | head -c 300; echo
ss -ltnp | grep -E '1883|8000'
```

Open `http://192.168.4.1:8000/` from a phone or laptop joined to your hotspot.

## 9. Put the Pi's data on the public website (optional)

The Pi keeps the only database. The public site on Vercel reads it through a second, read-only
API (`ccm-gateway public`, port 8001, loopback only) that Tailscale Funnel publishes at a stable
HTTPS address. Tailscale's free Personal plan includes Funnel; you need no domain and no router
port forwarding. The Pi needs internet for this, so use Ethernet or a USB WiFi adapter
(`wlan0` is the hotspot).

1. Pick a key and set it on the Pi:

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   sudo nano /etc/ccm/ccm.env          # CCM_PUBLIC_KEY=<that key>
   sudo deploy/pi/install.sh           # enables and starts ccm-public
   curl -s -H "Authorization: Bearer <key>" http://127.0.0.1:8001/api/status | head -c 200; echo
   ```

2. Install Tailscale, log in, and publish port 8001:

   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up                   # opens a login link
   sudo tailscale funnel --bg 8001     # the first time, it links to enable Funnel for the tailnet
   tailscale funnel status             # shows https://<pi-name>.<tailnet>.ts.net
   ```

3. On Vercel, set `CCM_PI_URL` to that `https://...ts.net` address and `CCM_PI_KEY` to the same
   key, then redeploy (`deploy/cloud/README.md`).

The public API answers only `GET /api/*` with the right key, so nobody on the internet can
approve, check in or change settings. When the Pi is off, the website falls back to its
simulator demo.

## Services

| Unit | Command | Runs as |
|---|---|---|
| `ccm-ingest.service` | `/opt/ccm/venv/bin/ccm-gateway ingest` | `ccm` |
| `ccm-analytics.service` | `/opt/ccm/venv/bin/ccm-gateway analytics` | `ccm` |
| `ccm-api.service` | `/opt/ccm/venv/bin/ccm-gateway api` | `ccm` |
| `ccm-public.service` | `/opt/ccm/venv/bin/ccm-gateway public` (only when `CCM_PUBLIC_KEY` is set) | `ccm` |
| `ccm-sim-crew.service` (only with `CCM_SIM_CREW=1`) | `/opt/ccm/venv/bin/ccm-sim run --scenario quiet_day --crew W1,W2,W3 --no-cabin --broker 127.0.0.1:1883` | `ccm` |

All units read `/etc/ccm/ccm.env`. They restart on failure; `ccm-sim-crew` restarts always, because its 24-hour scenario ends.

## Settings

`/etc/ccm/ccm.env` (created from `ccm.env.example`) holds every variable from
`gateway/src/ccm_gateway/config.py`:

| Variable | Pi value |
|---|---|
| `CCM_DB` | `/var/lib/ccm/ccm.sqlite3` |
| `CCM_MQTT_HOST` / `CCM_MQTT_PORT` | `127.0.0.1` / `1883` |
| `CCM_API_HOST` / `CCM_API_PORT` | `0.0.0.0` / `8000` |
| `CCM_DASHBOARD_DIST` | `/opt/ccm/dashboard/dist` |
| `CCM_RAW_RETENTION_DAYS` | `7` |
| `CCM_PUBLIC_PORT` / `CCM_PUBLIC_KEY` | `8001` / blank |

The public API stays off while `CCM_PUBLIC_KEY` is blank.

`/etc/ccm/hotspot.env` holds the hotspot SSID, password, interface, address and connection name.
Both files are mode 600.

## Troubleshooting

- **Dashboard shows 404 or a blank page.** `dashboard/dist` was not copied. Copy a build to
  `/opt/ccm/dashboard/dist`, then `sudo systemctl restart ccm-api`.
- **A service restarts in a loop.** Its `ccm-gateway` subcommand must exist in the installed gateway
  version. Pull the latest checkout, rerun `sudo deploy/pi/install.sh`, and check
  `journalctl -u <unit> -n 100`.
- **The ESP32 has no time (`t_node` is 0).** Check that chrony is running and that
  `/etc/chrony/conf.d/ccm.conf` has the `allow 192.168.4.0/24` and `local stratum 10` lines. Run
  `chronyc clients` after the node starts.
- **No internet on the Pi after the hotspot is up.** Expected: `wlan0` is the access point. Use
  Ethernet or a USB WiFi adapter.
- **No MQTT messages.** Check the broker with `systemctl status mosquitto` and subscribe with
  `mosquitto_sub -h 127.0.0.1 -t 'ccm/#' -v`.
- **Reinstalling.** `sudo deploy/pi/install.sh` is idempotent; it keeps existing
  `/etc/ccm/ccm.env` and `/etc/ccm/hotspot.env`.

## Demo-only choices

The broker accepts anonymous connections and has no TLS, and the hotspot is a plain WPA2 access
point. This is a demo LAN: do not expose the Pi's port 1883 or 8000 to an untrusted network.

These scripts have not been run on a physical Pi in this repository; they were checked with
`bash -n` and by reading the systemd units, but not with `systemd-analyze verify` or on Raspberry Pi
OS. Expect to adjust the hotspot or chrony details for your image.
