#!/usr/bin/env bash

set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
    echo "Run this installer as the user who should own the Ranboo service, without sudo." >&2
    exit 1
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_dir="$(cd -- "$script_dir/../../../.." && pwd -P)"
server_dir="$repo_dir/Code/Ranboo/server"
server_main="$server_dir/main.py"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
unit_file="$unit_dir/ranboo.service"

if [[ ! -f "$server_main" ]]; then
    echo "Ranboo server not found: $server_main" >&2
    exit 1
fi

python_bin="$(command -v python3 || true)"
if [[ -z "$python_bin" ]]; then
    echo "python3 is required to run the Ranboo server." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is required to install a systemd user service." >&2
    exit 1
fi

mkdir -p -- "$unit_dir"
cat > "$unit_file" <<EOF
[Unit]
Description=Ranboo server

[Service]
Type=simple
WorkingDirectory=$server_dir
ExecStart="$python_bin" "$server_main"
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable ranboo.service
systemctl --user restart ranboo.service
echo "Installed and started $unit_file"
