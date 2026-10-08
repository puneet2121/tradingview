from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import render
from django.utils.cache import add_never_cache_headers
from django.utils.deprecation import MiddlewareMixin


def no_client_ip(request):
    # Do not trust client-supplied forwarded IPs or throttle the shared proxy IP.
    return None


class SharingAuthenticationForm(AuthenticationForm):
    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if user.username not in settings.SHARING_USERS:
            raise self.get_invalid_login_error()


class SharingLoginMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.resolver_match.url_name == "login":
            return None
        if request.user.is_authenticated and request.user.get_username() in settings.SHARING_USERS:
            return None
        if request.path.startswith("/api/"):
            return JsonResponse({"error": "Sign in to continue.", "login_url": settings.LOGIN_URL}, status=401)
        return redirect_to_login(request.get_full_path(), settings.LOGIN_URL)

    def process_response(self, request, response):
        add_never_cache_headers(response)
        return response


def csrf_failure(request, reason=""):
    if request.path.startswith("/api/"):
        return JsonResponse({"error": "Security token expired. Reload the page and try again."}, status=403)
    return render(request, "registration/csrf_failure.html", status=403)
