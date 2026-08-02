from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from .bootstrap import inspect
from .control import CommandResult, GovernorController, PrivilegedRunner
from .i18n import Translator
from .messages import MessageError, UserMessage
from .power import PowerController, PowerState
from .presets import (
    MAX_TEMPERATURE_C,
    PRESETS,
    UINT32_MAX,
    validate_frequency_range,
    validate_temperature,
)
from .status import UNKNOWN, FanReading, StatusCollector, StatusSnapshot, SystemInfo


class MetricCard(Gtk.Box):
    def __init__(self, title: str = "", value: str = UNKNOWN, detail: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.add_css_class("telemetry-cell")
        self.set_hexpand(True)

        self.title = Gtk.Label(label=title, xalign=0)
        self.title.add_css_class("metric-title")
        self.append(self.title)

        self.value = Gtk.Label(label=value, xalign=0)
        self.value.add_css_class("metric-value")
        self.value.set_ellipsize(3)
        self.append(self.value)

        self.detail = Gtk.Label(label=detail, xalign=0, wrap=True)
        self.detail.add_css_class("metric-detail")
        self.detail.set_visible(False)
        self.append(self.detail)

    def update(self, value: str, detail: str | None = None) -> None:
        self.value.set_text(value)
        if detail is not None:
            self.detail.set_text(detail)


class InfoRow(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.add_css_class("info-row")
        self.label = Gtk.Label(xalign=0)
        self.label.add_css_class("info-key")
        self.label.set_hexpand(True)
        self.append(self.label)
        self.value = Gtk.Label(xalign=1, selectable=True)
        self.value.add_css_class("info-value")
        self.append(self.value)


class MainWindow(Gtk.ApplicationWindow):
    LANGUAGE_CODES = ("auto", "ko", "en", "ja", "zh-CN")
    PRESET_KEYS = (*PRESETS.keys(), "custom")
    CU_VALUES = (24, 32, 40)
    POWER_PRESET_MINUTES = (0, 5, 10, 15, 30, 60)

    def __init__(
        self,
        application: Gtk.Application,
        project_root: Path,
        translator: Translator,
        demo: bool = False,
    ) -> None:
        super().__init__(application=application, title=translator.gettext("app.title"))
        self.project_root = Path(project_root)
        self.translator = translator
        self.demo = demo
        self.collector = StatusCollector()
        self.controller = GovernorController()
        self.power_controller = PowerController()
        self.privileged = PrivilegedRunner(self.project_root)
        self._alive = True
        self._refreshing = False
        self._changing_language = False
        self._changing_cpu_toggle = False
        self._changing_power_controls = False
        self._controls_sensitive = True
        self._install_eligible = False
        self._governor_ready = False
        self._helper_ready = False
        self._cpu_mode_eligible = False
        self._cpu_mode_pending = False
        self._cpu_mode_target = False
        self._settings_hydrated = False
        self._last_snapshot: StatusSnapshot | None = None
        self._last_power_state: PowerState | None = None
        self.set_default_size(600, 700)
        self.set_size_request(560, 650)
        self.connect("close-request", self._on_close)
        self._build_ui()
        self._retranslate()
        self._schedule_refresh()
        GLib.timeout_add_seconds(1, self._schedule_refresh)

    def _build_ui(self) -> None:
        header = Gtk.HeaderBar()
        header.add_css_class("app-header")
        self.app_title = Gtk.Label(xalign=0)
        self.app_title.add_css_class("app-title")
        self.app_title.set_ellipsize(3)
        header.set_title_widget(self.app_title)

        self.status_badge = Gtk.Label()
        self.status_badge.add_css_class("status-badge")
        self.status_badge.add_css_class("status-warn")
        header.pack_start(self.status_badge)

        self.language_dropdown = Gtk.DropDown.new_from_strings(
            ["Auto · System", "한국어", "English", "日本語", "简体中文"]
        )
        self.language_dropdown.add_css_class("language-selector")
        self.language_dropdown.set_selected(self.LANGUAGE_CODES.index(self.translator.language))
        self.language_dropdown.connect("notify::selected", self._on_language_changed)
        header.pack_end(self.language_dropdown)
        self.last_update = Gtk.Label()
        self.last_update.add_css_class("last-update")
        header.pack_end(self.last_update)
        self.set_titlebar(header)

        dashboard = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        dashboard.add_css_class("dashboard")
        dashboard.set_margin_top(3)
        dashboard.set_margin_bottom(3)
        dashboard.set_margin_start(9)
        dashboard.set_margin_end(9)
        self.set_child(dashboard)

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hero.add_css_class("overview-strip")
        self.hero_title = Gtk.Label(label="BC-250", xalign=0)
        self.hero_title.add_css_class("hero-title")
        self.hero_title.set_hexpand(True)
        self.hero_title.set_ellipsize(3)
        hero.append(self.hero_title)
        self.hero_state = Gtk.Label()
        self.hero_state.add_css_class("hero-state")
        hero.append(self.hero_state)
        dashboard.append(hero)

        self.setup_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.setup_banner.add_css_class("setup-banner")
        setup_text = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        setup_text.set_hexpand(True)
        self.setup_title = Gtk.Label(xalign=0)
        self.setup_title.add_css_class("setup-title")
        self.setup_detail = Gtk.Label(xalign=0)
        self.setup_detail.set_hexpand(True)
        self.setup_detail.set_ellipsize(3)
        self.setup_detail.add_css_class("setup-detail")
        setup_text.append(self.setup_title)
        setup_text.append(self.setup_detail)
        self.setup_banner.append(setup_text)
        self.install_button = Gtk.Button()
        self.install_button.add_css_class("primary-action")
        self.install_button.connect("clicked", self._on_install)
        self.setup_banner.append(self.install_button)
        dashboard.append(self.setup_banner)

        telemetry_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.telemetry_title = Gtk.Label(xalign=0)
        self.telemetry_title.add_css_class("eyebrow")
        telemetry_box.append(self.telemetry_title)
        metrics = Gtk.Grid(column_spacing=0, row_spacing=0)
        metrics.add_css_class("telemetry-band")
        metrics.set_column_homogeneous(True)
        self.metric_cpu_temp = MetricCard()
        self.metric_cpu_temp.add_css_class("metric-cpu")
        self.metric_gpu_temp = MetricCard()
        self.metric_gpu_temp.add_css_class("metric-gpu")
        self.metric_power = MetricCard()
        self.metric_power.add_css_class("metric-power")
        self.metric_clock = MetricCard()
        self.metric_clock.add_css_class("metric-clock")
        self.metric_voltage = MetricCard()
        self.metric_voltage.add_css_class("metric-voltage")
        self.metric_fan = MetricCard()
        self.metric_fan.add_css_class("metric-fan")
        for index, card in enumerate((
            self.metric_cpu_temp,
            self.metric_gpu_temp,
            self.metric_power,
            self.metric_clock,
            self.metric_voltage,
            self.metric_fan,
        )):
            if index % 3 != 2:
                card.add_css_class("divider-right")
            if index < 3:
                card.add_css_class("divider-bottom")
            metrics.attach(card, index % 3, index // 3, 1, 1)
        telemetry_box.append(metrics)
        dashboard.append(telemetry_box)

        dashboard.append(self._build_tuning_surface())
        dashboard.append(self._build_core_surface())
        dashboard.append(self._build_power_surface())
        dashboard.append(self._build_system_strip())

    def _section_header(self) -> tuple[Gtk.Box, Gtk.Label, Gtk.Label]:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading = Gtk.Label(xalign=0)
        heading.add_css_class("section-title")
        detail = Gtk.Label(xalign=0, wrap=True)
        detail.add_css_class("section-subtitle")
        box.append(heading)
        box.append(detail)
        return box, heading, detail

    def _build_tuning_surface(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        card.add_css_class("tuning-surface")
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.control_title = Gtk.Label(xalign=0)
        self.control_title.add_css_class("section-title")
        self.control_title.set_hexpand(True)
        self.governor_value = Gtk.Label(xalign=1)
        self.governor_value.add_css_class("inline-status")
        title_row.append(self.control_title)
        title_row.append(self.governor_value)
        card.append(title_row)

        profile_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.profile_label = Gtk.Label(xalign=0)
        self.profile_label.add_css_class("field-label")
        self.profile_label.set_size_request(112, -1)
        self.profile_dropdown = Gtk.DropDown.new_from_strings([])
        self.profile_dropdown.set_selected(2)
        self.profile_dropdown.set_hexpand(True)
        self.profile_dropdown.connect("notify::selected", self._on_profile_changed)
        profile_row.append(self.profile_label)
        profile_row.append(self.profile_dropdown)
        card.append(profile_row)

        settings_grid = Gtk.Grid(column_spacing=8, row_spacing=3)
        settings_grid.set_column_homogeneous(True)
        self.custom_min_label = Gtk.Label(xalign=0)
        self.custom_min_label.add_css_class("field-label")
        self.custom_max_label = Gtk.Label(xalign=0)
        self.custom_max_label.add_css_class("field-label")
        self.throttle_label = Gtk.Label(xalign=0)
        self.throttle_label.add_css_class("field-label")
        self.recovery_label = Gtk.Label(xalign=0)
        self.recovery_label.add_css_class("field-label")
        self.custom_min_spin = Gtk.SpinButton.new_with_range(0, UINT32_MAX, 25)
        self.custom_min_spin.set_value(500)
        self.custom_max_spin = Gtk.SpinButton.new_with_range(0, UINT32_MAX, 25)
        self.custom_max_spin.set_value(1800)
        self.throttle_spin = Gtk.SpinButton.new_with_range(0, MAX_TEMPERATURE_C, 1)
        self.throttle_spin.set_value(85)
        self.recovery_spin = Gtk.SpinButton.new_with_range(0, MAX_TEMPERATURE_C, 1)
        self.recovery_spin.set_value(75)
        for column, (label, spin) in enumerate((
            (self.custom_min_label, self.custom_min_spin),
            (self.custom_max_label, self.custom_max_spin),
            (self.throttle_label, self.throttle_spin),
            (self.recovery_label, self.recovery_spin),
        )):
            settings_grid.attach(label, column, 0, 1, 1)
            settings_grid.attach(spin, column, 1, 1, 1)
        card.append(settings_grid)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.apply_button = Gtk.Button()
        self.apply_button.add_css_class("secondary-action")
        self.apply_button.set_hexpand(True)
        self.apply_button.connect("clicked", self._on_apply_runtime)
        self.save_button = Gtk.Button()
        self.save_button.add_css_class("primary-action")
        self.save_button.set_hexpand(True)
        self.save_button.connect("clicked", self._on_save_persistent)
        action_row.append(self.apply_button)
        action_row.append(self.save_button)
        card.append(action_row)
        return card

    def _build_core_surface(self) -> Gtk.Box:
        surface = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        surface.add_css_class("core-surface")
        self.core_title = Gtk.Label(xalign=0)
        self.core_title.add_css_class("section-title")
        surface.append(self.core_title)

        grid = Gtk.Grid(column_spacing=0, row_spacing=0)
        grid.set_column_homogeneous(True)
        cpu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        cpu.add_css_class("core-pane")
        cpu.add_css_class("divider-right")
        self.cpu_label = Gtk.Label(xalign=0)
        self.cpu_label.add_css_class("field-label")
        self.cpu_core_value = Gtk.Label(xalign=0)
        self.cpu_core_value.add_css_class("core-value")
        self.cpu_state_label = Gtk.Label(xalign=0)
        self.cpu_state_label.add_css_class("core-state")
        self.cpu_mode_toggle = Gtk.ToggleButton()
        self.cpu_mode_toggle.add_css_class("cpu-action")
        self.cpu_mode_toggle.connect("toggled", self._on_cpu_mode_toggled)
        cpu.append(self.cpu_label)
        cpu.append(self.cpu_core_value)
        cpu.append(self.cpu_state_label)
        cpu.append(self.cpu_mode_toggle)

        gpu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        gpu.add_css_class("core-pane")
        self.gpu_label = Gtk.Label(xalign=0)
        self.gpu_label.add_css_class("field-label")
        self.gpu_core_value = Gtk.Label(xalign=0)
        self.gpu_core_value.add_css_class("core-value")
        self.cu_state_label = Gtk.Label(xalign=0)
        self.cu_state_label.add_css_class("core-state")
        cu_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.cu_dropdown = Gtk.DropDown.new_from_strings([])
        self.cu_dropdown.set_selected(2)
        self.cu_dropdown.set_hexpand(True)
        cu_row.append(self.cu_dropdown)
        self.cu_save_button = Gtk.Button()
        self.cu_save_button.add_css_class("warning-action")
        self.cu_save_button.connect("clicked", self._on_save_cu)
        cu_row.append(self.cu_save_button)
        gpu.append(self.gpu_label)
        gpu.append(self.gpu_core_value)
        gpu.append(self.cu_state_label)
        gpu.append(cu_row)
        grid.attach(cpu, 0, 0, 1, 1)
        grid.attach(gpu, 1, 0, 1, 1)
        surface.append(grid)
        return surface

    def _build_power_surface(self) -> Gtk.Box:
        surface = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        surface.add_css_class("power-surface")

        identity = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        identity.set_hexpand(True)
        self.power_title = Gtk.Label(xalign=0)
        self.power_title.add_css_class("section-title")
        self.power_idle_value = Gtk.Label(xalign=0)
        self.power_idle_value.add_css_class("power-idle-value")
        self.power_idle_value.set_ellipsize(3)
        identity.append(self.power_title)
        identity.append(self.power_idle_value)
        surface.append(identity)

        controls = Gtk.Grid(column_spacing=6, row_spacing=1)
        controls.set_column_homogeneous(True)

        self.power_suspend_label = Gtk.Label(xalign=0)
        self.power_suspend_label.add_css_class("field-label")
        self.power_suspend_dropdown = Gtk.DropDown.new_from_strings([])
        self.power_suspend_dropdown.add_css_class("power-dropdown")
        self.power_suspend_dropdown.set_size_request(108, -1)
        self.power_suspend_custom_spin = Gtk.SpinButton.new_with_range(1, 240, 1)
        self.power_suspend_custom_spin.set_value(15)
        self.power_suspend_custom_spin.set_width_chars(3)
        self.power_suspend_custom_spin.set_visible(False)
        suspend_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        suspend_row.append(self.power_suspend_dropdown)
        suspend_row.append(self.power_suspend_custom_spin)
        controls.attach(self.power_suspend_label, 0, 0, 1, 1)
        controls.attach(suspend_row, 0, 1, 1, 1)

        self.power_display_label = Gtk.Label(xalign=0)
        self.power_display_label.add_css_class("field-label")
        self.power_display_dropdown = Gtk.DropDown.new_from_strings([])
        self.power_display_dropdown.add_css_class("power-dropdown")
        self.power_display_dropdown.set_size_request(108, -1)
        self.power_display_custom_spin = Gtk.SpinButton.new_with_range(1, 240, 1)
        self.power_display_custom_spin.set_value(5)
        self.power_display_custom_spin.set_width_chars(3)
        self.power_display_custom_spin.set_visible(False)
        display_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        display_row.append(self.power_display_dropdown)
        display_row.append(self.power_display_custom_spin)
        controls.attach(self.power_display_label, 1, 0, 1, 1)
        controls.attach(display_row, 1, 1, 1, 1)

        self.power_suspend_dropdown.connect("notify::selected", self._on_power_suspend_changed)
        self.power_display_dropdown.connect("notify::selected", self._on_power_display_changed)
        self.power_suspend_custom_spin.connect("value-changed", self._on_power_suspend_custom_changed)
        self.power_display_custom_spin.connect("value-changed", self._on_power_display_custom_changed)
        surface.append(controls)
        return surface

    def _build_system_strip(self) -> Gtk.Box:
        strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        strip.add_css_class("system-strip")
        bios = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        bios.add_css_class("system-cell")
        bios.add_css_class("divider-right")
        self.bios_label = Gtk.Label(xalign=0)
        self.bios_label.add_css_class("field-label")
        self.bios_value = Gtk.Label(xalign=0, selectable=True)
        self.bios_value.add_css_class("system-value")
        self.bios_value.set_ellipsize(3)
        bios.append(self.bios_label)
        bios.append(self.bios_value)
        kernel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        kernel.add_css_class("system-cell")
        self.kernel_label = Gtk.Label(xalign=0)
        self.kernel_label.add_css_class("field-label")
        self.kernel_value = Gtk.Label(xalign=0, selectable=True)
        self.kernel_value.add_css_class("system-value")
        self.kernel_value.set_ellipsize(3)
        kernel.append(self.kernel_label)
        kernel.append(self.kernel_value)
        strip.append(bios)
        strip.append(kernel)
        return strip

    def _on_language_changed(self, dropdown, _param) -> None:
        if self._changing_language:
            return
        code = self.LANGUAGE_CODES[dropdown.get_selected()]
        try:
            self.translator.set_language(code)
        except OSError as exc:
            self._retranslate()
            self._show_message(
                self.translator.gettext("status.error"),
                self.translator.gettext("error.locale_save") + f"\n{exc}",
                Gtk.MessageType.ERROR,
            )
            return
        self._retranslate()

    def _render_message(self, message: UserMessage | str) -> str:
        return self.translator.render(message) if isinstance(message, UserMessage) else str(message)

    def _exception_text(self, error: Exception) -> str:
        if isinstance(error, MessageError):
            return self.translator.render(error.message)
        return str(error)

    def _payload_text(self, payload: dict, fallback_key: str) -> str:
        raw_value = payload.get("message")
        raw_diagnostic = str(raw_value).strip() if raw_value is not None else ""
        message_id = payload.get("message_id")
        if isinstance(message_id, str):
            args = payload.get("message_args")
            render_args = dict(args) if isinstance(args, dict) else {}
            if raw_diagnostic and "detail" not in render_args:
                render_args["detail"] = raw_diagnostic
            rendered = self.translator.gettext(message_id, **render_args)
            if rendered == message_id:
                rendered = self.translator.gettext(fallback_key)
                if rendered == fallback_key and raw_diagnostic:
                    rendered = ""
            if payload.get("ok") is True:
                return rendered or self.translator.gettext(fallback_key)
            if message_id.startswith("platform.") and rendered:
                return rendered
            if raw_diagnostic and raw_diagnostic not in rendered:
                return f"{rendered}\n\n{raw_diagnostic}" if rendered else raw_diagnostic
            return rendered or raw_diagnostic or self.translator.gettext(fallback_key)
        return raw_diagnostic or self.translator.gettext(fallback_key)

    def _retranslate(self) -> None:
        preset_index = self.profile_dropdown.get_selected()
        cu_index = self.cu_dropdown.get_selected()
        power_suspend_index = self.power_suspend_dropdown.get_selected()
        power_display_index = self.power_display_dropdown.get_selected()
        if preset_index >= len(self.PRESET_KEYS):
            preset_index = 2
        if cu_index >= len(self.CU_VALUES):
            cu_index = 2
        if power_suspend_index > len(self.POWER_PRESET_MINUTES):
            power_suspend_index = self.POWER_PRESET_MINUTES.index(15)
        if power_display_index > len(self.POWER_PRESET_MINUTES):
            power_display_index = self.POWER_PRESET_MINUTES.index(5)

        self.set_title(self.translator.gettext("app.title"))
        self.app_title.set_text(self.translator.gettext("app.title"))
        self.setup_title.set_text(self.translator.gettext("setup.title"))
        self.install_button.set_label(self.translator.gettext("setup.install"))
        self.telemetry_title.set_text(self.translator.gettext("section.telemetry"))

        metric_text = (
            (self.metric_cpu_temp, "metric.cpu_temperature", "metric.tctl_sensor"),
            (self.metric_gpu_temp, "metric.gpu_temperature", "metric.edge_sensor"),
            (self.metric_power, "metric.power", "metric.driver_report"),
            (self.metric_clock, "metric.clock", "metric.instant_sensor"),
            (self.metric_voltage, "metric.voltage", "metric.smu_voltage"),
            (self.metric_fan, "metric.fan", None),
        )
        for card, title_key, detail_key in metric_text:
            card.title.set_text(self.translator.gettext(title_key))
            if self._last_snapshot is None:
                card.value.set_text(self.translator.gettext("common.unavailable"))
                card.detail.set_text(self.translator.gettext(detail_key) if detail_key else "")

        self.control_title.set_text(self.translator.gettext("section.control"))
        self.core_title.set_text(self.translator.gettext("section.core_control"))
        self.profile_label.set_text(self.translator.gettext("field.performance_preset"))
        self.custom_min_label.set_text(self.translator.gettext("field.custom_min_clock"))
        self.custom_max_label.set_text(self.translator.gettext("field.custom_max_clock"))
        self.throttle_label.set_text(self.translator.gettext("field.throttle"))
        self.recovery_label.set_text(self.translator.gettext("field.recovery"))
        self.cpu_label.set_text(self.translator.gettext("field.cpu_cores"))
        self.gpu_label.set_text(self.translator.gettext("field.gpu_cu"))
        self.bios_label.set_text(self.translator.gettext("field.bios_version"))
        self.kernel_label.set_text(self.translator.gettext("field.kernel"))
        self.power_title.set_text(self.translator.gettext("section.power_idle"))
        self.power_suspend_label.set_text(self.translator.gettext("power.suspend_block"))
        self.power_display_label.set_text(self.translator.gettext("power.display_block"))
        self.apply_button.set_label(self.translator.gettext("action.apply_now"))
        self.save_button.set_label(self.translator.gettext("action.apply_and_save"))
        self.cu_save_button.set_label(self.translator.gettext("action.save_boot_profile"))
        self._sync_cpu_toggle(self._cpu_mode_active())

        preset_names = [
            f"{self.translator.gettext(item.label_key)} · {item.max_mhz} MHz / {item.max_mv} mV"
            for item in PRESETS.values()
        ]
        preset_names.append(self.translator.gettext("preset.custom"))
        cu_names = [self.translator.gettext(f"cu.{count}") for count in self.CU_VALUES]
        power_names = [self.translator.gettext("power.blocked")]
        power_names.extend(
            self.translator.gettext("power.minutes", minutes=minutes)
            for minutes in self.POWER_PRESET_MINUTES[1:]
        )
        power_names.append(self.translator.gettext("preset.custom"))
        self._changing_power_controls = True
        self.profile_dropdown.set_model(Gtk.StringList.new(preset_names))
        self.cu_dropdown.set_model(Gtk.StringList.new(cu_names))
        self.power_suspend_dropdown.set_model(Gtk.StringList.new(power_names))
        self.power_display_dropdown.set_model(Gtk.StringList.new(power_names))
        self.profile_dropdown.set_selected(preset_index)
        self.cu_dropdown.set_selected(cu_index)
        self.power_suspend_dropdown.set_selected(power_suspend_index)
        self.power_display_dropdown.set_selected(power_display_index)
        self._changing_power_controls = False
        self._update_power_custom_visibility()
        self._update_custom_controls()

        self._changing_language = True
        self.language_dropdown.set_selected(self.LANGUAGE_CODES.index(self.translator.language))
        self._changing_language = False
        self._refresh_bootstrap()
        if self._last_snapshot is not None:
            self._render_snapshot(self._last_snapshot)
        else:
            self.last_update.set_text(self.translator.gettext("common.just_now"))
            self._set_badge(self.translator.gettext("status.checking"), "status-warn")
            self.hero_state.set_text(self.translator.gettext("status.checking"))
            unavailable = self.translator.gettext("common.unavailable")
            self.cpu_core_value.set_text(unavailable)
            self.gpu_core_value.set_text(unavailable)
            self.cpu_state_label.set_text(self.translator.gettext("status.checking"))
            self.cu_state_label.set_text(self.translator.gettext("status.checking"))
            self.bios_value.set_text(self.translator.gettext("status.checking"))
            self.kernel_value.set_text(self.translator.gettext("status.checking"))
            self.governor_value.set_text(self.translator.gettext("status.checking"))
            self.power_idle_value.set_text(self.translator.gettext("status.checking"))

    def _on_profile_changed(self, _dropdown, _param) -> None:
        self._update_custom_controls()

    def _update_custom_controls(self) -> None:
        custom = self.profile_dropdown.get_selected() == self.PRESET_KEYS.index("custom")
        for widget in (self.custom_min_label, self.custom_max_label, self.custom_min_spin, self.custom_max_spin):
            widget.set_sensitive(custom and self._controls_sensitive)

    def _on_close(self, *_args) -> bool:
        self._alive = False
        return False

    def _set_badge(self, text: str, style: str) -> None:
        for css_class in ("status-good", "status-warn", "status-error"):
            self.status_badge.remove_css_class(css_class)
        self.status_badge.add_css_class(style)
        self.status_badge.set_text(text)

    def _cpu_mode_active(self) -> bool:
        return bool(
            self._cpu_mode_pending
            or (
                self._last_snapshot is not None
                and self._last_snapshot.cpu_threads is not None
                and self._last_snapshot.cpu_threads >= 16
            )
        )

    def _sync_cpu_toggle(self, active: bool) -> None:
        self._changing_cpu_toggle = True
        self.cpu_mode_toggle.set_active(active)
        self.cpu_mode_toggle.set_label(
            self.translator.gettext("action.disable_cpu" if active else "action.enable_cpu")
        )
        self._changing_cpu_toggle = False

    def _apply_power_state(self, state: PowerState) -> None:
        self._last_power_state = state
        self.power_idle_value.set_text(
            self.translator.gettext(
                "power.hardware_idle",
                cpu=state.cpu_idle_mode,
                gpu=state.gpu_dpm_mode,
            )
        )
        self._changing_power_controls = True
        self._set_power_control_state(
            self.power_suspend_dropdown,
            self.power_suspend_custom_spin,
            state.suspend_minutes,
            15,
        )
        self._set_power_control_state(
            self.power_display_dropdown,
            self.power_display_custom_spin,
            state.display_minutes,
            5,
        )
        self._changing_power_controls = False
        self._update_power_custom_visibility()

    def _set_power_control_state(self, dropdown, custom_spin, minutes: int | None, fallback: int) -> None:
        current = fallback if minutes is None else max(0, min(240, int(minutes)))
        if current in self.POWER_PRESET_MINUTES:
            dropdown.set_selected(self.POWER_PRESET_MINUTES.index(current))
        else:
            custom_spin.set_value(max(1, current))
            dropdown.set_selected(len(self.POWER_PRESET_MINUTES))

    def _update_power_custom_visibility(self) -> None:
        custom_index = len(self.POWER_PRESET_MINUTES)
        self.power_suspend_custom_spin.set_visible(
            self.power_suspend_dropdown.get_selected() == custom_index
        )
        self.power_display_custom_spin.set_visible(
            self.power_display_dropdown.get_selected() == custom_index
        )

    def _refresh_bootstrap(self) -> None:
        report = inspect(self.project_root)
        self.setup_banner.set_visible(True)
        self._cpu_mode_eligible = False
        if not report.bundle.ok:
            self._governor_ready = False
            self._helper_ready = False
            detail = report.bundle.errors[0] if report.bundle.errors else UserMessage("error.bundle_invalid", {"detail": ""})
            self.setup_detail.set_text(self._render_message(detail))
            self._install_eligible = False
        elif not report.platform.supported:
            self._governor_ready = False
            self._helper_ready = False
            self.setup_detail.set_text(self._render_message(report.platform.message))
            self._install_eligible = False
        else:
            self._governor_ready = bool(report.governor_installed)
            self._helper_ready = bool(report.helper_installed)
            self._cpu_mode_eligible = True
            missing = []
            if not report.governor_installed:
                missing.append(self.translator.gettext("component.governor"))
            if not report.cu_manager_installed:
                missing.append(self.translator.gettext("component.cu_manager"))
            if not report.umr_installed:
                missing.append(self.translator.gettext("component.umr"))
            if not report.helper_installed:
                missing.append(self.translator.gettext("component.helper"))
            if not getattr(report, "cpu_mode_installed", False):
                missing.append(self.translator.gettext("component.cpu_mode"))
            if not getattr(report, "support_installed", False):
                missing.append(self.translator.gettext("component.support"))
            self.setup_detail.set_text(
                self.translator.gettext("setup.required", components=", ".join(missing))
                if missing
                else self.translator.gettext("setup.ready")
            )
            self._install_eligible = bool(missing)
        self.install_button.set_sensitive(self._controls_sensitive and self._install_eligible)
        cpu_toggle = getattr(self, "cpu_mode_toggle", None)
        if cpu_toggle is not None:
            cpu_toggle.set_sensitive(self._controls_sensitive and self._cpu_mode_eligible)

    def _schedule_refresh(self) -> bool:
        if not self._alive or self._refreshing:
            return self._alive
        self._refreshing = True
        thread = threading.Thread(target=self._collect_worker, daemon=True, name="bc250-status")
        thread.start()
        return self._alive

    def _demo_snapshot(self) -> StatusSnapshot:
        return StatusSnapshot(
            collected_at=datetime.now().astimezone(),
            cu_count=40,
            cu_saved_count=40,
            cu_state=UserMessage("status.cu_applied", {"count": 40}),
            cu_service="active",
            governor_service="active",
            governor_min=500,
            governor_max=1800,
            throttle=85,
            recovery=75,
            gpu_temperature="42.0 °C",
            cpu_temperature="43.8 °C",
            cpu_temperature_source="Tctl",
            fan=FanReading(1721, "Pump Fan", 1, "active"),
            power="36.2 W",
            voltage="699 mV",
            clock="1800 MHz",
            system=SystemInfo("American Megatrends", "Robin5.00", "07/01/2026", "6.19.8-bazzite", "x86_64"),
            cpu_cores=6,
            cpu_threads=12,
        )

    @staticmethod
    def _demo_power_state() -> PowerState:
        return PowerState("MWAIT", "auto", True, True, 0, 0)

    def _collect_worker(self) -> None:
        try:
            snapshot = self._demo_snapshot() if self.demo else self.collector.collect()
            power_state = self._demo_power_state() if self.demo else self.power_controller.inspect()
            GLib.idle_add(self._apply_snapshot, snapshot, power_state)
        except Exception as exc:
            GLib.idle_add(self._refresh_failed, str(exc))

    def _apply_snapshot(self, snapshot: StatusSnapshot, power_state: PowerState | None = None) -> bool:
        self._refreshing = False
        if not self._alive:
            return False
        self._last_snapshot = snapshot
        if not self._settings_hydrated:
            if snapshot.governor_min is not None:
                self.custom_min_spin.set_value(snapshot.governor_min)
            if snapshot.governor_max is not None:
                self.custom_max_spin.set_value(snapshot.governor_max)
            preset_ranges = tuple((item.min_mhz, item.max_mhz) for item in PRESETS.values())
            current_range = (snapshot.governor_min, snapshot.governor_max)
            if current_range in preset_ranges:
                self.profile_dropdown.set_selected(preset_ranges.index(current_range))
            elif snapshot.governor_min is not None and snapshot.governor_max is not None:
                self.profile_dropdown.set_selected(self.PRESET_KEYS.index("custom"))
            if snapshot.throttle is not None:
                self.throttle_spin.set_value(snapshot.throttle)
            if snapshot.recovery is not None:
                self.recovery_spin.set_value(snapshot.recovery)
            if snapshot.cu_saved_count in self.CU_VALUES:
                self.cu_dropdown.set_selected(self.CU_VALUES.index(snapshot.cu_saved_count))
            self._settings_hydrated = True
        self._render_snapshot(snapshot)
        if power_state is not None:
            self._apply_power_state(power_state)
        self._update_custom_controls()
        return False

    def _hero_status_text(self, snapshot) -> str:
        count_bearing_keys = {"status.cu_applied", "status.cu_mismatch"}
        if snapshot.errors:
            for error in snapshot.errors:
                if getattr(error, "key", "") not in count_bearing_keys:
                    return self._render_message(error)
            return self.translator.gettext("status.partial")
        if snapshot.cu_count == 40 and snapshot.governor_service == "active":
            return self.translator.gettext("common.active")
        return self.translator.gettext("status.partial")

    def _hero_hardware_text(self, snapshot) -> str:
        cpu_text = (
            f"{snapshot.cpu_cores}C/{snapshot.cpu_threads}T"
            if snapshot.cpu_cores is not None and snapshot.cpu_threads is not None
            else "—"
        )
        gpu_text = f"{snapshot.cu_count}/40 CU" if snapshot.cu_count is not None else "—"
        return f"BC-250 · CPU {cpu_text} · GPU {gpu_text}"

    def _hero_badge(self, snapshot):
        if snapshot.errors:
            return "status.needs_attention", "status-warn"
        if snapshot.cu_count == 40 and snapshot.governor_service == "active":
            return "status.normal", "status-good"
        return "status.partial", "status-warn"

    def _render_snapshot(self, snapshot: StatusSnapshot) -> None:
        def display(value: str) -> str:
            return self.translator.gettext("common.unavailable") if value == UNKNOWN else value

        self.metric_gpu_temp.update(display(snapshot.gpu_temperature), self.translator.gettext("metric.edge_sensor"))
        self.metric_cpu_temp.update(
            display(snapshot.cpu_temperature),
            snapshot.cpu_temperature_source or self.translator.gettext("common.unavailable"),
        )
        self.metric_power.update(display(snapshot.power), self.translator.gettext("metric.driver_report"))
        self.metric_clock.update(display(snapshot.clock), self.translator.gettext("metric.instant_sensor"))
        self.metric_voltage.update(display(snapshot.voltage), self.translator.gettext("metric.smu_voltage"))

        if snapshot.fan.state == "active":
            fan_value = f"{snapshot.fan.rpm:,} RPM"
            fan_detail = (
                snapshot.fan.label
                if snapshot.fan.active_count == 1
                else self.translator.gettext("metric.active_fans", count=snapshot.fan.active_count)
            )
        elif snapshot.fan.state == "stopped":
            fan_value = "0 RPM"
            fan_detail = self.translator.gettext("common.stopped_or_disconnected")
        else:
            fan_value = self.translator.gettext("common.unavailable")
            fan_detail = ""
        self.metric_fan.update(fan_value, fan_detail)

        info = snapshot.system
        if snapshot.governor_min is not None and snapshot.governor_max is not None:
            service = (
                self.translator.gettext("common.active")
                if snapshot.governor_service == "active"
                else display(snapshot.governor_service)
            )
            governor = f"{service} · {snapshot.governor_min}–{snapshot.governor_max} MHz"
        else:
            governor = display(snapshot.governor_service)
        self.governor_value.set_text(governor)
        self.bios_value.set_text(display(info.bios_version))
        self.bios_value.set_tooltip_text(
            f"{display(info.bios_vendor)} · {display(info.bios_version)} · {display(info.bios_date)}"
        )
        self.kernel_value.set_text(display(info.kernel_release))
        self.kernel_value.set_tooltip_text(f"{display(info.kernel_release)} · {display(info.architecture)}")

        if snapshot.cpu_cores is not None and snapshot.cpu_threads is not None:
            self.cpu_core_value.set_text(f"{snapshot.cpu_cores}C / {snapshot.cpu_threads}T")
        else:
            self.cpu_core_value.set_text(self.translator.gettext("common.unavailable"))
        if self._cpu_mode_pending:
            cpu_state_key = "status.cpu_pending"
        elif snapshot.cpu_threads is not None and snapshot.cpu_threads >= 16:
            cpu_state_key = "status.cpu_unlocked"
        elif snapshot.cpu_threads is not None:
            cpu_state_key = "status.cpu_stock"
        else:
            cpu_state_key = "common.unavailable"
        self.cpu_state_label.set_text(self.translator.gettext(cpu_state_key))
        self._sync_cpu_toggle(self._cpu_mode_pending or (snapshot.cpu_threads or 0) >= 16)

        self.gpu_core_value.set_text(
            f"{snapshot.cu_count}/40 CU"
            if snapshot.cu_count is not None
            else self.translator.gettext("common.unavailable")
        )
        self.cu_state_label.set_text(
            self._render_message(snapshot.cu_state)
            if snapshot.cu_state != UNKNOWN
            else self.translator.gettext("status.cu_saved", count=snapshot.cu_saved_count)
            if snapshot.cu_saved_count is not None
            else self.translator.gettext("common.unavailable")
        )
        self.last_update.set_text(
            self.translator.gettext("status.updated_at", time=snapshot.collected_at.strftime("%H:%M:%S"))
        )
        self.hero_title.set_text(self._hero_hardware_text(snapshot))
        badge_key, badge_style = self._hero_badge(snapshot)
        self._set_badge(self.translator.gettext(badge_key), badge_style)
        self.hero_state.set_text(self._hero_status_text(snapshot))
        self._set_controls_sensitive(self._controls_sensitive)

    def _refresh_failed(self, message: str) -> bool:
        self._refreshing = False
        self._set_badge(self.translator.gettext("status.error"), "status-error")
        self.hero_state.set_text(message)
        return False

    def _selected_values(self) -> tuple[str, int, int, int, int]:
        mode = self.PRESET_KEYS[self.profile_dropdown.get_selected()]
        throttle = self.throttle_spin.get_value_as_int()
        recovery = self.recovery_spin.get_value_as_int()
        validate_temperature(throttle, recovery)
        if mode == "custom":
            min_mhz, max_mhz = validate_frequency_range(
                int(self.custom_min_spin.get_value()),
                int(self.custom_max_spin.get_value()),
            )
        else:
            preset = PRESETS[mode]
            min_mhz, max_mhz = preset.min_mhz, preset.max_mhz
        return mode, min_mhz, max_mhz, throttle, recovery

    def _set_controls_sensitive(self, value: bool) -> None:
        self._controls_sensitive = value
        tuning_sensitive = value and self._governor_ready
        for name in ("apply_button", "save_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.set_sensitive(tuning_sensitive)
        cu_save_button = getattr(self, "cu_save_button", None)
        if cu_save_button is not None:
            cu_save_button.set_sensitive(value)
        cpu_toggle = getattr(self, "cpu_mode_toggle", None)
        if cpu_toggle is not None:
            cpu_toggle.set_sensitive(value and self._cpu_mode_eligible)
        for name in (
            "power_suspend_dropdown",
            "power_suspend_custom_spin",
            "power_display_dropdown",
            "power_display_custom_spin",
        ):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.set_sensitive(value)
        if hasattr(self, "profile_dropdown"):
            self.profile_dropdown.set_sensitive(value)
            self.cu_dropdown.set_sensitive(value)
            self.throttle_spin.set_sensitive(value)
            self.recovery_spin.set_sensitive(value)
            self._update_custom_controls()
        self.install_button.set_sensitive(value and self._install_eligible)

    def _run_async(self, function, done) -> None:
        self._set_controls_sensitive(False)

        def worker():
            try:
                result = function()
                GLib.idle_add(done, result)
            except Exception as exc:
                GLib.idle_add(self._operation_exception, exc)

        threading.Thread(target=worker, daemon=True, name="bc250-control").start()

    def _operation_exception(self, error: Exception) -> bool:
        self._set_controls_sensitive(True)
        self._show_message(
            self.translator.gettext("dialog.operation_failed"),
            self._exception_text(error),
            Gtk.MessageType.ERROR,
        )
        return False

    def _on_apply_runtime(self, _button) -> None:
        try:
            mode, min_mhz, max_mhz, throttle, recovery = self._selected_values()
        except ValueError as exc:
            self._show_message(
                self.translator.gettext("dialog.input_check"),
                self._exception_text(exc),
                Gtk.MessageType.WARNING,
            )
            return
        if mode == "custom":
            operation = lambda: self.controller.apply_custom(min_mhz, max_mhz, throttle, recovery)
        else:
            operation = lambda: self.controller.apply_runtime(mode, throttle, recovery)
        self._run_async(operation, self._runtime_done)

    def _runtime_done(self, result: CommandResult) -> bool:
        self._set_controls_sensitive(True)
        if result.ok:
            self._show_message(
                self.translator.gettext("dialog.apply_complete"),
                self.translator.gettext("dialog.apply_complete_detail"),
                Gtk.MessageType.INFO,
            )
            self._schedule_refresh()
        else:
            self._show_message(
                self.translator.gettext("dialog.apply_failed"),
                result.stderr or result.stdout,
                Gtk.MessageType.ERROR,
            )
        return False

    def _on_save_persistent(self, _button) -> None:
        try:
            mode, min_mhz, max_mhz, throttle, recovery = self._selected_values()
        except ValueError as exc:
            self._show_message(
                self.translator.gettext("dialog.input_check"),
                self._exception_text(exc),
                Gtk.MessageType.WARNING,
            )
            return
        self._confirm(
            self.translator.gettext("dialog.save_confirm"),
            self.translator.gettext("dialog.save_confirm_detail"),
            lambda: self._run_async(
                lambda: self.privileged.save_custom_settings(min_mhz, max_mhz, throttle, recovery)
                if mode == "custom"
                else self.privileged.save_settings(mode, throttle, recovery),
                self._privileged_done,
            ),
        )

    def _on_save_cu(self, _button) -> None:
        cu = self.CU_VALUES[self.cu_dropdown.get_selected()]
        self._confirm(
            self.translator.gettext("dialog.cu_confirm", count=cu),
            self.translator.gettext("dialog.cu_confirm_detail"),
            lambda: self._run_async(lambda: self.privileged.save_cu(cu), self._privileged_done),
        )

    def _on_cpu_mode_toggled(self, toggle) -> None:
        if self._changing_cpu_toggle:
            return
        target = bool(toggle.get_active())
        current = self._cpu_mode_active()
        self._sync_cpu_toggle(current)
        if target == current:
            return
        title_key = "dialog.cpu_enable_confirm" if target else "dialog.cpu_disable_confirm"
        detail_key = "dialog.cpu_enable_confirm_detail" if target else "dialog.cpu_disable_confirm_detail"
        self._confirm(
            self.translator.gettext(title_key),
            self.translator.gettext(detail_key),
            lambda: self._begin_cpu_mode_change(target),
        )

    def _begin_cpu_mode_change(self, target: bool) -> None:
        self._cpu_mode_target = target
        operation = (
            self.privileged.set_cpu_mode
            if self._helper_ready
            else self.privileged.install_then_set_cpu_mode
        )
        self._run_async(lambda: operation(target), lambda response: self._cpu_mode_done(target, response))

    def _cpu_mode_done(self, target: bool, response) -> bool:
        self._set_controls_sensitive(True)
        command, payload = response
        if command.returncode in (126, 127) and not payload.get("ok"):
            self._sync_cpu_toggle(self._cpu_mode_active())
            self._show_message(
                self.translator.gettext("dialog.cancelled"),
                self.translator.gettext("dialog.auth_cancelled"),
                Gtk.MessageType.INFO,
            )
        elif command.ok and payload.get("ok"):
            self._helper_ready = True
            self._cpu_mode_pending = bool(target and payload.get("reboot_required"))
            self._sync_cpu_toggle(target)
            self._refresh_bootstrap()
            self._show_message(
                self.translator.gettext("dialog.cpu_mode_complete"),
                self._payload_text(
                    payload,
                    "helper.cpu_mode_enabled" if target else "helper.cpu_mode_disabled",
                ),
                Gtk.MessageType.INFO,
            )
            self._schedule_refresh()
        else:
            self._sync_cpu_toggle(self._cpu_mode_active())
            self._show_message(
                self.translator.gettext("dialog.cpu_mode_failed"),
                self._payload_text(payload, "dialog.cpu_mode_failed"),
                Gtk.MessageType.ERROR,
            )
        return False

    def _power_minutes(self, dropdown, custom_spin) -> int:
        selected = dropdown.get_selected()
        if selected < len(self.POWER_PRESET_MINUTES):
            return self.POWER_PRESET_MINUTES[selected]
        return custom_spin.get_value_as_int()

    def _on_power_suspend_changed(self, _dropdown, _param) -> None:
        if self._changing_power_controls:
            return
        self._update_power_custom_visibility()
        minutes = self._power_minutes(self.power_suspend_dropdown, self.power_suspend_custom_spin)
        self._run_async(
            lambda: self._change_power_setting("suspend", minutes),
            lambda response: self._power_setting_done(response),
        )

    def _on_power_display_changed(self, _dropdown, _param) -> None:
        if self._changing_power_controls:
            return
        self._update_power_custom_visibility()
        minutes = self._power_minutes(self.power_display_dropdown, self.power_display_custom_spin)
        self._run_async(
            lambda: self._change_power_setting("display", minutes),
            lambda response: self._power_setting_done(response),
        )

    def _on_power_suspend_custom_changed(self, _spin) -> None:
        if self._changing_power_controls:
            return
        if self.power_suspend_dropdown.get_selected() == len(self.POWER_PRESET_MINUTES):
            self._on_power_suspend_changed(self.power_suspend_dropdown, None)

    def _on_power_display_custom_changed(self, _spin) -> None:
        if self._changing_power_controls:
            return
        if self.power_display_dropdown.get_selected() == len(self.POWER_PRESET_MINUTES):
            self._on_power_display_changed(self.power_display_dropdown, None)

    def _change_power_setting(self, kind: str, minutes: int):
        result = (
            self.power_controller.set_suspend_timeout(minutes)
            if kind == "suspend"
            else self.power_controller.set_display_timeout(minutes)
        )
        return result, self.power_controller.inspect() if result.ok else None

    def _power_setting_done(self, response) -> bool:
        self._set_controls_sensitive(True)
        result, state = response
        if result.ok and state is not None:
            self._apply_power_state(state)
        else:
            if self._last_power_state is not None:
                self._apply_power_state(self._last_power_state)
            self._show_message(
                self.translator.gettext("dialog.power_failed"),
                result.stderr or result.stdout,
                Gtk.MessageType.ERROR,
            )
        return False

    def _privileged_done(self, response) -> bool:
        self._set_controls_sensitive(True)
        command, payload = response
        if command.returncode in (126, 127) and not payload.get("ok"):
            self._show_message(
                self.translator.gettext("dialog.cancelled"),
                self.translator.gettext("dialog.auth_cancelled"),
                Gtk.MessageType.INFO,
            )
        elif command.ok and payload.get("ok"):
            self._show_message(
                self.translator.gettext("dialog.save_complete"),
                self._payload_text(payload, "dialog.save_complete"),
                Gtk.MessageType.INFO,
            )
            self._schedule_refresh()
        else:
            self._show_message(
                self.translator.gettext("dialog.save_failed"),
                self._payload_text(payload, "dialog.save_failed"),
                Gtk.MessageType.ERROR,
            )
        return False

    def _on_install(self, _button) -> None:
        self._run_async(self.privileged.install, self._install_done)

    def _install_done(self, response) -> bool:
        command, payload = response
        if command.ok and payload.get("ok"):
            self._show_message(
                self.translator.gettext("dialog.install_complete"),
                self._payload_text(payload, "dialog.install_complete"),
                Gtk.MessageType.INFO,
            )
            self._refresh_bootstrap()
        elif command.returncode in (126, 127):
            self._show_message(
                self.translator.gettext("dialog.cancelled"),
                self.translator.gettext("dialog.auth_cancelled"),
                Gtk.MessageType.INFO,
            )
        else:
            self._show_message(
                self.translator.gettext("dialog.install_failed"),
                self._payload_text(payload, "dialog.install_failed"),
                Gtk.MessageType.ERROR,
            )
        self._set_controls_sensitive(True)
        return False

    def _confirm(self, title: str, message: str, callback) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
            secondary_text=message,
        )
        dialog.add_button(self.translator.gettext("common.cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(self.translator.gettext("common.continue"), Gtk.ResponseType.ACCEPT)

        def response(current, response_id):
            current.close()
            if response_id == Gtk.ResponseType.ACCEPT:
                callback()

        dialog.connect("response", response)
        dialog.present()

    def _show_message(self, title: str, message: str, message_type: Gtk.MessageType) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=message_type,
            buttons=Gtk.ButtonsType.NONE,
            text=title,
            secondary_text=message or self.translator.gettext("common.details_missing"),
        )
        dialog.add_button(self.translator.gettext("common.close"), Gtk.ResponseType.CLOSE)
        dialog.connect("response", lambda current, _response: current.close())
        dialog.present()
