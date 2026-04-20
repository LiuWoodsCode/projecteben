Just some ideas I want to work on at a future time.

# Clipboard sync 
Sync the clipboards of the host and Raspberry Pi as one.

# deletescapeOS Port
A port of the application to deletescapeOS would be nice, as it shows that in fact, I do support my own mobile operating system (even if I have no device it can natively run on)

# Drag `n Drop 

## In
Simply drag content into the Eben Desktop window and it's shared with the Raspberry Pi:
* For websites:
    * Opens in the default internet browser set in RPI OS
* For files:
    * Copied into ~/Downloads/EbenDesktop

## Out
Probably wouldn't work due to limitations of VNC itself but in theory, you could drag things out of the Pi and into the host system.

# Seamless
> [!IMPORTANT]
> This is very easy to do on X11, but probably not possible through Wayland.
Might only be possible on Linux, but in theory this would allow apps from the RPi to blend in with your normal desktop applications

# Audio sharing 
Use the desktop as speakers. This is possible in VNC, but only kinda. It's specific to RealVNC's implementation AFAIK and you have to not only pay for it, but also must be using RealVNC Server AND Viewer. This implementation would mostly just forward the audio into the PC over WebRTC or similar.

# Notification forwarding
Even if the app is closed, it would be useful to be able to receive the notifications being sent to the system's tray on the client PC. This would also add an incentive to using the desktop client over the web client. 

# Thin client
A specialized version of the desktop application made to be used on a public access or low performance computer that allows hassle-free usage of the Eben device. 

For example, instead of using clunky Windows software to lock down a machine and wipe data after usage, you can install a flavor of Linux and an Ethernet extender into the machines in your hotel's business center and allow users to plug in Eben devices.

## Internet sharing
Most likely to provide a hassle-free setup, you would most likely want to display a "terms of usage" for the internet access, and then configure a network on the device which allows for it to get internet access through a special Wi-Fi network. 

# Ranboo Communication Protocol
Improve security by adding a new protocol for communication with the RPi. This runs on port 6767 and is, at a protocol level, still HTTP. However it's different from the current HTTP protocol on port 8000.