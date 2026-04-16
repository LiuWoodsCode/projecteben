sudo nft add table ip nat
sudo nft 'add chain ip nat postrouting { type nat hook postrouting priority 100 ; }'
sudo nft add rule ip nat postrouting oifname "wlan0" masquerade

sudo nft add table inet filter
sudo nft 'add chain inet filter forward { type filter hook forward priority 0 ; policy drop ; }'

sudo nft add rule inet filter forward iifname "eth0" oifname "wlan0" accept
sudo nft add rule inet filter forward iifname "wlan0" oifname "eth0" ct state related,established accept
