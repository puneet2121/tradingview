# Private Render or Railway Deployment With Supabase

This deployment keeps four Django logins and one shared paper account. It uses
Supabase PostgreSQL for server-side history, strategies, orders, sessions, and
alerts. Chart layouts remain browser-local. Supabase Auth is not used.

## Choose Hosting

For temporary browser-based testing, use **Render Free + your existing Supabase
project**. Render Free sleeps after 15 minutes without inbound traffic and loses
local files on restart. It supports custom domains and managed HTTPS, but has
usage limits. It is not suitable for unattended trading or scheduled alerts.
This configuration therefore leaves all background scanners off.
See [Render Free limitations](https://render.com/docs/free).

Railway is also configured. Its current Free offer starts with $5 credit for
30 days, then $1 monthly credit; that is not a guarantee of enough compute for
this app around the clock. Custom-domain availability depends on the plan.
See [Railway pricing](https://railway.com/pricing).

Supabase has its own storage, connection, inactivity, and backup limits. Check
your project's plan and maintain backups. Hosting does not change market-data
delay, entitlements, or redistribution permissions.

## 1. Prepare Supabase

Use a dedicated project if possible. Back up any existing database before
migrations. In Supabase's SQL Editor, create a new private schema:

```sql
create schema if not exists trading_app;
revoke all on schema trading_app from public, anon, authenticated;
```

Keep `trading_app` **out of the Data API's exposed schemas**, and do not grant
`anon` or `authenticated` access to it. Django applies access control through
its own backend; its user/session tables must not be exposed through Supabase's
public API. Do not reuse an existing schema containing unrelated tables.
See [Supabase schemas](https://supabase.com/docs/guides/database/tables).

Open **Connect**, choose the PostgreSQL URI for **Session pooler, port 5432**,
and supply the database password. URL-encode special characters in that
password. Store the resulting URI only in the hosting service's secret
`DATABASE_URL` variable. Do not paste it into chat, Git, or browser JavaScript.
Do not use a Supabase anon/service-role API key as the database password.

Session pooling provides IPv4 connectivity and preserves the per-connection
private-schema setting. **Transaction pooling on port 6543 is not supported
by this configuration.** The app sets and verifies its schema on each new
connection, requires TLS, and refuses to fall back to SQLite or public tables.
See [Supabase connection modes](https://supabase.com/docs/guides/database/connecting-to-postgres).

## 2. Prepare the Repository and Secrets

Deploy from a private Git repository. Review and include the app source,
migrations, requirements, templates, static source assets, `deploy/`,
`.python-version`, and the appropriate hosting configuration file. Do not
upload `.local-sharing/`, `.env` files, `.venv/`, SQLite files, backups, or logs.
Ignored files are not copied automatically. Review repository history for
previously committed secrets and rotate any that were exposed.

Generate a new cloud Django key privately, for example:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'
```

Store it as `DJANGO_SECRET_KEY` in the hosting dashboard. Keep it stable across
deployments. The variable must be named **`DJANGO_SECRET_KEY`**, not `SECRET_KEY`.
Paste only the generated value, with no surrounding quotes. A short generated
value is not sufficient; the app requires at least 50 characters.
Separately create four distinct passwords in your password manager,
each at least 12 characters. Store them in one secret environment variable,
`CLOUD_INITIAL_PASSWORDS`, as a JSON object. Replace these placeholders:

```json
{"trader1":"<unique-password-1>","trader2":"<unique-password-2>","trader3":"<unique-password-3>","trader4":"<unique-password-4>"}
```

These are **new cloud credentials**, not the passwords in local
`credentials.txt`. Give each person only their own login privately. There is no
public sign-up. All four users can change the shared paper account and strategies.

## 3. Deploy on Render

1. Select **New > Blueprint** and connect the reviewed private repository.
2. Use the repository's `render.yaml`. It creates one Free web service, **not**
   another database or a paid worker.
3. Fill the three secret prompts: `DATABASE_URL`, `DJANGO_SECRET_KEY`, and
   `CLOUD_INITIAL_PASSWORDS`. Keep the other defaults for the first deployment.
4. Deploy. The build installs dependencies, checks security settings, and
   collects static files. Startup applies migrations to the private schema,
   creates missing logins, and runs one Uvicorn worker.
5. Open the service's HTTPS `onrender.com` URL and sign in. The platform hostname
   is allowed automatically. An anonymous `/api/config/` request should return
   401; `/healthz/` returns only `{"status":"ok"}`. Health is a liveness check,
   not confirmation that providers or scanners are running.
6. Once all four accounts exist, remove `CLOUD_INITIAL_PASSWORDS` from Render's
   environment. Future starts preserve passwords, account status, and DB records.

For manual Web Service creation instead of Blueprint, choose Python, Free,
build command `bash deploy/build.sh`, start command `python deploy/start.py`,
and health path `/healthz/`. Add these environment variables:

| Variable | Value |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `trading_dashboard.cloud_settings` |
| `DATABASE_URL` | Secret Supabase Session pooler URI |
| `DJANGO_SECRET_KEY` | Secret random key, at least 50 characters |
| `CLOUD_INITIAL_PASSWORDS` | Secret JSON above; first deployment only |
| `SHARING_USERS` | `trader1,trader2,trader3,trader4` |
| `DATABASE_SCHEMA` | `trading_app` |
| `DATABASE_SSL_REQUIRE` | `true` |
| `CLOUD_SCANNERS_ENABLED` | `false` |
| `ALPACA_DATA_ENABLED` | `false` |
| `ALPACA_MANUAL_PAPER_ENABLED` | `false` |
| `DISCORD_ALERTS_ENABLED` | `false` |

To use your domain, first add it in Render's Custom Domains settings and follow
the DNS records Render gives you. Add `APP_HOSTS=trade.your-domain.com` to the
service environment and redeploy. Multiple hosts are comma-separated, with no
wildcards or ports. Use HTTPS after the certificate is ready. Keep your existing
ngrok/local server configuration separate.

## Railway Alternative

Create a project from the same repository; `railway.json` supplies the build,
start, health-check, and single-replica settings. Set the variables in the table
above before deploying. Generate a public service domain in Networking and
set `APP_HOSTS` to that hostname if `RAILWAY_PUBLIC_DOMAIN` is not yet available
during the first build. No additional Railway database or volume is required.
Keep one replica and check credits/usage before relying on the service.

## Data, Credentials, and Automation

- This is a **fresh cloud database**. Local SQLite trades, saved strategies,
  users, alert rules, and logs are not automatically imported. Plan and verify
  a separate migration before replacing your local installation. Existing
  broker positions remain at Alpaca; this does not import their local tracking
  records. Do not turn on a second bot while the original is running.
- The four users share cloud records with each other, not with the local SQLite
  installation. Browser chart layouts also do not migrate to a new domain.
- No provider keys or Discord webhook are copied from `.local-sharing/`.
  The local Discord fallback is stored in ignored `.local-sharing/discord.json`
  with owner-only permissions, not in source.
- For an optional read-only Alpaca connection, set `ALPACA_DATA_ENABLED=true`,
  `ALPACA_DATA_API_KEY`, and `ALPACA_DATA_SECRET_KEY` privately, using paper keys.
  Choose entitled `ALPACA_STOCK_FEED` and `ALPACA_OPTION_FEED` values. Manual
  paper orders require a separate `ALPACA_MANUAL_PAPER_ENABLED=true` opt-in.
- Always-on automation requires a suitable always-running host, one instance,
  reconciliation of existing positions, and stopping the old scanner first.
  Then `CLOUD_SCANNERS_ENABLED=true` **and** each desired worker's own flag are
  required: `PAPER_SCANNER_ENABLED`, `ALERT_SCANNER_ENABLED`,
  `SMART_MONEY_WORKER_ENABLED`, or `ALPACA_AUTO_PAPER_ENABLED`. Database leases
  cannot prevent duplicate bots running against different databases.
- Discord additionally needs secret `DISCORD_WEBHOOK_URL` and
  `DISCORD_ALERTS_ENABLED=true`. Smart Money collection and data-sharing
  permissions remain opt-in; see [Smart Money setup](smart-money.md).
- Do not use `share.py setup` on the cloud service. It configures local SQLite
  and ngrok, not cloud PostgreSQL. Use the cloud settings for maintenance.
- Back up before schema changes. A hosting code rollback does not roll back
  database migrations. Keep one instance: channel delivery and provider
  subscriptions currently rely on a single ASGI process.

## Troubleshooting

- **Build fails in `cloud_config.identity`:** Django has started, but an identity
  environment variable is missing or invalid. This is not a pip or database
  connection error. In Render's service Environment page, set `DJANGO_SECRET_KEY`
  to a random secret of at least 50 characters (see the generation command above).
  Set `SHARING_USERS=trader1,trader2,trader3,trader4`, or remove that variable to
  use those defaults; do not leave it blank. Set `APP_HOSTS` to the exact public
  hostname, such as `your-service.onrender.com`, or remove it to use Render's
  automatic hostname. Do not use wildcards, ports, or quotes. Choose **Save,
  rebuild, and deploy**. Updated validation names the failing variable without
  printing its value; older deployed commits show the combined error message.
- **Startup fails:** check that the private schema exists, the connection URI
  uses Session pooler port 5432 with a URL-encoded password, Supabase is active,
  and all required environment variables are present. Errors deliberately
  avoid printing the connection string or initial passwords.
- **Login fails:** use the new cloud credentials on the HTTPS hosting domain.
  Five failed attempts lock that username for 15 minutes. Redeploying does not
  reset an existing password. Password resets require a trusted management
  environment with cloud settings and the same database, using Django's
  `changepassword` command; never pass passwords as command-line arguments.
- **Host/CSRF error:** configure the exact custom domain in `APP_HOSTS`, restart,
  then reload the login page over HTTPS. Do not disable CSRF or secure cookies.
- **Django security warnings W005/W021:** subdomain-wide HSTS and browser
  preload are intentionally off. HTTPS and secure cookies remain required for
  this app without imposing a policy on every other subdomain you own.
- **Missing local history:** expected on a new database. Do not upload SQLite
  to an ephemeral host to work around this; arrange a database migration.
- **No automatic trades/alerts:** expected with the safe defaults. Free hosting
  is not an always-on scanner. Do not treat a sleeping process as monitoring
  option stops or take-profit rules.
