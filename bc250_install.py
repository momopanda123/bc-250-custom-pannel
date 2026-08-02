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

from bc250.bootstrap import detect_platform, load_manifest, verify_bundle
from bc250_privileged import run_action


EXTENDED_SAFE_POINTS = (
    (350, 700),
    (500, 700),
    (1000, 800),
    (1175, 850),
    (1500, 900),
    (1600, 910),
    (1700, 920),
    (1800, 930),
    (1850, 930),
    (2000, 960),
    (2050, 980),
    (2100, 1000),
    (2125, 1020),
    (2150, 1035),
    (2200, 1050),
    (2230, 1085),
    (2300, 1110),
    (2350, 1130),
    (2400, 1150),
)
LEGACY_SAFE_POINT_FREQUENCIES = {500, 1000, 1175, 1500, 1600, 1700, 1800}


def _result(ok: bool, message_id: str, message: str, message_args: dict | None = None, **extra) -> dict:
    return {
        "ok": ok,
        "message_id": message_id,
        "message_args": message_args or {},
        "message": message,
        **extra,
    }


def _rooted(root: Path, absolute: str) -> Path:
    if not absolute.startswith("/") or ".." in Path(absolute).parts:
        raise ValueError(f"안전하지 않은 설치 경로: {absolute}")
    return root / absolute.lstrip("/")


def _copy_atomic(source: Path, destination: Path, mode: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temp_name)
        os.chmod(temp_name, mode)
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def extend_governor_curve(text: str) -> tuple[str, bool]:
    pairs = [
        (int(frequency), int(voltage))
        for frequency, voltage in re.findall(
            r"\[\[safe-points\]\]\s*\r?\n\s*frequency\s*=\s*(\d+)[^\r\n]*\r?\n\s*voltage\s*=\s*(\d+)",
            text,
        )
    ]
    known = dict(EXTENDED_SAFE_POINTS)
    frequencies = {frequency for frequency, _voltage in pairs}
    if not LEGACY_SAFE_POINT_FREQUENCIES.issubset(frequencies):
        return text, False
    if any(frequency not in known or known[frequency] != voltage for frequency, voltage in pairs):
        return text, False

    missing = [
        (frequency, voltage)
        for frequency, voltage in EXTENDED_SAFE_POINTS
        if frequency not in frequencies
    ]
    if not missing:
        return text, False
    blocks = "\n\n".join(
        f"[[safe-points]]\nfrequency = {frequency}\nvoltage = {voltage}"
        for frequency, voltage in missing
    )
    return f"{text.rstrip()}\n\n{blocks}\n", True


def _migrate_governor_curve(path: Path) -> bool:
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return False
    updated, changed = extend_governor_curve(original)
    if not changed:
        return False

    backup = path.with_suffix(path.suffix + ".bc250-pre-range-update")
    shutil.copy2(path, backup)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp_name, path.stat().st_mode & 0o777)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


def service_commands() -> list[list[str]]:
    return [
        ["systemctl", "daemon-reload"],
        [
            "busctl",
            "--system",
            "call",
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ReloadConfig",
        ],
        ["systemctl", "enable", "cyan-skillfish-governor-smu.service"],
        ["systemctl", "restart", "cyan-skillfish-governor-smu.service"],
        ["systemctl", "enable", "bc250-cu-live-manager.service"],
        ["systemctl", "enable", "--now", "bc250-cpu-mode.service"],
    ]


def remove_service_commands() -> list[list[str]]:
    return [
        ["systemctl", "disable", "--now", "bc250-cpu-mode.service"],
        ["systemctl", "disable", "--now", "cyan-skillfish-governor-smu.service"],
        ["systemctl", "disable", "bc250-cu-live-manager.service"],
    ]


def install_bundle(project_root: Path, root: Path = Path("/"), manage_services: bool = True) -> dict:
    project_root = Path(project_root).resolve()
    root = Path(root).resolve()
    if root == Path("/") and os.geteuid() != 0:
        return _result(False, "install.auth_required", "시스템 설치에는 인증이 필요합니다.")
    if root == Path("/"):
        platform_report = detect_platform()
        if not platform_report.supported:
            message_id = platform_report.message.key
            if message_id == "platform.not_bazzite":
                message = "This system is not Bazzite."
            elif message_id == "platform.arch_unsupported":
                message = f"Unsupported architecture: {platform_report.architecture}"
            else:
                message = "Required device was not found."
            return _result(False, message_id, message, platform_report.message.params)
    report = verify_bundle(project_root)
    if not report.ok:
        details = [str(error.params.get("detail", error.key)) for error in report.errors]
        return _result(False, "error.bundle_invalid", details[0], {"detail": details[0]}, errors=details)
    installed: list[str] = []
    migrated: list[str] = []
    try:
        manifest = load_manifest(project_root)
        for component in manifest["components"]:
            install_path = component.get("install_path")
            if not install_path:
                continue
            source = (project_root / component["path"]).resolve()
            destination = _rooted(root, install_path)
            if component.get("preserve_existing") and destination.exists():
                continue
            mode = int(str(component.get("mode", "0644")), 8)
            _copy_atomic(source, destination, mode)
            installed.append(str(destination))
        governor_config = _rooted(root, "/etc/cyan-skillfish-governor-smu/config.toml")
        if _migrate_governor_curve(governor_config):
            migrated.append(str(governor_config))
        if manage_services and root == Path("/"):
            for command in service_commands():
                result = subprocess.run(command, text=True, capture_output=True, timeout=30, check=False)
                if result.returncode != 0:
                    return _result(False, "install.service_failed", result.stderr.strip() or "서비스 설정 실패", installed=installed)
        return _result(
            True,
            "install.complete",
            "번들 구성요소 설치 완료",
            installed=installed,
            migrated=migrated,
        )
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return _result(False, "dialog.operation_failed", str(exc), installed=installed)


def remove_bundle(project_root: Path, root: Path = Path("/"), manage_services: bool = True) -> dict:
    project_root = Path(project_root).resolve()
    root = Path(root).resolve()
    if root == Path("/") and os.geteuid() != 0:
        return _result(False, "install.auth_required", "시스템 제거에는 인증이 필요합니다.")
    removed: list[str] = []
    try:
        if manage_services and root == Path("/"):
            for command in remove_service_commands():
                subprocess.run(command, check=False)
        for component in load_manifest(project_root)["components"]:
            install_path = component.get("install_path")
            if not install_path or component.get("preserve_on_remove"):
                continue
            destination = _rooted(root, install_path)
            if destination.is_file():
                destination.unlink()
                removed.append(str(destination))
        if manage_services and root == Path("/"):
            subprocess.run(["systemctl", "daemon-reload"], check=False)
        return _result(True, "install.remove_complete", "번들 구성요소를 제거했습니다.", removed=removed)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        return _result(False, "dialog.operation_failed", str(exc), removed=removed)


def install_and_set_cpu_mode(
    project_root: Path,
    enabled: bool,
    root: Path = Path("/"),
    manage_services: bool = True,
) -> dict:
    installed = install_bundle(project_root, root, manage_services)
    if not installed.get("ok"):
        return installed
    result = run_action("set-cpu-mode", {"enabled": enabled}, root)
    return {**result, "installed": installed.get("installed", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description="BC-250 번들 구성요소 설치기")
    parser.add_argument("action", choices=("install", "remove"))
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--cpu-mode", choices=("on", "off"), help=argparse.SUPPRESS)
    parser.add_argument("--root", type=Path, default=Path("/"), help=argparse.SUPPRESS)
    parser.add_argument("--no-services", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.cpu_mode and args.action != "install":
        parser.error("--cpu-mode is only valid with install")
    if args.cpu_mode:
        result = install_and_set_cpu_mode(
            args.project_root,
            args.cpu_mode == "on",
            args.root,
            manage_services=not args.no_services,
        )
    else:
        function = install_bundle if args.action == "install" else remove_bundle
        result = function(args.project_root, args.root, manage_services=not args.no_services)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
