import json
import tempfile
import unittest
from pathlib import Path

from bc250.i18n import SUPPORTED_LANGUAGES, Translator, normalize_locale
from bc250.messages import MessageError, UserMessage

REQUIRED_KEYS = {
    "app.title", "app.subtitle", "app.hero_caption",
    "language.auto", "language.ko", "language.en", "language.ja", "language.zh_CN",
    "common.unavailable", "common.cancel", "common.continue", "common.close", "common.active",
    "common.stopped_or_disconnected", "common.just_now", "common.details_missing",
    "status.checking", "status.normal", "status.needs_attention", "status.partial", "status.error",
    "status.cu_applied", "status.cu_mismatch", "status.cu_saved", "status.updated_at",
    "status.cpu_stock", "status.cpu_unlocked", "status.cpu_pending",
    "setup.title", "setup.detail", "setup.install", "setup.required", "setup.ready",
    "component.governor", "component.cu_manager", "component.umr", "component.helper", "component.cpu_mode", "component.support",
    "metric.gpu_temperature", "metric.cpu_temperature", "metric.power", "metric.clock",
    "metric.voltage", "metric.fan", "metric.driver_report", "metric.edge_sensor",
    "metric.tctl_sensor", "metric.smu_voltage", "metric.instant_sensor", "metric.fan_auto",
    "metric.active_fans", "section.system", "section.system_subtitle", "section.control",
    "section.telemetry", "section.core_control", "section.power_idle", "power.hardware_idle",
    "power.suspend_block", "power.display_block", "power.blocked", "power.minutes",
    "section.control_subtitle", "field.bios_vendor", "field.bios_version", "field.bios_date",
    "field.kernel", "field.architecture", "field.governor", "field.performance_preset",
    "field.throttle", "field.recovery", "field.next_boot_cu", "field.cpu_cores",
    "field.gpu_cu", "field.custom_min_clock", "field.custom_max_clock",
    "action.unlock_cpu", "action.enable_cpu", "action.disable_cpu", "action.apply_now",
    "action.apply_and_save", "action.save_boot_profile", "preset.eco", "preset.eco_detail",
    "preset.balanced", "preset.balanced_detail", "preset.performance", "preset.performance_detail",
    "preset.custom", "preset.custom_detail",
    "cu.24", "cu.32", "cu.40", "dialog.operation_failed", "dialog.input_check",
    "dialog.apply_complete", "dialog.apply_complete_detail", "dialog.apply_failed",
    "dialog.save_confirm", "dialog.save_confirm_detail", "dialog.cu_confirm",
    "dialog.cu_confirm_detail", "dialog.cpu_unlock_confirm", "dialog.cpu_unlock_confirm_detail",
    "dialog.cpu_unlock_complete", "dialog.cpu_unlock_failed", "dialog.cpu_enable_confirm",
    "dialog.cpu_enable_confirm_detail", "dialog.cpu_disable_confirm", "dialog.cpu_disable_confirm_detail",
    "dialog.cpu_mode_complete", "dialog.cpu_mode_failed", "dialog.power_failed",
    "dialog.cancelled", "dialog.auth_cancelled",
    "dialog.save_complete", "dialog.save_failed", "dialog.install_confirm",
    "dialog.install_confirm_detail", "dialog.install_complete", "dialog.install_failed",
    "error.no_response", "error.invalid_preset", "error.invalid_cu", "error.invalid_frequency_range",
    "error.invalid_throttle", "error.invalid_recovery_gap", "error.cu_mismatch",
    "error.amdgpu_missing", "error.locale_save", "error.bundle_invalid",
    "platform.not_bazzite", "platform.arch_unsupported", "platform.device_missing",
    "platform.ready", "platform.skipped", "install.auth_required", "install.service_failed",
    "install.complete", "install.remove_complete", "helper.governor_saved",
    "helper.governor_restart_failed", "helper.cu_saved", "helper.cpu_unlock_armed",
    "helper.cpu_unlock_failed", "helper.cpu_mode_enabled", "helper.cpu_mode_disabled", "helper.backup_restored",
    "helper.auth_required", "helper.action_invalid",
}


class I18nTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.locales = self.root / "locales"
        self.locales.mkdir()
        catalogs = {
            "en": {
                "common.ok": "OK",
                "status.cu_applied": "{count}/40 CU applied",
                "fallback.only": "English fallback",
            },
            "ko": {"common.ok": "확인", "status.cu_applied": "{count}/40 CU 적용됨"},
            "ja": {"common.ok": "確認", "status.cu_applied": "{count}/40 CU 適用済み"},
            "zh-CN": {"common.ok": "确认", "status.cu_applied": "已应用 {count}/40 CU"},
        }
        for language, content in catalogs.items():
            (self.locales / f"{language}.json").write_text(
                json.dumps(content, ensure_ascii=False), encoding="utf-8"
            )
        self.settings = self.root / "config/settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_supported_language_order_is_stable(self):
        self.assertEqual(SUPPORTED_LANGUAGES, ("auto", "ko", "en", "ja", "zh-CN"))

    def test_locale_normalization_and_unsupported_fallback(self):
        self.assertEqual(normalize_locale("ko_KR.UTF-8"), "ko")
        self.assertEqual(normalize_locale("ja_JP.UTF-8"), "ja")
        self.assertEqual(normalize_locale("zh_CN.UTF-8"), "zh-CN")
        self.assertEqual(normalize_locale("zh_SG.UTF-8"), "zh-CN")
        self.assertEqual(normalize_locale("zh_TW.UTF-8"), "en")
        self.assertEqual(normalize_locale("de_DE.UTF-8"), "en")

    def test_auto_uses_environment_and_missing_key_uses_english(self):
        tr = Translator(self.locales, self.settings, {"LANG": "ko_KR.UTF-8"})
        self.assertEqual(tr.language, "auto")
        self.assertEqual(tr.effective_language, "ko")
        self.assertEqual(tr.gettext("common.ok"), "확인")
        self.assertEqual(tr.gettext("status.cu_applied", count=40), "40/40 CU 적용됨")
        self.assertEqual(tr.gettext("fallback.only"), "English fallback")

    def test_user_message_rendering(self):
        tr = Translator(self.locales, self.settings, {"LANG": "en_US.UTF-8"})
        self.assertEqual(tr.render(UserMessage("status.cu_applied", {"count": 32})), "32/40 CU applied")

    def test_message_error_preserves_key_and_parameters(self):
        error = MessageError("error.bundle_invalid", detail="checksum")
        self.assertEqual(error.message, UserMessage("error.bundle_invalid", {"detail": "checksum"}))

    def test_unknown_key_is_visible_and_recorded(self):
        tr = Translator(self.locales, self.settings, {"LANG": "en_US.UTF-8"})
        self.assertEqual(tr.gettext("missing.example"), "missing.example")
        self.assertEqual(tr.missing_keys, {"missing.example"})

    def test_manual_language_is_saved_and_reloaded(self):
        tr = Translator(self.locales, self.settings, {"LANG": "ko_KR.UTF-8"})
        tr.set_language("ja")
        restored = Translator(self.locales, self.settings, {"LANG": "ko_KR.UTF-8"})
        self.assertEqual(restored.language, "ja")
        self.assertEqual(restored.effective_language, "ja")

    def test_corrupt_setting_recovers_to_auto(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{broken", encoding="utf-8")
        tr = Translator(self.locales, self.settings, {"LANG": "en_US.UTF-8"})
        self.assertEqual(tr.language, "auto")

    def test_non_object_setting_recovers_to_auto(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("[]", encoding="utf-8")
        tr = Translator(self.locales, self.settings, {"LANG": "en_US.UTF-8"})
        self.assertEqual(tr.language, "auto")

    def test_corrupt_selected_catalog_uses_english(self):
        (self.locales / "ko.json").write_text("{broken", encoding="utf-8")
        tr = Translator(self.locales, self.settings, {"LANG": "ko_KR.UTF-8"})
        self.assertEqual(tr.gettext("common.ok"), "OK")
        self.assertTrue(tr.catalog_errors)

    def test_save_failure_keeps_session_language(self):
        blocked = self.root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        tr = Translator(self.locales, blocked / "settings.json", {"LANG": "ko_KR.UTF-8"})
        with self.assertRaises(OSError):
            tr.set_language("ja")
        self.assertEqual(tr.effective_language, "ja")

    def test_production_catalogs_have_identical_keys(self):
        import string

        project_locales = Path("bc250/locales")
        catalogs = {
            language: json.loads((project_locales / f"{language}.json").read_text(encoding="utf-8"))
            for language in SUPPORTED_LANGUAGES[1:]
        }
        english_keys = set(catalogs["en"])
        self.assertEqual(english_keys, REQUIRED_KEYS)
        for language, catalog in catalogs.items():
            with self.subTest(language=language):
                self.assertEqual(set(catalog), english_keys)
                self.assertTrue(all(value.strip() for value in catalog.values()))
                for key in english_keys:
                    expected_fields = {
                        field for _literal, field, _spec, _conversion
                        in string.Formatter().parse(catalogs["en"][key]) if field
                    }
                    actual_fields = {
                        field for _literal, field, _spec, _conversion
                        in string.Formatter().parse(catalog[key]) if field
                    }
                    self.assertEqual(actual_fields, expected_fields, key)

    def test_english_cu_mismatch_uses_canonical_middle_dot(self):
        catalog = json.loads(Path("bc250/locales/en.json").read_text(encoding="utf-8"))
        self.assertEqual(
            catalog["status.cu_mismatch"],
            "Current {current}/40 · saved profile differs",
        )
        self.assertNotIn("쨌", catalog["status.cu_mismatch"])

    def test_close_action_has_natural_translations(self):
        expected = {"en": "Close", "ko": "닫기", "ja": "閉じる", "zh-CN": "关闭"}
        for language, value in expected.items():
            with self.subTest(language=language):
                catalog = json.loads(
                    (Path("bc250/locales") / f"{language}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(catalog["common.close"], value)
