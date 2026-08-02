from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .presets import get_preset, validate_frequency_range, validate_temperature


@dataclass(frozen=True, slots=True)
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


def run_command(argv: Sequence[str]) -> CommandResult:
    try:
        result = subprocess.run(list(argv), text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(False, "", str(exc), 1)
    return CommandResult(result.returncode == 0, result.stdout.strip(), result.stderr.strip(), result.returncode)


class GovernorController:
    BUS = "com.cyanskillfish.Governor"
    PATH = "/com/cyanskillfish/Governor"
    INTERFACE = "com.cyanskillfish.Governor.PerformanceMode"

    def __init__(self, runner: Callable[[Sequence[str]], CommandResult] = run_command) -> None:
        self.runner = runner

    def _call(self, method: str, signature: str, *values: int) -> CommandResult:
        return self.runner(
            [
                "busctl",
                "--system",
                "call",
                self.BUS,
                self.PATH,
                self.INTERFACE,
                method,
                signature,
                *(str(value) for value in values),
            ]
        )

    def _apply_range(self, min_mhz: int, max_mhz: int, throttle: int, recovery: int) -> CommandResult:
        min_mhz, max_mhz = validate_frequency_range(min_mhz, max_mhz)
        throttle, recovery = validate_temperature(throttle, recovery)
        return self._call("SetTuning", "uuuu", min_mhz, max_mhz, throttle, recovery)

    def apply_runtime(self, preset_key: str, throttle: int, recovery: int) -> CommandResult:
        preset = get_preset(preset_key)
        return self._apply_range(preset.min_mhz, preset.max_mhz, throttle, recovery)

    def apply_custom(self, min_mhz: int, max_mhz: int, throttle: int, recovery: int) -> CommandResult:
        return self._apply_range(min_mhz, max_mhz, throttle, recovery)


class PrivilegedRunner:
    def __init__(self, project_root: Path, runner: Callable[[Sequence[str]], CommandResult] = run_command) -> None:
        self.project_root = Path(project_root).resolve()
        self.runner = runner

    @staticmethod
    def _json_result(result: CommandResult) -> tuple[CommandResult, dict]:
        if not result.stdout:
            return result, {
                "ok": False,
                "message_id": "error.no_response",
                "message_args": {},
                "message": result.stderr or "No response",
            }
        try:
            payload = json.loads(result.stdout.splitlines()[-1])
        except json.JSONDecodeError:
            return result, {"ok": False, "message": result.stderr or result.stdout}
        return result, payload

    def install(self) -> tuple[CommandResult, dict]:
        result = self.runner(
            ["pkexec", "python3", str(self.project_root / "bc250_install.py"), "install", "--project-root", str(self.project_root)]
        )
        return self._json_result(result)

    def _helper_command(self, action: str, *args: str) -> list[str]:
        return [
            "pkexec",
            "python3",
            str(self.project_root / "bc250_privileged.py"),
            action,
            *args,
        ]

    def save_settings(self, preset_key: str, throttle: int, recovery: int) -> tuple[CommandResult, dict]:
        get_preset(preset_key)
        validate_temperature(throttle, recovery)
        result = self.runner(
            self._helper_command(
                "save-governor",
                "--preset",
                preset_key,
                "--throttle",
                str(throttle),
                "--recovery",
                str(recovery),
            )
        )
        return self._json_result(result)

    def save_custom_settings(
        self,
        min_mhz: int,
        max_mhz: int,
        throttle: int,
        recovery: int,
    ) -> tuple[CommandResult, dict]:
        min_mhz, max_mhz = validate_frequency_range(min_mhz, max_mhz)
        validate_temperature(throttle, recovery)
        result = self.runner(
            self._helper_command(
                "save-governor-custom",
                "--min-mhz",
                str(min_mhz),
                "--max-mhz",
                str(max_mhz),
                "--throttle",
                str(throttle),
                "--recovery",
                str(recovery),
            )
        )
        return self._json_result(result)

    def save_cu(self, cu_count: int) -> tuple[CommandResult, dict]:
        result = self.runner(
            self._helper_command(
                "save-cu",
                "--cu",
                str(cu_count),
            )
        )
        return self._json_result(result)

    def unlock_cpu(self) -> tuple[CommandResult, dict]:
        result = self.runner(self._helper_command("unlock-cpu"))
        return self._json_result(result)

    def set_cpu_mode(self, enabled: bool) -> tuple[CommandResult, dict]:
        result = self.runner(
            self._helper_command(
                "set-cpu-mode",
                "--enabled",
                "on" if enabled else "off",
            )
        )
        return self._json_result(result)

    def install_then_set_cpu_mode(self, enabled: bool) -> tuple[CommandResult, dict]:
        result = self.runner(
            [
                "pkexec",
                "python3",
                str(self.project_root / "bc250_install.py"),
                "install",
                "--project-root",
                str(self.project_root),
                "--cpu-mode",
                "on" if enabled else "off",
            ]
        )
        return self._json_result(result)
