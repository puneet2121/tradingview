from datetime import datetime, timedelta, timezone as utc
import json
from unittest.mock import Mock, patch

import pandas as pd
from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, override_settings

from dashboard.data_source import fetch_yfinance_fundamentals
from .fundamentals import fetch_and_store, normalize, number, period_analysis, ratio, request_snapshot, statement_rows
from .models import FundamentalSnapshot
from .views import fundamentals

NOW = datetime(2026, 10, 25, tzinfo=utc.utc)


def raw_fixture():
    annual = pd.to_datetime(["2025-12-31", "2024-12-31", "2023-12-31"])
    quarterly = pd.to_datetime(["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30"])
    result = {"info": {"longName": "Fixture Inc", "quoteType": "EQUITY", "financialCurrency": "EUR", "currency": "USD",
              "marketCap": 900000000, "regularMarketPrice": 90, "regularMarketTime": int(NOW.timestamp())-3600,
              "trailingPE": 20, "forwardPE": 18, "trailingEps": 4.5, "forwardEps": 5, "sector": "Technology"},
              "warnings": [], "statements": {}}
    for name, dates, revenue in [("yearly", annual, [500, 400, 350]), ("quarterly", quarterly, [150, 140, 130, 120, 100])]:
        count = len(dates)
        result["statements"][name] = {
            "income": pd.DataFrame([revenue, [40]*count, [20]*count, [0.2]*count], index=["TotalRevenue", "OperatingIncome", "NetIncome", "DilutedEPS"], columns=dates),
            "balance": pd.DataFrame([[300]*count, [100]*count, [50]*count, [120]*count, [60]*count], index=["TotalAssets", "StockholdersEquity", "TotalDebt", "CurrentAssets", "CurrentLiabilities"], columns=dates),
            "cash_flow": pd.DataFrame([[40]*count, [-10]*count], index=["OperatingCashFlow", "CapitalExpenditure"], columns=dates),
        }
    return result


class FundamentalCalculationTests(TestCase):
    def test_snapshot_preserves_currency_and_quote_dates(self):
        data = normalize("AAPL", raw_fixture(), NOW)
        self.assertEqual(data["financial_currency"], "EUR")
        self.assertEqual(data["quote_currency"], "USD")
        self.assertNotEqual(data["quote_at"], data["fetched_at"])
        self.assertEqual(data["periods"]["yearly"]["analysis"]["period_end"], "2025-12-31")
        json.dumps(data, allow_nan=False)

    def test_quarterly_growth_is_year_over_year_not_previous_quarter(self):
        data = normalize("AAPL", raw_fixture(), NOW)
        analysis = data["periods"]["quarterly"]["analysis"]
        self.assertEqual(analysis["revenue_yoy"], 50)
        self.assertEqual(analysis["prior_period_end"], "2025-06-30")
        self.assertEqual(data["periods"]["yearly"]["analysis"]["revenue_yoy"], 25)

    def test_absent_values_do_not_become_zero(self):
        for value in (None, float("nan"), float("inf"), True, "123"):
            self.assertIsNone(number(value))
        data = normalize("AAPL", raw_fixture(), NOW)
        self.assertIsNone(data["periods"]["yearly"]["statements"]["income"][0]["gross_profit"])
        self.assertIsNone(data["periods"]["yearly"]["analysis"]["gross_margin"])
        self.assertIsNone(ratio(1e19, 1e-300))

    def test_negative_earnings_and_equity_not_positive_signals(self):
        raw = raw_fixture()
        raw["info"].update(trailingEps=-3, trailingPE=4)
        raw["statements"]["yearly"]["balance"].loc["StockholdersEquity"] = -100
        raw["statements"]["yearly"]["income"].loc["NetIncome"] = [-10, -20, -30]
        data = normalize("AAPL", raw, NOW)
        self.assertIsNone(data["valuation"]["trailing_pe"])
        a = data["periods"]["yearly"]["analysis"]
        self.assertIsNone(a["debt_equity"])
        self.assertIsNone(a["net_income_yoy"])
        self.assertLess(a["net_margin"], 0)
        self.assertTrue(any("Net loss" in line for line in a["observations"]))

    def test_cash_flow_derivation_does_not_guess_capex_sign(self):
        frame = raw_fixture()["statements"]["yearly"]["cash_flow"]
        self.assertEqual(statement_rows(frame, "cash_flow", NOW.date())[0]["free_cash_flow"], 30)
        frame.loc["CapitalExpenditure"] = 10
        self.assertIsNone(statement_rows(frame, "cash_flow", NOW.date())[0]["free_cash_flow"])
        frame.loc["FreeCashFlow"] = 27
        self.assertEqual(statement_rows(frame, "cash_flow", NOW.date())[0]["free_cash_flow"], 27)

    def test_no_mismatched_cash_flow_period(self):
        data = normalize("AAPL", raw_fixture(), NOW)
        statements = data["periods"]["yearly"]["statements"]
        statements["cash_flow"] = statements["cash_flow"][1:]
        self.assertIsNone(period_analysis(statements)["free_cash_flow"])

    def test_missing_prior_year_is_not_quarterly_growth(self):
        data = normalize("AAPL", raw_fixture(), NOW)
        statements = data["periods"]["quarterly"]["statements"]
        statements["income"] = statements["income"][:2]
        self.assertIsNone(period_analysis(statements)["revenue_yoy"])

    def test_future_and_all_null_columns_excluded(self):
        frame = pd.DataFrame([[100, float("nan"), 50]], index=["TotalRevenue"], columns=pd.to_datetime(["2027-12-31", "2025-12-31", "2024-12-31"]))
        self.assertEqual([r["period_end"] for r in statement_rows(frame, "income", NOW.date())], ["2024-12-31"])

    def test_etfs_do_not_get_corporate_ratios(self):
        raw = raw_fixture();raw["info"]["quoteType"] = "ETF"
        data = normalize("SPY", raw, NOW)
        self.assertFalse(data["supported"])
        self.assertNotIn("valuation", data)
        self.assertEqual(data["periods"], {})

    def test_provider_uses_existing_yfinance_without_trading(self):
        fake = Mock();fake.get_info.return_value = raw_fixture()["info"]
        for name in ("get_income_stmt", "get_balance_sheet", "get_cash_flow"):
            getattr(fake, name).return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=fake), patch("requests.post") as broker_post:
            result = fetch_yfinance_fundamentals("AAPL")
        self.assertEqual(len(result["warnings"]), 6)
        fake.get_income_stmt.assert_any_call(freq="yearly", pretty=False)
        fake.get_income_stmt.assert_any_call(freq="quarterly", pretty=False)
        broker_post.assert_not_called()


@override_settings(SHARING_ENABLED=False, SMART_MONEY={}, ALLOWED_HOSTS=["localhost"])
class FundamentalEndpointTests(TestCase):
    def setUp(self):
        self.client = Client(REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost")
        self.path = "/api/smart-money/fundamentals/"

    def test_get_is_read_only_and_does_not_fetch(self):
        with patch("smart_money.fundamentals.fetch_yfinance_fundamentals") as fetch:
            result = self.client.get(self.path, {"symbol": "AAPL"})
            self.assertEqual(result.status_code, 200)
            fetch.assert_not_called()
            self.assertFalse(FundamentalSnapshot.objects.exists())

    def test_first_request_starts_one_job_and_returns_pending(self):
        with patch("smart_money.fundamentals.threading.Thread") as thread, patch("smart_money.fundamentals._slots"):
            result = self.client.post(self.path, json.dumps({"symbol": "aapl"}), content_type="application/json")
            self.assertEqual(result.status_code, 202)
            self.assertTrue(result.json()["pending"])
            thread.return_value.start.assert_called_once()
            again = self.client.post(self.path, json.dumps({"symbol": "AAPL"}), content_type="application/json")
            self.assertEqual(again.status_code, 202)
            thread.return_value.start.assert_called_once()

    def test_completed_job_is_cached_and_independent_of_flow_provider(self):
        record = FundamentalSnapshot.objects.create(symbol="AAPL", refresh_token="test")
        with patch("smart_money.fundamentals.fetch_yfinance_fundamentals", return_value=raw_fixture()):
            fetch_and_store("AAPL", "test")
        record.refresh_from_db()
        self.assertTrue(record.payload["supported"])
        with patch("smart_money.fundamentals.threading.Thread") as thread:
            result = self.client.post(self.path, json.dumps({"symbol": "AAPL"}), content_type="application/json")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["data"]["company"], "Fixture Inc")
            thread.assert_not_called()

    def test_failure_preserves_stale_cache_and_sanitizes_error(self):
        FundamentalSnapshot.objects.create(symbol="AAPL", payload={"company": "Old snapshot"}, refresh_token="test")
        with patch("smart_money.fundamentals.fetch_yfinance_fundamentals", side_effect=RuntimeError("private cookie must not appear")):
            fetch_and_store("AAPL", "test")
        response = self.client.get(self.path, {"symbol": "AAPL"})
        self.assertTrue(response.json()["stale"])
        self.assertEqual(response.json()["data"]["company"], "Old snapshot")
        self.assertNotIn("private cookie", response.content.decode())

    def test_old_job_cannot_overwrite_new_token(self):
        record = FundamentalSnapshot.objects.create(symbol="AAPL", refresh_token="new", payload={"company": "Current"})
        with patch("smart_money.fundamentals.fetch_yfinance_fundamentals", return_value=raw_fixture()):
            fetch_and_store("AAPL", "old")
        record.refresh_from_db()
        self.assertEqual(record.payload, {"company": "Current"})

    def test_expired_job_lease_can_be_retried(self):
        past = datetime.now(utc.utc)-timedelta(hours=1)
        FundamentalSnapshot.objects.create(symbol="AAPL", refresh_token="old", lease_until=past, retry_at=past, expires_at=past)
        with patch("smart_money.fundamentals.threading.Thread"), patch("smart_money.fundamentals._slots"):
            result = request_snapshot("AAPL")
        self.assertTrue(result["pending"])
        self.assertNotEqual(FundamentalSnapshot.objects.get().refresh_token, "old")

    def test_manual_refresh_respects_cooldown(self):
        future = datetime.now(utc.utc)+timedelta(minutes=5)
        FundamentalSnapshot.objects.create(symbol="AAPL", payload={"company": "Current"}, retry_at=future)
        with patch("smart_money.fundamentals.threading.Thread") as thread:
            result = request_snapshot("AAPL", force=True)
            self.assertFalse(result["pending"])
            thread.assert_not_called()

    def test_bad_symbols_and_unexpected_parameters_rejected(self):
        for data in ({"symbol":"https://bad"},{"symbol":"AAPL", "refresh":"true"},{"symbol":"AAPL", "key":"secret"}):
            self.assertEqual(self.client.post(self.path, json.dumps(data), content_type="application/json").status_code, 400)

    def test_csrf_and_lan_access_protected(self):
        c = Client(enforce_csrf_checks=True, REMOTE_ADDR="127.0.0.1", HTTP_HOST="localhost")
        self.assertEqual(c.post(self.path, json.dumps({"symbol":"AAPL"}),content_type="application/json").status_code,403)
        self.assertEqual(Client(REMOTE_ADDR="192.168.1.5",HTTP_HOST="localhost").get(self.path,{"symbol":"AAPL"}).status_code,403)

    @override_settings(SHARING_ENABLED=True,SHARING_USERS=("reader",),SMART_MONEY={"allow_sharing":True})
    def test_sharing_rights_are_separate_from_opra(self):
        user = get_user_model().objects.create_user(username="reader")
        request = RequestFactory().get(self.path,{"symbol":"AAPL"},HTTP_HOST="localhost");request.user=user
        self.assertEqual(fundamentals(request).status_code,403)
        with override_settings(SMART_MONEY={"allow_fundamentals_sharing":True}):
            self.assertEqual(fundamentals(request).status_code,200)
