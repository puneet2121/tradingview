from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/config/", views.config, name="config"),
    path("api/history/", views.history, name="history"),
    path("api/quote/", views.quote, name="quote"),
    path("api/symbol/", views.symbol, name="symbol"),
]
