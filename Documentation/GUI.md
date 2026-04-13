# Accessing a desktop enviorment

Project Eben comes bundled with the PiXEL desktop, the same desktop that is bundled with stock Raspberry Pi OS images. 

You could connect a keyboard, mouse, and display to the Raspberry Pi itself, but you can also use noVNC. This allows you to interact with the desktop inside most modern web browsers.

If you are headless, to start noVNC, you first need to connect to the pi over SSH. Otherwise you can just start a terminal session.

Once you are at a shell, run the command `./start-gui.sh`. After some waiting you will be able to navigate your web browser to `http://[RASPI-IP]:6080/vnc.html`. Then simply click connect.

An example of the output this command will return: 
```plaintext
dapixelprowler@justsayori:~ $ ./start-gui.sh
[1/2] Starting wayvnc...
[2/2] Starting noVNC proxy...

wayvnc PID: 1976
noVNC PID:  1984
Access noVNC at: http://localhost:6080
Warning: could not find self.pem
Using local websockify at /home/dapixelprowler/noVNC/utils/websockify/run
Starting webserver and WebSockets proxy on port 6080
WebSocket server settings:
  - Listen on :6080
  - Web server. Web root: /home/dapixelprowler/noVNC
  - No SSL/TLS support (no cert file)
  - proxying from :6080 to localhost:5900


Navigate to this URL:

    http://justsayori:6080/vnc.html

Press Ctrl-C to exit
```

## Using noVNC
This method does not require any software to be installed on the client, but will require a modern browser to be available. This includes the following as of 2026-03-27:
* Blink 92 or higher 
  * Chrome 92+
  * Edge (Chromium) 92+
  * Opera 78+ 
  * Brave 1.28+
  * Electron 14+ 
  * MS Edge WebView2 (92+)
* Gecko 92 or higher 
  * Firefox 92+, Firefox ESR 91+
* WebKit 15 or higher (Apple platforms)
  * Safari 15+
* WebKitGTK 2.34 or higher
  * GNOME Web (Epiphany) 41+

Navigate to the URL specified in the command, you should see a page with a "Connect" button. Simply pressing it will connect you to the Wayland session.

Once connected, it is recommended to do the following:
1. Open the side menu if not already open
1. Click the gear icon to open the settings
1. Change "Scaling mode" to "Remote resizing"

Additionally, if you are connected to the Raspberry Pi over Ethernet, you should change the quality settings:

1. Open the side menu if not already open
1. Click the gear icon to open the settings
1. Click on "Advanced" to expand the advanced options
1. 

This config seemingly works well for me, your results may be different depending on your ethernet cable, wifi signal strength and your client computer's NIC:

For Wi-Fi:
```json
{
  "compression": "1",
  "resize": "remote",
  "quality": "6"
}
```
For Ethernet:
```json
{
  "compression": "0",
  "resize": "remote",
  "quality": "7"
}
```

## Using other VNC clients
