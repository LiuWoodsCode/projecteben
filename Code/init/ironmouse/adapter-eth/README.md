This is the version of the adapter side software you should use if you plan to use a Raspberry Pi Zero W or Raspberry Pi Zero 2 W as your Ironmouse adapter using USB gadget mode.

You will need to install a driver for RNDIS to function on Windows. This driver is available on [the github page for rpi-gadget-mode.](https://github.com/raspberrypi/rpi-usb-gadget?tab=readme-ov-file#windows-setup--troubleshooting-ics--rndis) 

If you cannot connect to the internet, another system running Linux can be configured to forward the Ironmouse device over Ethernet. You can also use Project Eben's ethernet sharing feature if 

## Issues

### Connected PC cannot obtain an IP address

In some cases, the PC's networking manager may get stuck trying to obtain an IP address from Ironmouse. On Linux with NetworkManager, this will often surface as the following in nmcli:

```
usb0: connecting (getting IP configuration) to Wired connection 1
        "Raspberry Pi Gadget"
        ethernet (cdc_ether), [mac], hw, mtu 1500
```

In most cases, triggering a reboot of the Ironmouse device resolves the issue.
