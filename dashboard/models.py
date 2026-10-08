from decimal import Decimal

from django.db import models
from django.conf import settings
from django.utils import timezone


class MarketAlert(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    enabled = models.BooleanField(default=True)
    symbols = models.JSONField(default=list)
    timeframes = models.JSONField(default=list)
    condition = models.JSONField(default=dict)
    cooldown_minutes = models.PositiveIntegerField(default=0)
    revision = models.PositiveIntegerField(default=1)
    armed_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-id"]


class MarketAlertState(models.Model):
    alert = models.ForeignKey(MarketAlert, on_delete=models.CASCADE, related_name="scan_states")
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=8)
    last_bar_time = models.BigIntegerField(null=True)
    checked_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=160, default="Waiting for scanner")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["alert", "symbol", "timeframe"], name="unique_market_alert_state")]


class MarketAlertEvent(models.Model):
    alert = models.ForeignKey(MarketAlert, null=True, on_delete=models.SET_NULL)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    revision = models.PositiveIntegerField()
    name = models.CharField(max_length=80)
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=8)
    bar_time = models.BigIntegerField()
    bar_closed_at = models.DateTimeField()
    snapshot = models.JSONField(default=dict)
    message = models.TextField()
    status = models.CharField(max_length=16, default="pending", db_index=True)
    error = models.CharField(max_length=200, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    retry_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-id"]
        constraints = [models.UniqueConstraint(fields=["alert", "revision", "symbol", "timeframe", "bar_time"], name="unique_market_alert_event")]


class MarketAlertWorker(models.Model):
    key = models.CharField(max_length=16, primary_key=True, default="alerts")
    lease_owner = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField(null=True)
    discord_retry_at = models.DateTimeField(null=True)


class PineIndicator(models.Model):
    name = models.CharField(max_length=80, unique=True)
    source = models.TextField()
    overlay = models.BooleanField(default=True)
    revision = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class TradeActivity(models.Model):
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.CharField(max_length=16)
    action = models.CharField(max_length=16)
    reason = models.CharField(max_length=64)
    asset = models.CharField(max_length=16)
    trade_id = models.BigIntegerField(null=True, blank=True)
    strategy = models.CharField(max_length=64, blank=True)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["-id"]


def default_strategy_rule_text():
    return """BUY CALL WHEN EMA(9) crosses above VWAP
AND volume is increasing
AND close > previous close

BUY PUT WHEN EMA(9) crosses below VWAP
AND volume is increasing
AND close < previous close"""


class Strategy(models.Model):
    UNDERLYING = "underlying"
    OPTION = "option"
    ATM = "atm"
    ITM = "itm"
    OTM = "otm"

    name = models.CharField(max_length=64, unique=True)
    enabled = models.BooleanField(default=True, db_index=True)
    rule_text = models.TextField(default=default_strategy_rule_text)
    trade_asset = models.CharField(
        max_length=16,
        choices=[(UNDERLYING, UNDERLYING), (OPTION, OPTION)],
        default=OPTION,
        db_index=True,
    )
    risk_percent = models.DecimalField(max_digits=7, decimal_places=6, default=Decimal("0.020000"))
    option_min_dte = models.PositiveIntegerField(default=7)
    option_max_dte = models.PositiveIntegerField(default=14)
    option_strike_mode = models.CharField(
        max_length=8,
        choices=[(ATM, ATM), (ITM, ITM), (OTM, OTM)],
        default=ATM,
    )
    option_take_profit_percent = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
        default=Decimal("1.000000"),
    )
    option_stop_loss_percent = models.DecimalField(
        max_digits=7,
        decimal_places=6,
        null=True,
        blank=True,
        default=Decimal("0.500000"),
    )
    max_spread_percent = models.DecimalField(max_digits=7, decimal_places=6, null=True, blank=True, default=Decimal("0.500000"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StrategyScanState(models.Model):
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name="scan_states")
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=8)
    last_signal_time = models.BigIntegerField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["strategy__name", "symbol", "timeframe"]
        constraints = [
            models.UniqueConstraint(fields=["strategy", "symbol", "timeframe"], name="unique_strategy_scan_state"),
        ]

    def __str__(self):
        return f"{self.strategy.name} {self.symbol} {self.timeframe}"


class PaperTrade(models.Model):
    LONG = "LONG"
    SHORT = "SHORT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"

    symbol = models.CharField(max_length=32, db_index=True)
    timeframe = models.CharField(max_length=8, db_index=True)
    strategy = models.CharField(max_length=64, default="testing1", db_index=True)
    side = models.CharField(max_length=8, choices=[(LONG, LONG), (SHORT, SHORT)])
    status = models.CharField(max_length=8, choices=[(OPEN, OPEN), (CLOSED, CLOSED)], default=OPEN, db_index=True)
    signal_time = models.BigIntegerField(db_index=True)
    entry_time = models.BigIntegerField(db_index=True)
    entry_price = models.DecimalField(max_digits=16, decimal_places=6)
    quantity = models.DecimalField(max_digits=18, decimal_places=6)
    risk_amount = models.DecimalField(max_digits=16, decimal_places=2)
    risk_per_share = models.DecimalField(max_digits=16, decimal_places=6)
    stop_price = models.DecimalField(max_digits=16, decimal_places=6)
    best_price = models.DecimalField(max_digits=16, decimal_places=6)
    contract_price = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    iv = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    delta = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    gamma = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    theta = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    vega = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    rho = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    execution_provider = models.CharField(max_length=32, default="local", db_index=True)
    broker_order_id = models.CharField(max_length=128, blank=True)
    broker_status = models.CharField(max_length=128, blank=True)
    exit_time = models.BigIntegerField(null=True, blank=True)
    exit_price = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    exit_reason = models.CharField(max_length=32, blank=True)
    pnl = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-entry_time", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["symbol", "timeframe", "strategy", "side", "signal_time"],
                name="unique_paper_signal",
            )
        ]

    def __str__(self):
        return f"{self.strategy} {self.side} {self.symbol} {self.timeframe} @{self.entry_price}"


class PaperWatchSymbol(models.Model):
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=8, default="3m")
    enabled = models.BooleanField(default=True, db_index=True)
    last_signal_time = models.BigIntegerField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["symbol", "timeframe"]
        constraints = [
            models.UniqueConstraint(fields=["symbol", "timeframe"], name="unique_paper_watch_symbol"),
        ]

    def __str__(self):
        return f"{self.symbol} {self.timeframe}"


class OptionPaperTrade(models.Model):
    LONG = "LONG"
    SHORT = "SHORT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CALL = "call"
    PUT = "put"

    underlying_symbol = models.CharField(max_length=32, db_index=True)
    contract_symbol = models.CharField(max_length=64, db_index=True)
    option_type = models.CharField(max_length=8, choices=[(CALL, CALL), (PUT, PUT)])
    expiration_date = models.DateField(db_index=True)
    strike = models.DecimalField(max_digits=16, decimal_places=6)
    strategy = models.CharField(max_length=64, blank=True, default="", db_index=True)
    timeframe = models.CharField(max_length=8, blank=True, default="")
    signal_time = models.BigIntegerField(null=True, blank=True, db_index=True)
    auto_trade = models.BooleanField(default=False, db_index=True)
    side = models.CharField(max_length=8, choices=[(LONG, LONG), (SHORT, SHORT)])
    status = models.CharField(max_length=8, choices=[(OPEN, OPEN), (CLOSED, CLOSED)], default=OPEN, db_index=True)
    quantity = models.PositiveIntegerField(default=1)
    entry_time = models.DateTimeField(auto_now_add=True)
    entry_price = models.DecimalField(max_digits=16, decimal_places=6)
    entry_underlying_price = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    entry_bid = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    entry_ask = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    entry_iv = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    entry_delta = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    entry_gamma = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    entry_theta = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    entry_vega = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    entry_rho = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    exit_time = models.DateTimeField(null=True, blank=True)
    exit_price = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    exit_underlying_price = models.DecimalField(max_digits=16, decimal_places=6, null=True, blank=True)
    exit_reason = models.CharField(max_length=32, blank=True)
    pnl = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    notes = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-entry_time", "-id"]

    def __str__(self):
        return f"{self.side} {self.quantity} {self.contract_symbol} @{self.entry_price}"
