#!/usr/bin/env bash
# Create the Crew Context Monitor WiFi hotspot with NetworkManager (Bookworm).
# The SSID and password are read from /etc/ccm/hotspot.env; nothing is hardcoded.
#
#   sudo deploy/pi/hotspot.sh
#
# wlan0 becomes the access point, so use Ethernet or a USB WiFi adapter for
# internet on the Pi itself.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run this script with sudo" >&2
  exit 1
fi

ENV_FILE="${CCM_HOTSPOT_ENV:-/etc/ccm/hotspot.env}"
if [ ! -r "$ENV_FILE" ]; then
  echo "missing $ENV_FILE; run deploy/pi/install.sh first" >&2
  exit 1
fi
# shellcheck disable=SC1090
. "$ENV_FILE"

: "${CCM_HOTSPOT_SSID:?set CCM_HOTSPOT_SSID in $ENV_FILE}"
: "${CCM_HOTSPOT_PASSWORD:?set CCM_HOTSPOT_PASSWORD in $ENV_FILE}"
IFACE="${CCM_HOTSPOT_IFACE:-wlan0}"
ADDRESS="${CCM_HOTSPOT_ADDRESS:-192.168.4.1/24}"
CON_NAME="${CCM_HOTSPOT_CONNECTION:-ccm-hotspot}"

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli not found; this script expects NetworkManager (Raspberry Pi OS Bookworm)" >&2
  exit 1
fi

if [ "${#CCM_HOTSPOT_PASSWORD}" -lt 8 ]; then
  echo "the hotspot password must be at least 8 characters" >&2
  exit 1
fi

# Recreate the profile so the script can be run repeatedly.
nmcli connection delete "$CON_NAME" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname "$IFACE" con-name "$CON_NAME" \
  autoconnect yes ssid "$CCM_HOTSPOT_SSID"
nmcli connection modify "$CON_NAME" \
  802-11-wireless.mode ap \
  802-11-wireless.band bg \
  ipv4.method shared \
  ipv4.addresses "$ADDRESS" \
  wifi-sec.key-mgmt wpa-psk \
  wifi-sec.psk "$CCM_HOTSPOT_PASSWORD"
nmcli connection up "$CON_NAME"

echo "Hotspot '$CCM_HOTSPOT_SSID' is up on $IFACE with address $ADDRESS."
echo "wlan0 is now the access point; use Ethernet or a USB WiFi adapter for internet."
