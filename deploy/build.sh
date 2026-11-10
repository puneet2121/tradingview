#!/usr/bin/env bash
set -euo pipefail
export DJANGO_SETTINGS_MODULE=trading_dashboard.cloud_settings
python -m pip install -r requirements-cloud.txt
python manage.py check --deploy
python manage.py collectstatic --noinput
