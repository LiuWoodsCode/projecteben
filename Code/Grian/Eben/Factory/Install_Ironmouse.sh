#!/bin/bash

set -euo pipefail

# ==============================================================================
# FACTORY CONFIGURATION
# ==============================================================================
#
# Change these values when building/customizing the factory image.
#
# These are BUILD-TIME settings. This script does not install a separate
# configuration system for changing them after deployment.
#
# SECURITY_STANDARD:
#   WEP
#   WPA
#   WPA2
#   WPA2/3
#   WPA3
#
# CIPHER:
#   AES
#   TKIP
#   WPA3
#
# Notes:
#   - AES means CCMP.
#   - WPA3 uses SAE + CCMP.
#   - WPA2/3 enables WPA2-PSK + WPA3-SAE transitional mode.
#   - WPA3 security requires WPA3-compatible cipher settings.
#   - WEP ignores CIPHER.
#
# ==============================================================================

FACTORY_SSID="Probably Not Jeff Geerling"
FACTORY_PASSPHRASE="TransRights123!"
FACTORY_SECURITY_STANDARD="WPA2"
FACTORY_CIPHER="AES"

WIFI_IFACE="wlan0"
ETH_IFACE="eth0"

# Factory boot behavior
FACTORY_WIFI_HOTSPOT_AUTOCONNECT="yes"
FACTORY_ETHERNET_SHARED_AUTOCONNECT="yes"

# NetworkManager autoconnect priorities.
# Higher values win when multiple profiles are eligible.
HOTSPOT_AUTOCONNECT_PRIORITY="100"
ETH_SHARED_AUTOCONNECT_PRIORITY="100"
ETH_NORMAL_AUTOCONNECT_PRIORITY="0"

# ==============================================================================

SCRIPT_PATH="/usr/local/bin/rpi-hotspot"
DESKTOP_DIR="$HOME/.local/share/applications"

HOTSPOT_CONN="rpi-hotspot-ap"
ETH_SHARED_CONN="rpi-eth-shared"
ETH_NORMAL_CONN="rpi-eth-normal"

echo "[*] Installing Raspberry Pi hotspot + Ethernet sharing setup..."

# ------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------

if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: nmcli is not installed."
    echo "Install NetworkManager first."
    exit 1
fi

case "$FACTORY_SECURITY_STANDARD" in
    WEP|WPA|WPA2|WPA2/3|WPA3)
        ;;
    *)
        echo "ERROR: Invalid FACTORY_SECURITY_STANDARD:"
        echo "  $FACTORY_SECURITY_STANDARD"
        echo
        echo "Valid values:"
        echo "  WEP"
        echo "  WPA"
        echo "  WPA2"
        echo "  WPA2/3"
        echo "  WPA3"
        exit 1
        ;;
esac

case "$FACTORY_CIPHER" in
    AES|TKIP|WPA3)
        ;;
    *)
        echo "ERROR: Invalid FACTORY_CIPHER:"
        echo "  $FACTORY_CIPHER"
        echo
        echo "Valid values:"
        echo "  AES"
        echo "  TKIP"
        echo "  WPA3"
        exit 1
        ;;
esac

case "$FACTORY_WIFI_HOTSPOT_AUTOCONNECT" in
    yes|no)
        ;;
    *)
        echo "ERROR: FACTORY_WIFI_HOTSPOT_AUTOCONNECT must be yes or no."
        exit 1
        ;;
esac

case "$FACTORY_ETHERNET_SHARED_AUTOCONNECT" in
    yes|no)
        ;;
    *)
        echo "ERROR: FACTORY_ETHERNET_SHARED_AUTOCONNECT must be yes or no."
        exit 1
        ;;
esac

if [[ "$FACTORY_SECURITY_STANDARD" == "WPA3" && "$FACTORY_CIPHER" != "WPA3" ]]; then
    echo "[!] WPA3-only security requires WPA3-compatible cipher settings."
    echo "[!] FACTORY_CIPHER will effectively use WPA3/CCMP."
fi

if [[ "$FACTORY_SECURITY_STANDARD" == "WPA2/3" && "$FACTORY_CIPHER" == "TKIP" ]]; then
    echo "ERROR: WPA2/3 transitional mode cannot use TKIP."
    echo "Use:"
    echo "  FACTORY_CIPHER=\"AES\""
    echo "or:"
    echo "  FACTORY_CIPHER=\"WPA3\""
    exit 1
fi

if [[ "$FACTORY_SECURITY_STANDARD" == "WPA" && "$FACTORY_CIPHER" == "WPA3" ]]; then
    echo "ERROR: WPA cannot use WPA3 cipher/security settings."
    exit 1
fi

if [[ "$FACTORY_SECURITY_STANDARD" == "WPA2" && "$FACTORY_CIPHER" == "WPA3" ]]; then
    echo "ERROR: WPA2 cannot use WPA3 cipher/security settings."
    exit 1
fi

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

conn_exists() {
    nmcli -t -f NAME connection show | grep -Fxq "$1"
}

ensure_connection_exists() {
    local name="$1"
    local type="$2"
    local iface="$3"

    if ! conn_exists "$name"; then
        echo "[*] Creating NetworkManager profile: $name"

        sudo nmcli connection add \
            type "$type" \
            ifname "$iface" \
            con-name "$name" \
            autoconnect no
    else
        echo "[*] NetworkManager profile already exists: $name"
    fi
}

# ------------------------------------------------------------------------------
# Create hotspot profile
# ------------------------------------------------------------------------------

echo "[*] Configuring factory Wi-Fi hotspot profile..."

if ! conn_exists "$HOTSPOT_CONN"; then
    sudo nmcli connection add \
        type wifi \
        ifname "$WIFI_IFACE" \
        con-name "$HOTSPOT_CONN" \
        autoconnect no \
        ssid "$FACTORY_SSID"
fi

# Clear security properties that may have been left behind by an older
# installation before applying the requested factory security configuration.

sudo nmcli connection modify "$HOTSPOT_CONN" \
    connection.interface-name "$WIFI_IFACE" \
    connection.autoconnect "$FACTORY_WIFI_HOTSPOT_AUTOCONNECT" \
    connection.autoconnect-priority "$HOTSPOT_AUTOCONNECT_PRIORITY" \
    802-11-wireless.ssid "$FACTORY_SSID" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    ipv6.method ignore

# Reset Wi-Fi security settings where NetworkManager permits it.
sudo nmcli connection modify "$HOTSPOT_CONN" \
    802-11-wireless-security.key-mgmt "" \
    802-11-wireless-security.proto "" \
    802-11-wireless-security.pairwise "" \
    802-11-wireless-security.group "" \
    802-11-wireless-security.psk "" \
    802-11-wireless-security.wep-key0 "" \
    802-11-wireless-security.wep-key-type 0 \
    2>/dev/null || true

case "$FACTORY_SECURITY_STANDARD" in

    WEP)
        echo "[*] Security: WEP"

        sudo nmcli connection modify "$HOTSPOT_CONN" \
            802-11-wireless-security.key-mgmt none \
            802-11-wireless-security.wep-key0 "$FACTORY_PASSPHRASE" \
            802-11-wireless-security.wep-key-type 1
        ;;

    WPA)
        echo "[*] Security: WPA"

        if [[ "$FACTORY_CIPHER" == "AES" ]]; then
            PAIRWISE="ccmp"
            GROUP="ccmp"
        else
            PAIRWISE="tkip"
            GROUP="tkip"
        fi

        sudo nmcli connection modify "$HOTSPOT_CONN" \
            802-11-wireless-security.key-mgmt wpa-psk \
            802-11-wireless-security.psk "$FACTORY_PASSPHRASE" \
            802-11-wireless-security.proto wpa \
            802-11-wireless-security.pairwise "$PAIRWISE" \
            802-11-wireless-security.group "$GROUP"
        ;;

    WPA2)
        echo "[*] Security: WPA2"

        if [[ "$FACTORY_CIPHER" == "AES" ]]; then
            PAIRWISE="ccmp"
            GROUP="ccmp"
        else
            PAIRWISE="tkip"
            GROUP="tkip"
        fi

        sudo nmcli connection modify "$HOTSPOT_CONN" \
            802-11-wireless-security.key-mgmt wpa-psk \
            802-11-wireless-security.psk "$FACTORY_PASSPHRASE" \
            802-11-wireless-security.proto rsn \
            802-11-wireless-security.pairwise "$PAIRWISE" \
            802-11-wireless-security.group "$GROUP"
        ;;

    WPA2/3)
        echo "[*] Security: WPA2/WPA3 transitional"

        sudo nmcli connection modify "$HOTSPOT_CONN" \
            802-11-wireless-security.key-mgmt "wpa-psk sae" \
            802-11-wireless-security.psk "$FACTORY_PASSPHRASE" \
            802-11-wireless-security.proto rsn \
            802-11-wireless-security.pairwise ccmp \
            802-11-wireless-security.group ccmp
        ;;

    WPA3)
        echo "[*] Security: WPA3-only"

        sudo nmcli connection modify "$HOTSPOT_CONN" \
            802-11-wireless-security.key-mgmt sae \
            802-11-wireless-security.psk "$FACTORY_PASSPHRASE" \
            802-11-wireless-security.proto rsn \
            802-11-wireless-security.pairwise ccmp \
            802-11-wireless-security.group ccmp
        ;;
esac

# ------------------------------------------------------------------------------
# Create shared Ethernet profile
# ------------------------------------------------------------------------------

echo "[*] Configuring factory shared Ethernet profile..."

ensure_connection_exists \
    "$ETH_SHARED_CONN" \
    ethernet \
    "$ETH_IFACE"

sudo nmcli connection modify "$ETH_SHARED_CONN" \
    connection.interface-name "$ETH_IFACE" \
    connection.autoconnect "$FACTORY_ETHERNET_SHARED_AUTOCONNECT" \
    connection.autoconnect-priority "$ETH_SHARED_AUTOCONNECT_PRIORITY" \
    ipv4.method shared \
    ipv6.method ignore

# ------------------------------------------------------------------------------
# Create normal Ethernet fallback profile
# ------------------------------------------------------------------------------

echo "[*] Configuring normal Ethernet fallback profile..."

ensure_connection_exists \
    "$ETH_NORMAL_CONN" \
    ethernet \
    "$ETH_IFACE"

sudo nmcli connection modify "$ETH_NORMAL_CONN" \
    connection.interface-name "$ETH_IFACE" \
    connection.autoconnect no \
    connection.autoconnect-priority "$ETH_NORMAL_AUTOCONNECT_PRIORITY" \
    ipv4.method auto \
    ipv6.method auto

# ------------------------------------------------------------------------------
# Install compatibility/manual control utility
# ------------------------------------------------------------------------------

echo "[*] Creating hotspot control utility..."

sudo tee "$SCRIPT_PATH" > /dev/null << 'EOF'
#!/bin/bash

set -euo pipefail

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

require_profiles() {
    local missing=0

    for profile in \
        "$HOTSPOT_CONN" \
        "$ETH_SHARED_CONN" \
        "$ETH_NORMAL_CONN"
    do
        if ! conn_exists "$profile"; then
            echo "Missing NetworkManager profile: $profile"
            missing=1
        fi
    done

    if [[ "$missing" -ne 0 ]]; then
        echo
        echo "The factory NetworkManager profiles are missing."
        echo "Run the factory setup script again to recreate them."
        exit 1
    fi
}

prepare() {
    require_nm
    wait_for_nm
    require_profiles
}

start_ap() {
    prepare

    nmcli radio wifi on || true
    nmcli device set "$WIFI_IFACE" managed yes || true
    nmcli connection up "$HOTSPOT_CONN"

    echo "Hotspot enabled on $WIFI_IFACE."
}

stop_ap() {
    prepare

    nmcli connection down "$HOTSPOT_CONN" || true
    nmcli device set "$WIFI_IFACE" managed yes || true

    echo "Hotspot disabled on $WIFI_IFACE."
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
    nmcli connection up "$ETH_NORMAL_CONN"

    echo "Ethernet sharing disabled on $ETH_IFACE."
    echo "It now behaves like a normal network interface."
}

router_mode() {
    start_ap
    enable_eth_share

    echo "Router mode enabled:"
    echo " - Wi-Fi AP on $WIFI_IFACE"
    echo " - Shared Ethernet on $ETH_IFACE"
}

station_mode() {
    stop_ap

    echo "AP disabled."
    echo "You can now use NetworkManager to connect $WIFI_IFACE"
    echo "to another Wi-Fi network as a station/client."
}

factory_boot_mode() {
    prepare

    nmcli connection modify "$HOTSPOT_CONN" \
        connection.autoconnect yes \
        connection.autoconnect-priority 100

    nmcli connection modify "$ETH_SHARED_CONN" \
        connection.autoconnect yes \
        connection.autoconnect-priority 100

    nmcli connection modify "$ETH_NORMAL_CONN" \
        connection.autoconnect no

    echo "Factory boot networking enabled."
    echo
    echo "On subsequent boots:"
    echo " - Wi-Fi hotspot will autoconnect."
    echo " - Shared Ethernet will autoconnect."
}

normal_boot_mode() {
    prepare

    nmcli connection modify "$HOTSPOT_CONN" \
        connection.autoconnect no

    nmcli connection modify "$ETH_SHARED_CONN" \
        connection.autoconnect no

    nmcli connection modify "$ETH_NORMAL_CONN" \
        connection.autoconnect yes \
        connection.autoconnect-priority 100

    echo "Normal boot networking enabled."
    echo
    echo "On subsequent boots:"
    echo " - Wi-Fi hotspot will not autoconnect."
    echo " - Ethernet will use normal DHCP configuration."
}

status() {
    require_nm

    echo "=== Device status ==="
    nmcli device status

    echo
    echo "=== Active connections ==="
    nmcli -t -f NAME,DEVICE,TYPE connection show --active || true

    echo
    echo "=== Managed profiles ==="
    nmcli \
        -f NAME,UUID,TYPE,DEVICE,AUTOCONNECT,AUTOCONNECT-PRIORITY \
        connection show |
        grep -E "^(NAME|$HOTSPOT_CONN|$ETH_SHARED_CONN|$ETH_NORMAL_CONN)" || true
}

case "${1:-}" in
    start)
        router_mode
        ;;

    stop)
        stop_ap
        ;;

    restart)
        stop_ap || true
        sleep 2
        router_mode
        ;;

    start-ap)
        start_ap
        ;;

    stop-ap)
        stop_ap
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

    station-mode)
        station_mode
        ;;

    factory-boot-mode)
        factory_boot_mode
        ;;

    normal-boot-mode)
        normal_boot_mode
        ;;

    status)
        status
        ;;

    *)
        echo "Usage:"
        echo "  $0 start"
        echo "  $0 stop"
        echo "  $0 restart"
        echo "  $0 start-ap"
        echo "  $0 stop-ap"
        echo "  $0 enable-eth-share"
        echo "  $0 disable-eth-share"
        echo "  $0 router-mode"
        echo "  $0 station-mode"
        echo "  $0 factory-boot-mode"
        echo "  $0 normal-boot-mode"
        echo "  $0 status"
        exit 1
        ;;
esac
EOF

sudo chmod 755 "$SCRIPT_PATH"

# ------------------------------------------------------------------------------
# Remove obsolete service from installations of an older version
# ------------------------------------------------------------------------------

# We deliberately DO NOT install a systemd service.
#
# If this setup script is being run on a system containing the previous
# implementation, remove the old service so it cannot race NetworkManager.

if systemctl list-unit-files rpi-hotspot.service \
    --no-legend 2>/dev/null |
    grep -q '^rpi-hotspot\.service'; then

    echo "[*] Removing obsolete rpi-hotspot.service..."

    sudo systemctl disable --now rpi-hotspot.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/rpi-hotspot.service
    sudo systemctl daemon-reload
fi

# ------------------------------------------------------------------------------
# Create menu shortcuts
# ------------------------------------------------------------------------------

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

# ------------------------------------------------------------------------------
# Reload NetworkManager profiles
# ------------------------------------------------------------------------------

echo "[*] Reloading NetworkManager connections..."

sudo nmcli connection reload

# ------------------------------------------------------------------------------
# Finished
# ------------------------------------------------------------------------------

echo
echo "[+] Done."
echo
echo "Factory network configuration:"
echo " - SSID:              $FACTORY_SSID"
echo " - Security:          $FACTORY_SECURITY_STANDARD"
echo " - Cipher:            $FACTORY_CIPHER"
echo " - Wi-Fi autoconnect: $FACTORY_WIFI_HOTSPOT_AUTOCONNECT"
echo " - Ethernet sharing:  $FACTORY_ETHERNET_SHARED_AUTOCONNECT"
echo
echo "Boot behavior:"
echo " - NetworkManager directly owns startup networking."
echo " - No rpi-hotspot systemd service is installed."
echo " - $HOTSPOT_CONN autoconnect: $FACTORY_WIFI_HOTSPOT_AUTOCONNECT"
echo " - $ETH_SHARED_CONN autoconnect: $FACTORY_ETHERNET_SHARED_AUTOCONNECT"
echo " - $ETH_NORMAL_CONN autoconnect: no"
echo
echo "Compatibility/manual control utility:"
echo " - $SCRIPT_PATH"
echo
echo "Useful manual commands:"
echo " - sudo rpi-hotspot router-mode"
echo " - sudo rpi-hotspot station-mode"
echo " - sudo rpi-hotspot enable-eth-share"
echo " - sudo rpi-hotspot disable-eth-share"
echo " - sudo rpi-hotspot factory-boot-mode"
echo " - sudo rpi-hotspot normal-boot-mode"
echo " - sudo rpi-hotspot status"
echo
echo "The NetworkManager profiles will take effect automatically on boot."