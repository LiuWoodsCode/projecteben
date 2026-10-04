#!/usr/bin/env bash

set -euo pipefail

if [[ "$EUID" -eq 0 ]]; then
    echo "Run this installer as the desktop user, without sudo." >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_dir="$(cd -- "$script_dir/../../../.." && pwd -P)"
evergarden="$repo_dir/Code/Kyoto/Evergarden/main.py"
yui="$repo_dir/Code/Kyoto/YuiHirasawa/main.py"
notification="$repo_dir/Code/Kyoto/YuiHirasawa/notification.py"

for program in "$evergarden" "$yui" "$notification"; do
    if [[ ! -f "$program" ]]; then
        echo "Missing Kyoto program: $program" >&2
        exit 1
    fi
done

if ! command -v apt-get >/dev/null 2>&1 || ! command -v dpkg-query >/dev/null 2>&1; then
    echo "This installer requires an apt-based Linux system." >&2
    exit 1
fi

packages=(
    labwc
    python3
    python3-gi
    python3-dbus
    python3-dbus-next
    python3-pywayland
    gir1.2-gtk-3.0
    gir1.2-gdkpixbuf-2.0
    gir1.2-gtklayershell-0.1
    gir1.2-gstreamer-1.0
    gstreamer1.0-plugins-base
    gstreamer1.0-plugins-good
)

missing=()
for package in "${packages[@]}"; do
    if [[ "$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true)" != "install ok installed" ]]; then
        missing+=("$package")
    fi
done

if (( ${#missing[@]} > 0 )); then
    echo "Installing missing apt dependencies: ${missing[*]}"
    sudo apt-get update
    sudo apt-get install -y "${missing[@]}"
fi

# Raspberry Pi OS supplies lwrespawn separately from Debian's labwc package.
if [[ ! -x /usr/bin/lwrespawn ]]; then
    if apt-cache show raspberrypi-ui-mods >/dev/null 2>&1; then
        sudo apt-get install -y raspberrypi-ui-mods
    fi
    if [[ ! -x /usr/bin/lwrespawn ]]; then
        echo "Missing /usr/bin/lwrespawn; install the Raspberry Pi OS package that provides it." >&2
        exit 1
    fi
fi

# Apt installs Python modules for the distribution's interpreter.
/usr/bin/python3 - <<'PY'
import dbus
import dbus_next
import gi
import pywayland
gi.require_version('Gtk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, GtkLayerShell, Gst
PY

autostart_dir="${XDG_CONFIG_HOME:-$HOME/.config}/labwc"
autostart="$autostart_dir/autostart"
begin_marker='# BEGIN Kyoto autostart (Install_Kyoto.sh)'
end_marker='# END Kyoto autostart (Install_Kyoto.sh)'

mkdir -p -- "$autostart_dir"
if [[ -f "$autostart" ]]; then
    begin_count="$(grep -Fxc -- "$begin_marker" "$autostart" || true)"
    end_count="$(grep -Fxc -- "$end_marker" "$autostart" || true)"
    if [[ "$begin_count" != "$end_count" || "$begin_count" -gt 1 ]]; then
        echo "Unbalanced Kyoto markers in $autostart; leaving it untouched." >&2
        exit 1
    fi
fi

temporary="$(mktemp "$autostart_dir/.autostart.XXXXXX")"
trap 'rm -f -- "$temporary"' EXIT

if [[ -f "$autostart" ]]; then
    awk -v begin="$begin_marker" -v end="$end_marker" '
        $0 == begin { in_block = 1; next }
        $0 == end && in_block { in_block = 0; next }
        !in_block { print }
    ' "$autostart" > "$temporary"
    chmod --reference="$autostart" "$temporary"
else
    chmod 644 "$temporary"
fi

cat >> "$temporary" <<EOF

$begin_marker
/usr/bin/lwrespawn /usr/bin/python3 "$evergarden" &
/usr/bin/lwrespawn /usr/bin/python3 "$yui" &
/usr/bin/lwrespawn /usr/bin/python3 "$notification" &
$end_marker
EOF

mv -- "$temporary" "$autostart"
echo "Installed Kyoto autostart in $autostart (takes effect at the next labwc login)."
