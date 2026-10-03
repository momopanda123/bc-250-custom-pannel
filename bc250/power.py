from __future__ import annotations

import configparser
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .control import CommandResult, run_command


POWER_SCHEMA = "org.gnome.settings-daemon.plugins.power"
SESSION_SCHEMA = "org.gnome.desktop.session"
SCREENSAVER_SCHEMA = "org.gnome.desktop.screensaver"

# PowerDevil changed its schema in Plasma 6. Plasma 5's suspend timeout
# is milliseconds, but DPMSControl is seconds. Plasma 6 uses seconds for both.
KDE_CONFIG_FILES = {5: "powermanagementprofilesrc", 6: "powerdevilrc"}
KDE_PROFILE_GROUP = "AC"  # BC-250 is an AC-powered desktop board.
KDE_DEFAULT_SUSPEND_MINUTES = 30
KDE_REFRESH = (
    "--user", "call", "org.kde.Solid.PowerManagement",
    "/org/kde/Solid/PowerManagement", "org.kde.Solid.PowerManagement", "refreshStatus",
)


@dataclass(frozen=True, slots=True)
class PowerState:
    cpu_idle_mode: str
    gpu_dpm_mode: str
    suspend_blocked: bool
    display_blank_blocked: bool
    suspend_minutes: int | None = None
    display_minutes: int | None = None


class PowerController:
    def __init__(
        self,
        runner: Callable[[Sequence[str]], CommandResult] = run_command,
        cpuidle_driver: Path = Path("/sys/devices/system/cpu/cpuidle/current_driver"),
        cpuinfo: Path = Path("/proc/cpuinfo"),
        drm_root: Path = Path("/sys/class/drm"),
        desktop: str | None = None,
        config_home: Path | None = None,
        kwriteconfig: str | None = None,
        plasma_version: int | None = None,
        busctl: str | None = None,
    ) -> None:
        self.runner = runner
        self.cpuidle_driver = Path(cpuidle_driver)
        self.cpuinfo = Path(cpuinfo)
        self.drm_root = Path(drm_root)
        detected = desktop if desktop is not None else os.environ.get("XDG_CURRENT_DESKTOP", "")
        self._desktop = detected.lower()
        if config_home is not None:
            self._config_home = Path(config_home)
        else:
            self._config_home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
        self._kwriteconfig = kwriteconfig
        self._busctl = busctl
        session_version = str(plasma_version) if plasma_version is not None else os.environ.get("KDE_SESSION_VERSION", "")
        self._plasma_version = int(session_version) if session_version in ("5", "6") else None
        self._version_checked = self._plasma_version is not None

    def _is_kde(self) -> bool:
        return "kde" in self._desktop.split(":")

    def _kde_version(self) -> int | None:
        if not self._version_checked:
            self._version_checked = True
            # Installed Qt tools do not identify the running session: both
            # versions may coexist. Prefer KDE_SESSION_VERSION, then Plasma.
            binary = shutil.which("plasmashell")
            if binary:
                result = self.runner([binary, "--version"])
                match = re.search(r"\bplasmashell\s+([56])\.", result.stdout) if result.ok else None
                if match:
                    self._plasma_version = int(match.group(1))
        return self._plasma_version

    @staticmethod
    def _kde_error(message: str) -> CommandResult:
        # 126/127 are reserved by the UI for cancelled authentication.
        return CommandResult(False, "", message, 1)

    def _kde_tools(self) -> tuple[str, str] | CommandResult:
        version = self._kde_version()
        if version is None:
            return self._kde_error("Cannot identify Plasma 5 or 6; no KDE power settings were changed")
        writer = self._kwriteconfig or shutil.which(f"kwriteconfig{version}")
        if not writer:
            return self._kde_error(f"KDE power settings need kwriteconfig{version}, which was not found")
        bus = self._busctl or shutil.which("busctl")
        if not bus:
            return self._kde_error("KDE power settings need busctl to refresh PowerDevil; no settings were changed")
        return writer, bus

    def _kde_write_many(self, settings: Sequence[tuple[str, str, str]]) -> CommandResult:
        tools = self._kde_tools()
        if isinstance(tools, CommandResult):
            return tools
        writer, bus = tools
        config_path = self._config_home / KDE_CONFIG_FILES[self._kde_version()]
        for group, key, value in settings:
            result = self.runner([
                writer, "--file", str(config_path), "--group", KDE_PROFILE_GROUP,
                "--group", group, "--key", key, value,
            ])
            if not result.ok:
                return self._kde_error(
                    f"Could not write KDE power setting {group}/{key}: "
                    f"{result.stderr or result.stdout}. Settings may be partially saved."
                )
        # kwriteconfig only saves a file. refreshStatus reparses and reloads the
        # active profile on both Plasma 5 and 6 (same call as KDE's settings UI).
        result = self.runner([bus, *KDE_REFRESH])
        if not result.ok:
            return self._kde_error(
                "KDE power settings were saved, but PowerDevil could not apply them: "
                + (result.stderr or result.stdout or "session D-Bus call failed")
            )
        return CommandResult(True, result.stdout, "", 0)

    def _kde_read_config(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        version = self._kde_version()
        if version is not None:
            try:
                parser.read(self._config_home / KDE_CONFIG_FILES[version], encoding="utf-8")
            except (OSError, UnicodeError, configparser.Error):
                return configparser.ConfigParser(interpolation=None)
        return parser

    @staticmethod
    def _kde_value(parser: configparser.ConfigParser, group: str, key: str) -> str | None:
        return parser.get(f"{KDE_PROFILE_GROUP}][{group}", key, fallback=None)

    @staticmethod
    def _kde_int(value: str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    @staticmethod
    def _kde_minutes(timeout: int | None, units_per_minute: int) -> int | None:
        if timeout is None or timeout < 0:
            return None
        # Do not round a positive sub-minute timeout down to the "Never" value.
        return 0 if timeout == 0 else max(1, (timeout + units_per_minute - 1) // units_per_minute)

    def _kde_inspect(self) -> PowerState:
        parser = self._kde_read_config()
        version = self._kde_version()
        suspend_minutes = display_minutes = None
        if version == 5:
            action = self._kde_int(self._kde_value(parser, "SuspendSession", "suspendType"))
            timeout = self._kde_int(self._kde_value(parser, "SuspendSession", "idleTime"))
            if action == 0 or timeout == 0:
                suspend_minutes = 0
            elif action == 1:
                suspend_minutes = self._kde_minutes(timeout, 60000)
            display_minutes = self._kde_minutes(
                self._kde_int(self._kde_value(parser, "DPMSControl", "idleTime")), 60,
            )
        elif version == 6:
            action = self._kde_int(self._kde_value(parser, "SuspendAndShutdown", "AutoSuspendAction"))
            timeout = self._kde_int(self._kde_value(parser, "SuspendAndShutdown", "AutoSuspendIdleTimeoutSec"))
            if action == 0:
                suspend_minutes = 0
            elif action == 1 and timeout is not None and timeout > 0:
                suspend_minutes = self._kde_minutes(timeout, 60)
            enabled = self._kde_value(parser, "Display", "TurnOffDisplayWhenIdle")
            if enabled is not None and enabled.strip().lower() in ("false", "0", "no", "off"):
                display_minutes = 0
            elif enabled is not None and enabled.strip().lower() in ("true", "1", "yes", "on"):
                timeout = self._kde_int(self._kde_value(parser, "Display", "TurnOffDisplayIdleTimeoutSec"))
                if timeout is not None and timeout > 0:
                    display_minutes = self._kde_minutes(timeout, 60)
        return PowerState(
            cpu_idle_mode=self._cpu_idle_mode(),
            gpu_dpm_mode=self._gpu_dpm_mode(),
            suspend_blocked=suspend_minutes == 0,
            display_blank_blocked=display_minutes == 0,
            suspend_minutes=suspend_minutes,
            display_minutes=display_minutes,
        )

    def _kde_set_suspend(self, minutes: int) -> CommandResult:
        version = self._kde_version()
        if version == 5:
            settings = [
                ("SuspendSession", "suspendType", "0"),
                ("SuspendSession", "idleTime", str(minutes * 60000)),
            ]
            if minutes:
                settings.extend([
                    ("SuspendSession", "suspendThenHibernate", "false"),
                    ("SuspendSession", "suspendType", "1"),
                ])
        else:
            settings = [
                ("SuspendAndShutdown", "AutoSuspendAction", "0"),
                ("SuspendAndShutdown", "AutoSuspendIdleTimeoutSec", str(minutes * 60)),
            ]
            if minutes:
                settings.extend([
                    ("SuspendAndShutdown", "SleepMode", "1"),
                    ("SuspendAndShutdown", "AutoSuspendAction", "1"),
                ])
        return self._kde_write_many(settings)

    def _kde_set_display(self, minutes: int) -> CommandResult:
        if self._kde_version() == 5:
            settings = [("DPMSControl", "idleTime", str(minutes * 60))]
        else:
            settings = [
                ("Display", "TurnOffDisplayWhenIdle", "false"),
                ("Display", "TurnOffDisplayIdleTimeoutSec", str(minutes * 60)),
            ]
            if minutes:
                settings.append(("Display", "TurnOffDisplayWhenIdle", "true"))
        return self._kde_write_many(settings)

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return ""

    def _setting(self, schema: str, key: str) -> str:
        result = self.runner(["gsettings", "get", schema, key])
        return result.stdout.strip().strip("'\"") if result.ok else ""

    def _setting_int(self, schema: str, key: str) -> int | None:
        value = self._setting(schema, key)
        try:
            return int(value.split()[-1])
        except (IndexError, ValueError):
            return None

    def _cpu_idle_mode(self) -> str:
        driver = self._read(self.cpuidle_driver)
        if driver and driver != "none":
            return driver
        flags = self._read(self.cpuinfo).lower().split()
        if "monitor" in flags or "mwaitx" in flags:
            return "MWAIT"
        return "scheduler"

    def _gpu_dpm_mode(self) -> str:
        for path in sorted(self.drm_root.glob("card[0-9]*/device/power_dpm_force_performance_level")):
            value = self._read(path)
            if value:
                return value
        return "unknown"

    def inspect(self) -> PowerState:
        if self._is_kde():
            return self._kde_inspect()
        suspend_blocked = all(
            self._setting(POWER_SCHEMA, key) == "nothing"
            for key in ("sleep-inactive-ac-type", "sleep-inactive-battery-type")
        )
        display_blank_blocked = (
            self._setting(SESSION_SCHEMA, "idle-delay") == "uint32 0"
            and self._setting(SCREENSAVER_SCHEMA, "idle-activation-enabled") == "false"
            and self._setting(POWER_SCHEMA, "idle-dim") == "false"
        )
        suspend_seconds = self._setting_int(POWER_SCHEMA, "sleep-inactive-ac-timeout")
        if suspend_seconds is None:
            suspend_seconds = self._setting_int(POWER_SCHEMA, "sleep-inactive-battery-timeout")
        display_seconds = self._setting_int(SESSION_SCHEMA, "idle-delay")
        return PowerState(
            cpu_idle_mode=self._cpu_idle_mode(),
            gpu_dpm_mode=self._gpu_dpm_mode(),
            suspend_blocked=suspend_blocked,
            display_blank_blocked=display_blank_blocked,
            suspend_minutes=(
                0 if suspend_blocked else suspend_seconds // 60
                if suspend_seconds is not None and suspend_seconds > 0 else None
            ),
            display_minutes=(
                0 if display_blank_blocked else display_seconds // 60
                if display_seconds is not None and display_seconds > 0 else None
            ),
        )

    def _set_many(self, settings: Sequence[tuple[str, str, str]]) -> CommandResult:
        output: list[str] = []
        for schema, key, value in settings:
            result = self.runner(["gsettings", "set", schema, key, value])
            if not result.ok:
                return result
            if result.stdout:
                output.append(result.stdout)
        return CommandResult(True, "\n".join(output), "", 0)

    def set_suspend_blocked(self, blocked: bool) -> CommandResult:
        if self._is_kde():
            # This convenience toggle uses a documented 30-minute resume default.
            return self.set_suspend_timeout(0 if blocked else KDE_DEFAULT_SUSPEND_MINUTES)
        value = "nothing" if blocked else "suspend"
        return self._set_many(
            (
                (POWER_SCHEMA, "sleep-inactive-ac-type", value),
                (POWER_SCHEMA, "sleep-inactive-battery-type", value),
            )
        )

    @staticmethod
    def _validate_timeout_minutes(minutes: int) -> int:
        minutes = int(minutes)
        if not 0 <= minutes <= 240:
            raise ValueError("timeout minutes must be between 0 and 240")
        return minutes

    def set_suspend_timeout(self, minutes: int) -> CommandResult:
        minutes = self._validate_timeout_minutes(minutes)
        if self._is_kde():
            return self._kde_set_suspend(minutes)
        mode = "nothing" if minutes == 0 else "suspend"
        seconds = str(minutes * 60)
        return self._set_many(
            (
                (POWER_SCHEMA, "sleep-inactive-ac-type", mode),
                (POWER_SCHEMA, "sleep-inactive-battery-type", mode),
                (POWER_SCHEMA, "sleep-inactive-ac-timeout", seconds),
                (POWER_SCHEMA, "sleep-inactive-battery-timeout", seconds),
            )
        )

    def set_display_timeout(self, minutes: int) -> CommandResult:
        minutes = self._validate_timeout_minutes(minutes)
        if self._is_kde():
            return self._kde_set_display(minutes)
        idle_delay, screensaver, idle_dim = (
            ("uint32 0", "false", "false")
            if minutes == 0
            else (f"uint32 {minutes * 60}", "true", "true")
        )
        return self._set_many(
            (
                (SESSION_SCHEMA, "idle-delay", idle_delay),
                (SCREENSAVER_SCHEMA, "idle-activation-enabled", screensaver),
                (POWER_SCHEMA, "idle-dim", idle_dim),
            )
        )

    def set_display_blank_blocked(self, blocked: bool) -> CommandResult:
        return self.set_display_timeout(0 if blocked else 5)
