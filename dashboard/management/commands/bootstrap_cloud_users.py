import json
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from trading_dashboard.cloud_errors import CloudBootstrapError


class Command(BaseCommand):
    help = "Create missing cloud logins without printing secrets or changing existing passwords."

    def handle(self, *args, **options):
        if not getattr(settings, "CLOUD_DEPLOYMENT", False):
            raise CloudBootstrapError("This command is only available with cloud_settings.")
        User = get_user_model()
        missing = set(settings.SHARING_USERS) - set(User.objects.filter(username__in=settings.SHARING_USERS).values_list("username", flat=True))
        if not missing:
            self.stdout.write("All four cloud logins already exist; passwords and account status unchanged.")
            return
        try:
            passwords = json.loads(os.environ.get("CLOUD_INITIAL_PASSWORDS", "{}"))
            if not isinstance(passwords, dict) or set(passwords) - set(settings.SHARING_USERS) or not missing <= set(passwords):
                raise ValueError
            for username in missing:
                password = passwords[username]
                if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
                    raise ValueError
                validate_password(password, User(username=username))
            if len({passwords[name] for name in missing}) != len(missing):
                raise ValueError
        except (ValueError, TypeError, ValidationError):
            raise CloudBootstrapError("Set CLOUD_INITIAL_PASSWORDS to a JSON object with a distinct strong password (at least 12 characters) for each missing SHARING_USERS login. No credentials were changed.") from None
        with transaction.atomic():
            for username in sorted(missing):
                user, created = User.objects.get_or_create(username=username)
                if created:
                    user.set_password(passwords[username])
                    user.save(update_fields=["password"])
        self.stdout.write("Missing cloud logins created. Existing logins were not reset; no secrets printed.")
