import unittest

import bot_v17


class BotSignalPolicyTests(unittest.TestCase):
    def test_pre5_does_not_rescue_sub_floor_solo_signal(self):
        score, comp = bot_v17.score_signal(
            value=106_152,
            atr_pct=11.55,
            pct_from_52w_high=-93.2,
            r3m=-0.096,
            spy_r3m=0.038,
            cluster=False,
            cluster_size=1,
            pre5_return=0.105,
        )

        self.assertEqual(comp["pts_pre5"], 0)
        self.assertEqual(score, 52)

    def test_pre5_still_rewards_signal_that_already_clears_floor(self):
        score, comp = bot_v17.score_signal(
            value=500_000,
            atr_pct=12.5,
            pct_from_52w_high=-60.0,
            r3m=-0.35,
            spy_r3m=0.02,
            cluster=False,
            cluster_size=1,
            pre5_return=0.01,
        )

        self.assertEqual(comp["pts_pre5"], 5)
        self.assertEqual(score, 73)

    def test_apply_filters_blocks_extreme_52w_decline(self):
        reason = bot_v17.apply_filters(
            ticker="NOMA",
            title="Director",
            is_10b5=False,
            cluster=False,
            cluster_size=1,
            score=80,
            r3m=-0.10,
            spy_r3m=0.03,
            routine=False,
            atr_pct=11.5,
            avg_vol_30d=74_000,
            value=106_152,
            h52=-95.0,
            days_to_earnings=None,
            insider_name="Sport City Cadiz S.L",
        )

        self.assertEqual(reason, "52w_too_far")

    def test_apply_filters_blocks_institutional_hft_buyer(self):
        reason = bot_v17.apply_filters(
            ticker="GMEX",
            title="N/A",
            is_10b5=False,
            cluster=False,
            cluster_size=1,
            score=80,
            r3m=-0.77,
            spy_r3m=0.03,
            routine=False,
            atr_pct=29.79,
            avg_vol_30d=4_000_000,
            value=75_828,
            h52=-80.0,
            days_to_earnings=None,
            insider_name="HRT Financial LP",
        )

        self.assertEqual(reason, "institutional_buyer")


if __name__ == "__main__":
    unittest.main()
