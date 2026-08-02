from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .control import CommandResult, run_command


POWER_SCHEMA = "org.gnome.settings-daemon.plugins.power"
SESSION_SCHEMA = "org.gnome.desktop.session"
SCREENSAVER_SCHEMA = "org.gnome.desktop.screensaver"


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
    ) -> None:
        self.runner = runner
        self.cpuidle_driver = Path(cpuidle_driver)
        self.cpuinfo = Path(cpuinfo)
        self.drm_root = Path(drm_root)

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
