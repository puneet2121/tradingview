import uuid

from django.core.management.base import BaseCommand
from smart_money.worker import loop, run_once


class Command(BaseCommand):
    help = "Run the read-only Smart Money worker; collection must be enabled separately."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        if options["once"]:
            run_once(uuid.uuid4().hex)
        else:
            loop()
