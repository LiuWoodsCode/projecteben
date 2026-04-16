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

ensure_hotspot_conn() {
    if ! conn_exists "$HOTSPOT_CONN"; then
        nmcli connection add \
            type wifi \
            ifname "$WIFI_IFACE" \
            con-name "$HOTSPOT_CONN" \
            autoconnect no \
            ssid "$SSID"
    fi

    nmcli connection modify "$HOTSPOT_CONN" \
        connection.interface-name "$WIFI_IFACE" \
        802-11-wireless.mode ap \
        802-11-wireless.band bg \
        wifi-sec.key-mgmt wpa-psk \
        wifi-sec.psk "$PASSWORD" \
        ipv4.method shared \
        ipv6.method ignore
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
    ensure_hotspot_conn
    ensure_eth_shared_conn
    ensure_eth_normal_conn
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
    nmcli connection up "$ETH_NORMAL_CONN" || true
    echo "Ethernet sharing disabled on $ETH_IFACE. It now behaves like a normal network interface."
}

router_mode() {
    # start_ap
    enable_eth_share
    echo "Router mode enabled: Wi-Fi AP on $WIFI_IFACE, shared Ethernet on $ETH_IFACE."
}

station_mode() {
    stop_ap
    echo "AP disabled. You can now use NetworkManager to connect $WIFI_IFACE to another Wi-Fi network as a station/client."
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
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|start-ap|stop-ap|enable-eth-share|disable-eth-share|router-mode|station-mode|status}"
        exit 1
        ;;
esac