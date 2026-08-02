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
CU_MASKS = {24: "0x07,0x07,0x07,0x07", 32: "0x0f,0x0f,0x0f,0x0f", 40: "0x1f,0x1f,0x1f,0x1f"}
CPU_MODE_CONFIG = "/etc/bc250-custom-pannel-cpu.conf"
CPU_SYS_ROOT = "/sys/devices/system/cpu"


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
    if not 80 <= throttle <= 90 or not 5 <= throttle - recovery <= 15:
        raise ValueError("허용되지 않은 온도 범위입니다.")
    return throttle, recovery


def _validate_frequency_range(min_mhz: int, max_mhz: int) -> tuple[int, int]:
    min_mhz = int(min_mhz)
    max_mhz = int(max_mhz)
    if not 500 <= min_mhz <= 1800 or not 500 <= max_mhz <= 1800 or max_mhz - min_mhz < 100:
        raise ValueError("GPU 클럭 범위는 500~1800 MHz이며 최소 100 MHz 간격이 필요합니다.")
    return min_mhz, max_mhz


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


def _save_governor_values(min_mhz: int, max_mhz: int, throttle: int, recovery: int, root: Path) -> dict:
    min_mhz, max_mhz = _validate_frequency_range(min_mhz, max_mhz)
    throttle, recovery = _validate_temperature(throttle, recovery)
    path = _rooted(root, "/etc/cyan-skillfish-governor-smu/config.toml")
    text = path.read_text(encoding="utf-8")
    updated = _replace_toml_values(
        text,
        {
            "frequency-range": {"min": str(min_mhz), "max": str(max_mhz)},
            "temperature": {"throttling": str(throttle), "throttling_recovery": str(recovery)},
        },
    )
    backup = _atomic_write(path, updated)
    if root == Path("/"):
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
    return _save_governor_values(500, PRESETS[preset], args["throttle"], args["recovery"], root)


def _save_custom_governor(args: dict, root: Path) -> dict:
    return _save_governor_values(
        args["min_mhz"],
        args["max_mhz"],
        args["throttle"],
        args["recovery"],
        root,
    )


def _save_cu(args: dict, root: Path) -> dict:
    cu = int(args.get("cu", 0))
    if cu not in CU_MASKS:
        raise ValueError("CU 프로필은 24, 32, 40만 허용됩니다.")
    path = _rooted(root, "/etc/bc250-cu-live-manager.conf")
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"^BC250_WGP_MASKS=.*$",
        f"BC250_WGP_MASKS={CU_MASKS[cu]}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError("CU 부팅 마스크 항목을 찾지 못했습니다.")
    backup = _atomic_write(path, updated)
    return _result(
        True,
        "helper.cu_saved",
        f"다음 부팅용 {cu} CU 프로필을 저장했습니다.",
        {"count": cu},
        backup=str(backup),
        reboot_required=True,
    )


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


def _apply_saved_cpu_mode(root: Path) -> dict:
    enabled = _read_saved_cpu_mode(root)
    if enabled is None:
        return _result(
            True,
            "helper.cpu_mode_unchanged",
            "CPU mode is unchanged until the user selects a mode.",
            enabled=None,
        )
    return _apply_cpu_online_mode(enabled, root)


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
        if action == "unlock-cpu":
            return _unlock_cpu(root)
        if action == "set-cpu-mode":
            return _save_cpu_mode(_parse_enabled(args.get("enabled")), root)
        if action == "apply-cpu-mode":
            return _apply_saved_cpu_mode(root)
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
    parser.add_argument("--cu", type=int)
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
            "cu": ns.cu,
            "enabled": ns.enabled,
            "target": ns.target,
        },
        ns.root,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
