import unittest
from datetime import datetime, timezone

import bot_v17


class BotSignalPolicyTests(unittest.TestCase):
    def test_get_equity_returns_none_when_alpaca_disabled(self):
        old_key = bot_v17.ALPACA_KEY
        old_secret = bot_v17.ALPACA_SECRET
        try:
            bot_v17.ALPACA_KEY = None
            bot_v17.ALPACA_SECRET = None
            self.assertIsNone(bot_v17.get_equity())
        finally:
            bot_v17.ALPACA_KEY = old_key
            bot_v17.ALPACA_SECRET = old_secret

    def test_fallback_market_hours_detects_weekend_closed(self):
        sunday_noon_utc = datetime(2026, 5, 3, 16, 0, tzinfo=timezone.utc)
        self.assertFalse(bot_v17._fallback_market_open(sunday_noon_utc))

    def test_smoke_mode_parser_shape(self):
        filing = {
            "accessionNo": "000123456789012345",
            "filedAt": "2026-04-30T00:00:00",
            "xml_text": """<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-04-29</periodOfReport>
  <issuer>
    <issuerCik>0000123456</issuerCik>
    <issuerName>Example Issuer</issuerName>
    <issuerTradingSymbol>ABCD</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>0</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle></officerTitle>
    </reportingOwnerRelationship>
    <reportingOwnerId>
      <rptOwnerName>Jane Insider</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares>
          <value>1000</value>
        </transactionShares>
        <transactionPricePerShare>
          <value>75</value>
        </transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>""",
        }

        txns = bot_v17.parse_filing_transactions(filing)

        self.assertEqual(txns[0]["issuer_name"], "Example Issuer")
        self.assertEqual(txns[0]["cik"], "0000123456")
        self.assertFalse(txns[0]["is_ten_percent_owner"])

    def test_parse_filing_transactions_uses_cached_transactions(self):
        filing = {
            "cached_transactions": [{
                "ticker": "CACHE",
                "accession": "123",
                "filed_at": "2026-04-30",
                "name": "Cached Owner",
                "title": "Director",
                "is_10b5": False,
                "value": 80000,
            }]
        }

        txns = bot_v17.parse_filing_transactions(filing)

        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["ticker"], "CACHE")
        self.assertEqual(txns[0]["value"], 80000)

    def test_parse_filing_transactions_from_edgar_xml(self):
        filing = {
            "accessionNo": "000123456789012345",
            "filedAt": "2026-04-30T00:00:00",
            "xml_text": """<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-04-29</periodOfReport>
  <issuer>
    <issuerTradingSymbol>ABCD</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>0</isOfficer>
      <officerTitle></officerTitle>
    </reportingOwnerRelationship>
    <reportingOwnerId>
      <rptOwnerName>Jane Insider</rptOwnerName>
    </reportingOwnerId>
  </reportingOwner>
  <footnotes>
    <footnote id="F1">Adopted pursuant to Rule 10b5-1.</footnote>
  </footnotes>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionCoding>
        <transactionCode>P</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares>
          <value>1000</value>
        </transactionShares>
        <transactionPricePerShare>
          <value>75</value>
        </transactionPricePerShare>
      </transactionAmounts>
      <footnoteId id="F1" />
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>""",
        }

        txns = bot_v17.parse_filing_transactions(filing)

        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["ticker"], "ABCD")
        self.assertEqual(txns[0]["accession"], "000123456789012345")
        self.assertEqual(txns[0]["filed_at"], "2026-04-29")
        self.assertEqual(txns[0]["name"], "Jane Insider")
        self.assertEqual(txns[0]["title"], "Director")
        self.assertTrue(txns[0]["is_10b5"])
        self.assertEqual(txns[0]["value"], 75_000)
        self.assertEqual(txns[0]["shares"], 1000)
        self.assertEqual(txns[0]["price_per_share"], 75)
        self.assertTrue(txns[0]["is_director"])

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
