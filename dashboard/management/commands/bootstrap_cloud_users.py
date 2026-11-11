import json
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    MinimumLengthValidator, get_default_password_validators, validate_password,
)
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from trading_dashboard.cloud_errors import CloudBootstrapError


class Command(BaseCommand):
    help = "Create missing cloud logins, or explicitly set a temporary shared password, without printing secrets."

    def handle(self, *args, **options):
        if not getattr(settings, "CLOUD_DEPLOYMENT", False):
            raise CloudBootstrapError("This command is only available with cloud_settings.")
        User = get_user_model()
        if "CLOUD_TEMPORARY_SHARED_PASSWORD" in os.environ:
            self.set_temporary_shared_password(User, os.environ["CLOUD_TEMPORARY_SHARED_PASSWORD"])
            return
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

    def set_temporary_shared_password(self, User, password):
        # Only this explicit bootstrap mode relaxes length and uniqueness checks.
        validators = [MinimumLengthValidator(10), *(
            validator for validator in get_default_password_validators()
            if not isinstance(validator, MinimumLengthValidator)
        )]
        existing = {user.username: user for user in User.objects.filter(username__in=settings.SHARING_USERS)}
        try:
            if len(password) > 256:
                raise ValueError
            for username in settings.SHARING_USERS:
                validate_password(password, existing.get(username) or User(username=username), password_validators=validators)
        except (ValueError, ValidationError):
            raise CloudBootstrapError(
                "CLOUD_TEMPORARY_SHARED_PASSWORD must contain 10 to 256 characters and pass "
                "the other password checks for every SHARING_USERS login. No credentials were changed."
            ) from None
        with transaction.atomic():
            for username in sorted(settings.SHARING_USERS):
                user, _ = User.objects.select_for_update().get_or_create(username=username)
                if not user.check_password(password):
                    user.set_password(password)
                    user.save(update_fields=["password"])
        self.stdout.write(
            "Temporary shared password applied to the configured cloud logins; account status unchanged. "
            "Remove CLOUD_TEMPORARY_SHARED_PASSWORD from the service environment after this deployment. "
            "No secrets printed."
        )
