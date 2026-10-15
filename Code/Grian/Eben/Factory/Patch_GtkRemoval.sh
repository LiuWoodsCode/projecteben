#!/usr/bin/env bash
set -euo pipefail

# Raspberry Pi OS GTK Rice Protection Patch
# Redirects GTK stylesheet references to decoy filenames.

if (( EUID != 0 )); then
    echo "Please run as root (sudo)." >&2
    exit 1
fi

FILES=(
    /usr/bin/labwc-pi
    /usr/bin/startx-rpd
    /usr/bin/x-session-manager
)

# IMPORTANT: Declare the associative array BEFORE using it.
declare -A processed=()

echo "=== Raspberry Pi OS Rice Protection Patch ==="

for file in "${FILES[@]}"; do
    if [[ ! -f "$file" ]]; then
        echo "[SKIP] $file does not exist."
        continue
    fi

    target=$(readlink -f "$file")

    # Avoid processing duplicate targets.
    if [[ -n "${processed[$target]:-}" ]]; then
        echo "[SKIP] $file (already processed)"
        continue
    fi

    processed["$target"]=1

    if ! grep -qE 'gtk(-dark)?\.css' "$target"; then
        echo "[SKIP] $target has no matching references."
        continue
    fi

    # Preserve original permissions and ownership.
    if [[ ! -f "${target}.rice-backup" ]]; then
        cp -p "$target" "${target}.rice-backup"
        echo "[BACKUP] ${target}.rice-backup"
    fi

    # Redirect GTK stylesheet references.
    sed -i \
        -e 's/gtk-dark\.css/ktg-dark.css/g' \
        -e 's/gtk\.css/ktg.css/g' \
        "$target"

    echo "[PATCHED] $target"
done

echo
echo "[SUCCESS] GTK stylesheet references patched."
echo "Original scripts backed up with .rice-backup extensions."