# Recovery 
If Project Eben will not boot into the operating system, or if the device cannot be accessed in any other manner, you can use the recovery functions to attempt to debug the issue.

## salvatod recovery
TODO: salvatod is not completed

## Using rpiboot/usbboot
The Raspberry Pi 5 hardware includes a function to bypass the embedded EEPROM loaded bootloader code and instead boot completely over the USB-C port.

This can be especially helpful if the Raspberry Pi is semi bricked due to an interupted firmware update.

### Setting up the host
Most likely, you will need to build rpiboot yourself. The project can be found on [the Raspberry Pi GitHub](https://github.com/raspberrypi/usbboot).

#### Bootloader update/restore
To perform a bootloader restore on the Raspberry Pi 5:
```bash
cd recovery5
./update-pieeprom.sh
../rpiboot -d .
```
#### USB MSD + UART
To expose the device as a USB Mass Storage + UART:
```bash
rpiboot -d mass-storage-gadget64
```

### Booting the Pi in usbboot mode
Make sure the Raspberry Pi is completely disconnected from power before doing any of this.

1. While the power is disconnected, hold down the power button.
1. Now, connect a USB-C cable connected to the host machine.
1. Keep holding the power button until the power light turns red.
1. Release the button once the light turns red.

Assuming rpiboot was running when you did all of this, your host should start sending boot files to your Raspberry Pi. 