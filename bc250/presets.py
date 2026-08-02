from __future__ import annotations

from dataclasses import dataclass

from .messages import MessageError


@dataclass(frozen=True, slots=True)
class PerformancePreset:
    key: str
    label_key: str
    description_key: str
    min_mhz: int
    max_mhz: int
    max_mv: int


PRESETS: dict[str, PerformancePreset] = {
    "eco": PerformancePreset("eco", "preset.eco", "preset.eco_detail", 500, 1500, 900),
    "balanced": PerformancePreset("balanced", "preset.balanced", "preset.balanced_detail", 500, 1700, 920),
    "performance": PerformancePreset("performance", "preset.performance", "preset.performance_detail", 500, 1800, 930),
}

CU_MASKS: dict[int, int] = {24: 0x07, 32: 0x0F, 40: 0x1F}
UINT32_MAX = (1 << 32) - 1
MIN_TEMPERATURE_C = 0
MAX_TEMPERATURE_C = 255


def get_preset(key: str) -> PerformancePreset:
    try:
        return PRESETS[key]
    except KeyError as exc:
        raise MessageError("error.invalid_preset") from exc


def cu_mask_csv(cu_count: int) -> str:
    try:
        mask = CU_MASKS[int(cu_count)]
    except (KeyError, TypeError, ValueError) as exc:
        raise MessageError("error.invalid_cu") from exc
    return ",".join(f"0x{mask:02x}" for _ in range(4))


def validate_temperature(throttle: int, recovery: int) -> tuple[int, int]:
    throttle = int(throttle)
    recovery = int(recovery)
    if not MIN_TEMPERATURE_C <= throttle <= MAX_TEMPERATURE_C:
        raise MessageError("error.invalid_throttle")
    if not MIN_TEMPERATURE_C <= recovery <= MAX_TEMPERATURE_C:
        raise MessageError("error.invalid_recovery_gap")
    return throttle, recovery


def validate_frequency_range(min_mhz: int, max_mhz: int) -> tuple[int, int]:
    min_mhz = int(min_mhz)
    max_mhz = int(max_mhz)
    if not 0 <= min_mhz <= UINT32_MAX:
        raise MessageError("error.invalid_frequency_range")
    if not 0 <= max_mhz <= UINT32_MAX:
        raise MessageError("error.invalid_frequency_range")
    if min_mhz and max_mhz and min_mhz > max_mhz:
        raise MessageError("error.invalid_frequency_range")
    return min_mhz, max_mhz
