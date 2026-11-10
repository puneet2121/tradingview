from datetime import datetime, timedelta, timezone as utc
import copy
import json
from unittest.mock import patch

from django.test import Client, TestCase, override_settings

from dashboard.market_alerts import EASTERN, calendar
from .fundamentals import normalize
from .models import FundamentalSnapshot, TechnicalResearchSnapshot
from .ratings import daily_snapshot, latest_session, fundamental_rating, technical_rating, combined_rating, wilder_rsi
from .research import request_technicals, response_for
from .test_fundamentals import NOW, raw_fixture


def bars_fixture(count=250):
    days = calendar().sessions_in_range("2025-01-01", "2026-10-23")[-count:]
    bars = []
    for i, day in enumerate(days):
        close = 100 + i * .3 + (i % 4) * .2
        stamp = datetime.combine(day.date(), datetime.min.time(), tzinfo=EASTERN).timestamp()
        bars.append({"time": stamp, "open": close-.1, "high": close+.5, "low": close-.5,
                     "close": close, "volume": 1500 if i == count-1 else 1000})
    return bars


class ResearchRatingTests(TestCase):
    def setUp(self):
        self.company = normalize("AAPL", raw_fixture(), NOW)
        self.daily = daily_snapshot(bars_fixture(), NOW)

    def test_weekend_and_holiday_use_last_completed_session(self):
        self.assertEqual(latest_session(NOW), "2026-10-23")
        self.assertEqual(latest_session(datetime(2026, 7, 3, 22, tzinfo=utc.utc)), "2026-07-02")
        self.assertEqual(latest_session(datetime(2026, 11, 27, 18, 10, tzinfo=utc.utc)), "2026-11-25")
        self.assertEqual(latest_session(datetime(2026, 11, 27, 18, 16, tzinfo=utc.utc)), "2026-11-27")

    def test_open_daily_candle_is_excluded_until_close_grace(self):
        raw = bars_fixture()
        before = datetime(2026, 10, 23, 20, 5, tzinfo=utc.utc)
        self.assertEqual(daily_snapshot(raw, before)["session"], "2026-10-22")
        raw[-1]["close"] = 9000
        raw[-1]["high"] = 9001
        self.assertEqual(daily_snapshot(raw, before), daily_snapshot(raw[:-1], before))
        self.assertEqual(daily_snapshot(raw, NOW)["session"], "2026-10-23")

    def test_daily_indicators_reuse_wilder_and_valid_history(self):
        raw = bars_fixture()
        self.assertAlmostEqual(self.daily["sma200"], sum(b["close"] for b in raw[-200:])/200)
        self.assertEqual(self.daily["relative_volume20"], 1.5)
        self.assertEqual(wilder_rsi([1]*30), 50)
        self.assertEqual(wilder_rsi(list(range(30))), 100)
        self.assertEqual(wilder_rsi(list(range(30, 0, -1))), 0)
        self.assertIsNone(wilder_rsi([1]*14))
        self.assertEqual(daily_snapshot(list(reversed(raw))+raw[-1:], NOW), self.daily)

    def test_missing_session_and_short_history_block_rating(self):
        raw = bars_fixture()
        for bars in (raw[-100:], raw[:-3]+raw[-2:]):
            score = technical_rating(daily_snapshot(bars, NOW), NOW)
            self.assertIsNone(score["score"])
            self.assertTrue(any("200 consecutive" in b for b in score["blockers"]))

    def test_invalid_ohlc_does_not_count_as_good_history(self):
        raw = bars_fixture()
        raw[-2]["high"] = -1
        data = daily_snapshot(raw, NOW)
        self.assertEqual(data["missing_sessions"], 1)
        self.assertIsNone(technical_rating(data, NOW)["score"])

    def test_technicals_score_bullish_and_bearish_evidence(self):
        bullish = technical_rating(self.daily, NOW)
        self.assertGreaterEqual(bullish["score"], 70)
        self.assertEqual(bullish["coverage"], 100)
        self.assertTrue(any("overbought" in c for c in bullish["cautions"]))
        data = {**self.daily, "close": 50, "previous_close": 51, "sma50": 60, "sma200": 70,
                "ema9": 52, "ema21": 58, "rsi14": 20, "relative_volume20": 2}
        bearish = technical_rating(data, NOW)
        self.assertEqual(bearish["score"], 0)
        self.assertTrue(any("not a buy signal" in c for c in bearish["cautions"]))
        json.dumps(bearish, allow_nan=False)

    def test_missing_volume_does_not_count_as_confirmation(self):
        data = {**self.daily, "relative_volume20": None}
        result = technical_rating(data, NOW)
        self.assertEqual(result["coverage"], 85)
        self.assertIsNone(result["score"])

    def test_old_price_or_expired_cache_cannot_produce_buy_rating(self):
        for data, stale in ((self.daily, True), ({**self.daily, "session":"2026-10-22"}, False)):
            t = technical_rating(data, NOW, stale)
            combined = combined_rating(t, fundamental_rating(self.company, "yearly", NOW))
            self.assertIsNone(combined["score"])
            self.assertEqual(combined["label"], "Not rated")

    def test_fundamental_score_has_explicit_weighted_breakdown(self):
        result = fundamental_rating(self.company, "yearly", NOW)
        self.assertEqual(result["coverage"], 100)
        # 25% revenue growth, flat net income, profitable, positive FCF, low leverage, liquid.
        self.assertEqual(result["score"], 92)
        self.assertEqual(sum(c["weight"] for c in result["checks"]), 100)
        self.assertEqual(result["as_of"], "2025-12-31")
        self.assertTrue(any("Valuation is not scored" in c for c in result["cautions"]))

    def test_negative_business_inputs_produce_weak_score(self):
        data = copy.deepcopy(self.company)
        a = data["periods"]["yearly"]["analysis"]
        a.update(revenue_yoy=-20, net_income_yoy=-50, operating_margin=-.1, net_margin=-.2,
                 free_cash_flow=-10, debt_equity=None, current_ratio=.5)
        data["periods"]["yearly"]["statements"]["balance"][0]["equity"] = -10
        f = fundamental_rating(data, "yearly", NOW)
        self.assertEqual(f["score"], 0)
        self.assertEqual(combined_rating(technical_rating(self.daily, NOW), f)["label"], "Weak buy case")

    def test_missing_cash_flow_is_not_a_strong_fundamental_rating(self):
        self.company["periods"]["yearly"]["analysis"]["free_cash_flow"] = None
        rating = fundamental_rating(self.company, "yearly", NOW)
        self.assertEqual(rating["coverage"], 85)
        self.assertIsNone(rating["score"])

    def test_unsupported_sector_asset_stale_period_and_cache_block_rating(self):
        for changes in ({"sector":"Financial Services"},{"sector":"Real Estate"},{"supported":False}):
            self.assertIsNone(fundamental_rating({**self.company, **changes}, "yearly", NOW)["score"])
        self.assertIsNone(fundamental_rating(self.company, "yearly", NOW, stale=True)["score"])
        self.assertIsNone(fundamental_rating(self.company, "quarterly", NOW+timedelta(days=200))["score"])

    def test_combined_requires_both_sides_and_explains_reasons(self):
        t, f = technical_rating(self.daily, NOW), fundamental_rating(self.company, "yearly", NOW)
        result = combined_rating(t, f)
        self.assertEqual(result["score"], round((t["score"]+f["score"])/2))
        self.assertEqual(result["label"], "Buy candidate")
        self.assertTrue(result["supporting"])
        self.assertTrue(result["against"])
        f["score"] = 55
        self.assertEqual(combined_rating(t, f)["label"], "Watch / wait")
        self.assertIsNone(combined_rating(t, fundamental_rating(None, "yearly", NOW))["score"])


@override_settings(SHARING_ENABLED=False, SMART_MONEY={}, ALLOWED_HOSTS=["localhost"])
class ResearchEndpointTests(TestCase):
    def setUp(self):
        self.client = Client(REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost")
        self.path = "/api/smart-money/research-ratings/"

    def test_get_never_fetches_or_creates_records(self):
        with patch("smart_money.research.fetch_yfinance_history") as fetch:
            result = self.client.get(self.path, {"symbol":"AAPL"})
        self.assertEqual(result.status_code, 200)
        self.assertIsNone(result.json()["ratings"]["yearly"]["combined"]["score"])
        fetch.assert_not_called()
        self.assertFalse(TechnicalResearchSnapshot.objects.exists())
        self.assertFalse(FundamentalSnapshot.objects.exists())

    def test_post_reads_daily_data_once_and_caches_without_orders(self):
        with patch("smart_money.research.timezone.now", return_value=NOW), \
             patch("smart_money.fundamentals.request_snapshot", return_value={}), \
             patch("smart_money.research.fetch_yfinance_history", return_value=bars_fixture()) as fetch, \
             patch("requests.post") as broker:
            for _ in range(2):
                result = self.client.post(self.path, json.dumps({"symbol":"AAPL"}), content_type="application/json")
                self.assertEqual(result.status_code, 200)
            fetch.assert_called_once_with("AAPL", "1d", period="2y", prepost=False, limit=550, ignore_tz=False)
            broker.assert_not_called()
        self.assertEqual(result.json()["ratings"]["yearly"]["technical"]["label"], "Bullish")
        self.assertIsNone(result.json()["ratings"]["yearly"]["combined"]["score"])

    def test_failed_refresh_retains_old_snapshot_but_blocks_score(self):
        past = NOW-timedelta(hours=1)
        TechnicalResearchSnapshot.objects.create(symbol="AAPL", payload=daily_snapshot(bars_fixture(), NOW),
            expires_at=past, lease_until=past, retry_at=past)
        with patch("smart_money.research.timezone.now", return_value=NOW), \
             patch("smart_money.research.fetch_yfinance_history", side_effect=RuntimeError("secret upstream error")):
            request_technicals("AAPL")
            response = response_for("AAPL")
        self.assertEqual(response["technical"]["data"]["session"], "2026-10-23")
        self.assertTrue(response["technical"]["stale"])
        self.assertIsNone(response["ratings"]["yearly"]["technical"]["score"])
        self.assertNotIn("secret", response["technical"]["error"])

    def test_active_lease_prevents_duplicate_requests(self):
        TechnicalResearchSnapshot.objects.create(symbol="AAPL", refresh_token="pending", lease_until=NOW+timedelta(minutes=1), retry_at=NOW)
        with patch("smart_money.research.timezone.now", return_value=NOW), patch("smart_money.research.fetch_yfinance_history") as fetch:
            request_technicals("AAPL", True)
            self.assertTrue(response_for("AAPL")["pending"])
        fetch.assert_not_called()

    def test_shared_permission_csrf_and_unknown_inputs(self):
        with override_settings(SMART_MONEY={"allow_sharing":True}, SHARING_ENABLED=True):
            self.assertEqual(self.client.get(self.path, {"symbol":"AAPL"}).status_code, 403)
        c = Client(enforce_csrf_checks=True, REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost")
        self.assertEqual(c.post(self.path, json.dumps({"symbol":"AAPL"}), content_type="application/json").status_code, 403)
        for data in ({"symbol":"AAPL", "execute":True},{"symbol":"AAPL", "refresh":"true"},{"symbol":"../bad"}):
            self.assertEqual(self.client.post(self.path, json.dumps(data), content_type="application/json").status_code, 400)
