#!/usr/bin/env bash
# Install the Crew Context Monitor gateway on Raspberry Pi OS Bookworm (64-bit).
# Idempotent: safe to run again after pulling new code.
#
#   sudo deploy/pi/install.sh
#
# The dashboard is built on another machine and copied into dashboard/dist in
# this checkout first; Node is never installed on the Pi.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

CCM_USER="ccm"
CCM_HOME="/var/lib/ccm"
CCM_OPT="/opt/ccm"
CCM_ETC="/etc/ccm"
VENV="$CCM_OPT/venv"

echo "Installing from $REPO_ROOT"

$SUDO apt-get update
$SUDO apt-get install -y --no-install-recommends \
  mosquitto mosquitto-clients chrony python3-venv git

if ! id -u "$CCM_USER" >/dev/null 2>&1; then
  $SUDO useradd --system --home-dir "$CCM_HOME" \
    --shell /usr/sbin/nologin "$CCM_USER"
fi
$SUDO install -d -o "$CCM_USER" -g "$CCM_USER" "$CCM_HOME"
$SUDO install -d "$CCM_OPT" "$CCM_ETC"

if [ ! -x "$VENV/bin/python" ]; then
  $SUDO python3 -m venv "$VENV"
fi
$SUDO "$VENV/bin/python" -m pip install --quiet --upgrade pip

# Editable installs: ccm_sim locates its scenarios, params and the contracts
# directory relative to its source tree, so the checkout must stay in place.
$SUDO "$VENV/bin/python" -m pip install --quiet \
  --editable "$REPO_ROOT/sim" --editable "$REPO_ROOT/gateway"

if [ -d "$REPO_ROOT/dashboard/dist" ]; then
  $SUDO install -d "$CCM_OPT/dashboard"
  $SUDO rm -rf "$CCM_OPT/dashboard/dist"
  $SUDO cp -a "$REPO_ROOT/dashboard/dist" "$CCM_OPT/dashboard/dist"
  $SUDO chown -R "$CCM_USER:$CCM_USER" "$CCM_OPT/dashboard"
else
  echo "No dashboard/dist in the checkout."
  echo "Copy a build to $CCM_OPT/dashboard/dist (see deploy/pi/README.md)."
fi

$SUDO install -m 644 "$SCRIPT_DIR/mosquitto-ccm.conf" /etc/mosquitto/conf.d/ccm.conf
$SUDO install -d /etc/chrony/conf.d
$SUDO install -m 644 "$SCRIPT_DIR/chrony-ccm.conf" /etc/chrony/conf.d/ccm.conf

if [ ! -f "$CCM_ETC/hotspot.env" ]; then
  $SUDO install -m 600 "$SCRIPT_DIR/hotspot.env.example" "$CCM_ETC/hotspot.env"
  echo "Created $CCM_ETC/hotspot.env from the example."
fi
if [ ! -f "$CCM_ETC/ccm.env" ]; then
  $SUDO install -m 600 "$SCRIPT_DIR/ccm.env.example" "$CCM_ETC/ccm.env"
  echo "Created $CCM_ETC/ccm.env from the example."
fi
$SUDO chown root:root "$CCM_ETC/ccm.env" "$CCM_ETC/hotspot.env"
$SUDO chmod 600 "$CCM_ETC/ccm.env" "$CCM_ETC/hotspot.env"

for unit in "$SCRIPT_DIR"/ccm-*.service; do
  $SUDO install -m 644 "$unit" "/etc/systemd/system/$(basename "$unit")"
done
$SUDO systemctl daemon-reload
$SUDO systemctl enable mosquitto chrony
$SUDO systemctl restart mosquitto chrony
$SUDO systemctl enable ccm-ingest.service ccm-analytics.service \
  ccm-api.service ccm-uplink.service ccm-sim-crew.service

for unit in ccm-ingest ccm-analytics ccm-api ccm-uplink ccm-sim-crew; do
  $SUDO systemctl restart "$unit.service" ||
    echo "warning: $unit did not start; check journalctl -u $unit"
done

echo
echo "Next:"
echo "  1. Set the hotspot SSID and password in $CCM_ETC/hotspot.env."
echo "  2. Run: sudo deploy/pi/hotspot.sh"
echo "  3. If the dashboard was missing, copy a build to $CCM_OPT/dashboard/dist"
echo "     and run: sudo systemctl restart ccm-api"
echo "  4. Open http://192.168.4.1:8000/ from a device on the hotspot."
