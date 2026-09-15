import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from .models import OptionPaperTrade, PaperTrade, PaperWatchSymbol, Strategy, StrategyScanState, TradeActivity
from .options_paper import close_option_trade, open_option_trade, option_state, reset_option_trades
from .paper_trading import STARTING_BALANCE, calculate_strategy_signals, calculate_testing1_signals, close_trade, ensure_default_watchlist, execute_option_signal, execute_signal, execute_strategy_signal, mark_automated_option_trades, mark_symbol, scan_strategy_for_watch, scan_watch_symbol
from .strategy_engine import DEFAULT_STRATEGY_RULE_TEXT, parse_strategy_text


class StrategyParserTests(TestCase):
    def test_default_strategy_parses_ema_vwap_rule(self):
        parsed = parse_strategy_text(DEFAULT_STRATEGY_RULE_TEXT)

        self.assertEqual([rule.side for rule in parsed.rules], ["BUY", "SELL"])
        for rule in parsed.rules:
            self.assertEqual(rule.ema_period, 9)
            self.assertTrue(rule.require_volume_increasing)
            self.assertTrue(rule.require_close_confirmation)

    def test_vwap_inverse_cross_parses(self):
        parsed = parse_strategy_text(
            """BUY CALL WHEN VWAP crosses below 9 EMA
AND volume > previous
BUY PUT WHEN VWAP crosses above 9 EMA"""
        )

        self.assertEqual([rule.side for rule in parsed.rules], ["BUY", "SELL"])
        self.assertTrue(parsed.rules[0].require_volume_increasing)
        self.assertFalse(parsed.rules[1].require_volume_increasing)

    def test_rejects_partial_testing2_including_valid_sell_branch(self):
        rules = DEFAULT_STRATEGY_RULE_TEXT.replace("crosses above VWAP", "crosses above EMA(21)")
        with self.assertRaisesMessage(ValueError, "Line 1: Only EMA/VWAP crosses are supported"):
            parse_strategy_text(rules)

    def test_rejects_every_unconsumed_or_contradictory_condition(self):
        for suffix in [
            "AND RSI(14) > 50", "AND volume is not increasing", "AND close < previous close",
            "AND volume is increasing OR RSI > 50", "AND", "AND unknown condition",
            "AND close > previous close extra words", "volume is increasing",
            "AND volume is increasing AND volume is increasing",
        ]:
            with self.subTest(suffix=suffix), self.assertRaisesMessage(ValueError, "Line 2:"):
                parse_strategy_text(f"BUY CALL WHEN EMA(9) crosses above VWAP\n{suffix}")

    def test_rejects_malformed_or_ambiguous_entries(self):
        for rule in [
            "BUY CALL WHEN EMA(0) crosses above VWAP",
            "BUY CALL WHEN EMA(501) crosses above VWAP",
            "BUY CALL WHEN EMA(1000) crosses above VWAP",
            "BUY CALL WHEN EMA(9 crosses above VWAP",
            "BUY CALL WHEN EMA crosses above VWAP",
            "BUY CALL WHEN EMA(9) crosses below VWAP",
            "BUY PUT WHEN EMA(9) crosses above VWAP",
            "SELL PUT WHEN EMA(9) crosses below VWAP",
            "BUY CALL WHEN VWAP crosses above VWAP",
            "AND volume is increasing",
            "BUY WHEN EMA(9) crosses above VWAP\nBUY WHEN EMA(21) crosses above VWAP",
        ]:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                parse_strategy_text(rule)

    def test_inline_case_and_inverse_forms(self):
        parsed = parse_strategy_text("Buy Call When VWAP Crosses Below EMA ( 15 ) AND volume > previous volume AND close > previous close")
        self.assertEqual(parsed.rules[0].ema_period, 15)
        self.assertTrue(parsed.rules[0].require_close_confirmation)


@override_settings(ALPACA_PAPER_ENABLED=False)
class StrategyValidationTests(TestCase):
    def setUp(self):
        self.invalid_rules = DEFAULT_STRATEGY_RULE_TEXT.replace("crosses above VWAP", "crosses above EMA(21)")
        self.strategy = Strategy.objects.create(name="testing 2", rule_text=self.invalid_rules)
        self.watch = PaperWatchSymbol.objects.create(symbol="AAPL", timeframe="3m")
        self.bars = [
            {"time": i * 180, "open": 100, "high": 102, "low": 98, "close": 100, "volume": 100}
            for i in range(1, 6)
        ]

    def test_existing_invalid_strategy_is_reported_without_mutating_rules(self):
        response = self.client.get("/api/strategies/")
        item = next(s for s in response.json()["strategies"] if s["name"] == self.strategy.name)
        self.assertFalse(item["valid"])
        self.assertIn("Line 1", item["validation_error"])
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.rule_text, self.invalid_rules)

    def test_invalid_save_never_overwrites_previous_valid_strategy(self):
        self.strategy.rule_text = DEFAULT_STRATEGY_RULE_TEXT
        self.strategy.save()
        response = self.client.post("/api/strategies/", {
            "id": self.strategy.id, "name": "changed", "rule_text": self.invalid_rules,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Line 1", response.json()["error"])
        self.strategy.refresh_from_db()
        self.assertEqual(self.strategy.name, "testing 2")
        self.assertEqual(self.strategy.rule_text, DEFAULT_STRATEGY_RULE_TEXT)

    def test_invalid_new_strategy_is_not_saved(self):
        response = self.client.post("/api/strategies/", {
            "name": "bad-new", "rule_text": DEFAULT_STRATEGY_RULE_TEXT + "\nAND RSI > 50",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Strategy.objects.filter(name="bad-new").exists())

    def test_invalid_strategy_rejected_even_on_first_scan(self):
        with patch("dashboard.paper_trading.mark_automated_option_trades") as mark:
            with self.assertRaises(ValueError):
                scan_strategy_for_watch(self.watch, self.strategy, self.bars, 720)
        mark.assert_called_once_with(self.strategy, "AAPL", "3m")

    def test_scanner_records_error_without_executing_partial_strategy(self):
        for initialized in [False, True]:
            with self.subTest(initialized=initialized):
                if initialized:
                    StrategyScanState.objects.update_or_create(
                        strategy=self.strategy, symbol="AAPL", timeframe="3m", defaults={"last_signal_time": 100},
                    )
                with patch("dashboard.paper_trading.get_history", return_value=self.bars), patch("dashboard.paper_trading.execute_strategy_signal") as execute:
                    self.assertEqual(scan_watch_symbol(self.watch), [])
                execute.assert_not_called()
                state = StrategyScanState.objects.get(strategy=self.strategy)
                self.assertIn("Line 1", state.last_error)
        self.assertFalse(PaperTrade.objects.exists())
        self.assertFalse(OptionPaperTrade.objects.exists())

    def test_direct_execution_paths_reject_invalid_saved_strategy(self):
        payload = {"symbol": "AAPL", "timeframe": "3m", "strategy": self.strategy.name}
        for execute in [execute_option_signal, execute_strategy_signal]:
            with self.subTest(execute=execute.__name__), self.assertRaises(ValueError):
                execute(self.strategy, payload)
        with self.assertRaises(ValueError):
            execute_signal(payload)
        self.assertFalse(TradeActivity.objects.exists())

    def test_correcting_rules_rebaselines_scanner(self):
        scan = StrategyScanState.objects.create(strategy=self.strategy, symbol="AAPL", timeframe="3m", last_signal_time=100, last_error="Invalid rules")
        response = self.client.post("/api/strategies/", {
            "id": self.strategy.id, "name": self.strategy.name, "rule_text": DEFAULT_STRATEGY_RULE_TEXT,
            "option_min_dte": 7, "option_max_dte": 14, "risk_percent": 2,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        scan.refresh_from_db()
        self.assertIsNone(scan.last_signal_time)
        self.assertEqual(scan.last_error, "")


class StrategySignalEvaluationTests(TestCase):
    def test_confirmation_conditions_and_periods_belong_to_their_own_rule(self):
        strategy = Strategy(rule_text="""BUY CALL WHEN EMA(9) crosses above VWAP
AND volume is increasing
AND close > previous close
BUY PUT WHEN EMA(21) crosses below VWAP""")
        bars = [{"time": i, "close": close, "volume": 100} for i, close in enumerate([10, 11, 12, 13])]
        with (
            patch("dashboard.paper_trading.calculate_ema", side_effect=lambda bars, period: [9, 11, 9, 8] if period == 9 else [11, 11, 9, 8]),
            patch("dashboard.paper_trading.calculate_vwap", return_value=[10, 10, 10, 10]),
        ):
            # BUY fails its volume check. SELL has no volume or close check.
            self.assertEqual(calculate_strategy_signals(bars, strategy), [{"time": 3, "side": "SELL"}])

    def test_default_signal_results_match_existing_testing1(self):
        closes = [100 + (i % 15 if (i // 15) % 2 else 15 - i % 15) for i in range(120)]
        bars = [{"time": 1788800400 + i * 180, "close": close, "high": close + 1, "low": close - 1, "volume": 100 + i} for i, close in enumerate(closes)]
        expected = calculate_testing1_signals(bars)
        self.assertTrue(expected)
        self.assertEqual(calculate_strategy_signals(bars, Strategy(rule_text=DEFAULT_STRATEGY_RULE_TEXT)), expected)


@override_settings(ALPACA_PAPER_ENABLED=False)
class PaperTradeSizingTests(TestCase):
    def test_default_watchlist_includes_requested_automatic_symbols(self):
        PaperWatchSymbol.objects.all().delete()

        ensure_default_watchlist()

        symbols = set(PaperWatchSymbol.objects.values_list("symbol", flat=True))
        self.assertTrue({"AVGO", "DRAM", "MU", "SNDK", "WMT", "SNOW", "MRVL", "IREN"}.issubset(symbols))
        for symbol in ["AVGO", "DRAM", "SNDK", "WMT", "SNOW", "MRVL", "IREN"]:
            self.assertTrue(PaperWatchSymbol.objects.filter(symbol=symbol, timeframe="3m").exists())
            self.assertTrue(PaperWatchSymbol.objects.filter(symbol=symbol, timeframe="5m").exists())

    def test_underlying_trade_is_capped_by_account_notional(self):
        trade = execute_signal(
            {
                "strategy": "sizing-test",
                "symbol": "ETH",
                "timeframe": "3m",
                "side": "BUY",
                "signal_time": 1,
                "entry_time": 1,
                "entry_price": "2496.20",
                "stop_price": "2492.30",
            }
        )

        self.assertIsNotNone(trade)
        self.assertLessEqual(trade.entry_price * trade.quantity, STARTING_BALANCE)

    def test_underlying_stop_is_logged_once_even_with_a_stale_trade_object(self):
        trade = execute_signal({
            "strategy": "stop-test", "symbol": "AAPL", "timeframe": "3m", "side": "BUY",
            "signal_time": 1, "entry_time": 1, "entry_price": 100, "stop_price": 90,
        })
        mark_symbol("AAPL", "3m", {"high": 101, "low": 89, "close": 90, "time": 2})
        close_trade(trade, 3, Decimal("95"), "STOP")
        event = TradeActivity.objects.get(action="CLOSE")
        self.assertEqual(event.reason, "STOP")
        self.assertEqual(event.snapshot["exit_price"], 90)
        self.assertEqual(event.snapshot["pnl"], -2000)

    def test_opposite_underlying_signal_is_scoped_to_its_strategy(self):
        payload = {
            "strategy": "first", "symbol": "AAPL", "timeframe": "3m", "side": "BUY",
            "signal_time": 1, "entry_time": 1, "entry_price": 100, "stop_price": 90,
        }
        first = execute_signal(payload)
        second = execute_signal({**payload, "strategy": "second", "side": "SELL", "stop_price": 110})
        first.refresh_from_db()
        self.assertEqual(first.status, "OPEN")
        self.assertEqual(second.status, "OPEN")
        self.assertFalse(TradeActivity.objects.filter(action="CLOSE").exists())


class AutomatedOptionTradeTests(TestCase):
    def test_option_signal_buys_contract_using_risk_budget(self):
        contract = {
            "contract_symbol": "AAPL260918C00100000",
            "option_type": OptionPaperTrade.CALL,
            "strike": 100,
            "expiration": "2026-09-18",
            "bid": 1.9,
            "ask": 2.0,
            "last_price": 2.0,
            "mid": 1.95,
            "iv": 0.3,
            "delta": 0.55,
            "gamma": 0.01,
            "theta": -0.02,
            "vega": 0.08,
            "rho": 0.03,
            "underlying_price": 100,
        }
        strategy = Strategy.objects.create(
            name="auto-options",
            trade_asset=Strategy.OPTION,
            risk_percent=Decimal("0.020000"),
            rule_text=DEFAULT_STRATEGY_RULE_TEXT,
        )

        with (
            patch("dashboard.options_paper.select_automated_contract", return_value=contract),
            patch("dashboard.options_paper.find_contract", return_value=contract),
        ):
            trade = execute_option_signal(
                strategy,
                {
                    "symbol": "AAPL",
                    "timeframe": "3m",
                    "side": "BUY",
                    "signal_time": 1,
                },
        )

        self.assertEqual(trade.contract_symbol, "AAPL260918C00100000")
        self.assertEqual(trade.quantity, 10)
        self.assertEqual(trade.entry_price, Decimal("2.000000"))
        self.assertTrue(trade.auto_trade)
        self.assertEqual(trade.strategy, "auto-options")

    def test_option_signal_allows_contract_up_to_fifteen_hundred_dollars(self):
        contract = {
            "contract_symbol": "AAPL260918C00100000",
            "option_type": OptionPaperTrade.CALL,
            "strike": 100,
            "expiration": "2026-09-18",
            "bid": 14.8,
            "ask": 15.0,
            "last_price": 15.0,
            "mid": 14.9,
            "underlying_price": 100,
        }
        strategy = Strategy.objects.create(
            name="auto-options-1500",
            trade_asset=Strategy.OPTION,
            risk_percent=Decimal("0.020000"),
            rule_text=DEFAULT_STRATEGY_RULE_TEXT,
        )

        with (
            patch("dashboard.options_paper.select_automated_contract", return_value=contract),
            patch("dashboard.options_paper.find_contract", return_value=contract),
        ):
            trade = execute_option_signal(
                strategy,
                {"symbol": "AAPL", "timeframe": "3m", "side": "BUY", "signal_time": 1},
            )

        self.assertEqual(trade.quantity, 1)
        self.assertEqual(trade.entry_price, Decimal("15.000000"))

    def test_option_signal_rejects_contract_above_fifteen_hundred_dollars(self):
        contract = {
            "contract_symbol": "AAPL260918C00100000",
            "option_type": OptionPaperTrade.CALL,
            "strike": 100,
            "expiration": "2026-09-18",
            "bid": 15.8,
            "ask": 16.0,
            "last_price": 16.0,
            "mid": 15.9,
            "underlying_price": 100,
        }
        strategy = Strategy.objects.create(
            name="auto-options-1600",
            trade_asset=Strategy.OPTION,
            risk_percent=Decimal("0.020000"),
            rule_text=DEFAULT_STRATEGY_RULE_TEXT,
        )

        with (
            patch("dashboard.options_paper.select_automated_contract", return_value=contract),
            patch("dashboard.options_paper.find_contract", return_value=contract),
        ):
            with self.assertRaisesMessage(ValueError, "premium cap"):
                execute_option_signal(
                    strategy,
                    {"symbol": "AAPL", "timeframe": "3m", "side": "BUY", "signal_time": 1},
                )

        self.assertFalse(OptionPaperTrade.objects.exists())


class ManualTradeProtectionTests(TestCase):
    def setUp(self):
        self.contract = {
            "contract_symbol": "SOFI260918C00020000", "option_type": "call",
            "strike": 20, "expiration": "2026-09-18", "bid": 2.55,
            "ask": 2.58, "underlying_price": 20,
        }
        self.payload = {
            "underlying_symbol": "SOFI", "contract_symbol": self.contract["contract_symbol"],
            "expiration_date": self.contract["expiration"], "quantity": 1, "side": "LONG",
            "timeframe": "3m",
        }
        self.quote = patch("dashboard.options_paper.find_contract", return_value=self.contract).start()
        self.addCleanup(patch.stopall)
        self.strategy = Strategy.objects.create(name="testing-manual-protection")

    def test_manual_endpoint_cannot_accept_automatic_ownership(self):
        with patch("dashboard.views.option_state", return_value={}):
            response = self.client.post("/api/options/paper/trade/", {
                **self.payload, "auto_trade": True, "strategy": self.strategy.name,
            }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        trade = OptionPaperTrade.objects.get(pk=response.json()["trade"])
        self.assertFalse(trade.auto_trade)
        self.assertEqual(trade.strategy, "")
        event = TradeActivity.objects.get()
        self.assertEqual((event.actor, event.action, event.reason), ("USER", "OPEN", "MANUAL_OPEN"))

    def test_shared_close_rejects_every_automatic_reason_on_manual_trade(self):
        trade = open_option_trade(self.payload)
        for reason in ["STOP_LOSS", "TAKE_PROFIT", "OPPOSITE_SIGNAL"]:
            with self.subTest(reason=reason), self.assertRaisesMessage(ValueError, "Manual trades are protected"):
                close_option_trade(trade.pk, reason, actor="STRATEGY", strategy_name=self.strategy.name)
        trade.refresh_from_db()
        self.assertEqual(trade.status, "OPEN")
        self.assertEqual(TradeActivity.objects.count(), 1)

    def test_scanner_exits_only_its_automated_positions(self):
        manual = open_option_trade(self.payload)
        automatic = open_option_trade(self.payload, strategy_name=self.strategy.name)
        for bid in [0.5, 6]:
            with self.subTest(bid=bid):
                self.contract["bid"] = bid
                mark_automated_option_trades(self.strategy, "SOFI", "3m")
                manual.refresh_from_db()
                self.assertEqual(manual.status, "OPEN")
        automatic.refresh_from_db()
        self.assertEqual(automatic.exit_reason, "STOP_LOSS")
        event = TradeActivity.objects.get(action="CLOSE")
        self.assertEqual(event.actor, "STRATEGY")
        self.assertEqual(event.strategy, self.strategy.name)

    def test_take_profit_still_closes_automatic_trade(self):
        trade = open_option_trade(self.payload, strategy_name=self.strategy.name)
        self.contract["bid"] = 6
        mark_automated_option_trades(self.strategy, "SOFI", "3m")
        trade.refresh_from_db()
        self.assertEqual(trade.exit_reason, "TAKE_PROFIT")

    def test_opposite_signal_leaves_manual_position_open(self):
        manual = open_option_trade(self.payload)
        automatic = open_option_trade(self.payload, strategy_name=self.strategy.name)
        put = {**self.contract, "contract_symbol": "SOFI260918P00020000", "option_type": "put", "ask": 1}
        with patch("dashboard.options_paper.select_automated_contract", return_value=put):
            self.quote.side_effect = lambda symbol, expiry, contract: put if contract == put["contract_symbol"] else self.contract
            new_trade = execute_option_signal(self.strategy, {
                "symbol": "SOFI", "timeframe": "3m", "side": "SELL", "signal_time": 10,
            })
        manual.refresh_from_db()
        automatic.refresh_from_db()
        self.assertEqual(manual.status, "OPEN")
        self.assertEqual(automatic.exit_reason, "OPPOSITE_SIGNAL")
        self.assertEqual(new_trade.option_type, "put")

    def test_strategy_cannot_close_another_strategys_position(self):
        trade = open_option_trade(self.payload, strategy_name=self.strategy.name)
        with self.assertRaises(ValueError):
            close_option_trade(trade.pk, "STOP_LOSS", actor="STRATEGY", strategy_name="someone-else")

    def test_user_close_is_logged_once_with_correct_prices_and_pnl(self):
        trade = open_option_trade(self.payload)
        with patch("dashboard.views.option_state", return_value={}):
            for _ in range(2):
                response = self.client.post("/api/options/paper/close/", {
                    "trade_id": trade.pk, "reason": "STOP_LOSS", "actor": "STRATEGY",
                }, content_type="application/json")
                self.assertEqual(response.status_code, 200)
        event = TradeActivity.objects.get(action="CLOSE")
        self.assertEqual(event.actor, "USER")
        self.assertEqual(event.reason, "MANUAL_CLOSE")
        self.assertEqual(event.snapshot["entry_price"], 2.58)
        self.assertEqual(event.snapshot["exit_price"], 2.55)
        self.assertEqual(event.snapshot["pnl"], -3)
        self.assertIsNotNone(event.snapshot["exit_time"])

    def test_short_manual_close_records_buy_to_close_profit(self):
        trade = open_option_trade({**self.payload, "side": "SHORT"})
        self.contract["ask"] = 2
        close_option_trade(trade.pk, "MANUAL_CLOSE", actor="USER")
        self.assertEqual(TradeActivity.objects.get(action="CLOSE").snapshot["pnl"], 55)

    def test_open_long_option_state_shows_unrealized_pnl_from_current_bid(self):
        open_option_trade(self.payload)
        self.contract.update({"bid": 3.1, "ask": 3.3, "last_price": 3.2, "mid": 3.2})

        trade = option_state()["trades"][0]

        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["current_bid"], 3.1)
        self.assertEqual(trade["current_ask"], 3.3)
        self.assertEqual(trade["current_exit_price"], 3.1)
        self.assertEqual(trade["current_value"], 310)
        self.assertEqual(trade["unrealized_pnl"], 52)
        self.assertEqual(trade["pnl"], 0.0)

    def test_open_short_option_state_shows_unrealized_pnl_from_current_ask(self):
        open_option_trade({**self.payload, "side": "SHORT"})
        self.contract.update({"bid": 2.0, "ask": 2.1, "last_price": 2.05, "mid": 2.05})

        trade = option_state()["trades"][0]

        self.assertEqual(trade["status"], "OPEN")
        self.assertEqual(trade["current_exit_price"], 2.1)
        self.assertEqual(trade["current_value"], 210)
        self.assertEqual(trade["unrealized_pnl"], 45)
        self.assertEqual(trade["pnl"], 0.0)

    def test_user_can_close_an_automated_position(self):
        trade = open_option_trade(self.payload, strategy_name=self.strategy.name)
        close_option_trade(trade.pk, "MANUAL_CLOSE", actor="USER")
        self.assertEqual(TradeActivity.objects.get(action="CLOSE").actor, "USER")

    def test_failed_quote_cannot_close_or_log_a_fill(self):
        trade = open_option_trade(self.payload)
        self.quote.side_effect = ValueError("Quote unavailable")
        with self.assertRaises(ValueError):
            close_option_trade(trade.pk, "MANUAL_CLOSE", actor="USER")
        trade.refresh_from_db()
        self.assertEqual(trade.status, "OPEN")
        self.assertFalse(TradeActivity.objects.filter(action="CLOSE").exists())

    def test_audit_failure_rolls_back_the_trade(self):
        with patch("dashboard.options_paper.record_trade_activity", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                open_option_trade(self.payload)
        self.assertFalse(OptionPaperTrade.objects.exists())

    def test_reset_preserves_activity_and_records_user_action(self):
        trade = open_option_trade(self.payload)
        reset_option_trades()
        self.assertFalse(OptionPaperTrade.objects.exists())
        self.assertEqual(TradeActivity.objects.get(action="OPEN").trade_id, trade.pk)
        self.assertEqual(TradeActivity.objects.get(action="RESET").snapshot["deleted_trades"], 1)

    def test_activity_paginates_without_quote_requests(self):
        TradeActivity.objects.bulk_create([
            TradeActivity(actor="USER", action="RESET", reason="ACCOUNT_RESET", asset="option")
            for _ in range(52)
        ])
        first = self.client.get("/api/paper/activity/").json()
        second = self.client.get("/api/paper/activity/", {"before": first["next_before"]}).json()
        self.assertEqual(len(first["events"]), 50)
        self.assertEqual(len(second["events"]), 2)
        self.assertIsNone(second["next_before"])
        self.assertGreater(first["events"][-1]["id"], second["events"][0]["id"])
        self.assertEqual(self.client.get("/api/paper/activity/?before=bad").status_code, 400)
        self.quote.assert_not_called()


class FakeDiscordResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


@override_settings(
    DISCORD_ALERTS_ENABLED=True,
    DISCORD_WEBHOOK_URL="https://discord.test/webhook",
    DISCORD_ALERT_STRATEGY_ONLY=True,
)
class DiscordAlertTests(TestCase):
    def setUp(self):
        self.contract = {
            "contract_symbol": "SOFI260918C00020000",
            "option_type": "call",
            "strike": 20,
            "expiration": "2026-09-18",
            "bid": 2.55,
            "ask": 2.58,
            "underlying_price": 20,
            "iv": 0.45,
            "delta": 0.52,
            "gamma": 0.08,
            "theta": -0.04,
            "vega": 0.12,
            "rho": 0.01,
        }
        self.payload = {
            "underlying_symbol": "SOFI",
            "contract_symbol": self.contract["contract_symbol"],
            "expiration_date": self.contract["expiration"],
            "quantity": 1,
            "side": "LONG",
            "timeframe": "3m",
            "signal_time": 1_789_000_000,
        }
        self.strategy = Strategy.objects.create(name="discord-test")
        self.quote = patch("dashboard.options_paper.find_contract", return_value=self.contract).start()
        self.addCleanup(patch.stopall)

    def test_strategy_option_open_sends_discord_alert_after_commit(self):
        with patch("dashboard.discord_alerts.request.urlopen", return_value=FakeDiscordResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                open_option_trade(self.payload, strategy_name=self.strategy.name)

        self.assertEqual(urlopen.call_count, 1)
        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["username"], "Trading Dashboard")
        self.assertIn("**BUY CALL SOFI 3m**", payload["content"])
        self.assertIn("Strategy: discord-test", payload["content"])
        self.assertIn("Reason: Strategy Signal", payload["content"])
        self.assertIn("Entry quote: bid 2.55 / ask 2.58", payload["content"])
        self.assertIn("Strategy risk: stop loss 50.0% | take profit 100.0%", payload["content"])

    def test_manual_option_trade_does_not_alert_by_default(self):
        with patch("dashboard.discord_alerts.request.urlopen", return_value=FakeDiscordResponse()) as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                open_option_trade(self.payload)

        urlopen.assert_not_called()
        self.assertTrue(TradeActivity.objects.filter(actor="USER", action="OPEN").exists())

    def test_discord_failure_does_not_roll_back_saved_trade(self):
        with patch("dashboard.discord_alerts.request.urlopen", side_effect=RuntimeError("discord down")):
            with self.assertLogs("dashboard.discord_alerts", level="ERROR"):
                with self.captureOnCommitCallbacks(execute=True):
                    trade = open_option_trade(self.payload, strategy_name=self.strategy.name)

        self.assertTrue(OptionPaperTrade.objects.filter(pk=trade.pk).exists())
        self.assertTrue(TradeActivity.objects.filter(actor="STRATEGY", action="OPEN").exists())

    @override_settings(DISCORD_ALERTS_ENABLED=False)
    def test_discord_test_endpoint_reports_disabled(self):
        response = self.client.post("/api/discord/test/")

        self.assertEqual(response.status_code, 400)
        self.assertIn("disabled", response.json()["error"])
