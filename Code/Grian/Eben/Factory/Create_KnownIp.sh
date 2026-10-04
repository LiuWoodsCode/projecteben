sudo nmcli connection add \
    type dummy \
    ifname device-local \
    con-name device-local \
    ipv4.method manual \
    ipv4.addresses 172.31.255.1/32 \
    ipv6.method disabled

sudo nmcli connection modify device-local \
    connection.autoconnect yes \
    connection.autoconnect-priority 999

sudo nmcli connection up device-local