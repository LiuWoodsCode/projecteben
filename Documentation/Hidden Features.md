These are features that are nice to know but we intentionally don't document in any user facing documentation as we see them as very technical or commonly unnessesary features.

# Use a USB drive to disable AP creation on boot

> [!IMPORTANT]
> This most likely will not work if your Raspberry Pi 4/5 is set to try USB boot first, though I have not tested this.

If for some reason (*cough cough Lightspeed System's Relay Filter Agent on iPadOS*) you need to connect to Wi-Fi on boot instead of hosting the default AP, you can use a USB drive to force the AP be disabled for this boot.

You will need:
* Project Eben (of course)
* A USB drive
    * Any size will work
    * Needs to be formatted in a filesystem that Linux can read
    * You can even use one with contents on it

Connect the USB drive to your computer and create a blank text file at the root of the drive named **EXACTLY** `EBEN_NOAP` (no extension). You don't need to put anything in this file. Then eject the disk.

Make sure the Raspberry Pi is off, then plug in the USB drive. Then you can power on the Raspberry Pi. You should notice that the AP does not appear.