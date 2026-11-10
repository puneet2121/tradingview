from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError


class CloudConfigurationError(ImproperlyConfigured):
    """An application-authored configuration message that contains no secret values."""


class CloudBootstrapError(CommandError):
    """An application-authored account setup message that contains no passwords."""
