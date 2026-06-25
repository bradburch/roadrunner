# Roadrunner

A multi-user web app that writes the species from your eBird and iNaturalist observations into your overlapping Strava activity descriptions. Users log in with Strava and link an eBird profile ID, an iNaturalist username, or both (iNaturalist reads need no API key).

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

## Roadmap / TODO

- **Deferred re-check for late checklists.** Today, when a Strava activity is
  created/updated the webhook checks for an overlapping eBird checklist *at that
  moment* — so if the checklist hasn't been saved yet, the activity is never
  updated. Add a scheduled job that, for an activity that had **no** matching
  checklist, re-checks **2, 4, and 8 hours** after the activity was updated, and
  fills in the birds if a checklist has since been saved. (Requires persisting
  per-activity sync state and a scheduler — e.g. Vercel Cron draining a small
  queue.)
