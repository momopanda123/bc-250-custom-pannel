import unittest

from bc250.messages import MessageError
from bc250.presets import (
    PRESETS,
    cu_mask_csv,
    get_preset,
    validate_frequency_range,
    validate_temperature,
)


class PresetTests(unittest.TestCase):
    def test_custom_frequency_range_accepts_full_dbus_domain_and_open_bounds(self):
        cases = (
            ((0, 0), (0, 0)),
            ((0, 4_294_967_295), (0, 4_294_967_295)),
            ((2_400, 0), (2_400, 0)),
            ((2_400, 2_400), (2_400, 2_400)),
            ((4_294_967_295, 4_294_967_295), (4_294_967_295, 4_294_967_295)),
        )
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(validate_frequency_range(*values), expected)

    def test_custom_frequency_range_rejects_values_outside_u32_and_reversed_closed_bounds(self):
        for values in ((-1, 1_500), (500, 4_294_967_296), (1_800, 1_700)):
            with self.subTest(values=values), self.assertRaises(MessageError) as caught:
                validate_frequency_range(*values)
            self.assertEqual(caught.exception.message.key, "error.invalid_frequency_range")

    def test_performance_presets_keep_expected_default_values(self):
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

    def test_temperature_validation_accepts_each_full_byte_range_independently(self):
        for values in ((0, 0), (85, 75), (255, 255), (0, 255), (255, 0)):
            with self.subTest(values=values):
                self.assertEqual(validate_temperature(*values), values)

    def test_temperature_validation_rejects_each_value_outside_byte_range(self):
        cases = (
            ((-1, 75), "error.invalid_throttle"),
            ((256, 75), "error.invalid_throttle"),
            ((85, -1), "error.invalid_recovery_gap"),
            ((85, 256), "error.invalid_recovery_gap"),
        )
        for values, expected_key in cases:
            with self.subTest(values=values), self.assertRaises(MessageError) as caught:
                validate_temperature(*values)
            self.assertEqual(caught.exception.message.key, expected_key)

    def test_all_validation_failures_have_stable_message_ids(self):
        invalid_calls = (
            (lambda: get_preset("turbo"), "error.invalid_preset"),
            (lambda: cu_mask_csv(36), "error.invalid_cu"),
            (lambda: validate_frequency_range(1_800, 1_700), "error.invalid_frequency_range"),
            (lambda: validate_temperature(85, 256), "error.invalid_recovery_gap"),
        )
        for invalid_call, expected_key in invalid_calls:
            with self.subTest(expected_key=expected_key), self.assertRaises(MessageError) as caught:
                invalid_call()
            self.assertEqual(caught.exception.message.key, expected_key)


if __name__ == "__main__":
    unittest.main()
