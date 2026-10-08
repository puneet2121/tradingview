"""Private ngrok sharing for four users; separate from normal local startup."""

import argparse
import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from trading_dashboard.sharing_config import CONFIG_PATH, ROOT, load_config, normalize_domain, validate_config


def private_write(path, content):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize_django():
    os.environ["DJANGO_SETTINGS_MODULE"] = "trading_dashboard.sharing_settings"
    os.environ["SHARING_SCANNER_ENABLED"] = "false"
    import django

    django.setup()


def setup(args):
    domain = normalize_domain(args.domain)
    if CONFIG_PATH.exists():
        config = load_config()
        if args.users and args.users != config["users"]:
            raise ValueError("Setup preserves existing users. Disable a user locally before changing the access list.")
        config["domain"] = domain
    else:
        config = {
            "domain": domain,
            "secret_key": secrets.token_urlsafe(64),
            "users": args.users or ["trader1", "trader2", "trader3", "trader4"],
        }
    validate_config(config)
    private_write(CONFIG_PATH, json.dumps(config, indent=2) + "\n")
    initialize_django()
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import transaction

    database = Path(settings.DATABASES["default"]["NAME"])
    if database.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = CONFIG_PATH.parent / f"db-before-sharing-{timestamp}.sqlite3"
        backup.touch(mode=0o600, exist_ok=False)
        source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        target = sqlite3.connect(backup)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        print(f"Database backup: {backup}")

    call_command("migrate", interactive=False)
    call_command("collectstatic", interactive=False, verbosity=0)
    call_command("check")
    credentials_path = CONFIG_PATH.parent / "credentials.txt"
    credentials = credentials_path.read_text() if credentials_path.exists() else ""
    User = get_user_model()
    with transaction.atomic():
        for username in config["users"]:
            if User.objects.filter(username=username).exists():
                print(f"Kept existing login: {username}")
                continue
            password = secrets.token_urlsafe(24)
            User.objects.create_user(username=username, password=password)
            credentials += f"Username: {username}\nPassword: {password}\n\n"
            print(f"Created login: {username}")
        if credentials:
            private_write(credentials_path, credentials)
    print(f"Private login details: {credentials_path}")
    print(f"Domain configured: https://{domain}")
    print("Setup complete. No tunnel has been opened.")


def serve(args):
    config = load_config()
    os.environ["DJANGO_SETTINGS_MODULE"] = "trading_dashboard.sharing_settings"
    os.environ["SHARING_SCANNER_ENABLED"] = "true" if args.scanner else "false"
    print(f"Sharing backend: 127.0.0.1:{args.port}; public host: {config['domain']}", flush=True)
    print("Paper scanner enabled: stop all other paper scanner servers first." if args.scanner else
          "Paper scanner disabled here; keep your existing paper scanner server running.", flush=True)
    print("Saved-alert monitoring runs independently (ALERT_SCANNER_ENABLED).", flush=True)
    os.execv(sys.executable, [
        sys.executable, "-m", "uvicorn", "trading_dashboard.asgi:application",
        "--host", "127.0.0.1", "--port", str(args.port), "--workers", "1", "--no-proxy-headers",
    ])


def tunnel(args):
    import requests

    config = load_config()
    ngrok = shutil.which("ngrok")
    if not ngrok:
        raise ValueError("Install ngrok and configure your authtoken locally first.")
    # Refuse to tunnel a normal, unprotected local dashboard by mistake.
    with requests.Session() as session:
        session.trust_env = False
        response = session.get(
            f"http://127.0.0.1:{args.port}/api/config/",
            headers={"Host": config["domain"], "X-Forwarded-Proto": "https"},
            timeout=5, allow_redirects=False,
        )
    if response.status_code != 401 or response.json().get("login_url") != "/accounts/login/":
        raise ValueError("Refusing to expose this port: the protected sharing server is not running there.")
    print(f"Opening https://{config['domain']} with Django login protection.", flush=True)
    os.execv(ngrok, [
        ngrok, "http", f"http://127.0.0.1:{args.port}", "--url", f"https://{config['domain']}",
        "--inspect=false",
    ])


def manage(args):
    load_config()
    os.environ["DJANGO_SETTINGS_MODULE"] = "trading_dashboard.sharing_settings"
    os.environ["SHARING_SCANNER_ENABLED"] = "false"
    if not args.arguments:
        raise ValueError("Provide a management command, e.g. changepassword trader1.")
    raise SystemExit(subprocess.call([sys.executable, str(ROOT / "manage.py"), *args.arguments]))


def main():
    os.chdir(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup", help="Back up the DB, migrate, collect static files, and create logins.")
    setup_parser.add_argument("--domain", required=True)
    setup_parser.add_argument("--users", nargs=4)
    setup_parser.set_defaults(run=setup)
    for name, function in (("serve", serve), ("tunnel", tunnel)):
        command = commands.add_parser(name)
        command.add_argument("--port", type=int, default=8003)
        command.set_defaults(run=function)
        if name == "serve":
            command.add_argument("--scanner", action="store_true", help="Enable only after stopping other scanners.")
    admin = commands.add_parser("manage", help="Run a Django management command with sharing settings.")
    admin.add_argument("arguments", nargs=argparse.REMAINDER)
    admin.set_defaults(run=manage)
    args = parser.parse_args()
    try:
        args.run(args)
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        parser.exit(1, f"Sharing setup failed: {exc}\n")


if __name__ == "__main__":
    main()
