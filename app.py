#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk

from bc250.bootstrap import inspect
from bc250.i18n import Translator
from bc250.window import MainWindow


PROJECT_ROOT = Path(__file__).resolve().parent


class BC250Application(Gtk.Application):
    def __init__(self, demo: bool = False, layout_check: bool = False) -> None:
        application_id = (
            "io.github.bc250.CustomPannel.LayoutCheck"
            if layout_check
            else "io.github.bc250.CustomPannel"
        )
        super().__init__(application_id=application_id, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.demo = demo
        self.layout_check = layout_check
        self.layout_ok: bool | None = None
        self._layout_check_scheduled = False
        self.translator = Translator(PROJECT_ROOT / "bc250/locales")
        self.window: MainWindow | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_path(str(PROJECT_ROOT / "bc250/style.css"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MainWindow(self, PROJECT_ROOT, self.translator, demo=self.demo)
        if self.layout_check and self.window.get_child() is not None:
            # A bare Xvfb session has no window manager to honor default_size.
            # Pin only the production width; height must follow the content.
            self.window.get_child().set_size_request(510, -1)
        self.window.present()
        if self.layout_check and not self._layout_check_scheduled:
            self._layout_check_scheduled = True
            GLib.timeout_add(800, self._report_layout)

    def _report_layout(self) -> bool:
        if self.window is None or self.window.get_child() is None:
            self.layout_ok = False
        else:
            content = self.window.get_child()
            width = self.window.get_width()
            height = self.window.get_height()
            content_width = content.get_width()
            content_height = content.get_height()
            content_outer_width = content_width + content.get_margin_start() + content.get_margin_end()
            content_outer_height = content_height + content.get_margin_top() + content.get_margin_bottom()
            minimum, natural, _minimum_baseline, _natural_baseline = content.measure(
                Gtk.Orientation.VERTICAL,
                content_outer_width,
            )
            child_layouts = []
            child = content.get_first_child()
            horizontal_fits = True
            index = 0
            while child is not None:
                child_min_h, child_nat_h, _min_base, _nat_base = child.measure(
                    Gtk.Orientation.VERTICAL,
                    content_width,
                )
                child_min_w, child_nat_w, _min_base, _nat_base = child.measure(
                    Gtk.Orientation.HORIZONTAL,
                    -1,
                )
                horizontal_fits = horizontal_fits and child_min_w <= child.get_width()
                child_layouts.append((
                    index,
                    child,
                    child_min_w,
                    child_nat_w,
                    child_min_h,
                    child_nat_h,
                ))
                child = child.get_next_sibling()
                index += 1
            self.layout_ok = (
                width <= 520
                and height <= 700
                and natural <= content_outer_height
                and horizontal_fits
            )
            print(
                f"Layout: window={width}x{height}, "
                f"content={content_width}x{content_height}, "
                f"content-outer={content_outer_width}x{content_outer_height}, "
                f"content-minimum={minimum}, content-natural={natural}, "
                f"horizontal-fits={'yes' if horizontal_fits else 'no'}, "
                f"fits={'yes' if self.layout_ok else 'no'}",
                flush=True,
            )
            for index, child, child_min_w, child_nat_w, child_min_h, child_nat_h in child_layouts:
                print(
                    f"Layout child[{index}]: classes={','.join(child.get_css_classes()) or '-'}, "
                    f"allocated={child.get_width()}x{child.get_height()}, "
                    f"width={child_min_w}/{child_nat_w}, height={child_min_h}/{child_nat_h}",
                    flush=True,
                )
        self.quit()
        return GLib.SOURCE_REMOVE


def check_environment(skip_platform: bool) -> int:
    translator = Translator(
        PROJECT_ROOT / "bc250/locales",
        settings_path=Path(os.devnull),
    )
    print(f"GTK4: OK ({Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()})")
    print(f"Project: {PROJECT_ROOT}")
    report = inspect(PROJECT_ROOT, skip_platform=skip_platform)
    print(f"Bundle: {'OK' if report.bundle.ok else 'FAIL'} ({len(report.bundle.checked)} files)")
    print(
        f"Platform: {'OK' if report.platform.supported else 'FAIL'} - "
        f"{translator.render(report.platform.message)}"
    )
    print(
        "Installed: "
        f"governor={'yes' if report.governor_installed else 'no'}, "
        f"cu-manager={'yes' if report.cu_manager_installed else 'no'}, "
        f"umr={'yes' if report.umr_installed else 'no'}, "
        f"helper={'yes' if report.helper_installed else 'no'}, "
        f"cpu-mode={'yes' if report.cpu_mode_installed else 'no'}, "
        f"support={'yes' if report.support_installed else 'no'}"
    )
    for error in report.bundle.errors:
        print(f"ERROR: {translator.render(error)}", file=sys.stderr)
    return 0 if report.bundle.ok and report.platform.supported else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="BC-250 Custom Pannel")
    parser.add_argument("--check", action="store_true", help="실행 환경만 검사합니다.")
    parser.add_argument("--skip-platform", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--demo", action="store_true", help="하드웨어 변경 없는 데모 상태로 실행합니다.")
    parser.add_argument("--layout-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.check:
        return check_environment(args.skip_platform)
    application = BC250Application(demo=args.demo or args.layout_check, layout_check=args.layout_check)
    result = application.run([sys.argv[0]])
    if args.layout_check and application.layout_ok is not True:
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
