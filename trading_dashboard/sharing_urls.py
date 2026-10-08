from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path

from .sharing_auth import SharingAuthenticationForm


urlpatterns = [
    path("accounts/login/", LoginView.as_view(authentication_form=SharingAuthenticationForm), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("", include("dashboard.urls")),
]
