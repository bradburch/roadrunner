# Roadrunner Django + Vercel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the `ebird-strava` CLI as a multi-user Django web app ("Roadrunner") deployable on Vercel, where users log in with Strava, link an eBird profile, and have matching eBird checklists written into their Strava activity descriptions — via a manual button and a Strava webhook.

**Architecture:** One Django project (`roadrunner`) with one app (`core`). The CLI's pure logic (time-overlap matching, species merge, eBird taxonomy lookup, description building) is ported into `core/services/`. State moves from `config.ini` to a single `Profile` model in Neon Postgres. Vercel runs Django as a WSGI serverless function; the webhook is processed inline.

**Tech Stack:** Python 3.12, Django 5.1, Neon Postgres (via `dj-database-url` + `psycopg`), `requests`, Vercel `@vercel/python`. Tests use Django's built-in test runner + `unittest.mock` (no extra test deps).

## Global Constraints

- Python `3.12`. Django `>=5.1,<6`.
- Dependencies limited to: `Django`, `dj-database-url`, `psycopg[binary]`, `requests`. Add nothing else without explicit need.
- App name in all user-facing copy: **Roadrunner**.
- Secrets only from environment variables — never committed. Required env vars: `SECRET_KEY`, `DATABASE_URL`, `EBIRD_API_TOKEN`, `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET`, `STRAVA_WEBHOOK_VERIFY_TOKEN`, `ALLOWED_HOSTS`, `DEBUG`.
- eBird token is a single shared app token (from `EBIRD_API_TOKEN`); users supply only a public eBird profile ID.
- Strava description writes use the idempotent marker block `<!-- roadrunner -->` … `<!-- /roadrunner -->` (append if absent, replace if present — never duplicate, never clobber user text).
- Tests run with `python manage.py test`. Each task commits only after its tests pass.
- Datetimes are timezone-aware UTC throughout (matches the CLI's existing convention).

---

## File Structure

```
manage.py                         # Django entry point
roadrunner/                       # project package
  __init__.py
  settings.py                     # env-driven settings, Neon DB, CSRF origins
  urls.py                         # include core.urls
  wsgi.py                         # WSGI application
core/
  __init__.py
  apps.py
  admin.py                        # register Profile (local inspection)
  models.py                       # Profile (the one model)
  urls.py                         # routes
  views.py                        # landing, oauth, dashboard, sync, webhook
  migrations/
  services/
    __init__.py
    timespan.py                   # IdDates dataclass (ported from id_dates.py)
    matching.py                   # compare, add_dict, build_block, upsert_block, create_bird_description
    ebird.py                      # eBird REST client + taxonomy (ported from ebird.py)
    strava.py                     # Strava OAuth + activities client (ported from strava.py)
    sync.py                       # process_account orchestration
  templates/core/
    base.html
    landing.html
    dashboard.html
  tests/
    __init__.py
    test_matching.py
    test_ebird.py
    test_strava.py
    test_sync.py
    test_views.py
api/
  index.py                        # Vercel WSGI entrypoint (exposes `app`)
vercel.json
requirements.txt
.env.example
.gitignore                        # adds config.ini, .env
```

**Removed at end (Task 11):** root `main.py`, `ebird.py`, `strava.py`, `utils.py`, `id_dates.py`, `config.ini`, `Dockerfile`, `.dockerignore` — their logic is ported into `core/services/`.

---

### Task 1: Project scaffold, settings, and credential cleanup

**Files:**
- Create: `requirements.txt`, `manage.py`, `roadrunner/__init__.py`, `roadrunner/settings.py`, `roadrunner/urls.py`, `roadrunner/wsgi.py`, `core/__init__.py`, `core/apps.py`, `core/urls.py`, `core/views.py`, `.env.example`
- Modify: `.gitignore`
- Remove from git tracking: `config.ini`

**Interfaces:**
- Produces: a runnable Django project with `core` app installed; `roadrunner.wsgi.application`; env-driven settings.

- [ ] **Step 1: Write `requirements.txt`**

```
Django>=5.1,<6
dj-database-url>=2.2,<3
psycopg[binary]>=3.2,<4
requests>=2.32,<3
```

- [ ] **Step 2: Install deps into a venv**

Run:
```bash
python3.12 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
```
Expected: installs succeed.

- [ ] **Step 3: Scaffold the project**

Run:
```bash
django-admin startproject roadrunner . && python manage.py startapp core
```
Expected: `manage.py`, `roadrunner/`, `core/` created.

- [ ] **Step 4: Replace `roadrunner/settings.py` with env-driven settings**

```python
import os
from pathlib import Path
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-key-change-me")
DEBUG = os.environ.get("DEBUG", "False") == "True"
ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "roadrunner.urls"
WSGI_APPLICATION = "roadrunner.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
        conn_max_age=0,  # serverless: do not persist connections
    )
}

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True

# App-specific config (read by core.services)
EBIRD_API_TOKEN = os.environ.get("EBIRD_API_TOKEN", "")
STRAVA_CLIENT_ID = os.environ.get("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")
STRAVA_WEBHOOK_VERIFY_TOKEN = os.environ.get("STRAVA_WEBHOOK_VERIFY_TOKEN", "")
```

`ponytail:` `conn_max_age=0` because Vercel opens a connection per invocation; persistent pooling lives in Neon's pooled URL, not in Django.

- [ ] **Step 5: Set `roadrunner/urls.py` to include core + admin**

```python
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
]
```

- [ ] **Step 6: Create a placeholder `core/urls.py` and `core/views.py`**

`core/urls.py`:
```python
from django.urls import path
from . import views

app_name = "core"
urlpatterns = [
    path("", views.landing, name="landing"),
]
```

`core/views.py`:
```python
from django.http import HttpResponse


def landing(request):
    return HttpResponse("Roadrunner")
```

- [ ] **Step 7: Write `.env.example`**

```
SECRET_KEY=
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=
DATABASE_URL=
EBIRD_API_TOKEN=
STRAVA_CLIENT_ID=
STRAVA_CLIENT_SECRET=
STRAVA_WEBHOOK_VERIFY_TOKEN=
```

- [ ] **Step 8: Update `.gitignore` and untrack the leaked credentials**

Append to `.gitignore`:
```
.env
config.ini
db.sqlite3
.venv/
__pycache__/
*.pyc
```

Run:
```bash
git rm --cached config.ini
```
Expected: `config.ini` removed from the index (kept on disk).

- [ ] **Step 9: Verify the project boots**

Run:
```bash
python manage.py check
```
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: scaffold Roadrunner Django project + env settings; untrack config.ini"
```

> **Manual security step (do outside this plan, now):** rotate the Strava client secret and eBird API token that were committed in `config.ini`, since they remain in git history. Optional history scrub is Task 12.

---

### Task 2: Profile model

**Files:**
- Modify: `core/models.py`, `core/admin.py`
- Create: `core/migrations/0001_initial.py` (via makemigrations)
- Test: `core/tests/__init__.py`, `core/tests/test_models.py`

**Interfaces:**
- Produces: `core.models.Profile` with fields `user` (OneToOne `auth.User`), `strava_athlete_id` (BigInteger, unique), `access_token`, `refresh_token`, `expires_at` (DateTime), `ebird_profile_id` (Char, blank). Helper `Profile.token_expired() -> bool`.

- [ ] **Step 1: Write the failing test**

`core/tests/__init__.py`: empty file.

`core/tests/test_models.py`:
```python
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from core.models import Profile


class ProfileTests(TestCase):
    def _profile(self, **kw):
        user = User.objects.create(username=str(kw.get("strava_athlete_id", 1)))
        defaults = dict(
            user=user, strava_athlete_id=1, access_token="a",
            refresh_token="r", expires_at=timezone.now() + timedelta(hours=1),
        )
        defaults.update(kw)
        return Profile.objects.create(**defaults)

    def test_token_not_expired_when_future(self):
        self.assertFalse(self._profile().token_expired())

    def test_token_expired_when_past(self):
        p = self._profile(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertTrue(p.token_expired())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_models -v2`
Expected: FAIL — `ImportError`/`Profile` has no field, no table.

- [ ] **Step 3: Write the model**

`core/models.py`:
```python
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    strava_athlete_id = models.BigIntegerField(unique=True)
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    ebird_profile_id = models.CharField(max_length=64, blank=True)

    def token_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def __str__(self) -> str:
        return f"Profile(athlete={self.strava_athlete_id})"
```

`core/admin.py`:
```python
from django.contrib import admin
from .models import Profile

admin.site.register(Profile)
```

- [ ] **Step 4: Make and run migrations, then run the test**

Run:
```bash
python manage.py makemigrations core
python manage.py test core.tests.test_models -v2
```
Expected: migration `0001_initial` created; tests PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add Profile model with token_expired helper"
```

---

### Task 3: Matching service (ported pure logic + idempotent block)

**Files:**
- Create: `core/services/__init__.py`, `core/services/timespan.py`, `core/services/matching.py`
- Test: `core/tests/test_matching.py`

**Interfaces:**
- Produces:
  - `timespan.IdDates(identifier, start_date, end_date=None, obs=None)` dataclass.
  - `matching.compare(a: IdDates, b: IdDates) -> bool` — true if the two windows overlap.
  - `matching.add_dict(current: dict, new: dict) -> dict` — merge species counts (numeric add; `"X"` if non-numeric).
  - `matching.create_bird_description(species_num: dict) -> str` — `"{count} {name}\n"` lines.
  - `matching.upsert_block(description: str | None, bird_list: str) -> str` — insert/replace the marker block.

- [ ] **Step 1: Write the failing test**

`core/tests/test_matching.py`:
```python
from datetime import datetime, timedelta, timezone
from django.test import SimpleTestCase
from core.services.timespan import IdDates
from core.services import matching


def _iv(start_h, end_h):
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return IdDates("x", base + timedelta(hours=start_h), base + timedelta(hours=end_h))


class CompareTests(SimpleTestCase):
    def test_overlapping_windows(self):
        self.assertTrue(matching.compare(_iv(0, 2), _iv(1, 3)))

    def test_disjoint_windows(self):
        self.assertFalse(matching.compare(_iv(0, 1), _iv(2, 3)))


class AddDictTests(SimpleTestCase):
    def test_numeric_counts_sum(self):
        self.assertEqual(matching.add_dict({"Robin": "3"}, {"Robin": "2"}), {"Robin": "5"})

    def test_non_numeric_becomes_X(self):
        self.assertEqual(matching.add_dict({"Robin": "X"}, {"Robin": "2"}), {"Robin": "X"})

    def test_new_species_added(self):
        self.assertEqual(
            matching.add_dict({"Robin": "1"}, {"Jay": "2"}), {"Robin": "1", "Jay": "2"}
        )


class DescriptionTests(SimpleTestCase):
    def test_description_lines(self):
        self.assertEqual(matching.create_bird_description({"Robin": "3"}), "3 Robin\n")


class UpsertBlockTests(SimpleTestCase):
    def test_empty_description_gets_block_only(self):
        out = matching.upsert_block("", "3 Robin\n")
        self.assertTrue(out.startswith("<!-- roadrunner -->"))
        self.assertIn("3 Robin", out)

    def test_existing_text_is_preserved_and_block_appended(self):
        out = matching.upsert_block("My ride.", "3 Robin\n")
        self.assertTrue(out.startswith("My ride."))
        self.assertIn("<!-- roadrunner -->", out)

    def test_resync_replaces_block_not_duplicates(self):
        first = matching.upsert_block("My ride.", "3 Robin\n")
        second = matching.upsert_block(first, "5 Robin\n")
        self.assertEqual(second.count("<!-- roadrunner -->"), 1)
        self.assertIn("5 Robin", second)
        self.assertNotIn("3 Robin", second)

    def test_idempotent_same_input(self):
        once = matching.upsert_block("My ride.", "3 Robin\n")
        twice = matching.upsert_block(once, "3 Robin\n")
        self.assertEqual(once, twice)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_matching -v2`
Expected: FAIL — modules not found.

- [ ] **Step 3: Implement `timespan.py`**

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass
class IdDates:
    identifier: str
    start_date: datetime
    end_date: datetime | None = None
    obs: list | None = None
```

- [ ] **Step 4: Implement `matching.py`**

```python
import re
from .timespan import IdDates

BLOCK_START = "<!-- roadrunner -->"
BLOCK_END = "<!-- /roadrunner -->"
_BLOCK_RE = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.DOTALL)


def compare(a: IdDates, b: IdDates) -> bool:
    latest_start = max(a.start_date, b.start_date)
    earliest_end = min(a.end_date, b.end_date)
    return earliest_end > latest_start


def add_dict(current: dict, new: dict) -> dict:
    merged = current.copy()
    for k, v in new.items():
        if k in merged:
            if v.isnumeric() and merged[k].isnumeric():
                merged[k] = str(int(v) + int(merged[k]))
            else:
                merged[k] = "X"
        else:
            merged[k] = v
    return merged


def create_bird_description(species_num: dict) -> str:
    return "".join(f"{value} {key}\n" for key, value in species_num.items())


def upsert_block(description: str | None, bird_list: str) -> str:
    description = description or ""
    block = f"{BLOCK_START}\nBirds seen during activity:\n{bird_list.rstrip()}\n{BLOCK_END}"
    if _BLOCK_RE.search(description):
        return _BLOCK_RE.sub(block, description)
    if description.strip():
        return f"{description.rstrip()}\n\n{block}"
    return block
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test core.tests.test_matching -v2`
Expected: PASS (all cases).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: port matching/merge logic + idempotent description block"
```

---

### Task 4: eBird service

**Files:**
- Create: `core/services/ebird.py`
- Test: `core/tests/test_ebird.py`

**Interfaces:**
- Consumes: `settings.EBIRD_API_TOKEN`, `timespan.IdDates`.
- Produces:
  - `ebird.get_recent_checklists(profile_id: str) -> list[IdDates]`
  - `ebird.get_dates_observation(id_date: IdDates) -> tuple[datetime | None, list | None]` (None,None if checklist has no `durationHrs`)
  - `ebird.build_bird_dict(observation: list[dict]) -> dict` — `{common_name: count_str}` via taxonomy.

- [ ] **Step 1: Write the failing test**

`core/tests/test_ebird.py`:
```python
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase
from core.services import ebird
from core.services.timespan import IdDates


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    m.status_code = 200
    return m


class EbirdTests(SimpleTestCase):
    @patch("core.services.ebird.requests.request")
    def test_get_recent_checklists_parses_ids_and_dates(self, req):
        req.return_value = _resp([
            {"subId": "S1", "isoObsDate": "2026-06-01 07:00"},
        ])
        result = ebird.get_recent_checklists("PROF")
        self.assertEqual(result[0].identifier, "S1")
        self.assertEqual(result[0].start_date.tzinfo, timezone.utc)

    @patch("core.services.ebird.requests.request")
    def test_get_dates_observation_returns_none_without_duration(self, req):
        req.return_value = _resp({"obs": []})  # no durationHrs
        idd = IdDates("S1", datetime(2026, 6, 1, 7, tzinfo=timezone.utc))
        self.assertEqual(ebird.get_dates_observation(idd), (None, None))

    @patch("core.services.ebird.requests.request")
    def test_get_dates_observation_computes_end(self, req):
        req.return_value = _resp({"durationHrs": 1.0, "obs": [{"x": 1}]})
        start = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
        end, obs = ebird.get_dates_observation(IdDates("S1", start))
        self.assertEqual((end - start).total_seconds(), 3600)
        self.assertEqual(obs, [{"x": 1}])

    @patch("core.services.ebird.requests.get")
    def test_build_bird_dict_maps_codes_to_names(self, get):
        get.return_value = _resp([{"speciesCode": "amerob", "comName": "American Robin"}])
        obs = [{"speciesCode": "amerob", "howManyStr": "3"}]
        self.assertEqual(ebird.build_bird_dict(obs), {"American Robin": "3"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_ebird -v2`
Expected: FAIL — `core.services.ebird` not found.

- [ ] **Step 3: Implement `ebird.py`**

```python
from datetime import datetime, timezone, timedelta
import requests
from django.conf import settings
from .timespan import IdDates

_API = "https://api.ebird.org/v2/"


def _headers() -> dict:
    return {"X-eBirdApiToken": settings.EBIRD_API_TOKEN}


def get_recent_checklists(profile_id: str) -> list[IdDates]:
    url = "https://ebird.org/prof/lists"
    resp = requests.request(
        "GET", url, headers=_headers(),
        params={"r": "world", "username": profile_id}, timeout=30,
    )
    out = []
    for cl in resp.json():
        start = datetime.fromisoformat(cl["isoObsDate"]).replace(tzinfo=timezone.utc)
        out.append(IdDates(cl["subId"], start))
    return out


def get_dates_observation(id_date: IdDates) -> tuple:
    observation = _get_observation(id_date.identifier)
    if "durationHrs" not in observation:
        return (None, None)
    end = id_date.start_date + timedelta(hours=observation["durationHrs"])
    return (end, observation["obs"])


def build_bird_dict(observation: list[dict]) -> dict:
    code_num = {o["speciesCode"]: o["howManyStr"] for o in observation}
    taxonomy = _get_taxonomy(list(code_num))
    code_name = {t["speciesCode"]: t["comName"] for t in taxonomy}
    return {code_name[code]: num for code, num in code_num.items()}


def _get_observation(sub_id: str) -> dict:
    url = f"{_API}product/checklist/view/{sub_id}"
    return requests.request("GET", url, headers=_headers(), timeout=30).json()


def _get_taxonomy(codes: list[str]) -> list:
    params = [("species", c) for c in codes] + [("fmt", "json")]
    url = f"{_API}ref/taxonomy/ebird"
    return requests.get(url, params=params, headers=_headers(), timeout=30).json()
```

`ponytail:` reuses the CLI's exact request shapes; `requests.request`/`requests.get` split mirrors the original so taxonomy multi-param `species` query keeps working.

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_ebird -v2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: port eBird service (checklists, durations, taxonomy)"
```

---

### Task 5: Strava service

**Files:**
- Create: `core/services/strava.py`
- Test: `core/tests/test_strava.py`

**Interfaces:**
- Consumes: `settings.STRAVA_CLIENT_ID`, `settings.STRAVA_CLIENT_SECRET`, `timespan.IdDates`.
- Produces:
  - `strava.exchange_code(code: str) -> dict` — OAuth token exchange; returns Strava token JSON (`access_token`, `refresh_token`, `expires_at`, `athlete`).
  - `strava.refresh_token(refresh_token: str) -> dict` — returns new token JSON.
  - `strava.get_recent_activities(access_token: str, per_page: int = 5) -> list[IdDates]`
  - `strava.get_activity(access_token: str, activity_id: int) -> IdDates`
  - `strava.update_description(access_token: str, activity_id: int, description: str) -> int` — returns HTTP status code.

- [ ] **Step 1: Write the failing test**

`core/tests/test_strava.py`:
```python
from datetime import timezone
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase
from core.services import strava


def _resp(json_data, status=200):
    m = MagicMock()
    m.json.return_value = json_data
    m.status_code = status
    return m


class StravaTests(SimpleTestCase):
    @patch("core.services.strava.requests.post")
    def test_exchange_code_posts_and_returns_json(self, post):
        post.return_value = _resp({"access_token": "a", "refresh_token": "r", "expires_at": 1})
        out = strava.exchange_code("CODE")
        self.assertEqual(out["access_token"], "a")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "authorization_code")

    @patch("core.services.strava.requests.post")
    def test_refresh_token_uses_refresh_grant(self, post):
        post.return_value = _resp({"access_token": "a2", "refresh_token": "r2", "expires_at": 2})
        out = strava.refresh_token("r")
        self.assertEqual(out["access_token"], "a2")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")

    @patch("core.services.strava.requests.get")
    def test_get_recent_activities_parses_windows(self, get):
        get.return_value = _resp([
            {"id": 99, "start_date_local": "2026-06-01T07:00:00", "elapsed_time": 3600},
        ])
        acts = strava.get_recent_activities("a")
        self.assertEqual(acts[0].identifier, 99)
        self.assertEqual((acts[0].end_date - acts[0].start_date).total_seconds(), 3600)
        self.assertEqual(acts[0].start_date.tzinfo, timezone.utc)

    @patch("core.services.strava.requests.get")
    def test_get_activity_single(self, get):
        get.return_value = _resp({"id": 5, "start_date_local": "2026-06-01T07:00:00", "elapsed_time": 60})
        act = strava.get_activity("a", 5)
        self.assertEqual(act.identifier, 5)

    @patch("core.services.strava.requests.put")
    def test_update_description_returns_status(self, put):
        put.return_value = _resp({}, status=200)
        self.assertEqual(strava.update_description("a", 5, "desc"), 200)
        self.assertEqual(put.call_args.kwargs["data"]["description"], "desc")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_strava -v2`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `strava.py`**

```python
from datetime import datetime, timezone, timedelta
import requests
from django.conf import settings
from .timespan import IdDates

_API = "https://www.strava.com/api/v3/"
_OAUTH = "https://www.strava.com/oauth/token"


def exchange_code(code: str) -> dict:
    return requests.post(_OAUTH, data={
        "client_id": settings.STRAVA_CLIENT_ID,
        "client_secret": settings.STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
    }, timeout=30).json()


def refresh_token(refresh: str) -> dict:
    return requests.post(_OAUTH, data={
        "client_id": settings.STRAVA_CLIENT_ID,
        "client_secret": settings.STRAVA_CLIENT_SECRET,
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }, timeout=30).json()


def _to_id_dates(activity: dict) -> IdDates:
    start = datetime.fromisoformat(activity["start_date_local"]).replace(tzinfo=timezone.utc)
    end = start + timedelta(seconds=activity["elapsed_time"])
    return IdDates(activity["id"], start, end)


def get_recent_activities(access_token: str, per_page: int = 5) -> list[IdDates]:
    resp = requests.get(
        f"{_API}activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": per_page, "page": 1}, timeout=30,
    )
    return [_to_id_dates(a) for a in resp.json()]


def get_activity(access_token: str, activity_id: int) -> IdDates:
    resp = requests.get(
        f"{_API}activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"}, timeout=30,
    )
    return _to_id_dates(resp.json())


def update_description(access_token: str, activity_id: int, description: str) -> int:
    resp = requests.put(
        f"{_API}activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"description": description}, timeout=30,
    )
    return resp.status_code
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python manage.py test core.tests.test_strava -v2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: port Strava service (OAuth, activities, description update)"
```

---

### Task 6: process_account orchestration

**Files:**
- Create: `core/services/sync.py`
- Test: `core/tests/test_sync.py`

**Interfaces:**
- Consumes: `Profile`, `ebird.*`, `strava.*`, `matching.*`.
- Produces:
  - `sync.ensure_fresh_token(profile: Profile) -> str` — refreshes + persists if expired; returns a valid access token.
  - `sync.process_account(profile: Profile, activity_ids: list[int] | None = None) -> list[int]` — returns the list of activity IDs whose descriptions were updated (HTTP 200).

- [ ] **Step 1: Write the failing test**

`core/tests/test_sync.py`:
```python
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone as djtz
from core.models import Profile
from core.services import sync
from core.services.timespan import IdDates


def _profile(expired=False):
    user = User.objects.create(username="42")
    return Profile.objects.create(
        user=user, strava_athlete_id=42, access_token="old", refresh_token="r",
        expires_at=djtz.now() + (timedelta(hours=-1) if expired else timedelta(hours=1)),
        ebird_profile_id="PROF",
    )


class EnsureFreshTokenTests(TestCase):
    @patch("core.services.sync.strava.refresh_token")
    def test_refreshes_and_persists_when_expired(self, refresh):
        refresh.return_value = {"access_token": "new", "refresh_token": "r2",
                                "expires_at": int(djtz.now().timestamp()) + 3600}
        p = _profile(expired=True)
        token = sync.ensure_fresh_token(p)
        self.assertEqual(token, "new")
        p.refresh_from_db()
        self.assertEqual(p.access_token, "new")
        self.assertEqual(p.refresh_token, "r2")

    @patch("core.services.sync.strava.refresh_token")
    def test_no_refresh_when_valid(self, refresh):
        token = sync.ensure_fresh_token(_profile(expired=False))
        self.assertEqual(token, "old")
        refresh.assert_not_called()


class ProcessAccountTests(TestCase):
    def _windows(self):
        base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
        activity = IdDates(99, base, base + timedelta(hours=1))
        checklist = IdDates("S1", base + timedelta(minutes=10))
        return activity, checklist

    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.ebird.build_bird_dict", return_value={"Robin": "3"})
    @patch("core.services.sync.ebird.get_dates_observation")
    @patch("core.services.sync.ebird.get_recent_checklists")
    @patch("core.services.sync.strava.get_recent_activities")
    def test_match_updates_activity(self, acts, lists, dates, build, update):
        activity, checklist = self._windows()
        acts.return_value = [activity]
        lists.return_value = [checklist]
        dates.return_value = (checklist.start_date + timedelta(hours=1), [{"speciesCode": "amerob", "howManyStr": "3"}])
        updated = sync.process_account(_profile())
        self.assertEqual(updated, [99])
        # description written contains the marker block + bird line
        desc = update.call_args.kwargs.get("description") or update.call_args.args[2]
        self.assertIn("3 Robin", desc)
        self.assertIn("<!-- roadrunner -->", desc)

    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.ebird.get_dates_observation")
    @patch("core.services.sync.ebird.get_recent_checklists", return_value=[])
    @patch("core.services.sync.strava.get_recent_activities")
    def test_no_match_updates_nothing(self, acts, lists, dates, update):
        activity, _ = self._windows()
        acts.return_value = [activity]
        self.assertEqual(sync.process_account(_profile()), [])
        update.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_sync -v2`
Expected: FAIL — `core.services.sync` not found.

- [ ] **Step 3: Implement `sync.py`**

```python
from datetime import datetime, timezone
from django.utils import timezone as djtz
from ..models import Profile
from . import ebird, strava, matching


def ensure_fresh_token(profile: Profile) -> str:
    if not profile.token_expired():
        return profile.access_token
    data = strava.refresh_token(profile.refresh_token)
    profile.access_token = data["access_token"]
    profile.refresh_token = data["refresh_token"]
    profile.expires_at = datetime.fromtimestamp(data["expires_at"], tz=timezone.utc)
    profile.save(update_fields=["access_token", "refresh_token", "expires_at"])
    return profile.access_token


def process_account(profile: Profile, activity_ids: list[int] | None = None) -> list[int]:
    access = ensure_fresh_token(profile)

    if activity_ids:
        activities = [strava.get_activity(access, i) for i in activity_ids]
    else:
        activities = strava.get_recent_activities(access)

    checklists = ebird.get_recent_checklists(profile.ebird_profile_id)
    activity_species: dict[int, dict] = {}

    for checklist in checklists:
        end, obs = ebird.get_dates_observation(checklist)
        if end is None:
            continue
        checklist.end_date, checklist.obs = end, obs
        for activity in activities:
            if matching.compare(activity, checklist):
                bird_dict = ebird.build_bird_dict(checklist.obs)
                existing = activity_species.get(activity.identifier)
                activity_species[activity.identifier] = (
                    matching.add_dict(existing, bird_dict) if existing else bird_dict
                )

    updated = []
    for activity_id, species in activity_species.items():
        bird_list = matching.create_bird_description(species)
        # Read current description so we preserve the user's text + idempotent block.
        current = strava.get_activity_raw_description(access, activity_id)
        description = matching.upsert_block(current, bird_list)
        if strava.update_description(access, activity_id, description) == 200:
            updated.append(activity_id)
    return updated
```

> NOTE: this introduces `strava.get_activity_raw_description`. Add it in the next step (small addition to the Strava service), keeping `process_account` honest about preserving existing text.

- [ ] **Step 4: Add `get_activity_raw_description` to `core/services/strava.py`**

Append:
```python
def get_activity_raw_description(access_token: str, activity_id: int) -> str:
    resp = requests.get(
        f"{_API}activities/{activity_id}",
        headers={"Authorization": f"Bearer {access_token}"}, timeout=30,
    )
    return resp.json().get("description") or ""
```

Add to `core/tests/test_strava.py`:
```python
    @patch("core.services.strava.requests.get")
    def test_get_activity_raw_description(self, get):
        get.return_value = _resp({"description": "hi"})
        self.assertEqual(strava.get_activity_raw_description("a", 5), "hi")
```

And patch it in the sync match test so it returns existing text:
```python
    # add this decorator to test_match_updates_activity (outermost) and param:
    @patch("core.services.sync.strava.get_activity_raw_description", return_value="My ride.")
```
(Insert the decorator directly above `def test_match_updates_activity`, and add a leading `desc_get` parameter to the method signature to match the new outermost patch.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_sync core.tests.test_strava -v2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add process_account orchestration + token refresh"
```

---

### Task 7: OAuth views (connect + callback) and base templates

**Files:**
- Modify: `core/views.py`, `core/urls.py`
- Create: `core/templates/core/base.html`, `core/templates/core/landing.html`
- Test: `core/tests/test_views.py`

**Interfaces:**
- Consumes: `strava.exchange_code`, `Profile`, Django `auth.login`.
- Produces routes: `core:connect` (`/strava/connect`), `core:callback` (`/strava/callback`), updated `core:landing`.

- [ ] **Step 1: Write the failing test**

`core/tests/test_views.py`:
```python
from datetime import datetime, timezone
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from core.models import Profile


class OAuthViewTests(TestCase):
    def test_connect_redirects_to_strava(self):
        resp = self.client.get(reverse("core:connect"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("strava.com/oauth/authorize", resp["Location"])
        self.assertIn("state=", resp["Location"])

    @patch("core.views.strava.exchange_code")
    def test_callback_creates_profile_and_logs_in(self, exchange):
        exchange.return_value = {
            "access_token": "a", "refresh_token": "r",
            "expires_at": int(datetime.now(tz=timezone.utc).timestamp()) + 3600,
            "athlete": {"id": 7, "firstname": "Wile", "lastname": "Coyote"},
        }
        # seed state into the session the way connect would
        session = self.client.session
        session["oauth_state"] = "xyz"
        session.save()
        resp = self.client.get(reverse("core:callback"), {"code": "C", "state": "xyz"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Profile.objects.filter(strava_athlete_id=7).exists())
        self.assertIn("_auth_user_id", self.client.session)

    def test_callback_rejects_bad_state(self):
        session = self.client.session
        session["oauth_state"] = "right"
        session.save()
        resp = self.client.get(reverse("core:callback"), {"code": "C", "state": "wrong"})
        self.assertEqual(resp.status_code, 400)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_views -v2`
Expected: FAIL — routes `core:connect`/`core:callback` missing.

- [ ] **Step 3: Implement the views**

Replace `core/views.py`:
```python
import secrets
from datetime import datetime, timezone
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from urllib.parse import urlencode
from .models import Profile
from .services import strava

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"


def landing(request):
    return render(request, "core/landing.html")


def connect(request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "redirect_uri": request.build_absolute_uri("/strava/callback"),
        "response_type": "code",
        "scope": "activity:read_all,activity:write",
        "state": state,
    }
    return redirect(f"{STRAVA_AUTH_URL}?{urlencode(params)}")


def callback(request):
    if request.GET.get("state") != request.session.get("oauth_state"):
        return HttpResponseBadRequest("Invalid OAuth state")
    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing code")

    data = strava.exchange_code(code)
    athlete = data["athlete"]
    user, _ = User.objects.get_or_create(username=str(athlete["id"]))
    Profile.objects.update_or_create(
        strava_athlete_id=athlete["id"],
        defaults={
            "user": user,
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": datetime.fromtimestamp(data["expires_at"], tz=timezone.utc),
        },
    )
    login(request, user)
    return redirect("core:dashboard")
```

- [ ] **Step 4: Add routes and templates**

`core/urls.py`:
```python
from django.urls import path
from . import views

app_name = "core"
urlpatterns = [
    path("", views.landing, name="landing"),
    path("strava/connect", views.connect, name="connect"),
    path("strava/callback", views.callback, name="callback"),
]
```

`core/templates/core/base.html`:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Roadrunner</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 3rem auto; padding: 0 1rem; }
    .btn { display: inline-block; background: #fc4c02; color: #fff; padding: .6rem 1rem;
           border-radius: 6px; text-decoration: none; border: 0; cursor: pointer; font-size: 1rem; }
    pre { background: #f4f4f4; padding: 1rem; border-radius: 6px; white-space: pre-wrap; }
    .msg { padding: .5rem 1rem; background: #eef; border-radius: 6px; }
  </style>
</head>
<body>
  <h1>🦅 Roadrunner</h1>
  {% if messages %}{% for m in messages %}<p class="msg">{{ m }}</p>{% endfor %}{% endif %}
  {% block content %}{% endblock %}
</body>
</html>
```

`core/templates/core/landing.html`:
```html
{% extends "core/base.html" %}
{% block content %}
<p>Write the birds from your eBird checklists into your Strava activities.</p>
<a class="btn" href="{% url 'core:connect' %}">Connect with Strava</a>
{% endblock %}
```

> NOTE: `callback` redirects to `core:dashboard`, added in Task 8. Until then this test asserts a 302 (target name resolves once Task 8 lands). To keep Task 7 green standalone, temporarily redirect to `core:landing`; switch to `core:dashboard` in Task 8.

- [ ] **Step 5: Run test to verify it passes**

Run: `python manage.py test core.tests.test_views -v2`
Expected: PASS (with the temporary `core:landing` redirect).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: Strava OAuth connect + callback with state, base templates"
```

---

### Task 8: Dashboard, eBird profile form, and Sync button

**Files:**
- Modify: `core/views.py`, `core/urls.py`
- Create: `core/templates/core/dashboard.html`
- Test: add to `core/tests/test_views.py`

**Interfaces:**
- Consumes: `Profile`, `sync.process_account`, `@login_required`.
- Produces routes: `core:dashboard` (`/dashboard`), `core:ebird_profile` (`/ebird-profile`, POST), `core:sync` (`/sync`, POST). Switches Task 7's callback redirect to `core:dashboard`.

- [ ] **Step 1: Write the failing tests (append to `core/tests/test_views.py`)**

```python
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta


class DashboardTests(TestCase):
    def _login(self):
        user = User.objects.create(username="7")
        Profile.objects.create(
            user=user, strava_athlete_id=7, access_token="a", refresh_token="r",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.force_login(user)
        return user

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 302)  # redirected to login/landing

    def test_dashboard_shows_for_logged_in(self):
        self._login()
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "eBird")

    def test_save_ebird_profile(self):
        self._login()
        resp = self.client.post(reverse("core:ebird_profile"), {"ebird_profile_id": "ABC123"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Profile.objects.get(strava_athlete_id=7).ebird_profile_id, "ABC123")

    @patch("core.views.process_account", return_value=[99])
    def test_sync_invokes_process_account(self, proc):
        self._login()
        resp = self.client.post(reverse("core:sync"))
        self.assertEqual(resp.status_code, 302)
        proc.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_views -v2`
Expected: FAIL — dashboard/ebird_profile/sync routes missing.

- [ ] **Step 3: Implement the views (append to `core/views.py`)**

```python
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .services.sync import process_account


@login_required
def dashboard(request):
    profile = request.user.profile
    return render(request, "core/dashboard.html", {"profile": profile})


@login_required
def ebird_profile(request):
    profile = request.user.profile
    profile.ebird_profile_id = request.POST.get("ebird_profile_id", "").strip()
    profile.save(update_fields=["ebird_profile_id"])
    messages.success(request, "eBird profile saved.")
    return redirect("core:dashboard")


@login_required
def sync_now(request):
    profile = request.user.profile
    if not profile.ebird_profile_id:
        messages.error(request, "Set your eBird profile ID first.")
        return redirect("core:dashboard")
    updated = process_account(profile)
    if updated:
        messages.success(request, f"Updated {len(updated)} activity(ies).")
    else:
        messages.info(request, "No matching checklists found for recent activities.")
    return redirect("core:dashboard")
```

Set `LOGIN_URL = "core:landing"` in `roadrunner/settings.py` so `@login_required` redirects to the landing page.

- [ ] **Step 4: Add routes, template, and fix callback redirect**

Append to `core/urls.py` `urlpatterns`:
```python
    path("dashboard", views.dashboard, name="dashboard"),
    path("ebird-profile", views.ebird_profile, name="ebird_profile"),
    path("sync", views.sync_now, name="sync"),
```

In `core/views.py` `callback`, change the final `return redirect("core:landing")` to `return redirect("core:dashboard")`.

`core/templates/core/dashboard.html`:
```html
{% extends "core/base.html" %}
{% block content %}
<p>Connected as Strava athlete <strong>{{ profile.strava_athlete_id }}</strong>.</p>

<form method="post" action="{% url 'core:ebird_profile' %}">
  {% csrf_token %}
  <label>eBird profile ID
    <input name="ebird_profile_id" value="{{ profile.ebird_profile_id }}" placeholder="e.g. MzkyNjAwNA">
  </label>
  <button class="btn" type="submit">Save</button>
</form>

<form method="post" action="{% url 'core:sync' %}" style="margin-top:1rem">
  {% csrf_token %}
  <button class="btn" type="submit">Sync now</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_views -v2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: dashboard, eBird profile form, Sync now button"
```

---

### Task 9: Strava webhook (verify + inline event processing)

**Files:**
- Modify: `core/views.py`, `core/urls.py`
- Test: add to `core/tests/test_views.py`

**Interfaces:**
- Consumes: `settings.STRAVA_WEBHOOK_VERIFY_TOKEN`, `Profile`, `process_account`.
- Produces route: `core:webhook` (`/webhook`), exempt from CSRF, handling GET (verification) and POST (event).

- [ ] **Step 1: Write the failing tests (append to `core/tests/test_views.py`)**

```python
import json


class WebhookTests(TestCase):
    def test_get_verification_echoes_challenge(self):
        with self.settings(STRAVA_WEBHOOK_VERIFY_TOKEN="vt"):
            resp = self.client.get(reverse("core:webhook"), {
                "hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "abc",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"hub.challenge": "abc"})

    def test_get_verification_rejects_bad_token(self):
        with self.settings(STRAVA_WEBHOOK_VERIFY_TOKEN="vt"):
            resp = self.client.get(reverse("core:webhook"), {
                "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "abc",
            })
        self.assertEqual(resp.status_code, 403)

    @patch("core.views.process_account", return_value=[99])
    def test_post_create_event_processes_owner(self, proc):
        user = User.objects.create(username="7")
        Profile.objects.create(
            user=user, strava_athlete_id=7, access_token="a", refresh_token="r",
            expires_at=timezone.now() + timedelta(hours=1), ebird_profile_id="P",
        )
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 99, "owner_id": 7}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        proc.assert_called_once()
        self.assertEqual(list(proc.call_args.args[1]), [99])

    @patch("core.views.process_account")
    def test_post_unknown_owner_is_noop_200(self, proc):
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 1, "owner_id": 999}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        proc.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_views.WebhookTests -v2`
Expected: FAIL — `core:webhook` route missing.

- [ ] **Step 3: Implement the webhook view (append to `core/views.py`)**

```python
import json as _json
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def webhook(request):
    if request.method == "GET":
        if request.GET.get("hub.verify_token") != settings.STRAVA_WEBHOOK_VERIFY_TOKEN:
            return HttpResponseForbidden("bad verify token")
        return JsonResponse({"hub.challenge": request.GET.get("hub.challenge")})

    event = _json.loads(request.body or b"{}")
    if event.get("object_type") == "activity" and event.get("aspect_type") in ("create", "update"):
        profile = Profile.objects.filter(strava_athlete_id=event.get("owner_id")).first()
        if profile and profile.ebird_profile_id:
            process_account(profile, [event["object_id"]])
    return JsonResponse({"status": "ok"})
```

`ponytail:` inline processing; if Strava starts retrying due to slow responses, move the body into a `WebhookEvent` row + cron drain (see spec "Skipped").

- [ ] **Step 4: Add the route**

Append to `core/urls.py` `urlpatterns`:
```python
    path("webhook", views.webhook, name="webhook"),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_views.WebhookTests -v2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: Strava webhook verification + inline event processing"
```

---

### Task 10: Vercel deployment artifacts

**Files:**
- Create: `api/index.py`, `vercel.json`
- Modify: `README.md`

**Interfaces:**
- Produces: a deployable Vercel configuration exposing `roadrunner.wsgi` as `app`.

- [ ] **Step 1: Create the Vercel WSGI entrypoint**

`api/index.py`:
```python
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roadrunner.settings")

from roadrunner.wsgi import application

app = application
```

- [ ] **Step 2: Create `vercel.json`**

```json
{
  "builds": [{ "src": "api/index.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "api/index.py" }]
}
```

- [ ] **Step 3: Run the full test suite (nothing should regress)**

Run: `python manage.py test -v2`
Expected: all tests PASS.

- [ ] **Step 4: Update `README.md`**

Replace the README with Roadrunner setup: env vars table, `python manage.py migrate`, local run (`python manage.py runserver`), Vercel deploy (`vercel`/`vercel --prod`), and the env vars to set in the Vercel dashboard (the Global Constraints list). Document that **migrations are run from a dev machine/CI against the Neon `DATABASE_URL`**, not on deploy.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: Vercel deployment config + README"
```

---

### Task 11: Remove the old CLI

**Files:**
- Remove: `main.py`, `ebird.py`, `strava.py`, `utils.py`, `id_dates.py`, `config.ini`, `Dockerfile`, `.dockerignore`

- [ ] **Step 1: Confirm nothing imports the old modules**

Run:
```bash
grep -rn --include=*.py -E "^(from|import) (main|ebird|strava|utils|id_dates)\b" . | grep -v "core/services"
```
Expected: no output (only `core/services` has same-named modules, which import relatively).

- [ ] **Step 2: Delete the old files**

Run:
```bash
git rm main.py ebird.py strava.py utils.py id_dates.py config.ini Dockerfile .dockerignore
```

- [ ] **Step 3: Run the full suite + check**

Run:
```bash
python manage.py test -v2 && python manage.py check
```
Expected: PASS / no issues.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove legacy CLI (logic ported into core/services)"
```

---

### Task 12: Live end-to-end verification (credentials required)

> Run after the code is merged/ready. Requires the Neon database and a Strava API application configured with callback domain = your Vercel URL.

- [ ] **Step 1: Provision Neon + set env vars**

Create a Neon Postgres database; copy the **pooled** connection string into Vercel as `DATABASE_URL`. Set all Global-Constraints env vars in the Vercel project (and a local `.env` for testing).

- [ ] **Step 2: Run migrations against Neon**

Run (with `DATABASE_URL` pointed at Neon):
```bash
python manage.py migrate
```
Expected: migrations applied.

- [ ] **Step 3: Deploy**

Run:
```bash
vercel --prod
```
Expected: a live URL. Set `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` to include that domain, redeploy if needed.

- [ ] **Step 4: Test OAuth + sync end-to-end**

Visit the URL → "Connect with Strava" → authorize → land on dashboard → enter your eBird profile ID → "Sync now". Verify a recent activity that overlaps an eBird checklist gets the bird block; verify re-syncing does not duplicate it.

- [ ] **Step 5: Register the Strava webhook subscription**

Run (substitute real values; callback must be the live `/webhook` URL):
```bash
curl -X POST https://www.strava.com/api/v3/push_subscriptions \
  -F client_id=$STRAVA_CLIENT_ID \
  -F client_secret=$STRAVA_CLIENT_SECRET \
  -F callback_url=https://<your-app>.vercel.app/webhook \
  -F verify_token=$STRAVA_WEBHOOK_VERIFY_TOKEN
```
Expected: Strava GETs `/webhook` (verification succeeds, returns the challenge), then returns a subscription `id`. Record a new Strava activity and confirm the description updates automatically.

- [ ] **Step 6: Optional — scrub leaked credentials from git history**

If the rotated old credentials must be removed entirely:
```bash
git filter-repo --path config.ini --invert-paths
```
(Requires `git-filter-repo`; force-pushes a rewritten history — coordinate before doing this on a shared remote.)

---

## Self-Review

**Spec coverage:**
- Multi-user + Strava-as-login → Tasks 2, 7. ✓
- Shared eBird token → settings `EBIRD_API_TOKEN`, Task 1/4. ✓
- Manual sync button → Task 8. ✓
- Webhook (verify + inline) → Task 9. ✓
- Append + idempotent block → Task 3 (`upsert_block`), exercised in Task 6. ✓
- Neon Postgres / pooled URL / `conn_max_age=0` → Task 1, Task 12. ✓
- One model (`Profile`) → Task 2. ✓
- One `process_account` for both triggers → Task 6, called by Tasks 8 + 9. ✓
- Security: OAuth `state` (Task 7), webhook verify_token (Task 9), gitignore + rotate + optional scrub (Tasks 1, 12). ✓
- Vercel artifacts → Task 10. ✓
- Test for matching/merge/block-replace → Task 3. ✓
- Legacy cleanup → Task 11. ✓

**Type consistency:** `IdDates` used uniformly (services return it; `compare` consumes it). `process_account(profile, activity_ids)` signature matches its callers in Tasks 8/9. `update_description` returns `int` status, checked against `200` in Task 6. `get_activity_raw_description` defined in Task 6 Step 4 before use. ✓

**Placeholder scan:** No TBD/TODO; every code step shows real code. The one forward-reference (`core:dashboard` in Task 7) is explicitly handled with a temporary redirect and resolved in Task 8. ✓
