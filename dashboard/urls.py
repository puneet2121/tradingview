from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("api/config/", views.config, name="config"),
    path("api/history/", views.history, name="history"),
    path("api/quote/", views.quote, name="quote"),
    path("api/symbol/", views.symbol, name="symbol"),
    path("api/paper/state/", views.paper_state, name="paper_state"),
    path("api/paper/activity/", views.trade_activity, name="trade_activity"),
    path("api/discord/test/", views.discord_test, name="discord_test"),
    path("api/paper/signal/", views.paper_signal, name="paper_signal"),
    path("api/paper/mark/", views.paper_mark, name="paper_mark"),
    path("api/paper/reset/", views.paper_reset, name="paper_reset"),
    path("api/paper/watchlist/", views.paper_watchlist, name="paper_watchlist"),
    path("api/paper/scan/", views.paper_scan, name="paper_scan"),
    path("api/strategies/", views.strategies, name="strategies"),
    path("api/options/expirations/", views.option_expirations, name="option_expirations"),
    path("api/options/chain/", views.option_chain, name="option_chain"),
    path("api/options/paper/state/", views.option_paper_state, name="option_paper_state"),
    path("api/options/paper/trade/", views.option_paper_trade, name="option_paper_trade"),
    path("api/options/paper/close/", views.option_paper_close, name="option_paper_close"),
    path("api/options/paper/reset/", views.option_paper_reset, name="option_paper_reset"),
]
