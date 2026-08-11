**English** | [한국어](README.ko.md)

# BC-250 Custom Pannel

A GTK4 control panel for monitoring and configuring an AMD BC-250 running Bazzite.

![BC-250 Control Panel](screenshot.png)

## Supported system

- AMD BC-250
- Bazzite x86_64
- Bazzite GNOME or Deck GNOME
- Wayland session

Other GPUs and Linux distributions are not supported installation targets.

## Install and run

Clone the repository and start the panel directly:

```bash
git clone https://github.com/momopanda123/bc-250-custom-pannel.git
cd bc-250-custom-pannel
./run.sh
```

`run.sh` is the main launcher. It starts the GUI immediately and does not require `install-app.sh`. Use `./run.sh --check` when you only want to validate the Bazzite environment without opening the panel.

To add an optional **BC-250 Control Panel** icon to the GNOME app grid, run this once:

```bash
./install-app.sh
```

After that, close the terminal and launch the panel from the app grid. The installed launcher uses `Terminal=false`, so it opens only the GUI. This step only creates a per-user desktop entry; it does not install system components or change CPU/GPU settings. Run it again if you move the cloned project folder.

The required executables and service files are included in the repository. You do not need to find and install Governor or UMR separately.

### Install components

If the top row reports that installation or an update is required:

1. Select `Install components`.
2. Complete the system authentication prompt.
3. Installation is complete when the row changes to `Components installed`.

The button is disabled when every required component is installed. Installing components alone does not change the selected CPU or CU configuration.

## Using the application

### Status

The top area displays:

- Current CPU core/thread and GPU CU counts
- BIOS and Linux kernel versions
- CPU and GPU temperatures
- GPU power, clock, and voltage
- Fan RPM

Sensor values refresh automatically.

### Control

Use this card to configure GPU operation.

| Setting | Function |
|---|---|
| Performance preset | Power saving, balanced, performance, or custom |
| Min MHz / Max MHz | Minimum and maximum GPU clock |
| Max mV | Voltage ceiling, not a fixed voltage |
| Throttle °C | Temperature at which clock throttling begins |
| Recovery °C | Temperature at which the normal clock is restored |

Select `Custom` to enter values directly. `0 MHz` leaves that clock bound open, while `0 mV` disables the additional voltage ceiling. A preset Recovery temperature below its Throttle temperature is intentional.

### CPU / GPU CORES

#### CPU

- Turn the toggle on to request 8C/16T.
- Turn the toggle off to request 6C/12T.
- CPU core changes take effect after a reboot.
- If a complete power loss returns a saved 8C/16T configuration to 6C/12T, the boot service requests the unlock again and performs at most one warm reboot when required.

The application displays a message after an operation that requires a reboot.

#### GPU CU

- WGP 0–2 are the fixed 24-CU base and cannot be disabled.
- Use WGP 3 and WGP 4 in each row to select optional CUs.
- The selectable range is 24–40 CUs in 2-CU steps.
- After changing the selection, choose `Apply` or `Save`.

The CU status in the GUI confirms that the requested register values were read back successfully. It does not guarantee that optional CUs are stable in games or sustained workloads. Test the selected configuration with the games or GPU workloads you normally use.

### POWER & SLEEP

Use `System sleep` and `Screen off` to select the automatic suspend and display-off timers.

- `Never`: do not trigger automatically
- 5/10/15/30/60 minutes: use the selected delay
- `Custom`: enter 1–240 minutes

These options control only the GNOME session timers. Selecting `Never` does not disable CPU idle states or automatic GPU power management.

### Apply and Save

- `Apply`: apply every draft setting to the current session.
- `Save`: apply every setting and store it for the next boot.

One action at the bottom processes the Control, CPU/GPU Cores, and Power & Sleep cards together.

## Language

Use the selector at the top right to choose 한국어, English, 日本語, or 简体中文. The selected language is retained for later launches.

## Remove

To remove only the GNOME app-grid entry:

```bash
./uninstall-app.sh
```

To remove the installed system components, run this from the project directory:

```bash
pkexec python3 ./bc250_install.py remove --project-root "$PWD"
```

The project folder and user backup files are not removed automatically.

## Before changing hardware settings

- Save active work before changing CU, clock, or voltage settings.
- Optional CPU cores and GPU CUs may differ in stability between boards.
- An unstable configuration can close a game, freeze the display, or restart the system.
- Fan speed is displayed but is not controlled by this application.
