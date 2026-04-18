# Power warning
> [!WARNING]
> DO NOT UNPLUG THE POWER SOURCE FROM THE SYSTEM UNLESS IT IS SHUT DOWN.

## How to turn the RPi off
Here are some methods on turning off the Raspberry Pi. Note that shutting down RPIs without power buttons will not fully shut down, rather the board is now safe to be disconnected from power.

### Using the power button
> [!IMPORTANT]
> This only applies to Raspberry Pi 5. 
The power button is located next to the power/activity light. Press the button twice and the system should shut down.

### Using 
## Exceptions
There are actually some exceptions to this rule
# switch to wifi
```bash
sudo nmcli c down rpi-hotspot-ap
sudo nmcli c up PWCS-Guest
```
