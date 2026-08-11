from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .messages import MessageError
from .presets import UINT32_MAX, validate_frequency_range, validate_temperature


BASE_WGP_MASK = 0x07
FULL_WGP_MASK = 0x1F
WGP_ROWS = 4


def validate_u32(value: int, error_key: str) -> int:
    value = int(value)
    if not 0 <= value <= UINT32_MAX:
        raise MessageError(error_key)
    return value


def validate_wgp_masks(values: Iterable[int]) -> tuple[int, int, int, int]:
    try:
        masks = tuple(int(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise MessageError("error.invalid_cu_masks") from exc
    if len(masks) != WGP_ROWS:
        raise MessageError("error.invalid_cu_masks")
    if any(mask < 0 or mask > FULL_WGP_MASK for mask in masks):
        raise MessageError("error.invalid_cu_masks")
    if any(mask & BASE_WGP_MASK != BASE_WGP_MASK for mask in masks):
        raise MessageError("error.base_cu_required")
    return masks  # type: ignore[return-value]


def masks_to_csv(values: Iterable[int]) -> str:
    masks = validate_wgp_masks(values)
    return ",".join(f"0x{mask:02x}" for mask in masks)


def masks_from_csv(value: str) -> tuple[int, int, int, int]:
    try:
        return validate_wgp_masks(int(item.strip(), 0) for item in str(value).split(","))
    except ValueError as exc:
        raise MessageError("error.invalid_cu_masks") from exc


def validate_power_minutes(value: int) -> int:
    value = int(value)
    if not 0 <= value <= 240:
        raise MessageError("error.invalid_power_timeout")
    return value


@dataclass(frozen=True, slots=True)
class DraftSettings:
    min_mhz: int
    max_mhz: int
    max_mv: int
    throttle: int
    recovery: int
    cpu_extra_cores: bool
    cu_masks: tuple[int, int, int, int]
    suspend_minutes: int
    display_minutes: int

    def __post_init__(self) -> None:
        min_mhz, max_mhz = validate_frequency_range(self.min_mhz, self.max_mhz)
        throttle, recovery = validate_temperature(self.throttle, self.recovery)
        object.__setattr__(self, "min_mhz", min_mhz)
        object.__setattr__(self, "max_mhz", max_mhz)
        object.__setattr__(self, "max_mv", validate_u32(self.max_mv, "error.invalid_voltage"))
        object.__setattr__(self, "throttle", throttle)
        object.__setattr__(self, "recovery", recovery)
        object.__setattr__(self, "cpu_extra_cores", bool(self.cpu_extra_cores))
        object.__setattr__(self, "cu_masks", validate_wgp_masks(self.cu_masks))
        object.__setattr__(self, "suspend_minutes", validate_power_minutes(self.suspend_minutes))
        object.__setattr__(self, "display_minutes", validate_power_minutes(self.display_minutes))

    @property
    def cu_count(self) -> int:
        return sum(mask.bit_count() * 2 for mask in self.cu_masks)

    @property
    def cu_masks_csv(self) -> str:
        return masks_to_csv(self.cu_masks)
