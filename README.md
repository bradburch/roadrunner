# Roadrunner

A multi-user web app that shares the nature you saw on your activities with your Strava followers. It pulls the species you logged in your favorite nature apps — eBird and iNaturalist — into your overlapping Strava activity descriptions. Log in with Strava and link an eBird profile ID, an iNaturalist username, or both (iNaturalist reads need no API key).

## Environment Variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django secret key — use a long random string in production |
| `DEBUG` | Set to `True` for local development; `False` in production |
| `ALLOWED_HOSTS` | Comma-separated list of hosts Django will serve (e.g. `localhost,127.0.0.1` or your deployed domain) |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated list of origins trusted for CSRF (e.g. `https://your-app.vercel.app`) |
| `DATABASE_URL` | Postgres connection string — use Neon's **pooled** connection string for production (serverless opens a new connection per invocation) |
| `EBIRD_API_TOKEN` | eBird API token — request one at https://ebird.org/data/download |
| `STRAVA_CLIENT_ID` | Strava API application client ID |
| `STRAVA_CLIENT_SECRET` | Strava API application client secret |
| `STRAVA_WEBHOOK_VERIFY_TOKEN` | A secret string you choose; used to verify Strava webhook subscriptions |
| `CRON_SECRET` | Secret string used to authenticate Vercel Cron calls to `/cron/rechecks`; Vercel sends it as `Authorization: Bearer <CRON_SECRET>` |

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in your values
python manage.py migrate
python manage.py runserver
```

Run the test suite:

```bash
python manage.py test
```

## Database

Roadrunner uses Postgres via `DATABASE_URL`. The recommended provider is [Neon](https://neon.tech). Use Neon's **pooled** connection string — each serverless invocation opens a fresh connection, so persistent connections are not used (`conn_max_age=0`).

Migrations are run from your dev machine or CI against the Neon `DATABASE_URL`. They are **not** run automatically on Vercel deploy.

```bash
DATABASE_URL=<neon-pooled-url> python manage.py migrate
```

## Vercel Deploy

1. Set all environment variables in the Vercel dashboard (Settings > Environment Variables).
2. Set `ALLOWED_HOSTS` to include your deployed domain (e.g. `your-app.vercel.app`).
3. Set `CSRF_TRUSTED_ORIGINS` to include the full origin (e.g. `https://your-app.vercel.app`).
4. Deploy:

```bash
vercel         # preview
vercel --prod  # production
```

## Strava Webhook

After deploying, register a Strava push subscription pointing at your app's `/webhook` endpoint. The `verify_token` must match `STRAVA_WEBHOOK_VERIFY_TOKEN`.

```bash
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -F client_id=<STRAVA_CLIENT_ID> \
  -F client_secret=<STRAVA_CLIENT_SECRET> \
  -F callback_url=https://<your-app>/webhook \
  -F verify_token=<STRAVA_WEBHOOK_VERIFY_TOKEN>
```

This step is completed after live deploy when your callback URL is reachable.

## Scheduled Re-checks

When a Strava activity is created/updated, the webhook checks for an overlapping
eBird/iNaturalist observation *at that moment*. Because birders often save their
checklists hours later, an activity with no match is queued for re-checks **2, 4,
and 8 hours later**. A scheduler hits `/cron/rechecks` (hourly) and drains due
re-checks; the moment a checklist is found the species are written and the queue
entry is dropped, and entries are discarded after the 8-hour attempt.

`/cron/rechecks` is driven by **Vercel Cron** on the free **Hobby** plan. Hobby
only allows cron expressions that run once per day, so `vercel.json` declares 24
daily entries, one per UTC hour, which together fire hourly. Hobby invokes each
anywhere within its hour — fine here, since the 2/4/8h ladder tolerates lateness.

Setup: set `CRON_SECRET` in the Vercel environment and redeploy (a deployment
captures the secret at build time). Vercel sends it automatically as
`Authorization: Bearer <CRON_SECRET>`; the endpoint denies all calls when
`CRON_SECRET` is unset. Crons run only against the production deployment.

After deploying, confirm the 24 jobs appear under Vercel → Settings → Cron Jobs.

**Backstop:** `.github/workflows/rechecks.yml` still `curl`s the endpoint hourly
from GitHub Actions (it needs the same value as an Actions secret named
`CRON_SECRET`). Draining twice in an hour is harmless. Delete the workflow once
the Vercel crons are seen firing; if they never register, it keeps re-checks
alive. The endpoint is a plain authenticated HTTP GET, so any other scheduler
(cron-job.org, etc.) can hit it too.
