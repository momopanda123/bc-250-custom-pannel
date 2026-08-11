import ast
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from bc250.i18n import Translator
from bc250.messages import UserMessage
from bc250.presets import PRESETS, validate_frequency_range, validate_temperature
from bc250.settings import BASE_WGP_MASK, DraftSettings


def find_window_method(name):
    source = Path("bc250/window.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    window_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
    )
    return next(
        node for node in window_class.body if isinstance(node, ast.FunctionDef) and node.name == name
    )


def load_window_method(name):
    method = find_window_method(name)
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"StatusSnapshot": object, "PowerState": object}
    exec(compile(module, "bc250/window.py", "exec"), namespace)
    return namespace[name], method


def css_rule(css, selector):
    start = css.index(f"{selector} {{")
    end = css.index("}", start)
    return css[start:end]


class SensitiveWidget:
    def __init__(self):
        self.sensitive = None
        self.label = None

    def set_sensitive(self, value):
        self.sensitive = value

    def set_label(self, value):
        self.label = value


class ValueWidget:
    def __init__(self, value):
        self.value = value

    def set_value(self, value):
        self.value = value


class IntegerSpinWidget(ValueWidget):
    def get_value_as_int(self):
        return int(self.value)


class UnsignedSpinWidget(ValueWidget):
    def get_value(self):
        return float(self.value)

    def get_value_as_int(self):
        raise AssertionError("The signed GTK integer accessor cannot represent the full u32 range")


class SelectionWidget:
    def __init__(self, selected):
        self.selected = selected

    def set_selected(self, selected):
        self.selected = selected

    def get_selected(self):
        return self.selected


class ToggleWidget:
    def __init__(self, active):
        self.active = active
        self.label = None
        self.tooltip = None

    def get_active(self):
        return self.active

    def set_active(self, active):
        self.active = active

    def set_label(self, label):
        self.label = label

    def set_tooltip_text(self, text):
        self.tooltip = text


class TextWidget:
    def __init__(self):
        self.text = None
        self.tooltip = None

    def set_text(self, text):
        self.text = text

    def set_tooltip_text(self, text):
        self.tooltip = text


class StyledTextWidget(TextWidget):
    def __init__(self):
        super().__init__()
        self.css_classes = set()

    def add_css_class(self, css_class):
        self.css_classes.add(css_class)

    def remove_css_class(self, css_class):
        self.css_classes.discard(css_class)


class MetricWidget:
    def update(self, _value, _detail=None):
        pass


class AppTests(unittest.TestCase):
    def test_check_mode_reports_gtk(self):
        result = subprocess.run([sys.executable, "app.py", "--check", "--skip-platform"], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("GTK4: OK", result.stdout)
        self.assertIn("cpu-mode=", result.stdout)
        self.assertNotIn("PyGIWarning", result.stderr)

    def test_css_contains_visual_system(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        for selector in (".dashboard", ".telemetry-band", ".core-surface", ".status-good", ".primary-action"):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)

    def test_compact_window_contract(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        self.assertIn("set_default_size(520, -1)", source)
        self.assertIn("set_size_request(500, -1)", source)
        self.assertIn("dashboard.set_margin_start(5)", source)
        self.assertIn("dashboard.set_margin_end(5)", source)
        self.assertNotIn("Gtk.ScrolledWindow", source)
        self.assertNotIn("Gtk.FlowBox", source)
        self.assertIn("self.cpu_core_value", source)
        self.assertIn("self.cpu_mode_toggle", source)
        self.assertIn("self.custom_min_spin", source)
        self.assertIn("self.custom_max_spin", source)
        self.assertIn("self.custom_mv_spin", source)
        self.assertIn("self.wgp_buttons", source)
        self.assertIn("self.language_dropdown", source)
        self.assertIn("def _retranslate", source)

    def test_compact_controls_do_not_expand_past_the_520x700_contract(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        self.assertIn("spin.set_width_chars(7)", source)
        self.assertIn("spin.set_max_width_chars(7)", source)
        self.assertIn("(self.recovery_label, self.recovery_spin, 1, 2)", source)
        self.assertIn(".core-surface .wgp-cell", css)
        self.assertIn("min-height: 14px;", css_rule(css, ".core-surface .wgp-cell"))
        self.assertIn("padding: 0;", css_rule(css, ".core-surface .wgp-cell"))

    def test_layout_probe_reports_actual_window_and_content_fit(self):
        source = Path("app.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--layout-check"', source)
        self.assertIn("Layout: window=", source)
        self.assertIn("content-natural=", source)
        self.assertIn("content_outer_height", source)
        self.assertIn("fits=", source)
        self.assertIn("width <= 520", source)
        self.assertIn("height <= 700", source)
        self.assertIn("set_size_request(510, -1)", source)
        self.assertIn("horizontal_fits", source)

    def test_setup_components_wrap_without_ellipsis(self):
        build = ast.unparse(find_window_method("_build_ui"))
        self.assertIn("Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)", build)
        self.assertIn("self.setup_detail.set_wrap(True)", build)
        self.assertNotIn("self.setup_detail.set_ellipsize", build)

    def test_bios_and_kernel_use_equal_overview_cells(self):
        build = ast.unparse(find_window_method("_build_ui"))
        self.assertIn("hardware.set_homogeneous(True)", build)
        self.assertIn("status.add_css_class('system-status')", build)
        self.assertIn("bios.set_hexpand(True)", build)
        self.assertIn("kernel.set_hexpand(True)", build)
        self.assertNotIn("summary.append(self.hero_state)", build)
        self.assertLess(build.index("hardware.append(status)"), build.index("hardware.append(bios)"))

    def test_core_columns_use_content_width_without_gpu_dead_space(self):
        node = find_window_method("_build_core_surface")
        build = ast.unparse(node)
        core_homogeneous_calls = [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_column_homogeneous"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "grid"
        ]
        self.assertEqual(core_homogeneous_calls, [])
        self.assertIn("gpu.set_hexpand(True)", build)
        self.assertIn("self.wgp_grid.set_hexpand(True)", build)
        self.assertIn("self.wgp_grid.set_column_homogeneous(True)", build)

    def test_header_is_single_line_without_duplicate_availability_status(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        build = ast.unparse(find_window_method("_build_ui"))
        header_build = build[:build.index("self.set_titlebar(header)")]
        self.assertNotIn("app_subtitle", source)
        self.assertNotIn("status_badge", build)
        self.assertNotIn("header.pack_start", build)
        self.assertNotIn("language_dropdown", header_build)
        self.assertIn("self.language_dropdown.add_css_class('language-selector')", build)
        self.assertIn("summary.append(self.language_dropdown)", build)
        self.assertNotIn("last_update", source)
        self.assertNotIn("_build_system_strip", source)
        self.assertIn("self.bios_label", build)
        self.assertIn("self.kernel_label", build)

    def test_availability_updates_only_the_overview_status_label(self):
        set_badge, _node = load_window_method("_set_badge")
        title_status = StyledTextWidget()
        overview_status = StyledTextWidget()
        window = types.SimpleNamespace(
            status_badge=title_status,
            hero_state=overview_status,
        )

        set_badge(window, "Partially available", "status-warn")

        self.assertIsNone(title_status.text)
        self.assertEqual(overview_status.text, "Partially available")
        self.assertEqual(overview_status.css_classes, {"status-warn"})

    def test_overview_availability_is_plain_text_without_pill_decoration(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        forbidden = ("border:", "border-radius:", "background", "padding:")
        for selector in (".hero-state", ".status-good", ".status-warn", ".status-error"):
            rule = css_rule(css, selector)
            for token in forbidden:
                with self.subTest(selector=selector, token=token):
                    self.assertNotIn(token, rule)

    def test_header_uses_compact_titlebar_controls(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        self.assertIn("min-height: 30px;", css_rule(css, ".app-header"))
        window_controls = css_rule(css, ".app-header windowcontrols button")
        self.assertIn("min-height: 24px;", window_controls)
        self.assertIn("min-width: 24px;", window_controls)

    def test_hero_hardware_summary_keeps_core_counts_without_repeating_app_name(self):
        tree = ast.parse(Path("bc250/window.py").read_text(encoding="utf-8"))
        window_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MainWindow"
        )
        methods = {
            node.name for node in window_class.body if isinstance(node, ast.FunctionDef)
        }
        self.assertIn("_hero_hardware_text", methods)

        formatter, _node = load_window_method("_hero_hardware_text")
        snapshot = types.SimpleNamespace(cpu_cores=6, cpu_threads=12, cu_count=40)
        self.assertEqual(
            formatter(object(), snapshot),
            "CPU 6C/12T · GPU 40/40 CU",
        )
        missing = types.SimpleNamespace(cpu_cores=None, cpu_threads=None, cu_count=None)
        self.assertEqual(
            formatter(object(), missing),
            "CPU — · GPU —",
        )

    def test_cpu_temperature_precedes_gpu_temperature_in_telemetry_grid(self):
        build = ast.unparse(find_window_method("_build_ui"))
        self.assertLess(build.index("self.metric_cpu_temp"), build.index("self.metric_gpu_temp"))
        card_tuple = build[build.index("for index, card in enumerate"):]
        self.assertLess(card_tuple.index("self.metric_cpu_temp"), card_tuple.index("self.metric_gpu_temp"))

    def test_telemetry_grid_has_no_redundant_heading(self):
        build = ast.unparse(find_window_method("_build_ui"))
        self.assertNotIn("telemetry_title", build)

    def test_ready_setup_row_remains_visible_with_install_disabled(self):
        refresh, _node = load_window_method("_refresh_bootstrap")
        report = types.SimpleNamespace(
            ready=True,
            bundle=types.SimpleNamespace(ok=True, errors=()),
            platform=types.SimpleNamespace(supported=True, message=UserMessage("platform.ready")),
            governor_installed=True,
            cu_manager_installed=True,
            umr_installed=True,
            helper_installed=True,
            cpu_mode_installed=True,
            support_installed=True,
        )
        visibility = []
        banner_classes = set()
        install = SensitiveWidget()
        with tempfile.TemporaryDirectory() as tmp:
            translator = Translator(Path("bc250/locales"), Path(tmp) / "settings.json", {"LANG": "en_US.UTF-8"})
            window = types.SimpleNamespace(
                project_root=Path("."),
                translator=translator,
                _controls_sensitive=True,
                setup_banner=types.SimpleNamespace(
                    set_visible=visibility.append,
                    add_css_class=banner_classes.add,
                    remove_css_class=banner_classes.discard,
                ),
                setup_title=TextWidget(),
                setup_detail=types.SimpleNamespace(set_text=lambda _value: None),
                install_button=install,
                _render_message=lambda message: translator.render(message),
            )
            refresh.__globals__.update({"inspect": lambda _root: report, "UserMessage": UserMessage})
            refresh(window)

        self.assertEqual(visibility, [True])
        self.assertFalse(install.sensitive)
        self.assertEqual(window.setup_title.text, "Components installed")
        self.assertEqual(install.label, "Installed")
        self.assertEqual(banner_classes, {"setup-ready"})

    def test_ready_setup_button_uses_a_quiet_completed_style(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        banner = css_rule(css, ".setup-banner.setup-ready")
        button = css_rule(css, ".setup-banner.setup-ready button")
        self.assertIn("@mint", banner)
        self.assertIn("background-color: transparent;", button)

    def test_component_install_starts_with_one_click_without_second_confirmation(self):
        node = find_window_method("_on_install")
        calls = [
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        ]
        self.assertIn("_run_async", calls)
        self.assertNotIn("_confirm", calls)

    def test_successful_component_install_refreshes_compatibility_before_reenabling_controls(self):
        install_done, _node = load_window_method("_install_done")
        events = []
        window = types.SimpleNamespace(
            _set_controls_sensitive=lambda value: events.append(("sensitive", value)),
            _show_message=lambda *_args: events.append(("message",)),
            _refresh_bootstrap=lambda: events.append(("refresh",)),
            _payload_text=lambda *_args: "installed",
            translator=types.SimpleNamespace(gettext=lambda key: key),
        )
        install_done.__globals__["Gtk"] = types.SimpleNamespace(
            MessageType=types.SimpleNamespace(INFO="info", ERROR="error")
        )
        command = types.SimpleNamespace(ok=True, returncode=0)

        self.assertFalse(install_done(window, (command, {"ok": True})))
        self.assertLess(events.index(("refresh",)), events.index(("sensitive", True)))

    def test_success_result_uses_a_warning_message_box_when_any_change_requires_reboot(self):
        show_success, _node = load_window_method("_show_success_result")
        messages = []
        window = types.SimpleNamespace(
            translator=types.SimpleNamespace(gettext=lambda key, **_kwargs: key),
            _show_message=lambda *args: messages.append(args),
            _payload_text=lambda *_args: "normal success",
        )
        show_success.__globals__["Gtk"] = types.SimpleNamespace(
            MessageType=types.SimpleNamespace(INFO="info", WARNING="warning")
        )

        show_success(
            window,
            {"ok": True, "reboot_required": True},
            "dialog.apply_complete",
            "dialog.apply_complete_detail",
        )

        self.assertEqual(
            messages,
            [("dialog.reboot_required", "dialog.reboot_required_detail", "warning")],
        )

    def test_success_result_keeps_the_normal_success_message_without_reboot(self):
        show_success, _node = load_window_method("_show_success_result")
        messages = []
        window = types.SimpleNamespace(
            translator=types.SimpleNamespace(gettext=lambda key, **_kwargs: key),
            _show_message=lambda *args: messages.append(args),
            _payload_text=lambda *_args: "normal success",
        )
        show_success.__globals__["Gtk"] = types.SimpleNamespace(
            MessageType=types.SimpleNamespace(INFO="info", WARNING="warning")
        )

        show_success(
            window,
            {"ok": True, "reboot_required": False},
            "dialog.apply_complete",
            "dialog.apply_complete_detail",
        )

        self.assertEqual(messages, [("dialog.apply_complete", "normal success", "info")])

    def test_status_refresh_does_not_overwrite_user_tuning_values(self):
        apply_snapshot, _node = load_window_method("_apply_snapshot")
        apply_snapshot.__globals__["PRESETS"] = PRESETS
        window = types.SimpleNamespace(
            _refreshing=True,
            _alive=True,
            _last_snapshot=None,
            _settings_hydrated=True,
            _draft_dirty=True,
            custom_min_spin=ValueWidget(650),
            custom_max_spin=ValueWidget(1750),
            throttle_spin=ValueWidget(88),
            recovery_spin=ValueWidget(78),
            profile_dropdown=SelectionWidget(3),
            PRESET_KEYS=(*PRESETS.keys(), "custom"),
            _render_snapshot=lambda _snapshot: None,
            _apply_power_state=lambda _state: None,
            _update_custom_controls=lambda: None,
        )
        refreshed = types.SimpleNamespace(
            governor_min=500,
            governor_max=1800,
            throttle=85,
            recovery=75,
            cu_saved_count=40,
            voltage_limit=930,
        )

        apply_snapshot(window, refreshed)

        self.assertEqual(window.custom_min_spin.value, 650)
        self.assertEqual(window.custom_max_spin.value, 1750)
        self.assertEqual(window.throttle_spin.value, 88)
        self.assertEqual(window.recovery_spin.value, 78)
        self.assertEqual(window.profile_dropdown.selected, 3)

    def test_custom_selection_preserves_full_unsigned_clock_values(self):
        selected_values, _node = load_window_method("_selected_values")
        selected_values.__globals__.update({
            "PRESETS": PRESETS,
            "validate_frequency_range": validate_frequency_range,
            "validate_temperature": validate_temperature,
            "DraftSettings": DraftSettings,
        })
        window = types.SimpleNamespace(
            PRESET_KEYS=(*PRESETS.keys(), "custom"),
            profile_dropdown=SelectionWidget(3),
            custom_min_spin=UnsignedSpinWidget(0),
            custom_max_spin=UnsignedSpinWidget(4_294_967_295),
            custom_mv_spin=UnsignedSpinWidget(4_294_967_295),
            throttle_spin=IntegerSpinWidget(255),
            recovery_spin=IntegerSpinWidget(255),
            cpu_mode_toggle=ToggleWidget(True),
            power_suspend_dropdown=SelectionWidget(0),
            power_suspend_custom_spin=IntegerSpinWidget(15),
            power_display_dropdown=SelectionWidget(6),
            power_display_custom_spin=IntegerSpinWidget(17),
            _selected_cu_masks=lambda: (0x07, 0x0F, 0x17, 0x1F),
            _power_minutes=lambda dropdown, spin: 0 if dropdown.get_selected() == 0 else spin.get_value_as_int(),
        )

        settings = selected_values(window)
        self.assertEqual((settings.min_mhz, settings.max_mhz, settings.max_mv), (0, 4_294_967_295, 4_294_967_295))
        self.assertEqual(settings.cu_masks, (0x07, 0x0F, 0x17, 0x1F))
        self.assertEqual((settings.suspend_minutes, settings.display_minutes), (0, 17))

    def test_cpu_control_is_a_draft_toggle_applied_by_the_global_action(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        self.assertIn("Gtk.ToggleButton", source)
        self.assertIn("def _on_cpu_mode_toggled", source)
        self.assertIn("self.privileged.apply_all", source)
        self.assertNotIn("install_then_set_cpu_mode", source)
        self.assertNotIn("self.cpu_unlock_button", source)

    def test_initial_cpu_draft_preserves_saved_unlock_when_live_topology_is_stock(self):
        apply_snapshot, _node = load_window_method("_apply_snapshot")
        apply_snapshot.__globals__["PRESETS"] = PRESETS
        selection, _node = load_window_method("_cpu_selection_from_snapshot")
        selected = []
        window = types.SimpleNamespace(
            _refreshing=True,
            _alive=True,
            _last_snapshot=None,
            _settings_hydrated=False,
            _draft_dirty=False,
            _changing_cpu_toggle=False,
            _cpu_mode_pending=False,
            _cpu_selection_from_snapshot=lambda snapshot: selection(window, snapshot),
            custom_min_spin=ValueWidget(500),
            custom_max_spin=ValueWidget(1800),
            custom_mv_spin=ValueWidget(930),
            profile_dropdown=SelectionWidget(2),
            PRESET_KEYS=(*PRESETS.keys(), "custom"),
            throttle_spin=ValueWidget(85),
            recovery_spin=ValueWidget(75),
            wgp_buttons={},
            _sync_cpu_toggle=lambda active: selected.append(active),
            _render_snapshot=lambda _snapshot: None,
            _apply_power_state=lambda _state: None,
            _update_custom_controls=lambda: None,
        )
        snapshot = types.SimpleNamespace(
            governor_min=None,
            governor_max=None,
            voltage_limit=None,
            throttle=None,
            recovery=None,
            cu_saved_masks=None,
            cu_masks=None,
            cpu_saved_mode=True,
            cpu_threads=12,
            cpu_recovery_phase=None,
        )

        apply_snapshot(window, snapshot)

        self.assertEqual(selected, [True])

    def test_cpu_snapshot_selection_prefers_saved_mode_over_temporary_live_topology(self):
        selection, _node = load_window_method("_cpu_selection_from_snapshot")
        window = types.SimpleNamespace(_cpu_mode_pending=False)
        saved_on_live_stock = types.SimpleNamespace(
            cpu_recovery_phase=None,
            cpu_saved_mode=True,
            cpu_threads=12,
        )
        saved_off_live_unlocked = types.SimpleNamespace(
            cpu_recovery_phase=None,
            cpu_saved_mode=False,
            cpu_threads=16,
        )

        self.assertTrue(selection(window, saved_on_live_stock))
        self.assertFalse(selection(window, saved_off_live_unlocked))

    def test_cpu_toggle_click_refreshes_the_action_from_native_state(self):
        handler, _node = load_window_method("_on_cpu_mode_toggled")
        resyncs = []
        window = types.SimpleNamespace(
            _changing_cpu_toggle=False,
            _draft_dirty=False,
            _cpu_draft_changed=False,
            _sync_cpu_toggle=lambda active: resyncs.append(active),
        )
        toggle = ToggleWidget(False)

        handler(window, toggle)

        self.assertTrue(window._draft_dirty)
        self.assertTrue(window._cpu_draft_changed)
        self.assertFalse(toggle.get_active())
        self.assertEqual(resyncs, [False])

        toggle.set_active(True)
        handler(window, toggle)
        self.assertEqual(resyncs, [False, True])

    def test_cpu_toggle_label_exposes_the_reverse_action_for_both_states(self):
        sync, _node = load_window_method("_sync_cpu_toggle")
        toggle = ToggleWidget(False)
        window = types.SimpleNamespace(
            _changing_cpu_toggle=False,
            cpu_mode_toggle=toggle,
            translator=types.SimpleNamespace(gettext=lambda key: key),
        )

        sync(window, False)
        self.assertEqual(toggle.label, "action.enable_cpu")
        sync(window, True)
        self.assertEqual(toggle.label, "action.disable_cpu")

    def test_cpu_state_uses_a_tooltip_instead_of_a_repeated_visible_line(self):
        render, _node = load_window_method("_render_snapshot")
        render.__globals__["UNKNOWN"] = "unknown"
        labels = {name: TextWidget() for name in (
            "governor_value", "bios_value", "kernel_value", "cpu_core_value",
            "cpu_state_label", "cu_state_label", "hero_title", "hero_state",
        )}
        window = types.SimpleNamespace(
            translator=types.SimpleNamespace(gettext=lambda key, **_kwargs: key),
            metric_gpu_temp=MetricWidget(),
            metric_cpu_temp=MetricWidget(),
            metric_power=MetricWidget(),
            metric_clock=MetricWidget(),
            metric_voltage=MetricWidget(),
            metric_fan=MetricWidget(),
            wgp_buttons={},
            _cpu_mode_pending=False,
            _draft_dirty=True,
            _cpu_draft_changed=True,
            _controls_sensitive=True,
            cpu_mode_toggle=ToggleWidget(False),
            _render_message=lambda message: str(message),
            _update_selected_cu_text=lambda: None,
            _hero_hardware_text=lambda _snapshot: "hardware",
            _hero_badge=lambda _snapshot: ("status.partial", "status-warn"),
            _hero_status_text=lambda _snapshot: "status",
            _set_badge=lambda _text, _style: None,
            _set_controls_sensitive=lambda _value: None,
            **labels,
        )
        snapshot = types.SimpleNamespace(
            gpu_temperature="42 C",
            cpu_temperature="43 C",
            cpu_temperature_source="Tctl",
            power="35 W",
            clock="1700 MHz",
            voltage="920 mV",
            fan=types.SimpleNamespace(state="unknown", rpm=0, label="", active_count=0),
            system=types.SimpleNamespace(
                bios_vendor="AMI", bios_version="P3.00", bios_date="2026-01-01",
                kernel_release="6.19", architecture="x86_64",
            ),
            governor_min=None,
            governor_max=None,
            governor_service="active",
            cpu_cores=6,
            cpu_threads=12,
            cpu_saved_mode=True,
            cpu_recovery_phase=None,
            cu_masks=None,
            cu_saved_masks=None,
            cu_verified=False,
            cu_state="unknown",
            cu_saved_count=None,
            collected_at=types.SimpleNamespace(strftime=lambda _format: "12:00:00"),
        )

        render(window, snapshot)

        self.assertEqual(window.cpu_mode_toggle.tooltip, "status.cpu_saved_unlock_mismatch")

    def test_gpu_core_card_has_fixed_base_and_individual_optional_wgp_cells(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        build = ast.unparse(find_window_method("_build_core_surface"))
        self.assertIn("for row, name in enumerate", build)
        self.assertIn("for wgp in range(5)", build)
        self.assertIn("button.set_sensitive(wgp >= 3)", build)
        self.assertIn("button.set_active(wgp < 3)", build)
        self.assertNotIn("cu_dropdown", source)
        self.assertNotIn("cu_save_button", source)
        self.assertNotIn("cpu_state_label", build)
        self.assertNotIn("cu_state_label", build)

    def test_wgp_checkboxes_center_only_the_square_indicator(self):
        build = ast.unparse(find_window_method("_build_core_surface"))
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        outer = css_rule(css, ".core-surface .wgp-cell")
        indicator = css_rule(css, ".core-surface .wgp-cell check")
        self.assertIn("button.set_halign(Gtk.Align.CENTER)", build)
        self.assertIn("background-color: transparent;", outer)
        self.assertIn("border: none;", outer)
        self.assertIn("border: 1px solid", indicator)
        self.assertIn("border-radius:", indicator)

    def test_fixed_base_wgp_cells_are_visually_disabled(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        disabled = css_rule(css, ".wgp-base:disabled")
        disabled_check = css_rule(css, ".wgp-base:disabled check")
        self.assertIn("opacity:", disabled)
        self.assertIn("background-color:", disabled_check)
        self.assertIn("border-color:", disabled_check)

    def test_cpu_toggle_checked_and_unchecked_styles_are_visibly_distinct(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        unchecked = css_rule(css, ".core-surface .cpu-action")
        checked = css_rule(css, ".core-surface .cpu-action:checked")
        self.assertIn("background-color:", unchecked)
        self.assertIn("background-color:", checked)
        self.assertNotEqual(unchecked, checked)

    def test_only_one_global_apply_and_save_pair_controls_all_cards(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        ui_build = ast.unparse(find_window_method("_build_ui"))
        build = ast.unparse(find_window_method("_build_global_actions"))
        self.assertIn("self.apply_button", build)
        self.assertIn("self.save_button", build)
        self.assertEqual(source.count("self.apply_button = Gtk.Button()"), 1)
        self.assertEqual(source.count("self.save_button = Gtk.Button()"), 1)
        self.assertTrue(ui_build.rstrip().endswith("dashboard.append(self._build_global_actions())"))

    def test_global_apply_and_save_stay_enabled_when_components_need_update(self):
        set_sensitive, _node = load_window_method("_set_controls_sensitive")
        apply_button = SensitiveWidget()
        save_button = SensitiveWidget()
        install_button = SensitiveWidget()
        window = types.SimpleNamespace(
            _governor_ready=False,
            _helper_ready=False,
            _install_eligible=True,
            _cpu_mode_eligible=True,
            apply_button=apply_button,
            save_button=save_button,
            install_button=install_button,
        )

        set_sensitive(window, True)

        self.assertTrue(apply_button.sensitive)
        self.assertTrue(save_button.sensitive)
        self.assertTrue(install_button.sensitive)

    def test_power_idle_card_uses_timeout_dropdowns_with_custom_minutes(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        for token in (
            "PowerController",
            "def _build_power_surface",
            "self.power_idle_value",
            "self.power_suspend_dropdown",
            "self.power_suspend_custom_spin",
            "self.power_display_dropdown",
            "self.power_display_custom_spin",
            "def _on_power_suspend_changed",
            "def _on_power_display_changed",
        ):
            with self.subTest(token=token):
                self.assertIn(token, source)
        self.assertNotIn("self.power_suspend_toggle", source)
        self.assertNotIn("self.power_display_toggle", source)
        self.assertNotIn("_run_async", ast.unparse(find_window_method("_on_power_suspend_changed")))
        self.assertNotIn("_run_async", ast.unparse(find_window_method("_on_power_display_changed")))

    def test_power_dropdown_values_are_visually_separate_from_card_actions(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        dropdown = css_rule(css, ".power-surface dropdown")
        button = css_rule(css, ".power-surface dropdown button")
        self.assertIn("background-color: @surface_raised;", dropdown)
        self.assertIn("background: transparent;", button)
        self.assertIn("border: none;", button)

    def test_each_card_has_its_own_accent_and_card_buttons_share_that_accent(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        for selector in (
            ".metric-cpu",
            ".metric-gpu",
            ".metric-power",
            ".metric-clock",
            ".metric-voltage",
            ".metric-fan",
            ".tuning-surface button",
            ".core-surface button",
            ".power-surface button",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)

        expected_card_accents = {
            ".overview-strip": "border-left: 3px solid @pink;",
            ".setup-banner": "@amber",
            ".tuning-surface": "border-top: 2px solid alpha(@violet, 0.65);",
            ".core-surface": "border-top: 2px solid alpha(@blue, 0.65);",
            ".power-surface": "border-left: 3px solid @mint;",
        }
        for selector, accent in expected_card_accents.items():
            with self.subTest(card=selector):
                self.assertIn(accent, css_rule(css, selector))

        white_text_selectors = (
            ".language-selector button",
            ".setup-banner button",
            ".tuning-surface button",
            ".core-surface button",
            ".power-surface button",
            ".primary-action",
            ".secondary-action",
            ".warning-action",
            ".cpu-action",
        )
        for selector in white_text_selectors:
            with self.subTest(text=selector):
                self.assertIn("color: #FFFFFF;", css_rule(css, selector))

    def test_graphite_instrument_tokens(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8").upper()
        for token in ("#090C10", "#111820", "#263341", "#F2F7FA", "#8FA0AE", "#52D3FF", "#9B8CFF"):
            with self.subTest(token=token):
                self.assertIn(token, css)

    def test_locale_assets_are_bundled(self):
        self.assertEqual(
            {path.name for path in Path("bc250/locales").glob("*.json")},
            {"en.json", "ko.json", "ja.json", "zh-CN.json"},
        )

    def test_payload_text_preserves_failure_diagnostics_without_success_noise(self):
        method, _node = load_window_method("_payload_text")
        with tempfile.TemporaryDirectory() as tmp:
            translator = Translator(
                Path("bc250/locales"),
                Path(tmp) / "settings.json",
                {"LANG": "en_US.UTF-8"},
            )
            window = types.SimpleNamespace(translator=translator)

            success = method(window, {
                "ok": True,
                "message_id": "helper.governor_saved",
                "message_args": {},
                "message": "legacy compatibility text",
            }, "dialog.save_complete")
            self.assertEqual(success, "Governor settings saved.")
            self.assertNotIn("legacy compatibility text", success)

            cases = (
                (
                    {
                        "ok": False,
                        "message_id": "helper.governor_restart_failed",
                        "message_args": {},
                        "message": "systemctl: unit failed",
                    },
                    "dialog.save_failed",
                    "Governor restart failed: systemctl: unit failed",
                ),
                (
                    {
                        "ok": False,
                        "message_id": "dialog.operation_failed",
                        "message_args": {},
                        "message": "permission denied",
                    },
                    "dialog.save_failed",
                    "Operation failed\n\npermission denied",
                ),
                (
                    {
                        "ok": False,
                        "message_id": "helper.future_failure",
                        "message_args": {},
                        "message": "future helper diagnostic",
                    },
                    "dialog.save_failed",
                    "Could not save boot profile\n\nfuture helper diagnostic",
                ),
            )
            for payload, fallback, expected in cases:
                with self.subTest(message_id=payload["message_id"]):
                    rendered = method(window, payload, fallback)
                    self.assertEqual(rendered, expected)
                    self.assertNotIn(payload["message_id"], rendered)
                    self.assertEqual(rendered.count(payload["message"]), 1)

    def test_payload_text_does_not_repeat_structured_platform_compatibility_message(self):
        method, _node = load_window_method("_payload_text")
        with tempfile.TemporaryDirectory() as tmp:
            translator = Translator(
                Path("bc250/locales"),
                Path(tmp) / "settings.json",
                {"LANG": "en_US.UTF-8"},
            )
            window = types.SimpleNamespace(translator=translator)
            rendered = method(window, {
                "ok": False,
                "message_id": "platform.arch_unsupported",
                "message_args": {"architecture": "aarch64"},
                "message": "Unsupported production host (aarch64)",
            }, "dialog.install_failed")
            self.assertEqual(rendered, "Unsupported architecture: aarch64")

    def test_unsupported_install_eligibility_survives_async_reenable(self):
        refresh, _refresh_node = load_window_method("_refresh_bootstrap")
        set_sensitive, _sensitive_node = load_window_method("_set_controls_sensitive")
        report = types.SimpleNamespace(
            ready=False,
            bundle=types.SimpleNamespace(ok=True, errors=()),
            platform=types.SimpleNamespace(
                supported=False,
                message=UserMessage("platform.arch_unsupported", {"architecture": "aarch64"}),
            ),
        )
        widgets = [SensitiveWidget() for _ in range(3)]
        window = types.SimpleNamespace(
            project_root=Path("."),
            translator=types.SimpleNamespace(gettext=lambda key, **_kwargs: key),
            _controls_sensitive=True,
            setup_banner=types.SimpleNamespace(
                set_visible=lambda _value: None,
                add_css_class=lambda _value: None,
                remove_css_class=lambda _value: None,
            ),
            setup_title=types.SimpleNamespace(set_text=lambda _value: None),
            setup_detail=types.SimpleNamespace(set_text=lambda _value: None),
            apply_button=widgets[0],
            save_button=widgets[1],
            install_button=widgets[2],
            _render_message=lambda _message: "unsupported",
        )
        refresh.__globals__.update({"inspect": lambda _root: report, "UserMessage": UserMessage})

        refresh(window)
        set_sensitive(window, True)

        self.assertEqual([widget.sensitive for widget in widgets[:2]], [False, False])
        self.assertFalse(widgets[2].sensitive)

    def test_show_message_builds_translated_close_action(self):
        node = find_window_method("_show_message")
        dialogs = [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "MessageDialog"
        ]
        self.assertEqual(len(dialogs), 1)
        buttons = next(keyword.value for keyword in dialogs[0].keywords if keyword.arg == "buttons")
        self.assertEqual(getattr(buttons, "attr", None), "NONE")
        add_button = [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "add_button"
        ]
        self.assertEqual(len(add_button), 1)
        gettext = add_button[0].args[0]
        self.assertEqual(getattr(gettext.func, "attr", None), "gettext")
        self.assertEqual(gettext.args[0].value, "common.close")
        self.assertEqual(getattr(add_button[0].args[1], "attr", None), "CLOSE")

    def test_message_dialog_uses_supported_gtk4_secondary_text_property(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        self.assertNotIn("set_secondary_text", source)
        self.assertNotIn("format_secondary_text", source)
        for method_name in ("_confirm", "_show_message"):
            node = find_window_method(method_name)
            constructors = [
                call for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "MessageDialog"
            ]
            with self.subTest(method=method_name):
                self.assertEqual(len(constructors), 1)
                self.assertIn("secondary_text", {keyword.arg for keyword in constructors[0].keywords})

    def test_installed_gtk4_exposes_secondary_text_formatter(self):
        try:
            import gi
        except ModuleNotFoundError:
            self.skipTest("PyGObject is only available on the Bazzite target")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        self.assertIn("secondary-text", {prop.name for prop in Gtk.MessageDialog.list_properties()})

    def test_initial_fan_placeholder_does_not_imply_control(self):
        node = find_window_method("_retranslate")
        constants = {item.value for item in ast.walk(node) if isinstance(item, ast.Constant)}
        self.assertNotIn("metric.fan_auto", constants)

    def test_bootstrap_inspection_is_not_duplicated_at_startup(self):
        node = find_window_method("__init__")
        refresh_calls = [
            call for call in ast.walk(node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_refresh_bootstrap"
        ]
        self.assertEqual(refresh_calls, [])

    def test_hero_status_never_repeats_live_cu_count(self):
        render_node = find_window_method("_render_snapshot")
        hero_status_calls = [
            call for call in ast.walk(render_node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "set_text"
            and isinstance(call.func.value, ast.Attribute)
            and call.func.value.attr == "hero_state"
        ]
        rendered_arguments = "\n".join(ast.unparse(call.args[0]) for call in hero_status_calls)
        self.assertNotIn("40 CU", rendered_arguments)
        self.assertNotIn("snapshot.cu_state", rendered_arguments)
        self.assertIn("self._hero_status_text(snapshot)", rendered_arguments)

        method, _node = load_window_method("_hero_status_text")
        with tempfile.TemporaryDirectory() as tmp:
            translator = Translator(
                Path("bc250/locales"),
                Path(tmp) / "settings.json",
                {"LANG": "en_US.UTF-8"},
            )
            window = types.SimpleNamespace(
                translator=translator,
                _render_message=lambda message: translator.render(message),
            )
            cases = (
                (
                    types.SimpleNamespace(
                        cu_count=40,
                        governor_service="active",
                        errors=(),
                        cu_state=UserMessage("status.cu_applied", {"count": 40}),
                    ),
                    "Active",
                ),
                (
                    types.SimpleNamespace(
                        cu_count=32,
                        governor_service="inactive",
                        errors=(),
                        cu_state=UserMessage("status.cu_mismatch", {"current": 32}),
                    ),
                    "Partially available",
                ),
                (
                    types.SimpleNamespace(
                        cu_count=32,
                        governor_service="active",
                        errors=(UserMessage("status.cu_mismatch", {"current": 32}),),
                        cu_state=UserMessage("status.cu_mismatch", {"current": 32}),
                    ),
                    "Partially available",
                ),
                (
                    types.SimpleNamespace(
                        cu_count=32,
                        governor_service="active",
                        errors=(UserMessage("error.amdgpu_missing"),),
                        cu_state=UserMessage("status.cu_mismatch", {"current": 32}),
                    ),
                    "The AMDGPU driver is not available.",
                ),
                (
                    types.SimpleNamespace(
                        cu_count=40,
                        governor_service="active",
                        errors=(UserMessage("error.amdgpu_missing"),),
                        cu_state=UserMessage("status.cu_applied", {"count": 40}),
                    ),
                    "The AMDGPU driver is not available.",
                ),
            )
            for snapshot, expected in cases:
                with self.subTest(expected=expected):
                    status = method(window, snapshot)
                    self.assertEqual(status, expected)
                    self.assertNotRegex(status, r"\d+\s*/\s*40\s+CU")

    def test_hero_badge_prioritizes_errors_over_normal_shortcut(self):
        render_node = find_window_method("_render_snapshot")
        calls = [
            ast.unparse(call)
            for call in ast.walk(render_node)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "_hero_badge"
        ]
        self.assertEqual(calls, ["self._hero_badge(snapshot)"])

        method, _node = load_window_method("_hero_badge")
        cases = (
            (
                types.SimpleNamespace(cu_count=40, governor_service="active", errors=()),
                ("status.normal", "status-good"),
            ),
            (
                types.SimpleNamespace(
                    cu_count=40,
                    governor_service="active",
                    errors=(UserMessage("error.amdgpu_missing"),),
                ),
                ("status.needs_attention", "status-warn"),
            ),
            (
                types.SimpleNamespace(cu_count=32, governor_service="active", errors=()),
                ("status.partial", "status-warn"),
            ),
        )
        for snapshot, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(method(object(), snapshot), expected)


if __name__ == "__main__":
    unittest.main()
