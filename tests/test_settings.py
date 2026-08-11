import unittest

from bc250.messages import MessageError
from bc250.settings import BASE_WGP_MASK, DraftSettings, masks_to_csv, validate_wgp_masks


class DraftSettingsTests(unittest.TestCase):
    def test_accepts_full_unsigned_control_domain_and_individual_optional_wgps(self):
        settings = DraftSettings(
            min_mhz=0,
            max_mhz=4_294_967_295,
            max_mv=4_294_967_295,
            throttle=255,
            recovery=255,
            cpu_extra_cores=True,
            cu_masks=(0x07, 0x0F, 0x17, 0x1F),
            suspend_minutes=0,
            display_minutes=17,
        )

        self.assertEqual(settings.cu_count, 32)
        self.assertEqual(settings.cu_masks_csv, "0x07,0x0f,0x17,0x1f")

    def test_rejects_disabling_any_factory_base_wgp(self):
        with self.assertRaises(MessageError):
            validate_wgp_masks((BASE_WGP_MASK, BASE_WGP_MASK, 0x06, BASE_WGP_MASK))

    def test_rejects_invalid_mask_count_or_bits(self):
        for masks in ((0x07,), (0x07, 0x07, 0x07, 0x27)):
            with self.subTest(masks=masks), self.assertRaises(MessageError):
                validate_wgp_masks(masks)

    def test_mask_csv_is_canonical(self):
        self.assertEqual(masks_to_csv((7, 15, 23, 31)), "0x07,0x0f,0x17,0x1f")


if __name__ == "__main__":
    unittest.main()
