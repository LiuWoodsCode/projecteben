# Stockwood Suspend
Stockwood suspend is a form of attempting to suspend the system by limiting activity in userspace, instead of relying on the CPU having a power state for sleep.

## How it works internally
The script does not ask the kernel for a real sleep state. Instead, it tries to make the machine behave like it is asleep by pausing user activity and dropping power draw in the desktop session.

- Entry point
	- The script does not ask the kernel for a real sleep state.
	- Instead, it tries to make the machine behave like it is asleep by pausing user activity and dropping power draw in the desktop session.

- Prepare the suspend snapshot
	- Scan /proc for regular-user processes.
	- Filter out process names and prefixes that should never be frozen, such as the shell, the session manager, the compositor, and the script itself.
	- Record the current state of:
		- Processes
		- Running systemd services
		- Displays
		- Thermal cooling devices
	- Store that state in temporary files under /tmp so the session can be restored later.

- Reduce active work
	- Stop active user services with systemctl.
	- Keep a small protected set running so the system remains reachable and stable.
	- Suspend the selected processes with SIGSTOP.

- Lower power draw
	- Write a slow governor and a low maximum frequency to the cpufreq sysfs controls.
	- Disable any cooling devices that were captured in the snapshot so the machine stays quiet and consistent while “asleep”.

- Blank the session
	- Use the current Wayland session environment to drive wlr-randr.
	- Turn the active outputs off.
	- Save their modes so they can be brought back exactly as they were.

- Wait for wake
	- Monitor input devices with evdev.
	- Treat a power key press, any key press, or relative input like mouse movement as a wake event.
	- While waiting, periodically re-apply the low-power settings.
	- Re-freeze or re-stop anything that managed to restart.

- Restore on wake
	- Restore display outputs.
	- Restore CPU settings.
	- Restore cooling device states.
	- Restart the stopped services.
	- Resume the suspended processes.
	- If requested, lock the session with swaylock after the restore completes.

If restore fails, the script falls back to showing an error dialog, and the panic path can still be used unless the no-panic flag is set.

## Runtime files
The suspend state is written to temporary files so the wake path can restore what was changed:

- Snapshot data
	- /tmp/pi_fake_sleep_pids.txt
	- /tmp/pi_fake_sleep_services.json
	- /tmp/pi_fake_sleep_displays.json
	- /tmp/pi_fake_sleep_cooling.json

## Known issues

### Session crash on Eben
When attempting to Stockwood suspend on a headless Eben system, your login session may die, sending you to a greeter that you cannot see.
#### Workaround
If you are the only user on the system, you can plug in a keyboard and type in your password, then press Enter to log back in.

### Windows may disappear from the panel
When resuming from a Stockwood suspend, the panel may lose all running applications at the time of suspend. Attempting to restart the panel does not bring the applications back.
#### Workaround
Use Alt+Tab to navigate between the windows, as they still show up with this method.