# Battery 
The battery currently used in Project Eben is a no-name USB power bank. In the future this should be replaced with a battery controller that allows the system to see battery information.

## Current cell
The battery currently used in Project Eben is a no-name USB power bank, and currently does not give any details to the OS.

The battery percentage is displayed above the USB-C port and can be shown using the following methods:
* Plug in a device if none are already plugged in
* Press the button on the right hand side

### Specifications
These were found on the body of the power bank and are probably not accurate. 
* Capacity
    * 6600mAh
* Battery Energy
    * 3.7v
    * 24.42Wh
* Input (both microUSB and USB-C)
    * 5V
    * 2A
* USB-C output
    * 5V
    * 2.1A
* USB-A output
    * 5V
    * 2.1A (port with 2 lightning bolts)
    * 1A (port with one lightning bolt)

## Troubleshooting

### Raspberry Pi shuts off during boot

If the Raspberry Pi is able to show a green LED for some time but abruptly switches to red, this is the Pi "browning out", as the power supply does not supply enough electricity to power the system.

If you're able to get a GUI session before it shuts down, you probably will see an undervolt warning in the taskbar and/or as a notification.

#### If using a battery 

Check the battery level by tapping on the side button of the power bank (USB power bank) or using a multimeter (custom battery)

##### If the battery level is 1 dot
The battery may not have enough charge to power the Raspberry Pi. Try charging it to 100% and try again.

##### If the battery is charged
The battery may not supply the necessary voltage or amperage for the Raspberry Pi 5. 