# Network sharing

Project Eben can create it's own local area network for use on machines where the default network cannot be used

## Modes 

### Wireless AP
Project Eben allows the Pi to communicate with another system even without connection to a Wi-Fi network or ethernet by hosting it's own wireless access point. 

The Raspberry Pi is considered the "gateway" of it's own network, and always is assigned the IP address of 10.42.0.1. 

This feature takes advantage of the hotspot feature of NetworkManager to create the AP. Thus you will not be able to use Wi-Fi networks while the AP is running.

### Ethernet sharing
Project Eben allows the Pi to communicate with another system by connecting the 2 systems's network interfaces over Ethernet.

The Raspberry Pi is considered the "gateway" of it's own network, and always is assigned the IP address of 10.42.0.1. 

You can use Ethernet sharing to allow the Raspberry Pi to connect to a Wi-Fi network you can't use for local communication (e.g a guest network) while still allowing local communication through the Ethernet. In many cases this will also let the other system to access the internet directly

## Troubleshooting

### rpi-hotspot disable-ap breaks Ethernet too
Sometimes using the cli tool to disable the Wi-Fi access point will also break the Ethernet connection.

As a workaround, you can do the following in PiXEL:
1. Get a desktop session
1. Click on the LAN icon (the 2 arrows) in the system tray
1. Click on "Probably Not Jeff Geerling" or whatever you set your SSID to
1. Confirm you want to disconnect from this Wi-Fi network

You should now be able to use the Raspberry Pi as a Wi-Fi station.