import ast
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from bc250.i18n import Translator
from bc250.messages import UserMessage
from bc250.presets import PRESETS


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

    def set_sensitive(self, value):
        self.sensitive = value


class ValueWidget:
    def __init__(self, value):
        self.value = value

    def set_value(self, value):
        self.value = value


class SelectionWidget:
    def __init__(self, selected):
        self.selected = selected

    def set_selected(self, selected):
        self.selected = selected


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
        self.assertIn("set_default_size(600, 700)", source)
        self.assertNotIn("Gtk.ScrolledWindow", source)
        self.assertNotIn("Gtk.FlowBox", source)
        self.assertIn("self.cpu_core_value", source)
        self.assertIn("self.cpu_mode_toggle", source)
        self.assertIn("self.custom_min_spin", source)
        self.assertIn("self.custom_max_spin", source)
        self.assertIn("self.language_dropdown", source)
        self.assertIn("def _retranslate", source)

    def test_layout_probe_reports_actual_window_and_content_fit(self):
        source = Path("app.py").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--layout-check"', source)
        self.assertIn("Layout: window=", source)
        self.assertIn("content-natural=", source)
        self.assertIn("fits=", source)

    def test_header_is_single_line_with_left_status_and_borderless_language_selector(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        build = ast.unparse(find_window_method("_build_ui"))
        self.assertNotIn("app_subtitle", source)
        self.assertIn("header.pack_start(self.status_badge)", build)
        self.assertIn("self.language_dropdown.add_css_class('language-selector')", build)

    def test_header_uses_compact_titlebar_controls(self):
        css = Path("bc250/style.css").read_text(encoding="utf-8")
        self.assertIn("min-height: 30px;", css_rule(css, ".app-header"))
        window_controls = css_rule(css, ".app-header windowcontrols button")
        self.assertIn("min-height: 24px;", window_controls)
        self.assertIn("min-width: 24px;", window_controls)

    def test_hero_hardware_summary_includes_cpu_and_gpu_core_counts(self):
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
            "BC-250 · CPU 6C/12T · GPU 40/40 CU",
        )
        missing = types.SimpleNamespace(cpu_cores=None, cpu_threads=None, cu_count=None)
        self.assertEqual(
            formatter(object(), missing),
            "BC-250 · CPU — · GPU —",
        )

    def test_cpu_temperature_precedes_gpu_temperature_in_telemetry_grid(self):
        build = ast.unparse(find_window_method("_build_ui"))
        self.assertLess(build.index("self.metric_cpu_temp"), build.index("self.metric_gpu_temp"))
        card_tuple = build[build.index("for index, card in enumerate"):]
        self.assertLess(card_tuple.index("self.metric_cpu_temp"), card_tuple.index("self.metric_gpu_temp"))

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
        )
        visibility = []
        install = SensitiveWidget()
        with tempfile.TemporaryDirectory() as tmp:
            translator = Translator(Path("bc250/locales"), Path(tmp) / "settings.json", {"LANG": "en_US.UTF-8"})
            window = types.SimpleNamespace(
                project_root=Path("."),
                translator=translator,
                _controls_sensitive=True,
                setup_banner=types.SimpleNamespace(set_visible=visibility.append),
                setup_detail=types.SimpleNamespace(set_text=lambda _value: None),
                install_button=install,
                _render_message=lambda message: translator.render(message),
            )
            refresh.__globals__.update({"inspect": lambda _root: report, "UserMessage": UserMessage})
            refresh(window)

        self.assertEqual(visibility, [True])
        self.assertFalse(install.sensitive)

    def test_component_install_starts_with_one_click_without_second_confirmation(self):
        node = find_window_method("_on_install")
        calls = [
            call.func.attr
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
        ]
        self.assertIn("_run_async", calls)
        self.assertNotIn("_confirm", calls)

    def test_status_refresh_does_not_overwrite_user_tuning_values(self):
        apply_snapshot, _node = load_window_method("_apply_snapshot")
        apply_snapshot.__globals__["PRESETS"] = PRESETS
        window = types.SimpleNamespace(
            _refreshing=True,
            _alive=True,
            _last_snapshot=None,
            _settings_hydrated=True,
            custom_min_spin=ValueWidget(650),
            custom_max_spin=ValueWidget(1750),
            throttle_spin=ValueWidget(88),
            recovery_spin=ValueWidget(78),
            profile_dropdown=SelectionWidget(3),
            cu_dropdown=SelectionWidget(1),
            PRESET_KEYS=(*PRESETS.keys(), "custom"),
            CU_VALUES=(24, 32, 40),
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
        )

        apply_snapshot(window, refreshed)

        self.assertEqual(window.custom_min_spin.value, 650)
        self.assertEqual(window.custom_max_spin.value, 1750)
        self.assertEqual(window.throttle_spin.value, 88)
        self.assertEqual(window.recovery_spin.value, 78)
        self.assertEqual(window.profile_dropdown.selected, 3)
        self.assertEqual(window.cu_dropdown.selected, 1)

    def test_cpu_control_is_a_reversible_toggle_and_can_auto_install_helper(self):
        source = Path("bc250/window.py").read_text(encoding="utf-8")
        self.assertIn("Gtk.ToggleButton", source)
        self.assertIn("def _on_cpu_mode_toggled", source)
        self.assertIn("install_then_set_cpu_mode", source)
        self.assertNotIn("self.cpu_unlock_button", source)

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
        widgets = [SensitiveWidget() for _ in range(4)]
        window = types.SimpleNamespace(
            project_root=Path("."),
            _controls_sensitive=True,
            setup_banner=types.SimpleNamespace(set_visible=lambda _value: None),
            setup_detail=types.SimpleNamespace(set_text=lambda _value: None),
            apply_button=widgets[0],
            save_button=widgets[1],
            cu_save_button=widgets[2],
            install_button=widgets[3],
            _render_message=lambda _message: "unsupported",
        )
        refresh.__globals__.update({"inspect": lambda _root: report, "UserMessage": UserMessage})

        refresh(window)
        set_sensitive(window, True)

        self.assertEqual([widget.sensitive for widget in widgets[:3]], [True, True, True])
        self.assertFalse(widgets[3].sensitive)

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
