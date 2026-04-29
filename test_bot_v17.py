import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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


class SecEdgarIngestionTests(unittest.TestCase):
    def test_fetch_current_form4_entries_dedupes_and_filters_by_time(self):
        feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>4 - Example 1</title>
    <updated>2026-04-29T13:04:53-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0001111111-26-000001</id>
    <category term="4" />
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1111111/000111111126000001/xslF345X05/primary_doc.xml-index.htm" />
  </entry>
  <entry>
    <title>4 - Duplicate issuer row</title>
    <updated>2026-04-29T13:04:53-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0001111111-26-000001</id>
    <category term="4" />
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1111111/000111111126000001/xslF345X05/primary_doc.xml-index.htm" />
  </entry>
  <entry>
    <title>4/A - Recent amendment</title>
    <updated>2026-04-29T12:30:00-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0002222222-26-000002</id>
    <category term="4/A" />
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/2222222/000222222226000002/xslF345X05/primary_doc.xml-index.htm" />
  </entry>
  <entry>
    <title>4 - Too old</title>
    <updated>2026-04-29T10:59:59-04:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0003333333-26-000003</id>
    <category term="4" />
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/3333333/000333333326000003/xslF345X05/primary_doc.xml-index.htm" />
  </entry>
</feed>
"""

        class FakeResponse:
            def __init__(self, text):
                self.status_code = 200
                self.text = text

        since_utc = datetime(2026, 4, 29, 16, 0, tzinfo=timezone.utc)
        with patch.object(bot_v17, "_sec_get", return_value=FakeResponse(feed_xml)):
            entries = bot_v17._fetch_current_form4_entries(since_utc)

        self.assertEqual(
            [entry["accessionNo"] for entry in entries],
            ["0001111111-26-000001", "0002222222-26-000002"],
        )
        self.assertEqual(
            entries[0]["link"],
            "https://www.sec.gov/Archives/edgar/data/1111111/000111111126000001/xslF345X05/primary_doc.xml-index.htm",
        )

    def test_normalized_form4_xml_preserves_purchase_data_needed_by_bot(self):
        xml_text = """<?xml version="1.0" encoding="UTF-8"?>
<ownershipDocument>
  <issuer>
    <issuerCik>0001234567</issuerCik>
    <issuerName>Acme Corp</issuerName>
    <issuerTradingSymbol>ACME</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerName>Jane Director</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>1</isDirector>
      <isOfficer>0</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <isOther>0</isOther>
    </reportingOwnerRelationship>
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
          <value>60.00</value>
        </transactionPricePerShare>
      </transactionAmounts>
      <footnoteId id="F1" />
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionCoding>
        <transactionCode>S</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares>
          <value>10</value>
        </transactionShares>
        <transactionPricePerShare>
          <value>61.00</value>
        </transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <footnotes>
    <footnote id="F1">Adopted pursuant to a Rule 10b5-1 trading plan.</footnote>
  </footnotes>
</ownershipDocument>
"""

        filings = bot_v17._normalize_form4_xml(
            accession="0001234567-26-000010",
            filed_at="2026-04-29T16:05:15-04:00",
            xml_text=xml_text,
        )

        self.assertEqual(len(filings), 1)
        filing = filings[0]
        self.assertEqual(filing["issuer"]["tradingSymbol"], "ACME")
        self.assertEqual(filing["reportingOwner"]["name"], "Jane Director")
        self.assertTrue(filing["footnotes"])

        txns = bot_v17.parse_filing_transactions(filing)
        self.assertEqual(len(txns), 1)
        self.assertEqual(txns[0]["ticker"], "ACME")
        self.assertEqual(txns[0]["title"], "Director")
        self.assertEqual(txns[0]["filed_at"], "2026-04-29")
        self.assertEqual(txns[0]["value"], 60000.0)
        self.assertTrue(txns[0]["is_10b5"])


if __name__ == "__main__":
    unittest.main()
