import unittest

from bc250.messages import MessageError
import bc250.presets as presets_module
from bc250.presets import PRESETS, cu_mask_csv, get_preset, validate_temperature


class PresetTests(unittest.TestCase):
    def test_custom_frequency_range_is_bounded_and_ordered(self):
        self.assertTrue(hasattr(presets_module, "validate_frequency_range"), "validate_frequency_range is missing")
        self.assertEqual(presets_module.validate_frequency_range(500, 1750), (500, 1750))
        for values in ((499, 1500), (500, 1801), (1700, 1600), (1750, 1800)):
            with self.subTest(values=values), self.assertRaises(Exception):
                presets_module.validate_frequency_range(*values)

    def test_performance_presets_have_expected_safe_limits(self):
        self.assertEqual((PRESETS["eco"].max_mhz, PRESETS["eco"].max_mv), (1500, 900))
        self.assertEqual((PRESETS["balanced"].max_mhz, PRESETS["balanced"].max_mv), (1700, 920))
        self.assertEqual((PRESETS["performance"].max_mhz, PRESETS["performance"].max_mv), (1800, 930))

    def test_performance_presets_expose_translation_keys(self):
        self.assertEqual(PRESETS["eco"].label_key, "preset.eco")
        self.assertEqual(PRESETS["balanced"].description_key, "preset.balanced_detail")
        self.assertEqual(PRESETS["performance"].label_key, "preset.performance")

    def test_cu_masks_are_limited_to_supported_profiles(self):
        self.assertEqual(cu_mask_csv(24), "0x07,0x07,0x07,0x07")
        self.assertEqual(cu_mask_csv(32), "0x0f,0x0f,0x0f,0x0f")
        self.assertEqual(cu_mask_csv(40), "0x1f,0x1f,0x1f,0x1f")
        with self.assertRaises(ValueError):
            cu_mask_csv(36)

    def test_temperature_validation_enforces_safe_relationship(self):
        self.assertEqual(validate_temperature(85, 75), (85, 75))
        for values in ((79, 70), (91, 80), (85, 81), (85, 69)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_temperature(*values)

    def test_invalid_temperature_has_stable_message_id(self):
        with self.assertRaises(MessageError) as caught:
            validate_temperature(95, 75)
        self.assertEqual(caught.exception.message.key, "error.invalid_throttle")

    def test_all_validation_failures_have_stable_message_ids(self):
        invalid_calls = (
            (lambda: get_preset("turbo"), "error.invalid_preset"),
            (lambda: cu_mask_csv(36), "error.invalid_cu"),
            (lambda: validate_temperature(85, 81), "error.invalid_recovery_gap"),
        )
        for invalid_call, expected_key in invalid_calls:
            with self.subTest(expected_key=expected_key), self.assertRaises(MessageError) as caught:
                invalid_call()
            self.assertEqual(caught.exception.message.key, expected_key)


if __name__ == "__main__":
    unittest.main()
