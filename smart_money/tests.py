"""Synthetic fixtures are isolated to the test database, never the production feed."""
from datetime import date, datetime, timedelta, timezone as utc
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, override_settings

from .analytics import classify, directional, horizons, technical_snapshot, unusual_score
from .disclosures import holding_changes
from .enrichment import enrich_one
from .ingestion import collect_cursor, ingest_page
from .models import (AlertConfiguration, CollectionSettings, FlowCursor, InstitutionalHolding,
    MarketDataProvider, OptionContract, OptionsTransaction, SecFiling, TransactionPriceSnapshot,
    UnusualActivityAlert, WorkerState)
from .notifications import deliver, in_session, queue_events
from .providers import AlpacaOpra, BudgetExhausted, ProviderError, decimal, timestamp
from .sec import SecClient, parse_holdings, parse_insiders, xml_document
from .validation import filters
from .views import alerts, status
from .worker import claim, run_once

NOW = datetime(2026, 10, 23, 14, 30, tzinfo=utc.utc)
SM = {"provider": "none", "allow_storage": False, "allow_sharing": False,
      "allow_discord": False, "delay_seconds": 900, "sec_user_agent": ""}
CATALOG = [{"symbol": "NVDA261120C00200000", "underlying": "NVDA", "company_name": "Fixture Company",
            "strike": "200", "expiration": "2026-11-20", "option_type": "CALL", "multiplier": "100"}]


@override_settings(SMART_MONEY=SM, SHARING_ENABLED=False, ALLOWED_HOSTS=["testserver","localhost"])
class FlowTests(TestCase):
    def setUp(self):
        self.provider = MarketDataProvider.objects.create(code="alpaca_opra", name="Alpaca OPRA")
        self.state = {"start": (NOW-timedelta(minutes=5)).isoformat(), "end": NOW.isoformat()}
        self.print = {"t": "2026-10-23T14:29:00.123456789Z", "p": 5, "s": 200, "x": "A", "c": " "}
        self.payload = {CATALOG[0]["symbol"]: [self.print]}
        self.client = Client(REMOTE_ADDR="127.0.0.1",HTTP_HOST="localhost")

    def ingest(self, catalog=None, payload=None, state=None):
        return ingest_page(self.provider, catalog or CATALOG, payload or self.payload, state or self.state, Decimal("0"), NOW)

    def test_exact_premium_actual_multiplier(self):
        row = {**CATALOG[0], "multiplier": "25"}
        events, _ = self.ingest([row])
        self.assertEqual(events[0].premium, Decimal("25000"))
        self.assertEqual(events[0].contract.expiration, date(2026, 11, 20))
        self.assertEqual(events[0].direction, "UNKNOWN")

    def test_missing_multiplier_never_assumes_100(self):
        event = self.ingest([{**CATALOG[0], "multiplier": None}])[0][0]
        self.assertIsNone(event.premium)
        self.assertIsNone(event.multiplier)

    def test_duplicate_replay_and_identical_prints(self):
        payload = {CATALOG[0]["symbol"]: [self.print, self.print]}
        self.assertEqual(len(self.ingest(payload=payload)[0]), 2)
        self.assertEqual(len(self.ingest(payload=payload)[0]), 0)
        self.assertEqual(OptionsTransaction.objects.count(), 2)

    def test_page_boundary_identical_prints(self):
        _, counts = self.ingest()
        events, _ = self.ingest(state={**self.state, "occurrences": counts})
        self.assertEqual(len(events), 1)
        self.assertEqual(OptionsTransaction.objects.count(), 2)

    def test_nanosecond_identity_preserved(self):
        payload = {CATALOG[0]["symbol"]: [self.print, {**self.print, "t": "2026-10-23T14:29:00.123456790Z"}]}
        events, _ = self.ingest(payload=payload)
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0].timestamp_raw, events[1].timestamp_raw)

    def test_out_of_window_fails(self):
        with self.assertRaises(ProviderError):
            self.ingest(payload={CATALOG[0]["symbol"]: [{**self.print, "t": "2026-10-22T14:29:00Z"}]})

    def test_cursor_checkpoint_rolls_back_invalid_page(self):
        cursor = FlowCursor.objects.create(provider=self.provider, symbol="NVDA", state={**self.state,"catalog":CATALOG,"catalog_total":1,"catalog_at":NOW.isoformat()})
        cfg = CollectionSettings.objects.create(symbols=["NVDA"])
        adapter = Mock()
        adapter.trades.return_value = {"trades": {CATALOG[0]["symbol"]: [self.print, {**self.print,"s":-1}]}}
        with patch("smart_money.ingestion.timezone.now", return_value=NOW), self.assertRaises(ProviderError):
            collect_cursor(adapter,self.provider,cursor,cfg,lambda:None)
        self.assertEqual(OptionsTransaction.objects.count(),0)
        cursor.refresh_from_db()
        self.assertEqual(cursor.state["end"],self.state["end"])

    def test_repeated_page_token_rejected(self):
        cursor=FlowCursor.objects.create(provider=self.provider,symbol="NVDA",state={**self.state,"catalog":CATALOG,"catalog_at":NOW.isoformat(),"tokens":["same"]})
        adapter=Mock();adapter.trades.return_value={"trades":self.payload,"next_page_token":"same"}
        with self.assertRaises(ProviderError):
            collect_cursor(adapter,self.provider,cursor,CollectionSettings(revision=1),lambda:None)
        self.assertFalse(OptionsTransaction.objects.exists())

    def test_catalog_refresh_preserves_gap_cursor(self):
        last=NOW-timedelta(hours=1)
        cursor=FlowCursor.objects.create(provider=self.provider,symbol="NVDA",state={"last_end":last.isoformat()})
        adapter=Mock();adapter.catalog.return_value=(CATALOG,1);adapter.trades.return_value={"trades":{}}
        with patch("smart_money.ingestion.timezone.now",return_value=NOW):
            collect_cursor(adapter,self.provider,cursor,CollectionSettings(revision=1,min_premium=50000),lambda:None)
        self.assertEqual(adapter.trades.call_args.args[1],(last-timedelta(seconds=60)).isoformat())

    def test_filter_validation_rejects_unknown_and_invalid(self):
        for data in ({"normal_volume":10},{"option_type":"BULL"},{"min_score":101},{"min_quantity":True},{"symbols":["https://bad"]}):
            with self.assertRaises(ValueError): filters(data)

    def test_api_reads_no_provider_requests(self):
        self.ingest()
        with patch("dashboard.alpaca.read_get",side_effect=AssertionError("network on GET")):
            for path in ("status/","preferences/","activity/?from=2026-10-23&to=2026-10-23","analytics/?from=2026-10-23&to=2026-10-23","filings/","alerts/"):
                self.assertEqual(self.client.get("/api/smart-money/"+path).status_code,200,path)

    def test_api_stable_pagination_and_filter(self):
        event=self.ingest()[0][0]
        OptionsTransaction.objects.bulk_create([OptionsTransaction(provider=self.provider,contract=event.contract,event_key=str(i),ticker="NVDA",option_type="CALL",trade_at=event.trade_at,timestamp_raw=event.timestamp_raw,price=5,quantity=200,premium=100000) for i in range(55)])
        path="/api/smart-money/activity/"
        params={"from":"2026-10-23","to":"2026-10-23"}
        first=self.client.get(path,params).json()
        second=self.client.get(path,{**params,"cursor":first["next"]}).json()
        ids=[e["id"] for e in first["results"]+second["results"]]
        self.assertEqual(len(ids),56);self.assertEqual(len(set(ids)),56)
        self.assertEqual(self.client.get(path,{**params,"filters":json.dumps({"option_type":"PUT"})}).json()["count"],0)

    def test_api_detail_nulls_and_timestamps(self):
        event=self.ingest()[0][0]
        response=self.client.get(f"/api/smart-money/activity/{event.pk}/").json()
        self.assertIsNone(response["bid"])
        self.assertEqual(response["timestamp_raw"],self.print["t"])
        self.assertIn("received_at",response)
        self.assertIn("outcomes",response)

    def test_csrf_required_even_on_local_server(self):
        client=Client(enforce_csrf_checks=True,REMOTE_ADDR="127.0.0.1",HTTP_HOST="localhost")
        self.assertEqual(client.post("/api/smart-money/preferences/",json.dumps({"symbols":[],"filters":{}}),content_type="application/json").status_code,403)
        self.assertEqual(client.get("/smart-money/").status_code,200)
        token=client.cookies["csrftoken"].value
        self.assertEqual(client.post("/api/smart-money/preferences/",json.dumps({"symbols":["SOFI"],"filters":{}}),content_type="application/json",HTTP_X_CSRFTOKEN=token).status_code,200)

    def test_lan_unauthenticated_denied(self):
        self.assertEqual(Client(REMOTE_ADDR="192.168.1.10").get("/api/smart-money/status/").status_code,403)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_tunnel_host_does_not_bypass_local_access(self):
        client=Client(REMOTE_ADDR="127.0.0.1",HTTP_HOST="example.ngrok-free.dev")
        self.assertEqual(client.get("/api/smart-money/status/").status_code,403)

    @override_settings(SHARING_ENABLED=True,SHARING_USERS=("reader",),SMART_MONEY=SM)
    def test_shared_flow_blocked_without_redistribution_permission(self):
        user=get_user_model().objects.create_user(username="reader")
        request=RequestFactory().get("/api/smart-money/status/",REMOTE_ADDR="127.0.0.1");request.user=user
        response=status(request)
        self.assertFalse(json.loads(response.content)["can_view"])
        from .views import activity
        self.assertEqual(activity(request).status_code,403)

    @override_settings(SHARING_ENABLED=True,SHARING_USERS=("one","two"))
    def test_alert_owner_isolation(self):
        one=get_user_model().objects.create_user(username="one")
        two=get_user_model().objects.create_user(username="two")
        AlertConfiguration.objects.create(owner=one,name="Private rule",filters=filters({}),enabled=False)
        request=RequestFactory().get("/api/smart-money/alerts/");request.user=two
        self.assertEqual(json.loads(alerts(request).content)["rules"],[])

    def test_optimistic_collection_revision(self):
        data={"enabled":False,"symbols":["AAPL"],"min_premium":50000,"revision":1,"sec_enabled":False,"sec_ciks":[]}
        path="/api/smart-money/collection/"
        self.assertEqual(self.client.post(path,json.dumps(data),content_type="application/json").status_code,200)
        self.assertEqual(self.client.post(path,json.dumps(data),content_type="application/json").status_code,409)
        self.assertEqual(CollectionSettings.objects.get().symbols,["AAPL"])

    def test_unsupported_alert_rule_rejected_not_skipped(self):
        data={"name":"Sweep", "enabled":False,"filters":{"structure":"SWEEP"}}
        self.assertEqual(self.client.post("/api/smart-money/alerts/",json.dumps(data),content_type="application/json").status_code,400)
        self.assertFalse(AlertConfiguration.objects.exists())

    def test_queue_deduplicates_and_does_not_replay_old_trades(self):
        event=self.ingest()[0][0]
        rule=AlertConfiguration.objects.create(name="Large prints",filters=filters({}),armed_at=NOW-timedelta(minutes=5))
        queue_events([event]);queue_events([event])
        self.assertEqual(UnusualActivityAlert.objects.count(),1)
        rule.armed_at=NOW;rule.save()
        UnusualActivityAlert.objects.all().delete()
        queue_events([event]);self.assertFalse(UnusualActivityAlert.objects.exists())

    @override_settings(SMART_MONEY={**SM,"allow_discord":True,"allow_sharing":True})
    def test_discord_outside_hours_never_posts(self):
        with patch("smart_money.notifications.timezone.now",return_value=NOW.replace(hour=23)), patch("smart_money.notifications.discord_ready",return_value=True), patch("smart_money.notifications.post_discord_message") as post:
            deliver(lambda:None)
            post.assert_not_called()

    @override_settings(SMART_MONEY={**SM,"allow_discord":True,"allow_sharing":True})
    def test_discord_sent_once(self):
        event=self.ingest()[0][0]
        AlertConfiguration.objects.create(name="Large prints",filters=filters({}),armed_at=NOW-timedelta(minutes=5))
        queue_events([event]);WorkerState.objects.create(discord_retry_at=NOW)
        UnusualActivityAlert.objects.update(retry_at=NOW)
        with patch("smart_money.notifications.timezone.now",return_value=NOW),patch("smart_money.notifications.discord_ready",return_value=True),patch("smart_money.notifications.post_discord_message") as post:
            deliver(lambda:None);deliver(lambda:None)
            post.assert_called_once()
        self.assertEqual(UnusualActivityAlert.objects.get().status,"sent")

    def test_only_one_worker_lease(self):
        self.assertTrue(claim("one"));self.assertFalse(claim("two"));self.assertTrue(claim("one"))

    def test_paused_worker_no_network(self):
        with patch("dashboard.alpaca.read_get") as read,patch("requests.post") as post:
            run_once("test")
            read.assert_not_called();post.assert_not_called()

    def test_historical_outcomes_are_not_option_profit(self):
        event=self.ingest()[0][0]
        start=NOW.replace(hour=13,minute=30)
        bars=[{"time":int((start+timedelta(minutes=5*i)).timestamp()),"open":100+i,"high":102+i,"low":99+i,"close":101+i,"volume":100+i} for i in range(24)]
        adapter=Mock();adapter.stock_bars.return_value=(bars,None)
        with patch("smart_money.enrichment.timezone.now",return_value=NOW+timedelta(hours=1)),patch("smart_money.enrichment.alpaca.data_config",return_value={"stock_feed":"iex"}):
            enrich_one(adapter,event,lambda:None)
        event.refresh_from_db()
        self.assertEqual(event.technical["price"],111)
        outcome=event.outcomes.get(horizon="15m")
        self.assertIsNotNone(outcome.return_percent)
        self.assertIsNone(outcome.favorable_percent)
        self.assertIn("not option P/L",outcome.status)
        self.assertTrue(event.outcomes.filter(horizon="20d",observed_at__isnull=True).exists())

    def test_outcome_groups_and_unknown_direction(self):
        event=self.ingest()[0][0]
        TransactionPriceSnapshot.objects.create(transaction=event,horizon="15m",target_at=NOW,observed_at=NOW,price=200,return_percent=3)
        for group in ("type","premium","structure"):
            response=self.client.get("/api/smart-money/analytics/",{"from":"2026-10-23","to":"2026-10-23","group":group})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()["history"][0]["classified"],0)

    @override_settings(SMART_MONEY={**SM,"provider":"alpaca","allow_storage":True})
    def test_provider_failure_backoff_preserves_records(self):
        CollectionSettings.objects.create(enabled=True,symbols=["NVDA"])
        self.provider.retry_at=NOW-timedelta(days=1);self.provider.save()
        self.ingest()
        with patch("smart_money.providers.AlpacaOpra.readiness",return_value=""),patch("smart_money.providers.AlpacaOpra.catalog",side_effect=ProviderError("Alpaca rate limit reached.")):
            run_once("reader")
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.failures,1)
        self.assertEqual(OptionsTransaction.objects.count(),1)


class ComputationTests(TestCase):
    def test_numeric_rejects_non_finite_and_boolean(self):
        for value in ("NaN","Infinity",True,"1e100"):
            with self.assertRaises(ProviderError):decimal(value)
        with self.assertRaises(ProviderError):timestamp("2026-10-23T10:00:00")

    def test_quote_classification_conservative(self):
        self.assertEqual(classify(Decimal("5.1"),Decimal("5"),Decimal("5.1"),NOW,NOW)[0],"ASK_SIDE")
        self.assertEqual(directional("PUT","ASK_SIDE"),"BEARISH")
        self.assertEqual(directional("CALL","BID_SIDE"),"BEARISH")
        for quote_time in (NOW+timedelta(seconds=1),NOW-timedelta(seconds=3)):
            self.assertEqual(classify(Decimal("5.1"),Decimal("5"),Decimal("5.1"),quote_time,NOW)[0],"UNKNOWN")
        self.assertEqual(classify(5,None,None,None,NOW)[0],"UNKNOWN")

    def test_score_does_not_invent_oi_or_normal_volume(self):
        value,evidence=unusual_score(100000,1,"UNKNOWN",volume=100,oi=0)
        self.assertEqual(value,20)
        self.assertIn("No historical-normal-volume baseline yet",evidence["limitations"])

    def test_technical_snapshot_no_lookahead(self):
        start=NOW.replace(hour=13,minute=30)
        bars=[{"time":int((start+timedelta(minutes=5*i)).timestamp()),"open":100+i,"high":102+i,"low":99+i,"close":101+i,"volume":100+i} for i in range(12)]
        before=technical_snapshot(bars,NOW,"UNKNOWN")
        future={"time":NOW.timestamp(),"open":900,"high":999,"low":900,"close":999,"volume":1000000}
        self.assertEqual(before,technical_snapshot(bars+[future],NOW,"UNKNOWN"))
        self.assertEqual(before["bar_closed_at"],NOW.timestamp())
        self.assertEqual(before["confirmation"],"Mixed or inconclusive")

    def test_horizons_use_trading_days_and_market_hours(self):
        targets=horizons(NOW)
        self.assertEqual(targets["1d"].date(),date(2026,10,26))
        self.assertEqual(targets["close"].hour,20)
        self.assertTrue(in_session(NOW))
        self.assertFalse(in_session(NOW+timedelta(days=1)))

    def test_provider_request_budget_get_only(self):
        adapter=AlpacaOpra(budget=2)
        with patch("dashboard.alpaca.read_get",return_value={}) as read:
            adapter.trades([CATALOG[0]["symbol"]],NOW.isoformat(),NOW.isoformat())
            with self.assertRaises(BudgetExhausted):adapter.get("/v2/stocks/AAPL/trades/latest")
            self.assertEqual(read.call_count,2)
            self.assertEqual(read.call_args_list[0].args[1]["feed"],"opra")
            self.assertEqual(read.call_args.args[0],"/v1beta1/options/trades")

    @override_settings(SMART_MONEY={**SM,"allow_storage":True,"option_feed":"indicative"},ALPACA_DATA={"enabled":True,"option_feed":"indicative"})
    def test_indicative_not_accepted_as_real_flow(self):
        with patch.dict("os.environ",{"ALPACA_DATA_API_KEY":"fixture", "ALPACA_DATA_SECRET_KEY":"fixture"}):
            self.assertIn("Indicative",AlpacaOpra.readiness())

    @override_settings(SMART_MONEY={**SM,"allow_storage":True,"option_feed":"opra"},ALPACA_DATA={"enabled":True,"option_feed":"indicative"})
    def test_flow_does_not_require_changing_existing_chain_feed(self):
        with patch.dict("os.environ",{"ALPACA_DATA_API_KEY":"fixture", "ALPACA_DATA_SECRET_KEY":"fixture"}):
            self.assertEqual(AlpacaOpra.readiness(),"")

    def test_opra_entitlement_denial_prevents_historical_ingestion(self):
        from dashboard.alpaca import AlpacaDataError
        with patch("dashboard.alpaca.read_get",side_effect=AlpacaDataError("Alpaca denied access.")) as read:
            with self.assertRaises(ProviderError):
                AlpacaOpra().trades([CATALOG[0]["symbol"]],NOW.isoformat(),NOW.isoformat())
            read.assert_called_once()
            self.assertEqual(read.call_args.args[0],"/v1beta1/options/trades/latest")

    def test_sec_dtd_and_utf16_rejected(self):
        for raw in (b'<!DOCTYPE foo [<!ENTITY x "test">]><foo>&x;</foo>', '<foo>test</foo>'.encode("utf-16")):
            with self.assertRaises(ProviderError):xml_document(raw)

    def test_sec_provider_fixed_hosts(self):
        with override_settings(SMART_MONEY={**SM,"sec_user_agent":"Tests tests@example.com"}):
            with self.assertRaises(ProviderError):SecClient().get("http://127.0.0.1/private")

    def test_13f_units_not_invented(self):
        rows=parse_holdings(xml_document(b'<informationTable xmlns="example"><infoTable><nameOfIssuer>ACME</nameOfIssuer><cusip>123456789</cusip><value>123</value><shrsOrPrnAmt><sshPrnamt>100</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable></informationTable>'))
        self.assertEqual(rows[0]["reported_value"],123)
        self.assertEqual(rows[0]["value_unit"],"As reported")

    def test_form4_award_is_not_purchase(self):
        rows=parse_insiders(xml_document(b'<ownershipDocument><issuer><issuerTradingSymbol>AAPL</issuerTradingSymbol></issuer><nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-10-20</value></transactionDate><transactionCoding><transactionCode>A</transactionCode></transactionCoding><transactionAmounts><transactionShares><value>30</value></transactionShares></transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>'))
        self.assertEqual(rows[0]["code"],"A")
        self.assertIsNone(rows[0]["price"])

    def test_holdings_changes_require_adjacent_original_quarters(self):
        first=SecFiling.objects.create(accession="first",cik="0000000001",filer="Fixture",form="13F-HR",report_date=date(2026,6,30),filed_at=NOW,url="https://www.sec.gov/")
        second=SecFiling.objects.create(accession="second",cik=first.cik,filer="Fixture",form="13F-HR",report_date=date(2026,9,30),filed_at=NOW,url=first.url)
        for filing,shares in ((first,100),(second,150)):
            InstitutionalHolding.objects.create(filing=filing,issuer="Test",cusip="123",shares=shares,reported_value=1,line_number=1)
        result=holding_changes(second)
        self.assertEqual(result["rows"][0]["status"],"INCREASED")
        SecFiling.objects.create(accession="amended",cik=first.cik,filer="Fixture",form="13F-HR/A",report_date=first.report_date,filed_at=NOW,url=first.url)
        self.assertEqual(holding_changes(second)["rows"],[])
