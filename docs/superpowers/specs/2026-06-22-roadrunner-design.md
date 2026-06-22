# Roadrunner — Design

**Date:** 2026-06-22
**Status:** Approved (lean v1)

## Summary

Roadrunner is a multi-user Django web app, deployed on Vercel, that links a
user's Strava account and their eBird profile. When a Strava activity's time
window overlaps an eBird checklist, Roadrunner writes the observed species and
counts into the activity's description.

It is the web evolution of the existing `ebird-strava` CLI script. The script's
core logic (time-overlap matching, species merge, eBird taxonomy lookup,
description building) is sound and is reused; the CLI's `config.ini`-based,
single-user, stateful-filesystem model is replaced because Vercel is serverless
and stateless.

## Decisions (from brainstorming)

- **Multi-user.** Anyone can sign in and link their own Strava + eBird profile.
- **Strava is the login.** "Connect with Strava" both authenticates and creates
  the account (OAuth-as-identity). No passwords. Most common pattern for
  Strava-integrated apps.
- **Shared eBird API token.** The app owner holds one eBird API token in env
  vars; any public eBird profile can be queried with it. Users only enter their
  public eBird profile ID.
- **Both triggers in v1:** a manual "Sync now" button *and* a Strava webhook.
- **Append + idempotent block** when writing descriptions: keep the user's
  existing text, add/replace a delimited bird block. No data loss, no
  duplication on re-sync.
- **Database:** Neon Postgres (provisioned via Vercel), using the **pooled**
  connection string (serverless opens a connection per invocation).

## Architecture

Vercel runs Django as a **WSGI serverless function**. Three consequences shape
the design:

1. No writable disk → tokens live in Neon, not `config.ini`.
2. No background workers → the webhook is processed **inline** in the request.
3. `migrate` cannot reliably run at deploy → run it from a dev machine / CI
   against Neon.

### Project layout

One Django project, **one app** (`core`). The existing modules move into
`core/services/` largely intact, with the `configparser` globals removed and
tokens/profile IDs passed in explicitly:

- `core/services/ebird.py` — eBird REST client + taxonomy lookup (from `ebird.py`).
- `core/services/strava.py` — Strava OAuth + activities client (from `strava.py`).
- `core/services/matching.py` — overlap `compare`, `add_dict` merge,
  description builder, idempotent block (from `utils.py` + `ebird.py`).

Pure functions kept verbatim where possible: `compare`, `add_dict`,
`build_bird_dict`, `create_bird_description`, end-time calculations, `parse`.

### Data model — one model

`Profile` (OneToOne with Django's stock `auth.User`):

| field | purpose |
|-------|---------|
| `strava_athlete_id` | unique; ties Strava identity to the user |
| `access_token` | Strava access token |
| `refresh_token` | Strava refresh token |
| `expires_at` | access token expiry (drives refresh) |
| `ebird_profile_id` | user's public eBird profile ID |

Identity, sessions, login state, and admin reuse `django.contrib.auth`. The
stock `User` is created with `username = strava_athlete_id` and logged in via
`django.contrib.auth.login()` after OAuth. No custom user model.

Tokens are stored as plaintext columns, relying on Neon's encryption-at-rest
and access controls (see Skipped).

### Views / URLs

| route | method | purpose |
|-------|--------|---------|
| `/` | GET | Landing page with "Connect with Strava" |
| `/strava/connect` | GET | Redirect to Strava OAuth (with `state`) |
| `/strava/callback` | GET | Exchange code, upsert User+Profile, `login()`, redirect |
| `/dashboard` | GET | Connection status, eBird-ID form, "Sync now" button |
| `/ebird-profile` | POST | Save/update `ebird_profile_id` |
| `/sync` | POST | Run `process_account` for recent activities |
| `/webhook` | GET | Strava subscription verification (echo `hub.challenge`) |
| `/webhook` | POST | Receive event, process inline |

### Core processing — one function, two callers

```
process_account(profile, activity_ids=None):
    1. ensure fresh Strava token (refresh if expired; persist new tokens)
    2. fetch activities:
         - activity_ids given (webhook)  -> fetch those
         - else (button)                 -> fetch recent N
    3. fetch eBird checklists (with durations) for profile.ebird_profile_id
    4. overlap-match activities <-> checklists  (reuse compare)
    5. merge species per activity               (reuse add_dict, build_bird_dict)
    6. build bird block; replace-or-append the marker block in the description
    7. PUT updated description to Strava
```

The "Sync now" button calls it with no `activity_ids`; the webhook calls it
with the single activity id from the event. Same code path.

### Idempotency without a table

The bird list is wrapped in a marker block in the Strava description:

```
<user's existing description>

<!-- roadrunner -->
Birds seen during activity:
3 American Robin
2 Black-capped Chickadee
<!-- /roadrunner -->
```

Re-sync finds the marker block and **replaces** it (or appends if absent), so
the activity description itself is the source of truth. No `ProcessedActivity`
table is needed for correctness.

## Data flow

- **Connect:** "Connect with Strava" → OAuth (with `state`) → callback stores
  tokens, creates/logs in user → user enters eBird profile ID on the dashboard.
- **Manual sync:** "Sync now" → `process_account(profile)` over recent
  activities.
- **Webhook:** Strava POSTs a new-activity event → `process_account(profile,
  [object_id])` inline → return 200. If processing fails, return non-200 and
  Strava retries.
- **Token refresh:** on demand when expired/401; refreshed tokens persisted to
  Neon.

## Security & error handling

- OAuth `state` parameter (CSRF protection on the callback).
- Webhook protected by Strava's `verify_token` on the GET handshake.
- All secrets in Vercel env vars (never committed).
- Graceful handling of Strava 429 rate limits (shared per-application bucket —
  the constraint that comes with multi-user), eBird/Strava failures, and
  401 → refresh → retry.
- **Leaked-credential cleanup (required):** the current `config.ini` contains
  real-looking live credentials committed to git. `.gitignore` it, rotate the
  Strava client secret and eBird API token, and scrub them from git history.

## Testing

One `test_matching.py` covering the only non-trivial logic:

- overlap match (activity window vs. checklist window),
- species merge across multiple checklists matching one activity,
- description block **replace, not duplicate**, on re-sync.

HTTP clients and OAuth/webhook endpoints get tests only as they earn them.

## Vercel deployment artifacts

- `vercel.json` — route all requests to the WSGI handler.
- `api/index.py` — exposes the Django WSGI application.
- `@vercel/python` runtime.
- `dj-database-url` + Neon **pooled** `DATABASE_URL`.
- Documented `python manage.py migrate` step (run against Neon from dev/CI).
- Env vars: `SECRET_KEY`, `DATABASE_URL`, `EBIRD_API_TOKEN`,
  `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_WEBHOOK_VERIFY_TOKEN`,
  `ALLOWED_HOSTS`.

Frontend is server-rendered Django templates with minimal inline styling. No
static-asset pipeline in v1.

## Skipped (YAGNI) — add when

- **Fernet token encryption at rest** — Neon already encrypts at rest and
  access-controls the DB. Add app-layer crypto when a threat model requires it.
- **WebhookEvent queue + Vercel Cron drain** — webhook is processed inline;
  Strava's hard 2-second rule applies to the verification handshake, not event
  POSTs (it retries failed deliveries). Add a queue when Strava times out or
  rate limits bite.
- **`ProcessedActivity` table + sync-history dashboard** — idempotency lives in
  the description marker. Add the table when you want a visible sync history.
- **Three-app split** — one `core` app. Split when it actually grows.
- **WhiteNoise / static pipeline** — add when there are real static assets.

## Non-goals (v1)

- No scheduled polling cron (webhooks cover real-time; button covers manual).
- No email/password accounts.
- No per-user eBird API tokens.
