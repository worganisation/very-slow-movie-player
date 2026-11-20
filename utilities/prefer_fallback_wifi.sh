#!/bin/sh
# Prefer the temporary router when it is available on consecutive scans.

set -eu

interface=wlan0
main_ssid=iot-wg
preferred=iot-wg-fallback
seen="${RUNTIME_DIRECTORY:?}/seen"
retry_after="$RUNTIME_DIRECTORY/retry-after"

main_uuid=$(nmcli --get-values GENERAL.CON-UUID device show "$interface")
if [ -z "$main_uuid" ] || [ "$main_uuid" = "--" ]; then
    rm -f -- "$seen"
    exit 0
fi

active_ssid=$(nmcli --escape no --get-values 802-11-wireless.ssid connection show uuid "$main_uuid")
if [ "$active_ssid" != "$main_ssid" ]; then
    rm -f -- "$seen"
    exit 0
fi

if [ -f "$retry_after" ] && [ "$(date +%s)" -lt "$(cat "$retry_after")" ]; then
    exit 0
fi

nmcli connection show id "$preferred" >/dev/null
if ! access_points=$(nmcli --wait 10 --escape no --get-values SSID device wifi list ifname "$interface" --rescan yes); then
    rm -f -- "$seen"
    exit 0
fi

if ! printf '%s\n' "$access_points" | grep -Fxq -- "$preferred"; then
    rm -f -- "$seen"
    exit 0
fi

if [ ! -f "$seen" ]; then
    touch "$seen"
    exit 0
fi

rm -f -- "$seen"
if [ "$(nmcli --get-values GENERAL.CON-UUID device show "$interface")" != "$main_uuid" ]; then
    exit 0
fi

if nmcli --wait 15 connection up id "$preferred" ifname "$interface"; then
    exit 0
fi

# Give the main connection time to recover before retrying a failed switch.
printf '%s\n' "$(($(date +%s) + 120))" > "$retry_after"
if [ "$(nmcli --get-values GENERAL.CON-UUID device show "$interface")" != "$main_uuid" ]; then
    nmcli --wait 15 connection up uuid "$main_uuid" ifname "$interface"
fi
