#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


PRESETS = {"eco": 1500, "balanced": 1700, "performance": 1800}
PRESET_VOLTAGES = {"eco": 900, "balanced": 920, "performance": 930}
CU_MASKS = {24: "0x07,0x07,0x07,0x07", 32: "0x0f,0x0f,0x0f,0x0f", 40: "0x1f,0x1f,0x1f,0x1f"}
UINT32_MAX = (1 << 32) - 1
CPU_MODE_CONFIG = "/etc/bc250-custom-pannel-cpu.conf"
CPU_SYS_ROOT = "/sys/devices/system/cpu"
CPU_RECOVERY_STATE = "/var/lib/bc250-custom-pannel/cpu-recovery.json"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
BASE_WGP_MASK = 0x07
FULL_WGP_MASK = 0x1F


def _result(ok: bool, message_id: str, message: str, message_args: dict | None = None, **extra) -> dict:
    return {
        "ok": ok,
        "message_id": message_id,
        "message_args": message_args or {},
        "message": message,
        **extra,
    }


def _validate_temperature(throttle: int, recovery: int) -> tuple[int, int]:
    throttle = int(throttle)
    recovery = int(recovery)
    if not 0 <= throttle <= 255 or not 0 <= recovery <= 255:
        raise ValueError("Temperature values must be between 0 and 255.")
    return throttle, recovery


def _validate_frequency_range(min_mhz: int, max_mhz: int) -> tuple[int, int]:
    min_mhz = int(min_mhz)
    max_mhz = int(max_mhz)
    if not 0 <= min_mhz <= UINT32_MAX or not 0 <= max_mhz <= UINT32_MAX:
        raise ValueError("GPU clock values must be unsigned 32-bit integers.")
    if min_mhz and max_mhz and min_mhz > max_mhz:
        raise ValueError("GPU clock minimum cannot exceed the maximum when both bounds are set.")
    return min_mhz, max_mhz


def _validate_u32(value: int, label: str) -> int:
    value = int(value)
    if not 0 <= value <= UINT32_MAX:
        raise ValueError(f"{label} must be an unsigned 32-bit integer.")
    return value


def _validate_wgp_masks(value) -> tuple[int, int, int, int]:
    items = value.split(",") if isinstance(value, str) else tuple(value)
    if len(items) != 4:
        raise ValueError("Exactly four WGP row masks are required.")
    try:
        masks = tuple(int(str(item).strip(), 0) if isinstance(item, str) else int(item) for item in items)
    except (TypeError, ValueError) as exc:
        raise ValueError("WGP masks must be integers.") from exc
    if any(mask < 0 or mask > FULL_WGP_MASK for mask in masks):
        raise ValueError("WGP masks must be between 0x00 and 0x1f.")
    if any(mask & BASE_WGP_MASK != BASE_WGP_MASK for mask in masks):
        raise ValueError("The factory WGP0-WGP2 base cores must remain enabled.")
    return masks  # type: ignore[return-value]


def _mask_csv(masks) -> str:
    return ",".join(f"0x{mask:02x}" for mask in _validate_wgp_masks(masks))


def _rooted(root: Path, absolute: str) -> Path:
    if not absolute.startswith("/"):
        raise ValueError("고정된 절대 경로만 허용됩니다.")
    return root / absolute.lstrip("/")


def _atomic_write(path: Path, content: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup = path.with_suffix(path.suffix + ".bc250-backup")
    if path.exists():
        shutil.copy2(path, backup)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return backup


def _replace_toml_values(text: str, updates: dict[str, dict[str, str]]) -> str:
    current = ""
    seen: set[tuple[str, str]] = set()
    output: list[str] = []
    for line in text.splitlines(keepends=True):
        section = re.match(r"^\s*\[([^]]+)]\s*$", line)
        if section:
            current = section.group(1)
            output.append(line)
            continue
        key_match = re.match(r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)([^#\r\n]*)(.*)$", line)
        if key_match and current in updates and key_match.group(2) in updates[current]:
            key = key_match.group(2)
            newline = "\n" if line.endswith("\n") else ""
            output.append(f"{key_match.group(1)}{key}{key_match.group(3)}{updates[current][key]}{key_match.group(5).rstrip()}" + newline)
            seen.add((current, key))
        else:
            output.append(line)
    required = {(section, key) for section, values in updates.items() for key in values}
    missing = required - seen
    if missing:
        names = ", ".join(f"[{section}] {key}" for section, key in sorted(missing))
        raise ValueError(f"설정 항목을 찾지 못했습니다: {names}")
    return "".join(output)


def _ensure_toml_value(text: str, section: str, key: str, value: str) -> str:
    section_match = re.search(rf"(?m)^\s*\[{re.escape(section)}]\s*$", text)
    if not section_match:
        return f"{text.rstrip()}\n\n[{section}]\n{key} = {value}\n"
    remainder = text[section_match.end():]
    next_section = re.search(r"(?m)^\s*\[[^]]+]\s*$", remainder)
    insert_at = section_match.end() + (next_section.start() if next_section else len(remainder))
    section_text = text[section_match.end():insert_at]
    if re.search(rf"(?m)^\s*{re.escape(key)}\s*=", section_text):
        return text
    prefix = "" if text[:insert_at].endswith("\n") else "\n"
    return f"{text[:insert_at]}{prefix}{key} = {value}\n{text[insert_at:]}"


def _save_governor_values(
    min_mhz: int,
    max_mhz: int,
    throttle: int,
    recovery: int,
    root: Path,
    max_mv: int = 0,
    restart_service: bool = True,
) -> dict:
    min_mhz, max_mhz = _validate_frequency_range(min_mhz, max_mhz)
    max_mv = _validate_u32(max_mv, "GPU voltage limit")
    throttle, recovery = _validate_temperature(throttle, recovery)
    path = _rooted(root, "/etc/cyan-skillfish-governor-smu/config.toml")
    text = path.read_text(encoding="utf-8")
    text = _ensure_toml_value(text, "gpu", "voltage-limit", str(max_mv))
    updated = _replace_toml_values(
        text,
        {
            "frequency-range": {"min": str(min_mhz), "max": str(max_mhz)},
            "temperature": {"throttling": str(throttle), "throttling_recovery": str(recovery)},
            "gpu": {"voltage-limit": str(max_mv)},
        },
    )
    backup = _atomic_write(path, updated)
    if root == Path("/") and restart_service:
        service = subprocess.run(
            ["systemctl", "restart", "cyan-skillfish-governor-smu.service"],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        if service.returncode != 0:
            shutil.copy2(backup, path)
            return _result(False, "helper.governor_restart_failed", f"거버너 재시작 실패: {service.stderr.strip()}")
    return _result(True, "helper.governor_saved", "거버너 설정을 저장했습니다.", backup=str(backup))


def _save_governor(args: dict, root: Path) -> dict:
    preset = str(args.get("preset", ""))
    if preset not in PRESETS:
        raise ValueError("허용되지 않은 성능 프리셋입니다.")
    return _save_governor_values(
        500,
        PRESETS[preset],
        args["throttle"],
        args["recovery"],
        root,
        PRESET_VOLTAGES[preset],
    )


def _save_custom_governor(args: dict, root: Path) -> dict:
    return _save_governor_values(
        args["min_mhz"],
        args["max_mhz"],
        args["throttle"],
        args["recovery"],
        root,
        int(args.get("max_mv") or 0),
    )


def _save_cu_masks(masks, root: Path) -> dict:
    masks = _validate_wgp_masks(masks)
    csv = _mask_csv(masks)
    cu_count = sum(mask.bit_count() * 2 for mask in masks)
    path = _rooted(root, "/etc/bc250-cu-live-manager.conf")
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"^BC250_WGP_MASKS=.*$",
        f"BC250_WGP_MASKS={csv}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError("BC250_WGP_MASKS was not found in the CU boot profile.")
    backup = _atomic_write(path, updated)
    return _result(
        True,
        "helper.cu_saved",
        f"Saved {cu_count} CU boot profile.",
        {"count": cu_count},
        backup=str(backup),
        masks=csv,
    )


def _save_cu(args: dict, root: Path) -> dict:
    cu = int(args.get("cu", 0))
    if cu in CU_MASKS:
        return {**_save_cu_masks(CU_MASKS[cu], root), "reboot_required": True}
    raise ValueError("CU profile must be 24, 32, or 40.")


def _parse_cpu_list(value: str) -> tuple[int, ...]:
    result: set[int] = set()
    for token in value.strip().split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            first, last = (int(item) for item in token.split("-", 1))
            if first < 0 or last < first:
                raise ValueError(f"invalid CPU range: {token}")
            result.update(range(first, last + 1))
        else:
            cpu_id = int(token)
            if cpu_id < 0:
                raise ValueError(f"invalid CPU id: {token}")
            result.add(cpu_id)
    if not result:
        raise ValueError("no present CPUs were reported")
    return tuple(sorted(result))


def _cpu_core_groups(cpu_root: Path) -> list[tuple[tuple[int, int], tuple[int, ...]]]:
    present = _parse_cpu_list((cpu_root / "present").read_text(encoding="utf-8"))
    grouped: dict[tuple[int, int], list[int]] = {}
    for cpu_id in present:
        topology = cpu_root / f"cpu{cpu_id}" / "topology"
        package = int((topology / "physical_package_id").read_text(encoding="utf-8").strip())
        core = int((topology / "core_id").read_text(encoding="utf-8").strip())
        grouped.setdefault((package, core), []).append(cpu_id)
    return [(key, tuple(sorted(cpu_ids))) for key, cpu_ids in sorted(grouped.items())]


def _write_cpu_online(path: Path, online: bool) -> None:
    value = "1" if online else "0"
    try:
        path.write_text(value + "\n", encoding="utf-8")
        observed = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise OSError(f"CPU hotplug write failed at {path}: {exc}") from exc
    if observed != value:
        raise OSError(f"CPU hotplug state did not change at {path}: expected {value}, got {observed}")


def _apply_cpu_online_mode(enabled: bool, root: Path) -> dict:
    cpu_root = _rooted(root, CPU_SYS_ROOT)
    groups = _cpu_core_groups(cpu_root)
    thread_count = sum(len(cpu_ids) for _key, cpu_ids in groups)
    if thread_count < 16:
        if not enabled:
            return _result(True, "helper.cpu_mode_disabled", "CPU extra cores are disabled.", enabled=False)
        return _unlock_cpu(root)
    if len(groups) != 8 or thread_count != 16:
        raise ValueError(f"unexpected BC-250 CPU topology: {len(groups)} cores / {thread_count} threads")

    extra_cpu_ids = tuple(cpu_id for _key, cpu_ids in groups[-2:] for cpu_id in cpu_ids)
    changed: list[tuple[Path, bool]] = []
    try:
        for cpu_id in extra_cpu_ids:
            path = cpu_root / f"cpu{cpu_id}" / "online"
            before = path.read_text(encoding="utf-8").strip() == "1"
            if before == enabled:
                continue
            _write_cpu_online(path, enabled)
            changed.append((path, before))
    except (OSError, ValueError):
        for path, before in reversed(changed):
            try:
                _write_cpu_online(path, before)
            except OSError:
                pass
        raise
    return _result(
        True,
        "helper.cpu_mode_enabled" if enabled else "helper.cpu_mode_disabled",
        "CPU extra cores are enabled." if enabled else "CPU extra cores are disabled.",
        enabled=enabled,
        reboot_required=False,
    )


def _parse_enabled(value) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    raise ValueError(f"invalid CPU mode: {value}")


def _save_cpu_mode(enabled: bool, root: Path) -> dict:
    result = _apply_cpu_online_mode(enabled, root)
    if not result.get("ok"):
        return result
    _atomic_write(
        _rooted(root, CPU_MODE_CONFIG),
        f"CPU_EXTRA_CORES={'on' if enabled else 'off'}\n",
    )
    if enabled and result.get("reboot_required"):
        _write_cpu_recovery_state(
            root,
            {"phase": "armed", "boot_id": _read_boot_id(root), "message": "CPU unlock armed"},
        )
    return result


def _read_saved_cpu_mode(root: Path) -> bool | None:
    path = _rooted(root, CPU_MODE_CONFIG)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"^CPU_EXTRA_CORES=(.+)$", text, re.MULTILINE)
    if not match or match.group(1).strip().lower() == "auto":
        return None
    return _parse_enabled(match.group(1))


def _read_boot_id(root: Path) -> str:
    try:
        value = _rooted(root, BOOT_ID_PATH).read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    return value or "unknown"


def _read_cpu_recovery_state(root: Path) -> dict:
    try:
        payload = json.loads(_rooted(root, CPU_RECOVERY_STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cpu_recovery_state(root: Path, payload: dict) -> None:
    _atomic_write(
        _rooted(root, CPU_RECOVERY_STATE),
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _read_cpu_unlock_mask(root: Path) -> int | None:
    manager = _rooted(root, "/usr/local/bin/bc250-cu-live-manager")
    if not manager.is_file() or not os.access(manager, os.X_OK):
        return None
    completed = subprocess.run(
        [str(manager), "--yes", "cpu-mask"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        return None
    matches = re.findall(r"0x([0-9a-fA-F]{1,8})", completed.stdout or "")
    return int(matches[-1], 16) & 0xFF if matches else None


def _schedule_warm_reboot(root: Path) -> None:
    if root != Path("/"):
        return
    completed = subprocess.run(
        ["systemctl", "reboot", "--no-wall", "--no-block"],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "failed to schedule warm reboot")


def _apply_cpu_boot_recovery(enabled: bool, root: Path) -> dict:
    if not enabled:
        return _apply_cpu_online_mode(False, root)

    groups = _cpu_core_groups(_rooted(root, CPU_SYS_ROOT))
    thread_count = sum(len(cpu_ids) for _key, cpu_ids in groups)
    boot_id = _read_boot_id(root)
    if thread_count >= 16:
        result = _apply_cpu_online_mode(True, root)
        _write_cpu_recovery_state(
            root,
            {"phase": "active", "boot_id": boot_id, "threads": thread_count},
        )
        return result

    mask = _read_cpu_unlock_mask(root)
    state = _read_cpu_recovery_state(root)
    if mask == 0x77:
        unlocked = _unlock_cpu(root)
        if not unlocked.get("ok"):
            return unlocked
        _write_cpu_recovery_state(
            root,
            {"phase": "armed", "boot_id": boot_id, "mask": "0x77"},
        )
        _schedule_warm_reboot(root)
        return _result(
            True,
            "helper.cpu_recovery_reboot",
            "CPU unlock was armed after cold boot; one warm reboot was scheduled.",
            reboot_required=True,
        )

    if mask == 0xFF and state.get("phase") == "armed" and state.get("boot_id") != boot_id:
        _write_cpu_recovery_state(
            root,
            {"phase": "failed", "boot_id": boot_id, "mask": "0xFF", "threads": thread_count},
        )
        return _result(
            True,
            "helper.cpu_recovery_failed",
            "CPU unlock remained at 6C/12T after the one-time warm reboot; automatic reboot was stopped.",
            enabled=True,
            reboot_required=False,
        )

    if mask == 0xFF and state.get("phase") == "armed" and state.get("boot_id") == boot_id:
        return _result(
            True,
            "helper.cpu_recovery_pending",
            "CPU unlock is armed and the warm reboot is already pending.",
            reboot_required=True,
        )

    if mask == 0xFF and not state:
        _write_cpu_recovery_state(root, {"phase": "armed", "boot_id": boot_id, "mask": "0xFF"})
        _schedule_warm_reboot(root)
        return _result(
            True,
            "helper.cpu_recovery_reboot",
            "An existing CPU unlock was found; one warm reboot was scheduled.",
            reboot_required=True,
        )

    _write_cpu_recovery_state(
        root,
        {"phase": "failed", "boot_id": boot_id, "mask": None if mask is None else f"0x{mask:02X}"},
    )
    return _result(
        True,
        "helper.cpu_recovery_failed",
        "CPU unlock mask could not be verified; automatic reboot was not attempted.",
        enabled=True,
        reboot_required=False,
    )


def _apply_saved_cpu_mode(root: Path, boot: bool = False) -> dict:
    enabled = _read_saved_cpu_mode(root)
    if enabled is None:
        return _result(
            True,
            "helper.cpu_mode_unchanged",
            "CPU mode is unchanged until the user selects a mode.",
            enabled=None,
        )
    return _apply_cpu_boot_recovery(enabled, root) if boot else _apply_cpu_online_mode(enabled, root)


def _unlock_cpu(root: Path) -> dict:
    manager = _rooted(root, "/usr/local/bin/bc250-cu-live-manager")
    if not manager.is_file() or not os.access(manager, os.X_OK):
        raise ValueError("CPU 언락 관리자를 찾을 수 없습니다.")
    completed = subprocess.run(
        [str(manager), "--yes", "cpu-unlock"],
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    diagnostic = (completed.stderr or completed.stdout).strip()
    if completed.returncode != 0:
        return _result(False, "helper.cpu_unlock_failed", f"CPU 코어 언락 실패: {diagnostic or 'unknown error'}")
    return _result(
        True,
        "helper.cpu_unlock_armed",
        "CPU 코어 언락을 예약했습니다. 재부팅 후 8코어/16스레드가 적용됩니다.",
        reboot_required=True,
    )


def _apply_governor_runtime(
    min_mhz: int,
    max_mhz: int,
    max_mv: int,
    throttle: int,
    recovery: int,
) -> dict:
    completed = subprocess.run(
        [
            "busctl",
            "--system",
            "call",
            "com.cyanskillfish.Governor",
            "/com/cyanskillfish/Governor",
            "com.cyanskillfish.Governor.PerformanceMode",
            "SetTuningWithVoltage",
            "uuuuu",
            str(min_mhz),
            str(max_mhz),
            str(max_mv),
            str(throttle),
            str(recovery),
        ],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        return _result(
            False,
            "helper.governor_apply_failed",
            completed.stderr.strip() or completed.stdout.strip() or "Governor rejected the settings.",
        )
    return _result(True, "helper.governor_applied", "Governor settings applied.")


def _apply_cu_masks(masks, root: Path) -> dict:
    csv = _mask_csv(masks)
    manager = _rooted(root, "/usr/local/bin/bc250-cu-live-manager")
    if not manager.is_file() or not os.access(manager, os.X_OK):
        raise ValueError("CU live manager was not found.")
    completed = subprocess.run(
        [str(manager), "--yes", "apply-masks", csv],
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    diagnostic = (completed.stderr or completed.stdout).strip()
    if completed.returncode != 0:
        return _result(False, "helper.cu_apply_failed", diagnostic or "CU register readback failed.")
    return _result(
        True,
        "helper.cu_applied",
        diagnostic or "CU masks applied and verified.",
        masks=csv,
        count=sum(mask.bit_count() * 2 for mask in _validate_wgp_masks(masks)),
    )


def _apply_all(args: dict, root: Path) -> dict:
    min_mhz, max_mhz = _validate_frequency_range(args["min_mhz"], args["max_mhz"])
    max_mv = _validate_u32(args["max_mv"], "GPU voltage limit")
    throttle, recovery = _validate_temperature(args["throttle"], args["recovery"])
    cpu_enabled = _parse_enabled(args["cpu_extra_cores"])
    masks = _validate_wgp_masks(args["cu_masks"])
    persist = _parse_enabled(args["persist"])

    governor = _apply_governor_runtime(min_mhz, max_mhz, max_mv, throttle, recovery)
    if not governor.get("ok"):
        return governor
    cu = _apply_cu_masks(masks, root)
    if not cu.get("ok"):
        return cu
    cpu = _save_cpu_mode(cpu_enabled, root) if persist else _apply_cpu_online_mode(cpu_enabled, root)
    if not cpu.get("ok"):
        return cpu

    saved: list[str] = []
    if persist:
        governor_saved = _save_governor_values(
            min_mhz,
            max_mhz,
            throttle,
            recovery,
            root,
            max_mv,
            restart_service=False,
        )
        if not governor_saved.get("ok"):
            return governor_saved
        cu_saved = _save_cu_masks(masks, root)
        saved.extend((str(governor_saved.get("backup", "")), str(cu_saved.get("backup", ""))))

    return _result(
        True,
        "helper.all_saved" if persist else "helper.all_applied",
        "All hardware settings were applied and saved." if persist else "All hardware settings were applied.",
        persist=persist,
        cu_masks=_mask_csv(masks),
        cu_count=sum(mask.bit_count() * 2 for mask in masks),
        cpu_extra_cores=cpu_enabled,
        reboot_required=bool(cpu.get("reboot_required")),
        saved=[item for item in saved if item],
    )


def _restore(args: dict, root: Path) -> dict:
    target = str(args.get("target", ""))
    allowed = {
        "governor": "/etc/cyan-skillfish-governor-smu/config.toml",
        "cu": "/etc/bc250-cu-live-manager.conf",
    }
    if target not in allowed:
        raise ValueError("복원 대상은 governor 또는 cu만 허용됩니다.")
    path = _rooted(root, allowed[target])
    backup = path.with_suffix(path.suffix + ".bc250-backup")
    if not backup.is_file():
        raise ValueError("복원할 백업이 없습니다.")
    shutil.copy2(backup, path)
    return _result(True, "helper.backup_restored", "백업 설정을 복원했습니다.")


def run_action(action: str, args: dict, root: Path = Path("/")) -> dict:
    root = Path(root).resolve()
    if root == Path("/") and os.geteuid() != 0:
        return _result(False, "helper.auth_required", "이 작업은 시스템 인증이 필요합니다.")
    try:
        if action == "save-governor":
            return _save_governor(args, root)
        if action == "save-governor-custom":
            return _save_custom_governor(args, root)
        if action == "save-cu":
            return _save_cu(args, root)
        if action == "apply-all":
            return _apply_all(args, root)
        if action == "unlock-cpu":
            return _unlock_cpu(root)
        if action == "set-cpu-mode":
            return _save_cpu_mode(_parse_enabled(args.get("enabled")), root)
        if action == "apply-cpu-mode":
            return _apply_saved_cpu_mode(root, bool(args.get("boot")))
        if action == "restore-backup":
            return _restore(args, root)
        return _result(False, "helper.action_invalid", "허용되지 않은 작업입니다.")
    except (KeyError, OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        return _result(False, "dialog.operation_failed", str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description="BC-250 제한 권한 설정 도우미")
    parser.add_argument(
        "action",
        choices=(
            "save-governor",
            "save-governor-custom",
            "save-cu",
            "apply-all",
            "unlock-cpu",
            "set-cpu-mode",
            "apply-cpu-mode",
            "restore-backup",
        ),
    )
    parser.add_argument("--preset")
    parser.add_argument("--throttle", type=int)
    parser.add_argument("--recovery", type=int)
    parser.add_argument("--min-mhz", type=int)
    parser.add_argument("--max-mhz", type=int)
    parser.add_argument("--max-mv", type=int)
    parser.add_argument("--cu", type=int)
    parser.add_argument("--cu-masks")
    parser.add_argument("--cpu-extra-cores")
    parser.add_argument("--persist")
    parser.add_argument("--boot", action="store_true")
    parser.add_argument("--enabled")
    parser.add_argument("--target")
    parser.add_argument("--root", type=Path, default=Path("/"), help=argparse.SUPPRESS)
    ns = parser.parse_args()
    payload = run_action(
        ns.action,
        {
            "preset": ns.preset,
            "throttle": ns.throttle,
            "recovery": ns.recovery,
            "min_mhz": ns.min_mhz,
            "max_mhz": ns.max_mhz,
            "max_mv": ns.max_mv,
            "cu": ns.cu,
            "cu_masks": ns.cu_masks,
            "cpu_extra_cores": ns.cpu_extra_cores,
            "persist": ns.persist,
            "boot": ns.boot,
            "enabled": ns.enabled,
            "target": ns.target,
        },
        ns.root,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
