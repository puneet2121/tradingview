from django.urls import include, path
from .cloud_views import health

urlpatterns = [
    path("healthz/", health, name="cloud_health"),
    path("", include("trading_dashboard.sharing_urls")),
]
