#!/bin/bash

set -euo pipefail

echo "[*] Installing Raspberry Pi hotspot + Ethernet sharing setup..."

SCRIPT_PATH="/usr/local/bin/rpi-hotspot"
SERVICE_PATH="/etc/systemd/system/rpi-hotspot.service"

DESKTOP_DIR="$HOME/.local/share/applications"

# Default interface names
WIFI_IFACE="wlan0"
ETH_IFACE="eth0"

# ---- Create hotspot control script ----
echo "[*] Creating hotspot control script..."
sudo tee "$SCRIPT_PATH" > /dev/null << 'EOF'
#!/bin/bash
set -euo pipefail

SSID="Probably Not Jeff Geerling"
PASSWORD="TransRights123!"

WIFI_IFACE="wlan0"
ETH_IFACE="eth0"

HOTSPOT_CONN="rpi-hotspot-ap"
ETH_SHARED_CONN="rpi-eth-shared"
ETH_NORMAL_CONN="rpi-eth-normal"

require_nm() {
    command -v nmcli >/dev/null 2>&1 || {
        echo "nmcli is not installed. Install NetworkManager first."
        exit 1
    }
}

wait_for_nm() {
    local i
    for i in {1..20}; do
        if nmcli general status >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "NetworkManager did not become ready in time."
    exit 1
}

conn_exists() {
    nmcli -t -f NAME connection show | grep -Fxq "$1"
}

ensure_eth_shared_conn() {
    if ! conn_exists "$ETH_SHARED_CONN"; then
        nmcli connection add \
            type ethernet \
            ifname "$ETH_IFACE" \
            con-name "$ETH_SHARED_CONN" \
            autoconnect no
    fi

    nmcli connection modify "$ETH_SHARED_CONN" \
        connection.interface-name "$ETH_IFACE" \
        ipv4.method shared \
        ipv6.method ignore
}

ensure_eth_normal_conn() {
    if ! conn_exists "$ETH_NORMAL_CONN"; then
        nmcli connection add \
            type ethernet \
            ifname "$ETH_IFACE" \
            con-name "$ETH_NORMAL_CONN" \
            autoconnect no
    fi

    nmcli connection modify "$ETH_NORMAL_CONN" \
        connection.interface-name "$ETH_IFACE" \
        ipv4.method auto \
        ipv6.method auto
}

prepare() {
    require_nm
    wait_for_nm
    ensure_eth_shared_conn
    ensure_eth_normal_conn
}

enable_eth_share() {
    prepare
    nmcli connection down "$ETH_NORMAL_CONN" || true
    nmcli connection up "$ETH_SHARED_CONN"
    echo "Ethernet sharing enabled on $ETH_IFACE."
}

disable_eth_share() {
    prepare
    nmcli connection down "$ETH_SHARED_CONN" || true
    nmcli connection up "$ETH_NORMAL_CONN" || true
    echo "Ethernet sharing disabled on $ETH_IFACE. It now behaves like a normal network interface."
}

router_mode() {
    enable_eth_share
    echo "shared Ethernet on $ETH_IFACE."
}

status() {
    echo "=== Device status ==="
    nmcli device status
    echo
    echo "=== Active connections ==="
    nmcli -t -f NAME,DEVICE,TYPE connection show --active || true
    echo
    echo "=== Managed profiles ==="
    nmcli -f NAME,UUID,TYPE,DEVICE connection show | grep -E "^(NAME|$HOTSPOT_CONN|$ETH_SHARED_CONN|$ETH_NORMAL_CONN)"
}

case "${1:-}" in
    start)
        router_mode
        ;;
    restart)
        stop_ap || true
        sleep 2
        router_mode
        ;;
    enable-eth-share)
        enable_eth_share
        ;;
    disable-eth-share)
        disable_eth_share
        ;;
    router-mode)
        router_mode
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|start-ap|stop-ap|enable-eth-share|disable-eth-share|router-mode|station-mode|status}"
        exit 1
        ;;
esac
EOF

sudo chmod 755 "$SCRIPT_PATH"

# ---- Create systemd service ----
echo "[*] Creating systemd service..."
sudo tee "$SERVICE_PATH" > /dev/null << 'EOF'
[Unit]
Description=Raspberry Pi Hotspot and optional Ethernet sharing (NetworkManager)
After=NetworkManager.service network-online.target
Wants=NetworkManager.service network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/rpi-hotspot router-mode
ExecStop=/usr/local/bin/rpi-hotspot stop-ap
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# ---- Reload systemd ----
echo "[*] Reloading systemd..."
sudo systemctl daemon-reexec
sudo systemctl daemon-reload

# ---- Enable service ----
echo "[*] Enabling service..."
sudo systemctl enable rpi-hotspot.service

# ---- Create menu shortcuts ----
echo "[*] Creating menu shortcuts..."
mkdir -p "$DESKTOP_DIR"

cat > "$DESKTOP_DIR/hotspot-router-mode.desktop" << 'EOF'
[Desktop Entry]
Name=Enable Hotspot + Ethernet Sharing
Comment=Turn the Pi into an AP/router
Exec=/usr/bin/pkexec /usr/local/bin/rpi-hotspot router-mode
Icon=network-wireless
Terminal=true
Type=Application
Categories=Network;
Keywords=wifi;hotspot;router;ethernet;
EOF

cat > "$DESKTOP_DIR/hotspot-station-mode.desktop" << 'EOF'
[Desktop Entry]
Name=Disable Hotspot (Use Wi-Fi as Client)
Comment=Turn off the AP so wlan0 can join another Wi-Fi network
Exec=/usr/bin/pkexec /usr/local/bin/rpi-hotspot station-mode
Icon=network-wireless-off
Terminal=true
Type=Application
Categories=Network;
Keywords=wifi;station;client;network;
EOF

cat > "$DESKTOP_DIR/hotspot-enable-eth-share.desktop" << 'EOF'
[Desktop Entry]
Name=Enable Ethernet Sharing
Comment=Make eth0 a shared/router interface
Exec=/usr/bin/pkexec /usr/local/bin/rpi-hotspot enable-eth-share
Icon=network-wired
Terminal=true
Type=Application
Categories=Network;
Keywords=ethernet;share;router;network;
EOF

cat > "$DESKTOP_DIR/hotspot-disable-eth-share.desktop" << 'EOF'
[Desktop Entry]
Name=Disable Ethernet Sharing
Comment=Make eth0 behave like a normal network interface
Exec=/usr/bin/pkexec /usr/local/bin/rpi-hotspot disable-eth-share
Icon=network-wired
Terminal=true
Type=Application
Categories=Network;
Keywords=ethernet;normal;lan;network;
EOF

cat > "$DESKTOP_DIR/hotspot-status.desktop" << 'EOF'
[Desktop Entry]
Name=Hotspot Status
Comment=Show hotspot/network mode status
Exec=/usr/bin/pkexec /usr/local/bin/rpi-hotspot status
Icon=network-workgroup
Terminal=true
Type=Application
Categories=Network;
Keywords=status;wifi;ethernet;network;
EOF

chmod 644 "$DESKTOP_DIR/"*.desktop
update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true

echo "[+] Done."
echo
echo "Menu entries created:"
echo " - Enable Hotspot + Ethernet Sharing"
echo " - Disable Hotspot (Use Wi-Fi as Client)"
echo " - Enable Ethernet Sharing"
echo " - Disable Ethernet Sharing"
echo " - Hotspot Status"
echo
echo "Boot behavior:"
echo " - The systemd service starts router mode at boot."
echo " - If you want to stop that permanently:"
echo "     sudo systemctl disable rpi-hotspot.service"
echo
echo "Useful manual commands:"
echo " - sudo /usr/local/bin/rpi-hotspot router-mode"
echo " - sudo /usr/local/bin/rpi-hotspot station-mode"
echo " - sudo /usr/local/bin/rpi-hotspot enable-eth-share"
echo " - sudo /usr/local/bin/rpi-hotspot disable-eth-share"
echo " - sudo /usr/local/bin/rpi-hotspot status"