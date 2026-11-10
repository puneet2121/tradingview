from django.conf import settings
from django.db import models
from django.utils import timezone


class MarketDataProvider(models.Model):
    code = models.CharField(max_length=24, primary_key=True)
    name = models.CharField(max_length=80)
    capabilities = models.JSONField(default=dict)
    checked_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=240, default="Not connected")
    retry_at = models.DateTimeField(default=timezone.now)
    failures = models.PositiveIntegerField(default=0)


class CollectionSettings(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=False)
    min_premium = models.DecimalField(max_digits=18, decimal_places=2, default=50000)
    symbols = models.JSONField(default=list)
    revision = models.PositiveIntegerField(default=1)
    armed_at = models.DateTimeField(null=True)
    updated_at = models.DateTimeField(auto_now=True)
    sec_ciks = models.JSONField(default=list)
    sec_enabled = models.BooleanField(default=False)
    sec_checked_at = models.DateTimeField(null=True)
    sec_status = models.CharField(max_length=240, default="Not configured")


class WorkerState(models.Model):
    key = models.CharField(max_length=24, primary_key=True, default="flow")
    lease_owner = models.CharField(max_length=64, blank=True)
    lease_until = models.DateTimeField(default=timezone.now)
    heartbeat = models.DateTimeField(null=True)
    status = models.CharField(max_length=240, default="Paused")
    discord_retry_at = models.DateTimeField(default=timezone.now)


class FlowCursor(models.Model):
    provider = models.ForeignKey(MarketDataProvider, on_delete=models.PROTECT)
    symbol = models.CharField(max_length=16)
    revision = models.PositiveIntegerField(default=1)
    state = models.JSONField(default=dict)
    checked_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=240, default="Waiting")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "symbol"], name="sm_cursor_unique")]


class OptionContract(models.Model):
    provider = models.ForeignKey(MarketDataProvider, on_delete=models.PROTECT)
    symbol = models.CharField(max_length=64)
    underlying = models.CharField(max_length=16, db_index=True)
    company_name = models.CharField(max_length=160, blank=True)
    option_type = models.CharField(max_length=4)
    strike = models.DecimalField(max_digits=20, decimal_places=6)
    expiration = models.DateField(db_index=True)
    multiplier = models.DecimalField(max_digits=16, decimal_places=6, null=True)
    metadata_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "symbol"], name="sm_contract_unique")]


class OptionsTransaction(models.Model):
    provider = models.ForeignKey(MarketDataProvider, on_delete=models.PROTECT)
    contract = models.ForeignKey(OptionContract, on_delete=models.PROTECT)
    event_key = models.CharField(max_length=96)
    ticker = models.CharField(max_length=16)
    option_type = models.CharField(max_length=4)
    trade_at = models.DateTimeField()
    timestamp_raw = models.CharField(max_length=48)
    received_at = models.DateTimeField(default=timezone.now)
    price = models.DecimalField(max_digits=20, decimal_places=6)
    quantity = models.PositiveBigIntegerField()
    multiplier = models.DecimalField(max_digits=16, decimal_places=6, null=True)
    premium = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    bid = models.DecimalField(max_digits=20, decimal_places=6, null=True)
    ask = models.DecimalField(max_digits=20, decimal_places=6, null=True)
    quote_at = models.DateTimeField(null=True)
    exchange = models.CharField(max_length=32, blank=True)
    condition = models.CharField(max_length=100, blank=True)
    classification = models.CharField(max_length=24, default="UNKNOWN")
    structure = models.CharField(max_length=24, default="UNKNOWN")
    direction = models.CharField(max_length=12, default="UNKNOWN")
    score = models.PositiveSmallIntegerField(default=0)
    score_version = models.CharField(max_length=24, default="observed-v1")
    evidence = models.JSONField(default=dict)
    feed = models.CharField(max_length=32, default="opra")
    delayed = models.BooleanField(default=True)
    iv = models.DecimalField(max_digits=16, decimal_places=8, null=True)
    delta = models.DecimalField(max_digits=16, decimal_places=8, null=True)
    volume = models.PositiveBigIntegerField(null=True)
    open_interest = models.PositiveBigIntegerField(null=True)
    metrics_at = models.DateTimeField(null=True)
    technical = models.JSONField(default=dict)
    technical_at = models.DateTimeField(null=True)
    technical_status = models.CharField(max_length=160, default="Pending historical bars")
    excluded = models.BooleanField(default=False)

    class Meta:
        ordering = ["-trade_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["provider", "event_key"], name="sm_trade_unique")]
        indexes = [models.Index(fields=["ticker", "trade_at"], name="sm_ticker_time"),
                   models.Index(fields=["option_type", "trade_at"], name="sm_type_time"),
                   models.Index(fields=["premium", "trade_at"], name="sm_premium_time")]


class SmartMoneyWatchlist(models.Model):
    owner_key = models.CharField(max_length=160, unique=True)
    symbols = models.JSONField(default=list)
    filters = models.JSONField(default=dict)


class AlertConfiguration(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=80)
    enabled = models.BooleanField(default=True)
    filters = models.JSONField(default=dict)
    armed_at = models.DateTimeField(default=timezone.now)
    revision = models.PositiveIntegerField(default=1)


class UnusualActivityAlert(models.Model):
    configuration = models.ForeignKey(AlertConfiguration, on_delete=models.CASCADE)
    transaction = models.ForeignKey(OptionsTransaction, on_delete=models.PROTECT)
    revision = models.PositiveIntegerField()
    message = models.TextField()
    status = models.CharField(max_length=16, default="pending", db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    retry_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True)
    error = models.CharField(max_length=160, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["configuration", "transaction"], name="sm_notice_unique")]


class SecFiling(models.Model):
    accession = models.CharField(max_length=32, unique=True)
    cik = models.CharField(max_length=10, db_index=True)
    filer = models.CharField(max_length=200)
    form = models.CharField(max_length=16)
    filed_at = models.DateTimeField()
    report_date = models.DateField(null=True)
    url = models.URLField(max_length=500)
    status = models.CharField(max_length=160, default="Imported")
    ingested_at = models.DateTimeField(auto_now_add=True)


class InstitutionalHolding(models.Model):
    filing = models.ForeignKey(SecFiling, on_delete=models.CASCADE, related_name="holdings")
    issuer = models.CharField(max_length=200)
    cusip = models.CharField(max_length=12, db_index=True)
    security_class = models.CharField(max_length=100, blank=True)
    put_call = models.CharField(max_length=8, blank=True)
    shares_type = models.CharField(max_length=8, blank=True)
    shares = models.DecimalField(max_digits=24, decimal_places=6)
    reported_value = models.DecimalField(max_digits=24, decimal_places=2)
    value_unit = models.CharField(max_length=24, default="As reported")
    line_number = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["filing", "line_number"], name="sm_holding_line")]


class InsiderTransaction(models.Model):
    filing = models.ForeignKey(SecFiling, on_delete=models.CASCADE, related_name="insiders")
    ticker = models.CharField(max_length=16, db_index=True)
    owner_name = models.CharField(max_length=200)
    transaction_date = models.DateField()
    code = models.CharField(max_length=4)
    security = models.CharField(max_length=200)
    quantity = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    price = models.DecimalField(max_digits=24, decimal_places=6, null=True)
    derivative = models.BooleanField(default=False)
    line_number = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["filing", "line_number"], name="sm_insider_line")]


class TransactionPriceSnapshot(models.Model):
    transaction = models.ForeignKey(OptionsTransaction, on_delete=models.CASCADE, related_name="outcomes")
    horizon = models.CharField(max_length=8)
    target_at = models.DateTimeField()
    observed_at = models.DateTimeField(null=True)
    price = models.DecimalField(max_digits=20, decimal_places=6, null=True)
    return_percent = models.FloatField(null=True)
    favorable_percent = models.FloatField(null=True)
    adverse_percent = models.FloatField(null=True)
    status = models.CharField(max_length=100, default="Pending")
    source = models.CharField(max_length=32, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["transaction", "horizon"], name="sm_outcome_unique")]


class FundamentalSnapshot(models.Model):
    symbol = models.CharField(max_length=16, primary_key=True)
    payload = models.JSONField(default=dict)
    fetched_at = models.DateTimeField(null=True)
    attempted_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(default=timezone.now)
    retry_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(default=timezone.now)
    refresh_token = models.CharField(max_length=32, blank=True)
    error = models.CharField(max_length=200, blank=True)


class TechnicalResearchSnapshot(models.Model):
    symbol = models.CharField(max_length=16, primary_key=True)
    payload = models.JSONField(default=dict)
    fetched_at = models.DateTimeField(null=True)
    attempted_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField(default=timezone.now)
    retry_at = models.DateTimeField(default=timezone.now)
    lease_until = models.DateTimeField(default=timezone.now)
    refresh_token = models.CharField(max_length=32, blank=True)
    error = models.CharField(max_length=200, blank=True)
