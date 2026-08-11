from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .messages import UserMessage


INSTALL_RECEIPT_PATH = "/opt/bc250-custom-pannel/install-receipt.json"
INSTALL_RECEIPT_SCHEMA = 1
RECEIPT_ONLY_INSTALL_PATHS = frozenset({
    "/etc/polkit-1/rules.d/49-bc250-custom-pannel.rules",
})


@dataclass(frozen=True, slots=True)
class BundleReport:
    ok: bool
    checked: tuple[str, ...]
    errors: tuple[UserMessage, ...]


@dataclass(frozen=True, slots=True)
class PlatformReport:
    supported: bool
    bazzite: bool
    architecture: str
    bc250_present: bool
    message: UserMessage


@dataclass(frozen=True, slots=True)
class BootstrapReport:
    bundle: BundleReport
    platform: PlatformReport
    governor_installed: bool
    cu_manager_installed: bool
    umr_installed: bool
    helper_installed: bool
    cpu_mode_installed: bool
    support_installed: bool = True

    @property
    def ready(self) -> bool:
        return (
            self.bundle.ok
            and self.platform.supported
            and self.governor_installed
            and self.cu_manager_installed
            and self.umr_installed
            and self.helper_installed
            and self.cpu_mode_installed
            and self.support_installed
        )


def _within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def load_manifest(project_root: Path) -> dict:
    path = project_root / "VENDOR-MANIFEST.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("components"), list):
        raise ValueError("지원하지 않는 번들 매니페스트 형식입니다.")
    return data


def verify_bundle(project_root: Path) -> BundleReport:
    project_root = Path(project_root)
    checked: list[str] = []
    errors: list[UserMessage] = []
    try:
        manifest = load_manifest(project_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return BundleReport(False, (), (UserMessage("error.bundle_invalid", {"detail": str(exc)}),))
    for component in manifest["components"]:
        rel = component.get("path", "")
        path = project_root / rel
        if not rel or not _within(project_root, path):
            errors.append(UserMessage("error.bundle_invalid", {"detail": f"Unsafe bundle path: {rel}"}))
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(UserMessage("error.bundle_invalid", {"detail": f"Bundle file missing: {rel} ({exc})"}))
            continue
        expected = str(component.get("sha256", "")).lower()
        if digest != expected:
            errors.append(UserMessage("error.bundle_invalid", {"detail": f"SHA-256 mismatch: {rel}"}))
            continue
        checked.append(rel)
    return BundleReport(not errors, tuple(checked), tuple(errors))


def installed_component_matches(
    project_root: Path,
    component: dict,
    system_root: Path = Path("/"),
) -> bool:
    project_root = Path(project_root)
    system_root = Path(system_root)
    install_path = str(component.get("install_path", ""))
    rel = str(component.get("path", ""))
    if not install_path.startswith("/") or ".." in Path(install_path).parts:
        return False
    if not rel or not _within(project_root, project_root / rel):
        return False
    destination = system_root / install_path.lstrip("/")
    try:
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    except OSError:
        return False
    return digest == str(component.get("sha256", "")).lower()


def load_install_receipt(system_root: Path = Path("/")) -> dict[str, str]:
    system_root = Path(system_root)
    path = system_root / INSTALL_RECEIPT_PATH.lstrip("/")
    try:
        file_stat = path.stat()
        if file_stat.st_mode & 0o022:
            return {}
        if system_root.resolve() == Path("/") and file_stat.st_uid != 0:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    components = payload.get("components")
    if payload.get("schema") != INSTALL_RECEIPT_SCHEMA or not isinstance(components, dict):
        return {}
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in components.items()):
        return {}
    return {key: value.lower() for key, value in components.items()}


def _read_os_release() -> str:
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def _read_pci_ids() -> str:
    found: list[str] = []
    for device in Path("/sys/bus/pci/devices").glob("*"):
        try:
            vendor = (device / "vendor").read_text().strip().removeprefix("0x")
            product = (device / "device").read_text().strip().removeprefix("0x")
        except OSError:
            continue
        found.append(f"{vendor}:{product}".lower())
    return "\n".join(found)


def detect_platform(
    os_release: str | None = None,
    arch: str | None = None,
    pci_devices: str | None = None,
) -> PlatformReport:
    os_release = _read_os_release() if os_release is None else os_release
    arch = platform.machine() if arch is None else arch
    pci_devices = _read_pci_ids() if pci_devices is None else pci_devices
    bazzite = "bazzite" in os_release.lower()
    bc250_present = "1002:13fe" in pci_devices.lower()
    supported = bazzite and arch == "x86_64" and bc250_present
    if not bazzite:
        message = UserMessage("platform.not_bazzite")
    elif arch != "x86_64":
        message = UserMessage("platform.arch_unsupported", {"architecture": arch})
    elif not bc250_present:
        message = UserMessage("platform.device_missing")
    else:
        message = UserMessage("platform.ready")
    return PlatformReport(supported, bazzite, arch, bc250_present, message)


def check_umr_runtime(path: Path, runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None) -> tuple[bool, str]:
    if not path.is_file():
        return False, "UMR 실행 파일이 없습니다."
    runner = runner or (lambda argv: subprocess.run(list(argv), text=True, capture_output=True, timeout=8, check=False))
    try:
        result = runner(["ldd", str(path)])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"UMR 호환성 검사 실패: {exc}"
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or "not found" in output:
        return False, "UMR에 필요한 공유 라이브러리가 없습니다."
    return True, "UMR 공유 라이브러리 확인 완료"


def inspect(
    project_root: Path,
    skip_platform: bool = False,
    system_root: Path = Path("/"),
) -> BootstrapReport:
    project_root = Path(project_root)
    system_root = Path(system_root)
    bundle = verify_bundle(project_root)
    try:
        components = load_manifest(project_root)["components"]
    except (OSError, ValueError, json.JSONDecodeError, KeyError):
        components = []
    by_install_path = {
        str(component.get("install_path")): component
        for component in components
        if component.get("install_path")
    }
    install_receipt = load_install_receipt(system_root)

    def compatible(install_path: str) -> bool:
        component = by_install_path.get(install_path)
        if not component:
            return False
        if installed_component_matches(project_root, component, system_root):
            return True
        return bool(
            install_path in RECEIPT_ONLY_INSTALL_PATHS
            and install_receipt.get(install_path) == str(component.get("sha256", "")).lower()
        )

    def installed(install_path: str) -> bool:
        return (system_root / install_path.lstrip("/")).is_file()

    platform_report = (
        PlatformReport(True, True, platform.machine(), True, UserMessage("platform.skipped")) if skip_platform else detect_platform()
    )
    governor_installed = (
        compatible("/etc/cyan-skillfish-governor-smu/cyan-skillfish-governor-smu")
        and installed("/etc/cyan-skillfish-governor-smu/config.toml")
        and compatible("/etc/systemd/system/cyan-skillfish-governor-smu.service")
        and compatible("/etc/dbus-1/system.d/com.cyanskillfish.Governor.conf")
    )
    cu_manager_installed = (
        compatible("/usr/local/bin/bc250-cu-live-manager")
        and installed("/etc/bc250-cu-live-manager.conf")
        and compatible("/etc/systemd/system/bc250-cu-live-manager.service")
    )
    helper_installed = (
        compatible("/usr/local/libexec/bc250-custom-pannel-privileged")
        and compatible("/etc/polkit-1/rules.d/49-bc250-custom-pannel.rules")
    )
    support_installed = (
        compatible("/opt/bc250-custom-pannel/licenses/cyan-skillfish-governor-MIT.txt")
        and compatible("/opt/bc250-custom-pannel/licenses/umr-MIT.txt")
    )
    return BootstrapReport(
        bundle=bundle,
        platform=platform_report,
        governor_installed=governor_installed,
        cu_manager_installed=cu_manager_installed,
        umr_installed=installed("/usr/bin/umr") or compatible("/opt/bc250-custom-pannel/bin/umr"),
        helper_installed=helper_installed,
        cpu_mode_installed=(
            installed("/etc/bc250-custom-pannel-cpu.conf")
            and compatible("/etc/systemd/system/bc250-cpu-mode.service")
        ),
        support_installed=support_installed,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="BC-250 번들 및 플랫폼 검사")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--skip-platform", action="store_true")
    args = parser.parse_args()
    report = inspect(args.project_root, args.skip_platform)
    print(f"Bundle: {'OK' if report.bundle.ok else 'FAIL'} ({len(report.bundle.checked)} files)")
    print(f"Platform: {'OK' if report.platform.supported else 'FAIL'} - {report.platform.message.key}")
    for error in report.bundle.errors:
        print(f"ERROR: {error.key}: {error.params.get('detail', '')}")
    return 0 if report.bundle.ok and report.platform.supported else 1


if __name__ == "__main__":
    raise SystemExit(main())
