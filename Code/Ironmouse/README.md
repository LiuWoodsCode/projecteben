# Project Ironmouse

Project Ironmouse is a project that allows a Raspberry Pi Zero or Zero 2 with wireless to be usable as an Ethernet adapter.

## Startup

Connect a microUSB cable from your Pi Zero to the target PC. You **MUST** connect the cable to the "USB" port on the RPi. You may also need to supply power using a seperate USB power source if the PC does not supply enough current.

Once connected, wait a few minutes for an ethernet adapter to show up on the target PC. 

> [!NOTE]
> This will eventually be done automatically.

Now you can connect to the Zero over SSH. The Zero is always given the IP `10.12.194.1` so connect to that.

Once you are logged in, run the following commands:
```bash
cd adapter-usb
./nat.sh
sudo .venv/bin/python app.py
```
You can now use the client application to add a WLAN access point to your connections and connect to the internet.
