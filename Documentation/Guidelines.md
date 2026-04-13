This is a list of some programming guidelines and such:

# We are targeting Raspberry Pi OS on a Raspberry Pi 5

Contributors should know that Project Eben is targeting the default Raspberry Pi OS (ARM64) running on a Raspberry Pi 5. This distro comes with many supporting applications for the Pi, and by default uses the Wayfire desktop enviorment.

That being said, I do realize that the main point of cyberdecks is customization, and we will expect people to use different desktop environments, perhaps even a custom kernel. However, we do not currently expect to support other distros.

# Power Efficiency  

Most users are going to run Project Eben off of a battery for portability, and we do need to work with this in mind, as the Broadcom BCM2712 is not the most power efficient SoC.

Many people who are just getting into cyberdecks most likely do not realize how much heat the Pi 5 can generate. 