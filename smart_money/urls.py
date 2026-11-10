from django.urls import path
from . import views

app_name = "smart_money"
urlpatterns = [
    path("smart-money/", views.dashboard, name="dashboard"),
    path("api/smart-money/status/", views.status, name="status"),
    path("api/smart-money/fundamentals/", views.fundamentals, name="fundamentals"),
    path("api/smart-money/research-ratings/", views.research_ratings, name="research_ratings"),
    path("api/smart-money/collection/", views.collection, name="collection"),
    path("api/smart-money/preferences/", views.preferences, name="preferences"),
    path("api/smart-money/activity/", views.activity, name="activity"),
    path("api/smart-money/activity/<int:pk>/", views.detail, name="detail"),
    path("api/smart-money/analytics/", views.analytics, name="analytics"),
    path("api/smart-money/filings/", views.filings, name="filings"),
    path("api/smart-money/alerts/", views.alerts, name="alerts"),
    path("api/smart-money/alerts/<int:pk>/", views.alert_item, name="alert_item"),
]
