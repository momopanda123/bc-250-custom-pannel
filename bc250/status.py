from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from .messages import UserMessage


UNKNOWN = "—"
CU_SUCCESS = re.compile(r"^\[ OK \] dispatch registers updated \((24|32|40)/40 CUs target\)$", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class SystemInfo:
    bios_vendor: str = UNKNOWN
    bios_version: str = UNKNOWN
    bios_date: str = UNKNOWN
    kernel_release: str = UNKNOWN
    architecture: str = UNKNOWN


@dataclass(frozen=True, slots=True)
class FanReading:
    rpm: int | None
    label: str
    active_count: int
    state: str


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    collected_at: datetime
    cu_count: int | None
    cu_saved_count: int | None
    cu_state: UserMessage | str
    cu_service: str
    governor_service: str
    governor_min: int | None
    governor_max: int | None
    throttle: int | None
    recovery: int | None
    gpu_temperature: str
    cpu_temperature: str
    cpu_temperature_source: str
    fan: FanReading
    power: str
    voltage: str
    clock: str
    system: SystemInfo
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    errors: tuple[UserMessage, ...] = field(default_factory=tuple)


def _format_scaled(raw: str | None, divisor: int, decimals: int, suffix: str) -> str:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return UNKNOWN
    return f"{value / divisor:.{decimals}f} {suffix}"


def format_temperature(raw: str | None) -> str:
    return _format_scaled(raw, 1000, 1, "°C")


def format_power(raw: str | None) -> str:
    return _format_scaled(raw, 1_000_000, 1, "W")


def format_voltage(raw: str | None) -> str:
    return _format_scaled(raw, 1, 0, "mV")


def format_clock(raw: str | None) -> str:
    return _format_scaled(raw, 1_000_000, 0, "MHz")


def parse_cu_result(journal: str) -> int | None:
    match = CU_SUCCESS.search(journal or "")
    return int(match.group(1)) if match else None


def masks_to_cu_count(csv: str | None) -> int | None:
    if not csv:
        return None
    items = [item.strip() for item in csv.split(",")]
    if len(items) != 4:
        return None
    try:
        masks = [int(item, 0) for item in items]
    except ValueError:
        return None
    if any(mask < 0 or mask > 0x1F for mask in masks):
        return None
    return sum(mask.bit_count() * 2 for mask in masks)


def read_saved_masks(path: Path = Path("/etc/bc250-cu-live-manager.conf")) -> tuple[str | None, int | None]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    match = re.search(r"^BC250_WGP_MASKS=(.+)$", text, re.MULTILINE)
    if not match:
        return None, None
    value = match.group(1).strip().strip('"')
    return value, masks_to_cu_count(value)


def _read_text(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return UNKNOWN
    return value or UNKNOWN


def read_system_info(
    dmi_root: Path = Path("/sys/class/dmi/id"),
    uname: tuple[str, str] | None = None,
) -> SystemInfo:
    kernel_release, architecture = uname or (platform.release(), platform.machine())
    return SystemInfo(
        bios_vendor=_read_text(dmi_root / "bios_vendor"),
        bios_version=_read_text(dmi_root / "bios_version"),
        bios_date=_read_text(dmi_root / "bios_date"),
        kernel_release=kernel_release or UNKNOWN,
        architecture=architecture or UNKNOWN,
    )


def find_amdgpu_hwmon(drm_root: Path = Path("/sys/class/drm")) -> Path | None:
    for name_file in sorted(drm_root.glob("card*/device/hwmon/hwmon*/name")):
        if _read_text(name_file) == "amdgpu":
            return name_file.parent
    return None


def _named_hwmons(hwmon_root: Path, names: set[str]) -> list[Path]:
    return [
        name_file.parent
        for name_file in sorted(hwmon_root.glob("hwmon*/name"))
        if _read_text(name_file) in names
    ]


def _labeled_input(hwmon: Path, prefix: str, wanted: str) -> str | None:
    for label_file in sorted(hwmon.glob(f"{prefix}*_label")):
        if _read_text(label_file) != wanted:
            continue
        input_file = label_file.with_name(label_file.name.replace("_label", "_input"))
        value = _read_text(input_file)
        return None if value == UNKNOWN else value
    return None


def read_cpu_temperature(hwmon_root: Path = Path("/sys/class/hwmon")) -> tuple[str, str]:
    for hwmon in _named_hwmons(hwmon_root, {"k10temp"}):
        value = _labeled_input(hwmon, "temp", "Tctl")
        if value is not None:
            temperature = format_temperature(value)
            if temperature != UNKNOWN:
                return temperature, "Tctl"
    for hwmon in _named_hwmons(hwmon_root, {"nct6686", "nct6687"}):
        value = _labeled_input(hwmon, "temp", "CPU")
        if value is not None:
            temperature = format_temperature(value)
            if temperature != UNKNOWN:
                return temperature, "CPU"
    return UNKNOWN, ""


def _parse_cpu_list(value: str) -> tuple[int, ...]:
    cpu_ids: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start < 0 or end < start:
                    return ()
                cpu_ids.update(range(start, end + 1))
            else:
                cpu_id = int(token)
                if cpu_id < 0:
                    return ()
                cpu_ids.add(cpu_id)
        except ValueError:
            return ()
    return tuple(sorted(cpu_ids))


def read_cpu_topology(cpu_root: Path = Path("/sys/devices/system/cpu")) -> tuple[int | None, int | None]:
    online = _read_text(cpu_root / "online")
    source = online if online != UNKNOWN else _read_text(cpu_root / "present")
    cpu_ids = _parse_cpu_list(source) if source != UNKNOWN else ()
    if not cpu_ids:
        return None, None

    cores: set[tuple[str, str]] = set()
    for cpu_id in cpu_ids:
        topology = cpu_root / f"cpu{cpu_id}" / "topology"
        package_id = _read_text(topology / "physical_package_id")
        core_id = _read_text(topology / "core_id")
        if UNKNOWN in (package_id, core_id):
            continue
        cores.add((package_id, core_id))
    return (len(cores) or None), len(cpu_ids)


def read_active_fans(hwmon_root: Path = Path("/sys/class/hwmon")) -> FanReading:
    hwmons = _named_hwmons(hwmon_root, {"nct6686", "nct6687"})
    if not hwmons:
        return FanReading(None, "", 0, "unavailable")

    active: list[tuple[int, str]] = []
    usable_count = 0
    has_unusable_channel = False
    for hwmon in hwmons:
        for label_file in sorted(hwmon.glob("fan*_label")):
            input_file = label_file.with_name(label_file.name.replace("_label", "_input"))
            try:
                rpm = int(_read_text(input_file))
            except ValueError:
                has_unusable_channel = True
                continue
            if rpm < 0:
                has_unusable_channel = True
                continue
            usable_count += 1
            if rpm > 0:
                active.append((rpm, _read_text(label_file)))

    if not active:
        if not usable_count or has_unusable_channel:
            return FanReading(None, "", 0, "unavailable")
        return FanReading(0, "", 0, "stopped")
    rpm, label = max(active, key=lambda reading: reading[0])
    return FanReading(rpm, label, len(active), "active")


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), text=True, capture_output=True, timeout=4, check=False)


def _property_number(runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]], path: str, interface: str, name: str) -> int | None:
    result = runner(
        [
            "busctl",
            "--system",
            "get-property",
            "com.cyanskillfish.Governor",
            path,
            interface,
            name,
        ]
    )
    if result.returncode != 0:
        return None
    match = re.search(r"(-?\d+)\s*$", result.stdout)
    return int(match.group(1)) if match else None


class StatusCollector:
    def __init__(
        self,
        runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _default_runner,
        drm_root: Path = Path("/sys/class/drm"),
        dmi_root: Path = Path("/sys/class/dmi/id"),
        cu_config: Path = Path("/etc/bc250-cu-live-manager.conf"),
        hwmon_root: Path = Path("/sys/class/hwmon"),
        cpu_root: Path = Path("/sys/devices/system/cpu"),
    ) -> None:
        self.runner = runner
        self.drm_root = drm_root
        self.dmi_root = dmi_root
        self.cu_config = cu_config
        self.hwmon_root = hwmon_root
        self.cpu_root = cpu_root

    def _command_text(self, argv: Sequence[str]) -> str:
        try:
            result = self.runner(argv)
        except (OSError, subprocess.SubprocessError):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    def collect(self) -> StatusSnapshot:
        errors: list[UserMessage] = []
        journal = self._command_text(
            ["journalctl", "-b", "-u", "bc250-cu-live-manager.service", "--no-pager", "-o", "cat"]
        )
        applied_cu = parse_cu_result(journal)
        _, saved_cu = read_saved_masks(self.cu_config)
        if applied_cu is not None and saved_cu == applied_cu:
            cu_count = applied_cu
            cu_state = UserMessage("status.cu_applied", {"count": cu_count})
        elif applied_cu is not None:
            cu_count = applied_cu
            cu_state = UserMessage("status.cu_mismatch", {"current": applied_cu})
            errors.append(UserMessage("error.cu_mismatch"))
        else:
            cu_count = None
            cu_state = UNKNOWN

        hwmon = find_amdgpu_hwmon(self.drm_root)
        if hwmon is None:
            gpu_temperature = power = voltage = clock = UNKNOWN
            errors.append(UserMessage("error.amdgpu_missing"))
        else:
            gpu_temperature = format_temperature(_read_text(hwmon / "temp1_input"))
            power = format_power(_read_text(hwmon / "power1_average"))
            voltage = format_voltage(_read_text(hwmon / "in0_input"))
            clock = format_clock(_read_text(hwmon / "freq1_input"))

        cpu_temperature, cpu_temperature_source = read_cpu_temperature(self.hwmon_root)
        cpu_cores, cpu_threads = read_cpu_topology(self.cpu_root)
        fan = read_active_fans(self.hwmon_root)

        range_path = "/com/cyanskillfish/Governor/Range/Current"
        range_iface = "com.cyanskillfish.Governor.Range"
        main_path = "/com/cyanskillfish/Governor"
        main_iface = "com.cyanskillfish.Governor.PerformanceMode"
        return StatusSnapshot(
            collected_at=datetime.now().astimezone(),
            cu_count=cu_count,
            cu_saved_count=saved_cu,
            cu_state=cu_state,
            cu_service=self._command_text(["systemctl", "is-active", "bc250-cu-live-manager.service"]) or UNKNOWN,
            governor_service=self._command_text(["systemctl", "is-active", "cyan-skillfish-governor-smu.service"]) or UNKNOWN,
            governor_min=_property_number(self.runner, range_path, range_iface, "Min"),
            governor_max=_property_number(self.runner, range_path, range_iface, "Max"),
            throttle=_property_number(self.runner, main_path, main_iface, "TemperatureThrottling"),
            recovery=_property_number(self.runner, main_path, main_iface, "TemperatureRecovery"),
            gpu_temperature=gpu_temperature,
            cpu_temperature=cpu_temperature,
            cpu_temperature_source=cpu_temperature_source,
            fan=fan,
            power=power,
            voltage=voltage,
            clock=clock,
            system=read_system_info(self.dmi_root),
            cpu_cores=cpu_cores,
            cpu_threads=cpu_threads,
            errors=tuple(errors),
        )
