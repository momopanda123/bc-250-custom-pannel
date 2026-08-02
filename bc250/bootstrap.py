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


def inspect(project_root: Path, skip_platform: bool = False) -> BootstrapReport:
    bundle = verify_bundle(project_root)
    platform_report = (
        PlatformReport(True, True, platform.machine(), True, UserMessage("platform.skipped")) if skip_platform else detect_platform()
    )
    return BootstrapReport(
        bundle=bundle,
        platform=platform_report,
        governor_installed=Path("/etc/cyan-skillfish-governor-smu/cyan-skillfish-governor-smu").is_file(),
        cu_manager_installed=Path("/usr/local/bin/bc250-cu-live-manager").is_file(),
        umr_installed=Path("/usr/bin/umr").is_file() or Path("/opt/bc250-custom-pannel/bin/umr").is_file(),
        helper_installed=Path("/usr/local/libexec/bc250-custom-pannel-privileged").is_file(),
        cpu_mode_installed=(
            Path("/etc/bc250-custom-pannel-cpu.conf").is_file()
            and Path("/etc/systemd/system/bc250-cpu-mode.service").is_file()
        ),
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
