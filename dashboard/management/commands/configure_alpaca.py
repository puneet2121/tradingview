import getpass
import json
import os
import tempfile

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from dashboard import alpaca


class Command(BaseCommand):
    help = "Configure Alpaca paper account/data access. Setup never submits orders."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Check the existing connection without changing it.")
        parser.add_argument("--stock-feed", choices=("iex", "sip"), default="iex")
        parser.add_argument("--option-feed", choices=("indicative", "opra"), default="indicative")

    def handle(self, *args, **options):
        if options["check"]:
            config = settings.ALPACA_DATA
        else:
            self.stdout.write("Paper API keys only. Input is hidden; no orders will be placed.")
            config = {
                "enabled": True, "key": getpass.getpass("Paper API key: ").strip(),
                "secret": getpass.getpass("Paper secret key: ").strip(),
                "stock_feed": options["stock_feed"], "option_feed": options["option_feed"],
            }
        try:
            with override_settings(ALPACA_DATA=config):
                alpaca.read_get("/v2/account", paper=True)
                alpaca.read_get("/v2/stocks/AAPL/trades/latest", {"feed": config["stock_feed"]})
        except alpaca.AlpacaDataError as exc:
            raise CommandError(str(exc)) from None
        if not options["check"]:
            directory = settings.BASE_DIR / ".local-sharing"
            directory.mkdir(mode=0o700, exist_ok=True)
            fd, temporary = tempfile.mkstemp(dir=directory, prefix="alpaca-data-")
            try:
                with os.fdopen(fd, "w") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    json.dump(config, handle)
                os.replace(temporary, directory / "alpaca-data.json")
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            self.stdout.write("Saved private paper connection. Restart the server to apply it. Manual orders require review and confirmation in the app.")
        self.stdout.write(self.style.SUCCESS("Paper account and stock feed verified. Option feed entitlement is checked when loading a chain. No orders sent."))
