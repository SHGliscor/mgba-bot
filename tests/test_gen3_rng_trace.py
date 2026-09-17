import unittest

from gen3_rng_trace import RngTrace, parse_status_fields


class TestGen3RngTrace(unittest.TestCase):
    def test_status_rtc_and_seed(self):
        rtc, seed = parse_status_fields(
            "PB3 STATUS RTC=19:42:13 RTC_SEED=12345678 FAST=1"
        )
        self.assertEqual(rtc, "19:42:13")
        self.assertEqual(seed, "12345678")

    def test_missing_fields_are_not_invented(self):
        rtc, seed = parse_status_fields("PB3 STATUS FAST=1")
        self.assertEqual(rtc, "unavailable")
        self.assertEqual(seed, "unavailable")

    def test_trace_format(self):
        trace = RngTrace(
            reset=1,
            rtc="19:42:13",
            rtc_seed="12345678",
            rng_after_reset="11111111",
            rng_overworld="22222222",
            rng_before_encounter="33333333",
            rng_after_encounter="44444444",
            pid="AABBCCDD",
            iv_rng="55555555",
            species="Torchic",
            shiny=False,
            ivs={"hp": 1, "atk": 2, "def": 3, "spe": 4, "spa": 5, "spd": 6},
            starter_result={},
        )
        text = trace.format()
        self.assertIn("RTC:       19:42:13", text)
        self.assertIn("RTC seed:  12345678", text)
        self.assertIn("PID:       AABBCCDD", text)
        self.assertIn("IV RNG:    55555555", text)


if __name__ == "__main__":
    unittest.main()
