#!/bin/bash
set -e

nft add table ip nat
nft 'add chain ip nat postrouting { type nat hook postrouting priority 100 ; }'
nft add rule ip nat postrouting oifname "wlan0" masquerade

nft add table inet filter
nft 'add chain inet filter forward { type filter hook forward priority 0 ; policy drop ; }'

nft add rule inet filter forward iifname "usb0" oifname "wlan0" accept
nft add rule inet filter forward iifname "wlan0" oifname "usb0" ct state related,established accept

exec /home/dapixelprowler/adapter/.venv/bin/python app.py