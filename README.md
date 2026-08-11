**English** | [한국어](README.ko.md)

# BC-250 Custom Pannel

BC-250 Custom Pannel is a GTK4 control panel for AMD BC-250 systems running Bazzite. It provides a single interface for monitoring GPU status and firmware information and for applying performance settings. The `Pannel` spelling is intentionally retained for compatibility with the existing project path.

This project is more than a Python GUI. It includes the GPU governor, CU boot manager, a Bazzite 43-compatible UMR binary, service files, and policy templates required at runtime. Their versions and SHA-256 hashes are pinned in the repository.

![BC-250 Control Panel dashboard](screenshot.png)

The image above shows the application running on Bazzite GNOME. Temperature, power, clock, fan, CPU, and CU readings vary depending on the device and current workload.

## Features

- Shows the current CPU core/thread count and GPU CU count together in the top summary
- Refreshes CPU/GPU temperature, GPU power, clock, voltage, and fan RPM every second
- Provides Power Saving, Balanced, and Performance presets plus user-defined GPU clock bounds and an mV ceiling across the governor's full `u32` input range
- Configures throttle and recovery temperatures using the bundled voltage curve
- Selects each optional GPU WGP individually while keeping the 24 factory CUs fixed, then shows selected, live, saved, and verified-readback states
- Toggles between 6C/12T and 8C/16T and performs one guarded warm-reboot recovery after a complete power loss
- Controls automatic suspend and display-off timers with Disabled, preset, and custom 1–240 minute options
- Applies all Control, CPU/GPU Cores, and Power/IDLE drafts with one global `Apply` or `Save` action
- Displays BIOS and kernel information and supports Korean, English, Japanese, and Simplified Chinese
- Checks executables, configuration, services, policies, and license files and updates missing or incompatible items in one operation

## Supported systems

- AMD BC-250 (`1002:13fe`)
- Bazzite x86_64
- Bazzite GNOME or Deck GNOME image
- Wayland session

Other GPUs and general Fedora, Ubuntu, or Arch/CachyOS installations are not supported targets. The installer checks the platform first and stops without changing system files when the environment does not match.

## Quick start

From the cloned project directory, run:

```bash
./run.sh
```

The GUI opens first. The component status row is always visible and wraps long component lists instead of truncating them. The `Install components` button is enabled when a governor, CU manager, UMR, privileged helper, CPU mode component, systemd unit, D-Bus policy, Polkit rule, or license file is missing or incompatible. After one press and system authentication, the installer verifies every bundled SHA-256 and installs or updates all 14 manifest-declared system files at fixed paths and modes. It also writes a root-owned, read-only installation receipt so the unprivileged GUI can verify the Polkit rule even though Bazzite restricts `/etc/polkit-1/rules.d`. The install button is disabled only when the complete compatible set is present.

To register the application in the GNOME app grid, run once:

```bash
./install-app.sh
```

This script resolves the current clone location automatically. It does not hard-code a user name or installation path.

## Interface

### Compact real-time dashboard

The window uses a fixed 520-pixel width and a compact natural height that depends on the selected language. It does not use a vertical scrolling container: real-time status, governor controls, CPU/GPU core controls, power settings, and BIOS/kernel information are all visible in one window. The compact CPU/GPU split gives the WGP grid the remaining horizontal space instead of leaving an unused strip on the right. The six sensors are arranged in one two-row status band in the following order:

The title bar shows only the application name. The top overview places the current CPU/GPU state on the left and the language selector on the right, followed by three equal-width cells for availability, BIOS version, and kernel version. There is no footer row, so the global Apply and Save buttons form the bottom edge of the interface. Availability appears only once as plain colored text with no pill border or background. Major areas use distinct accent colors, while button and combo-box text is consistently white for readability on the dark background.

1. CPU temperature
2. GPU temperature
3. GPU power
4. GPU clock
5. SMU voltage
6. Active fan RPM

The GPU core area normally shows one unambiguous `24 / 40 CU enabled` line, matching the number-first form of the adjacent CPU topology. A different checkbox selection is added to that same line only as a pending change until it is applied. The CPU core area shows the current `6C / 12T` or `8C / 16T` state and one reversible action toggle. A saved 8C/16T preference that did not survive a cold boot is exposed as the toggle tooltip instead of adding another repetitive visible status line.

Sensors refresh in the background every second so slow system calls do not freeze the window. Governor, throttle, recovery-temperature, and CU fields are populated only once at startup; later sensor refreshes do not overwrite user input. BC-250 GPU clock and software power sensors reported by the driver can differ from wall-power measurements under load. If the driver does not expose a sensor, the application displays `Unavailable` instead of inventing a value.

### Language

When the language is set to `Auto`, the application checks `LC_ALL`, `LC_MESSAGES`, and `LANG` in that order. Korean, English, 日本語, and 简体中文 can be selected directly from the top-right of the overview. Unsupported locales and missing translation keys fall back to English. The selection is stored in `~/.config/bc250-custom-pannel/settings.json` and is applied immediately without restarting the application.

### CPU and fan sensor safety

CPU temperature is read from the `Tctl` value of `k10temp` first, with the board sensor labeled `CPU` used as a fallback. Fan RPM is read from an `nct6686` or `nct6687` sensor. The application discovers hwmon devices by name and label on every refresh instead of hard-coding hwmon numbers. This release never writes PWM values; fan control is strictly read-only. Fan control remains out of scope until the physical device connected to `Pump Fan` has been verified.

### Power / IDLE

The `System sleep` and `Screen off` combo boxes control only the GNOME session timers. They provide Never, 5, 10, 15, 30, and 60 minute presets plus a custom setting. A selection remains a draft until the global `Apply` or `Save` button is pressed. Choosing the custom setting reveals a 1–240 minute input field. Selecting Never for both timers does not disable the CPU's MWAIT idle state or AMDGPU DPM automatic clock and power management.

On BC-250, the cpuidle driver file may report `none` while kernel logs and CPU capabilities still confirm MWAIT usage. In that case, the GUI displays `CPU MWAIT`. A GPU DPM value of `auto` means that automatic clock and power scaling is active according to load.

Automatic suspend is saved for both AC and battery sessions, while display-off time is stored as the GNOME idle timer. Disabled sets the corresponding timer to zero. These are user-session settings and do not require administrator authentication or a reboot.

### BIOS and kernel

- BIOS vendor
- BIOS version
- BIOS date
- Linux kernel version
- System architecture

The information is read from `/sys/class/dmi/id` and the running kernel. Administrator privileges are not required.

### Performance and temperature

| Preset | Clock range | Validated curve limit | Intended use |
|---|---:|---:|---|
| Power Saving | 500–1500 MHz | 900 mV | Lower heat and power consumption |
| Balanced | 500–1700 MHz | 920 mV | Balance between performance and efficiency |
| Performance | 500–1800 MHz | 930 mV | Highest setting validated on the current system |

The two global buttons below all cards replace the former per-card action buttons. `Apply` validates every draft first, then applies the governor, exact WGP masks, CPU mode, and GNOME power timers for the current session. `Save` performs the same application and also stores the governor, WGP, and CPU choices for boot. The buttons remain available when installed components need an update; the separate setup row communicates and performs that update. The governor's minimum clock, maximum clock, mV ceiling, throttle temperature, and recovery temperature are delivered together in one D-Bus call. Persistent files are backed up and written atomically.

Selecting `Custom` below the presets allows direct minimum and maximum GPU clock configuration from 0 to 4,294,967,295 MHz, matching the governor D-Bus interface's unsigned 32-bit fields. A clock value of `0` leaves that bound open. When both bounds are nonzero, minimum must not exceed maximum; the application does not impose the former 500–1800 MHz range or a 100 MHz gap. The mV field is a voltage ceiling: at each clock the governor uses the lower of the bundled curve value and the selected ceiling. `0 mV` disables the additional ceiling and uses the curve unchanged. Raising the ceiling never forces a curve point upward. The bundled curve covers 350–2400 MHz while the default operating range remains 500–1800 MHz. Values above 2400 MHz can be applied only after an appropriate voltage point is added to the Governor configuration.

Throttle and recovery temperature fields each accept 0–255°C. The patched bundled Governor handles the same range independently without imposing ordering or a hysteresis gap; `0` disables that threshold. Defaults remain 85°C/75°C. A failed persistent service restart restores the previous configuration.

## Safety limits

- Custom GPU clock fields accept the full unsigned 32-bit range; `0` means an open bound, and two nonzero bounds must be ordered minimum first.
- Throttle and recovery fields independently accept 0–255°C without an application-defined gap.
- The mV field accepts the full unsigned 32-bit input domain, but it is a ceiling over the bundled curve rather than a command to force a constant voltage. `0` means no extra ceiling.
- The software does not claim that a user-selected clock or voltage is stable on every BC-250 board.
- This is not an exact wattage limiter. Power is displayed from sensors and is controlled indirectly through clock and voltage presets.
- Performance control uses only the existing `cyan-skillfish-governor-smu` service. Do not run another GPU governor at the same time.
- Applying a changed WGP selection writes live GPU registers and verifies the exact readback. This can hang the display or workload on an unstable CU, so save work first.

### Individual WGP selection and readback

The four GPU rows each expose five WGP cells. WGP 0–2 are the fixed factory base (24 CUs total) and cannot be disabled. WGP 3 and WGP 4 are individually selectable per row, providing any even CU count from 24 through 40 instead of only three fixed profiles. The GUI distinguishes the current draft, exact live register readback, and saved boot mask. A readback mismatch is shown as a failure even when the total CU count happens to match.

`Apply` writes the selected masks and accepts success only after all four registers read back exactly. `Save` also backs up and updates `/etc/bc250-cu-live-manager.conf` so the same masks are requested at boot. The factory 24 CU cells are visibly disabled because they are fixed on; only the optional cells can be changed. The manager writes an atomic state record under `/run/bc250-custom-pannel/cu-state.json`; the GUI does not infer 40 CUs from a service log. Optional CUs may be unstable or defective on an individual board, so test each selection under load.

### CPU 8-core / 16-thread toggle

The fixed-label `Use 8C / 16T` switch is a draft included in the global action. Off requests 6C/12T and on requests 8C/16T. Enabling it from the default `6C / 12T` state requests the bundled `bc250-cu-live-manager --yes cpu-unlock`; the additional cores appear only after a reboot. The switch preserves the saved CPU preference when the temporary live topology differs, and periodic refresh never overwrites an explicit user change. Applying or saving another card therefore cannot silently change a saved 8C/16T preference back to 6C/12T. Any successful operation that reports a required reboot replaces the ordinary success notice with a clear restart-warning message box; this handling is generic rather than CPU-specific.

If the privileged helper has not yet been installed, pressing the CPU toggle first combines component installation and CPU mode application into one administrator operation so authentication is not requested twice in succession.

Disabling the toggle from an `8C / 16T` state does not perform an unvalidated reverse SMU write. Instead, it uses the Linux CPU hotplug interface to offline the threads belonging to the two additional physical cores. The requested state is saved in `/etc/bc250-custom-pannel-cpu.conf`, and `bc250-cpu-mode.service` reapplies it during boot. If a write fails partway through, already modified threads are restored to their original state.

Installing the components alone does not change the CPU state and does not start the CPU-mode service immediately. The initial mode is `auto`, which preserves the current state until the user explicitly saves a choice. Because the SMU unlock is volatile, a complete power loss can return the board to 6C/12T. When 8C/16T is saved, the boot service detects the cold state, arms the unlock, and schedules exactly one warm reboot. If the next boot is still 6C/12T, it records a failure and stops automatic rebooting; it never loops. The two optional cores may be unstable or defective, so load testing is still required.

## Bundled components

Exact files, source URLs, installation paths, modes, and SHA-256 hashes are recorded in [`VENDOR-MANIFEST.json`](VENDOR-MANIFEST.json).

| Component | Pinned version | Purpose |
|---|---|---|
| cyan-skillfish-governor-smu | v0.4.11-bc250.2 | Atomic clock, mV-ceiling, and 0–255°C temperature updates |
| bc250-cu-live-manager | 2026-08-11 readback build | Applies exact WGP masks and verifies register readback |
| UMR | 1.0.10-6.fc43 | Accesses BC-250 GPU registers |
| Privileged helper | 0.3.0 | Validates and applies one complete settings draft |
| CPU mode service | 2 | Applies saved CPU mode with one-shot cold-boot recovery |

The governor and UMR are distributed under the MIT License. The Governor changes are included as a reproducible source patch under `vendor/patches/`, while original license text and copyright notices remain under `vendor/licenses/` and in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). The Polkit rule is installed under `/etc/polkit-1/rules.d/` instead of the read-only `/usr/share` tree. The installer writes no system files if any manifest hash differs. The manifest is not digitally signed, however, so the hash check detects file corruption and individual modifications but cannot prove provenance if both the repository and manifest are compromised together.

The bundled UMR binary was validated on Bazzite 43 x86_64. The CU boot service explicitly selects the installed `/opt/bc250-custom-pannel/bin/umr`. It is not forced into use when `ldd` reports a missing shared library. A later Bazzite base version may require the distribution UMR package and one reboot after rpm-ostree applies the change.

## Installation scope and administrator privileges

Sensor reads, BIOS/kernel information, language changes, and GNOME automatic-suspend/display-off settings run with normal user privileges. Running `./run.sh` alone does not gain administrator privileges or modify system files. System authentication is requested only for the following operations:

- Installing or removing bundled components
- Persistently saving governor settings and restarting the service
- Saving the next-boot CU profile
- Requesting CPU unlock and saving the CPU online/offline mode

After system authentication, the installer and privileged helper use only the following fixed locations:

| Path | Contents |
|---|---|
| `/etc/cyan-skillfish-governor-smu/` | Governor configuration and safe voltage curve |
| `/etc/bc250-cu-live-manager.conf` | Next-boot CU profile |
| `/etc/bc250-custom-pannel-cpu.conf` | CPU mode applied during boot |
| `/usr/local/bin/bc250-cu-live-manager` | CU/CPU management utility |
| `/opt/bc250-custom-pannel/` | Pinned UMR binary and third-party licenses |
| `/usr/local/libexec/bc250-custom-pannel-privileged` | Restricted privileged helper |
| `/etc/systemd/system/` | Governor, CU, and CPU services |
| `/etc/dbus-1/system.d/` | Governor D-Bus policy |
| `/etc/polkit-1/rules.d/` | Administrator authentication policy |

The services run as root because they need access to GPU registers and system configuration, but they do not open external ports or start a network server. The project has no feature for storing API keys or account passwords. GNOME app registration modifies only the current user's `~/.local/share/applications/` directory.

## Security review status

> **Public distribution status: on hold.** The following findings reflect a review of the code as of 2026-08-02. They are not a security certification or a guarantee that the project is defect-free. Exposure is relatively limited on a single-user system, but the following three issues should be addressed before distributing the application to other users.

1. **Polkit authorization retention scope — High**
   `vendor/templates/49-bc250-custom-pannel.rules` uses `AUTH_ADMIN_KEEP` with the generic `org.freedesktop.policykit.exec` action. After the administrator password is entered, approval may be reused by a different `pkexec` program during the short authorization-retention window. Before distribution, replace it with `AUTH_ADMIN` or define a dedicated Polkit action with a fixed executable. The [official Polkit documentation](https://polkit.pages.freedesktop.org/polkit/polkit.8.html) also warns against using `*_KEEP` for rules that depend on variables.

2. **Governor D-Bus write permission — Medium**
   `vendor/templates/com.cyanskillfish.Governor.conf` allows default local users to call the governor's write interface. Another process running on the same device can therefore call the upstream governor API without passing through the GUI's unsigned-integer and bound-order validation. The risk is lower on a single-user device, but GUI validation is not a system-wide security boundary. Before public distribution, restrict write access through a dedicated user group or a separately authorized helper.

3. **Optional UMR source installation — Medium, conditional**
   The manual `install-umr` function in the bundled `bc250-cu-live-manager` can clone a remote default branch that is not pinned to an immutable commit or release hash, then build and install it as root. The GUI's `Install components` button does not use this path; it installs the verified bundled UMR binary. The manual function should nevertheless be removed from a public package or pinned to an immutable version with a verified hash.

Evidence confirmed during the current review:

- All 17 bundled components listed in `VENDOR-MANIFEST.json` matched their recorded SHA-256 hashes.
- The bundled Governor was rebuilt from official v0.4.11 commit `60ab6e5b354f01f287c73d920990dcd618a674cc`; its complete source delta is stored in `vendor/patches/`, and all 37 Rust tests passed in the isolated build.
- The GNU build ID of the bundled UMR executable matched the [Fedora 43 `1.0.10-6.fc43` package record](https://packages.fedoraproject.org/pkgs/umr/umr/fedora-43.html).
- Microsoft Defender found no threats in the project files during the 2026-08-02 scan.
- No private key, API token, or external network listener was found in the source.
- All 171 unit tests passed on the target BC-250 Bazzite system, including the GTK4 environment check and the 14-file installation reproduction.

These results do not prove that the bundled binaries are absolutely safe. The executables have not received a complete source audit, while antivirus scanning and hash matching confirm only known-threat detection and file identity respectively. Public releases should use signed tags, signed checksums, or distribution package signatures.

## Copyright and redistribution

- The original MIT license text and copyright notices for `cyan-skillfish-governor-smu` and UMR are included under `vendor/licenses/` and in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
- **There is currently no project-level `LICENSE` file in the repository root.** The project's original code is therefore covered by default copyright, and no explicit permission is granted to third parties to use, modify, or redistribute it. The copyright holder should select and add a project license before public distribution. See [GitHub's repository licensing guide](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository) for details.
- The prebuilt Governor executable does not currently include a separate complete license inventory or SBOM for statically linked Rust dependencies. Review transitive dependency licenses and add any required notices before distributing the binary to third parties.
- This repository is not an official product of AMD, Bazzite, Fedora, or the developers of the bundled tools. All names and trademarks belong to their respective owners.

Minimum checklist before public distribution:

- [ ] Remove Polkit `AUTH_ADMIN_KEEP` or replace it with a dedicated action
- [ ] Restrict Governor D-Bus write permission
- [ ] Remove the manual `install-umr` download or pin it to a version and verified hash
- [ ] Add a project-level `LICENSE`
- [ ] Document transitive binary licenses and produce an SBOM
- [ ] Repeat installation, removal, and reboot testing on a Bazzite target device

## Project structure

```text
app.py                    GTK4 application entry point
bc250/                    Status, environment detection, control, and GUI modules
bc250_install.py          Verified bundle installer and removal helper
bc250_privileged.py       Restricted persistent-setting helper
vendor/                   Pinned binaries, source patch, service templates, and licenses
VENDOR-MANIFEST.json      Installation file hashes and provenance
screenshot.png            Actual Bazzite GNOME application screenshot
README.ko.md              Korean documentation
run.sh                    Location-independent launcher
install-app.sh            GNOME app-grid registration
uninstall-app.sh          GNOME app-grid registration removal
tests/                    unittest and execution validation
```

## Recovery and removal

The following backups are created before persistent settings are changed:

- `/etc/cyan-skillfish-governor-smu/config.toml.bc250-backup`
- `/etc/cyan-skillfish-governor-smu/config.toml.bc250-pre-range-update`
- `/etc/bc250-cu-live-manager.conf.bc250-backup`

To remove only the GNOME app-grid registration, run:

```bash
./uninstall-app.sh
```

To remove the system components installed from the bundle, run the installer removal operation from the project directory:

```bash
pkexec python3 ./bc250_install.py remove --project-root "$PWD"
```

The remover targets only files listed in the manifest and preserves configuration backups. It does not delete the project directory.

## Troubleshooting

### The GUI does not open

Run `./run.sh --check` to verify Python, GTK4, bundle hashes, and target-platform detection. Bazzite GNOME images include the required Python GTK4 bindings by default.

### Bundle SHA-256 error

A file is damaged or has been modified. Do not proceed with system installation. Obtain a clean copy from Git. There is no option to disable hash verification.

### UMR compatibility failure

The bundled executable does not match a shared library such as LLVM in the current Bazzite base version. Use the distribution package path shown by the application and reboot once if rpm-ostree requires it.

### CU count shows `Unavailable`

The application displays a confirmed value only when it can verify both a successful `bc250-cu-live-manager.service` log for the current boot and the saved mask. It never guesses that the GPU has 40 CUs.

### Temperature, power, or clock shows `Unavailable`

The AMDGPU hwmon path is missing or the driver does not expose the corresponding sensor. Even when the display is functioning normally, the application does not fabricate unavailable sensor values.

### Governor setting fails

Check whether another governor is running at the same time. This application controls only `cyan-skillfish-governor-smu.service`.

## Tests

Run all host-safe tests with:

```bash
./tests/run-tests.sh
./run.sh --check
```

Privileged-helper tests use a temporary root directory and do not modify the real `/etc` tree, systemd services, or GPU registers.

## Warning

Enabling BC-250 CUs or changing clocks and voltage can cause system freezes, loss of display output, data corruption, and increased heat. Use a stable power supply and active cooling, and save important work before changing settings. The application forwards user-selected values and creates backups for persistent configuration changes, but it cannot guarantee that a value is supported or stable on every individual board.
