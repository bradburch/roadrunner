# Deferred re-check for late checklists — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a Strava activity webhook fires but no overlapping eBird/iNaturalist observation exists *yet*, re-check the activity 2, 4, and 8 hours later and fill in the species once a checklist has been saved — stopping the moment data lands or after the 8-hour attempt.

**Architecture:** A new `ActivityRecheck` row persists per-activity retry state (the activity's cached time window + which of the 2/4/8h tiers is next). The webhook creates a row only when the initial sync found nothing; a Vercel Cron job hits `/cron/rechecks` on a schedule, which drains all due rows, batches them per profile through `sync.process_account`, and either deletes the row (data found, or 8h exhausted) or advances it to the next tier. Rechecks reuse the cached window, so the common "no data" case makes **no per-activity Strava read** (only a possible token refresh + the eBird/iNat fetch, once per profile per drain).

**Tech Stack:** Django 6.0, Python 3.12, Postgres, Vercel Cron. No new dependencies.

## Global Constraints

- Django >= 6.0; no new third-party dependencies.
- HTTP calls use `timeout=30` (existing convention).
- Re-check tiers are exactly **2, 4, and 8 hours**, measured from when the activity was first seen with no data.
- Most activities never get data → the recheck path must **fail fast**: no per-activity Strava reads (reuse the cached window), one eBird/iNat fetch per profile (batched), bounded drain size.
- Once an activity is successfully updated (initially or on any recheck), it must **not** be carried into later triggers — its row is deleted.
- The cron endpoint is **authenticated** with `CRON_SECRET` (Vercel sends `Authorization: Bearer <CRON_SECRET>`); constant-time compare; deny when unconfigured in production.
- Tests: `SimpleTestCase` for pure logic, `TestCase` when DB rows are needed. Run with `python manage.py test <dotted.path> -v 2`.
- Migrations are **not** run on Vercel deploy — they run from a dev machine/CI against `DATABASE_URL`.

---

### Task 1: `ActivityRecheck` model + migration

**Files:**
- Modify: `core/models.py`
- Create: `core/migrations/0004_activityrecheck.py` (generated)

**Interfaces:**
- Produces:
  - `ActivityRecheck.OFFSETS == [timedelta(hours=2), timedelta(hours=4), timedelta(hours=8)]`
  - Fields: `profile` (FK→Profile, CASCADE), `activity_id` (BigIntegerField), `start_date` (DateTimeField), `end_date` (DateTimeField), `created_at` (DateTimeField, default `timezone.now`), `tier` (PositiveSmallIntegerField, default 0 — index of the *next* offset to fire), `due_at` (DateTimeField, `db_index=True`).
  - Unique constraint on `(profile, activity_id)` named `uniq_pending_recheck`.

The model is exercised by Task 3's service tests (create → drain → advance/delete); no
dedicated model test — a model with no custom methods is framework behavior. (ponytail:
don't TDD the framework.)

- [ ] **Step 1: Add the model**

In `core/models.py`, change the imports at the top to include `timedelta`:

```python
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
```

Then append at the end of the file:

```python
class ActivityRecheck(models.Model):
    """A queued late-checklist re-check for one Strava activity.

    Created only when a webhook sync found no overlapping observation. A Vercel
    Cron job drains due rows (see core.services.recheck). `tier` is the index of
    the next offset to fire; the row is deleted on success or after the last
    offset, so most rows live at most 8 hours.
    """

    # 2/4/8h ladder, measured from `created_at` (when we first saw no data).
    OFFSETS = [timedelta(hours=2), timedelta(hours=4), timedelta(hours=8)]

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    activity_id = models.BigIntegerField()
    # Cached activity window so rechecks need no Strava read (fail fast).
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    tier = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "activity_id"], name="uniq_pending_recheck"
            )
        ]

    def __str__(self) -> str:
        return f"ActivityRecheck(activity={self.activity_id}, tier={self.tier})"
```

- [ ] **Step 2: Generate the migration**

Run: `python manage.py makemigrations core`
Expected: creates `core/migrations/0004_activityrecheck.py` adding the model.

- [ ] **Step 3: Run the suite to confirm nothing broke**

Run: `python manage.py test core.tests.test_models -v 2`
Expected: PASS (existing tests unaffected).

- [ ] **Step 4: Commit**

```bash
git add core/models.py core/migrations/
git commit -m "feat: ActivityRecheck model for deferred re-checks"
```

---

### Task 2: `sync.process_account` — accept pre-resolved activities

**Files:**
- Modify: `core/services/sync.py:30-37`
- Test: `core/tests/test_sync.py`

**Interfaces:**
- Produces: `process_account(profile, activity_ids=None, activities=None) -> list[int]`.
  When `activities` (a `list[IdDates]`) is given, skip the Strava read entirely and
  sync those directly. `activity_ids` and `activities` are mutually exclusive;
  `activities` wins if both are passed. Existing callers (no `activities`) are unchanged.

- [ ] **Step 1: Write the failing test**

Add to `class ProcessAccountTests` in `core/tests/test_sync.py`:

```python
    @patch("core.services.sync.strava.get_activity")
    @patch("core.services.sync.strava.get_activity_raw_description", return_value="")
    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.inaturalist.collect_species", return_value={})
    @patch("core.services.sync.ebird.collect_species", return_value={99: {"Robin": "3"}})
    def test_activities_arg_skips_strava_read(
        self, ebird_c, inat_c, update, desc_get, get_activity
    ):
        updated = sync.process_account(_profile(), activities=[self._activity()])
        self.assertEqual(updated, [99])
        get_activity.assert_not_called()  # window supplied; no Strava read
        ebird_c.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_sync.ProcessAccountTests.test_activities_arg_skips_strava_read -v 2`
Expected: FAIL — `process_account()` got an unexpected keyword argument `activities`.

- [ ] **Step 3: Implement the parameter**

In `core/services/sync.py`, replace the signature and activity-resolution block (lines 30-37):

```python
def process_account(
    profile: Profile,
    activity_ids: list[int] | None = None,
    activities: list | None = None,
) -> list[int]:
    access = ensure_fresh_token(profile)

    # `activities` (pre-resolved IdDates) lets rechecks reuse a cached window and
    # skip the Strava read — the common no-data path then makes no Strava call.
    if activities is None:
        if activity_ids is not None:
            activities = [strava.get_activity(access, i) for i in activity_ids]
        else:
            activities = strava.get_recent_activities(access)
```

(The rest of `process_account` is unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_sync -v 2`
Expected: PASS (new test plus all existing process_account tests).

- [ ] **Step 5: Commit**

```bash
git add core/services/sync.py core/tests/test_sync.py
git commit -m "feat: process_account accepts pre-resolved activities"
```

---

### Task 3: `core/services/recheck.py` — schedule, reconcile, drain

**Files:**
- Create: `core/services/recheck.py`
- Test: `core/tests/test_recheck.py`

**Interfaces:**
- Consumes: `ActivityRecheck` (Task 1), `IdDates` (`core.services.timespan`),
  `sync.process_account(profile, activities=...)` (Task 2).
- Produces:
  - `schedule(profile, activity: IdDates) -> None` — `get_or_create` a tier-0 row due `now + 2h`, caching the window. Idempotent: a second call for the same activity does not duplicate or reset the row.
  - `clear(profile, activity_id: int) -> None` — delete any row for that activity.
  - `reconcile(profile, activity: IdDates, found: bool) -> None` — `clear` if found, else `schedule`.
  - `run_due_rechecks(limit: int = 50) -> int` — drain rows with `due_at <= now`, batch per profile through `process_account`, delete on match or after the last tier, else advance the tier. Returns the number of rows processed. A failing profile batch is logged and left for the next tick.

- [ ] **Step 1: Write the failing tests**

Create `core/tests/test_recheck.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone as djtz
from core.models import ActivityRecheck, Profile
from core.services import recheck
from core.services.timespan import IdDates


def _profile(athlete_id=42):
    user = User.objects.create(username=str(athlete_id))
    return Profile.objects.create(
        user=user, strava_athlete_id=athlete_id, access_token="a", refresh_token="r",
        expires_at=djtz.now() + timedelta(hours=1), ebird_profile_id="PROF",
    )


def _activity(identifier=99):
    base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
    return IdDates(identifier, base, base + timedelta(hours=1))


class ScheduleTests(TestCase):
    def test_schedule_creates_tier0_row_due_in_2h(self):
        p = _profile()
        before = djtz.now()
        recheck.schedule(p, _activity())
        row = ActivityRecheck.objects.get(profile=p, activity_id=99)
        self.assertEqual(row.tier, 0)
        self.assertEqual(row.start_date, _activity().start_date)
        self.assertEqual(row.end_date, _activity().end_date)
        delta = row.due_at - before
        self.assertGreater(delta, timedelta(hours=2) - timedelta(minutes=1))
        self.assertLess(delta, timedelta(hours=2) + timedelta(minutes=1))

    def test_schedule_is_idempotent(self):
        p = _profile()
        recheck.schedule(p, _activity())
        first = ActivityRecheck.objects.get(profile=p, activity_id=99)
        first.tier = 2  # simulate the ladder having advanced
        first.save(update_fields=["tier"])
        recheck.schedule(p, _activity())  # second webhook for same activity
        self.assertEqual(ActivityRecheck.objects.filter(activity_id=99).count(), 1)
        self.assertEqual(ActivityRecheck.objects.get(activity_id=99).tier, 2)

    def test_reconcile_found_clears(self):
        p = _profile()
        recheck.schedule(p, _activity())
        recheck.reconcile(p, _activity(), found=True)
        self.assertFalse(ActivityRecheck.objects.filter(activity_id=99).exists())

    def test_reconcile_not_found_schedules(self):
        p = _profile()
        recheck.reconcile(p, _activity(), found=False)
        self.assertTrue(ActivityRecheck.objects.filter(activity_id=99).exists())


class RunDueRechecksTests(TestCase):
    def _row(self, profile, identifier, tier=0, due_offset=timedelta(hours=-1)):
        now = djtz.now()
        a = _activity(identifier)
        return ActivityRecheck.objects.create(
            profile=profile, activity_id=identifier,
            start_date=a.start_date, end_date=a.end_date,
            created_at=now - timedelta(hours=2), tier=tier,
            due_at=now + due_offset,
        )

    @patch("core.services.recheck.process_account", return_value=[])
    def test_no_match_advances_tier(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        self.assertEqual(recheck.run_due_rechecks(), 1)
        row = ActivityRecheck.objects.get(activity_id=99)
        self.assertEqual(row.tier, 1)
        # due_at re-anchored to created_at + OFFSETS[1] (4h)
        self.assertEqual(row.due_at, row.created_at + ActivityRecheck.OFFSETS[1])

    @patch("core.services.recheck.process_account", return_value=[99])
    def test_match_deletes_row(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        recheck.run_due_rechecks()
        self.assertFalse(ActivityRecheck.objects.filter(activity_id=99).exists())

    @patch("core.services.recheck.process_account", return_value=[])
    def test_last_tier_no_match_deletes_row(self, proc):
        p = _profile()
        self._row(p, 99, tier=2)  # 8h tier was the one just run
        recheck.run_due_rechecks()
        self.assertFalse(ActivityRecheck.objects.filter(activity_id=99).exists())

    @patch("core.services.recheck.process_account", return_value=[])
    def test_future_rows_untouched(self, proc):
        p = _profile()
        self._row(p, 99, tier=0, due_offset=timedelta(hours=1))  # not due yet
        self.assertEqual(recheck.run_due_rechecks(), 0)
        proc.assert_not_called()
        self.assertEqual(ActivityRecheck.objects.get(activity_id=99).tier, 0)

    @patch("core.services.recheck.process_account", return_value=[])
    def test_batches_activities_per_profile(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        self._row(p, 100, tier=0)
        recheck.run_due_rechecks()
        proc.assert_called_once()
        activities = proc.call_args.kwargs["activities"]
        self.assertEqual({a.identifier for a in activities}, {99, 100})

    @patch("core.services.recheck.process_account", side_effect=Exception("boom"))
    def test_failed_batch_leaves_rows(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        with self.assertLogs("core.services.recheck", level="ERROR"):
            recheck.run_due_rechecks()
        self.assertTrue(ActivityRecheck.objects.filter(activity_id=99).exists())
        self.assertEqual(ActivityRecheck.objects.get(activity_id=99).tier, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_recheck -v 2`
Expected: FAIL — `ModuleNotFoundError: core.services.recheck`.

- [ ] **Step 3: Implement the service**

Create `core/services/recheck.py`:

```python
import logging
from collections import defaultdict

from django.utils import timezone as djtz

from ..models import ActivityRecheck, Profile
from .sync import process_account
from .timespan import IdDates

logger = logging.getLogger(__name__)

# Bound a single drain so one cron invocation can't fan out unboundedly.
DRAIN_LIMIT = 50


def schedule(profile: Profile, activity: IdDates) -> None:
    """Queue an activity for 2/4/8h re-checks. No-op if already queued."""
    now = djtz.now()
    ActivityRecheck.objects.get_or_create(
        profile=profile,
        activity_id=activity.identifier,
        defaults={
            "start_date": activity.start_date,
            "end_date": activity.end_date,
            "created_at": now,
            "tier": 0,
            "due_at": now + ActivityRecheck.OFFSETS[0],
        },
    )


def clear(profile: Profile, activity_id: int) -> None:
    ActivityRecheck.objects.filter(profile=profile, activity_id=activity_id).delete()


def reconcile(profile: Profile, activity: IdDates, found: bool) -> None:
    """After a sync: drop the ladder if data landed, else (re)queue it."""
    if found:
        clear(profile, activity.identifier)
    else:
        schedule(profile, activity)


def run_due_rechecks(limit: int = DRAIN_LIMIT) -> int:
    """Drain due rows, batched per profile. Returns rows processed."""
    now = djtz.now()
    due = list(
        ActivityRecheck.objects.filter(due_at__lte=now)
        .select_related("profile")
        .order_by("due_at")[:limit]
    )

    by_profile: dict[Profile, list[ActivityRecheck]] = defaultdict(list)
    for row in due:
        by_profile[row.profile].append(row)

    processed = 0
    for profile, rows in by_profile.items():
        # One fetch per source covers every due activity for this profile; the
        # cached windows mean no Strava read happens for the no-match case.
        activities = [IdDates(r.activity_id, r.start_date, r.end_date) for r in rows]
        try:
            updated = set(process_account(profile, activities=activities))
        except Exception:
            logger.exception(
                "Recheck batch failed for athlete %s", profile.strava_athlete_id
            )
            continue  # leave rows; the next cron tick retries them
        for row in rows:
            if row.activity_id in updated:
                row.delete()  # data landed — don't carry into later triggers
                continue
            row.tier += 1
            if row.tier >= len(ActivityRecheck.OFFSETS):
                row.delete()  # 8h exhausted — give up
            else:
                row.due_at = row.created_at + ActivityRecheck.OFFSETS[row.tier]
                row.save(update_fields=["tier", "due_at"])
        processed += len(rows)
    return processed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_recheck -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/services/recheck.py core/tests/test_recheck.py
git commit -m "feat: recheck service — schedule, reconcile, drain due rows"
```

---

### Task 4: Webhook integration + authenticated cron endpoint

**Files:**
- Modify: `core/views.py` (imports; webhook body; add `run_rechecks` view)
- Modify: `core/urls.py`
- Test: `core/tests/test_views.py`

**Interfaces:**
- Consumes: `recheck.reconcile`, `recheck.run_due_rechecks`, `sync.ensure_fresh_token`, `strava.get_activity`, `settings.CRON_SECRET` (Task 5 adds the setting).
- Produces:
  - Webhook now resolves the activity once, syncs via `activities=[...]`, and calls `recheck.reconcile` so a no-data activity is queued and a hit clears any queue.
  - `run_rechecks(request)` view at route `core:run_rechecks` (`/cron/rechecks`): 403 unless `Authorization: Bearer <CRON_SECRET>` matches (constant-time); 403 when `CRON_SECRET` is unset and not `DEBUG`; otherwise drains and returns `{"status": "ok", "processed": <int>}`.

- [ ] **Step 1: Update the existing webhook tests for the new flow**

The webhook now calls `ensure_fresh_token` + `strava.get_activity` + `recheck.reconcile`
around `process_account`, and passes the activity as `activities=[...]` (kwarg), not a
positional id list. Four existing tests in `class WebhookTests` (`core/tests/test_views.py`)
mock **only** `process_account` and assert on `proc.call_args.args[1]`; they must be
updated or they break (real network call from `get_activity`; `args[1]` IndexError).

First, add this import near the top of `core/tests/test_views.py` (with the other imports):

```python
from core.services.timespan import IdDates
```

Replace `test_post_create_event_processes_owner` (lines 223-236) with:

```python
    @patch("core.views.ensure_fresh_token", return_value="tok")
    @patch("core.views.strava.get_activity")
    @patch("core.views.recheck.reconcile")
    @patch("core.views.process_account", return_value=[99])
    def test_post_create_event_processes_owner(self, proc, reconc, get_act, tok):
        get_act.return_value = IdDates(
            99, dj_timezone.now(), dj_timezone.now() + timedelta(hours=1)
        )
        user = User.objects.create(username="7")
        Profile.objects.create(
            user=user, strava_athlete_id=7, access_token="a", refresh_token="r",
            expires_at=dj_timezone.now() + timedelta(hours=1), ebird_profile_id="P",
        )
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 99, "owner_id": 7}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        proc.assert_called_once()
        self.assertEqual(
            [a.identifier for a in proc.call_args.kwargs["activities"]], [99]
        )
        # data found (proc returned [99]) → reconcile told it was found
        self.assertIs(reconc.call_args.kwargs["found"], True)
```

Replace `test_post_processing_error_still_returns_200` (lines 254-266) with:

```python
    @patch("core.views.ensure_fresh_token", return_value="tok")
    @patch("core.views.strava.get_activity")
    @patch("core.views.process_account", side_effect=Exception("boom"))
    def test_post_processing_error_still_returns_200(self, proc, get_act, tok):
        get_act.return_value = IdDates(
            55, dj_timezone.now(), dj_timezone.now() + timedelta(hours=1)
        )
        user = User.objects.create(username="8")
        Profile.objects.create(
            user=user, strava_athlete_id=8, access_token="a", refresh_token="r",
            expires_at=dj_timezone.now() + timedelta(hours=1), ebird_profile_id="P",
        )
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 55, "owner_id": 8}
        with self.assertLogs("core.views", level="ERROR"):
            resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                    content_type="application/json")
        self.assertEqual(resp.status_code, 200)
```

Replace `test_post_processes_inaturalist_only_owner` (lines 283-295) with:

```python
    @patch("core.views.ensure_fresh_token", return_value="tok")
    @patch("core.views.strava.get_activity")
    @patch("core.views.recheck.reconcile")
    @patch("core.views.process_account", return_value=[99])
    def test_post_processes_inaturalist_only_owner(self, proc, reconc, get_act, tok):
        get_act.return_value = IdDates(
            99, dj_timezone.now(), dj_timezone.now() + timedelta(hours=1)
        )
        user = User.objects.create(username="11")
        Profile.objects.create(
            user=user, strava_athlete_id=11, access_token="a", refresh_token="r",
            expires_at=dj_timezone.now() + timedelta(hours=1), inaturalist_user_id="me",
        )
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 99, "owner_id": 11}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        proc.assert_called_once()
```

Replace `test_webhook_processes_after_cooldown` (lines 297-314) with:

```python
    @patch("core.views.ensure_fresh_token", return_value="tok")
    @patch("core.views.strava.get_activity")
    @patch("core.views.recheck.reconcile")
    @patch("core.views.process_account", return_value=[77])
    def test_webhook_processes_after_cooldown(self, proc, reconc, get_act, tok):
        get_act.return_value = IdDates(
            77, dj_timezone.now(), dj_timezone.now() + timedelta(hours=1)
        )
        user = User.objects.create(username="10")
        profile = Profile.objects.create(
            user=user, strava_athlete_id=10, access_token="a", refresh_token="r",
            expires_at=dj_timezone.now() + timedelta(hours=1), ebird_profile_id="P",
            last_webhook_at=dj_timezone.now() - timedelta(seconds=WEBHOOK_COOLDOWN_SECONDS + 5),
        )
        old_last_webhook_at = profile.last_webhook_at
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 77, "owner_id": 10}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        proc.assert_called_once()
        self.assertEqual(
            [a.identifier for a in proc.call_args.kwargs["activities"]], [77]
        )
        profile.refresh_from_db()
        self.assertGreater(profile.last_webhook_at, old_last_webhook_at)
```

(The other `WebhookTests` — verification, unknown owner, malformed body, throttled —
never reach the sync block, so they need no change.)

- [ ] **Step 2: Write the new failing tests**

Add to `class WebhookTests` a no-data-schedules-recheck test:

```python
    @patch("core.views.ensure_fresh_token", return_value="tok")
    @patch("core.views.strava.get_activity")
    @patch("core.views.recheck.reconcile")
    @patch("core.views.process_account", return_value=[])
    def test_webhook_no_data_schedules_recheck(self, proc, reconc, get_act, tok):
        get_act.return_value = IdDates(
            99, dj_timezone.now(), dj_timezone.now() + timedelta(hours=1)
        )
        user = User.objects.create(username="55")
        Profile.objects.create(
            user=user, strava_athlete_id=55, access_token="a", refresh_token="r",
            expires_at=dj_timezone.now() + timedelta(hours=1), ebird_profile_id="P",
        )
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 99, "owner_id": 55}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        reconc.assert_called_once()
        self.assertIs(reconc.call_args.kwargs["found"], False)
```

Add a new cron-endpoint test class:

```python
from django.test import override_settings


class CronRechecksTests(TestCase):
    @override_settings(CRON_SECRET="s3cret")
    @patch("core.views.recheck.run_due_rechecks", return_value=3)
    def test_valid_secret_drains(self, drain):
        resp = self.client.get(
            reverse("core:run_rechecks"), HTTP_AUTHORIZATION="Bearer s3cret"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["processed"], 3)
        drain.assert_called_once()

    @override_settings(CRON_SECRET="s3cret")
    @patch("core.views.recheck.run_due_rechecks")
    def test_bad_secret_forbidden(self, drain):
        resp = self.client.get(
            reverse("core:run_rechecks"), HTTP_AUTHORIZATION="Bearer wrong"
        )
        self.assertEqual(resp.status_code, 403)
        drain.assert_not_called()

    @override_settings(CRON_SECRET="s3cret")
    @patch("core.views.recheck.run_due_rechecks")
    def test_missing_secret_forbidden(self, drain):
        resp = self.client.get(reverse("core:run_rechecks"))
        self.assertEqual(resp.status_code, 403)
        drain.assert_not_called()

    @override_settings(CRON_SECRET="", DEBUG=False)
    @patch("core.views.recheck.run_due_rechecks")
    def test_unconfigured_forbidden_in_prod(self, drain):
        resp = self.client.get(reverse("core:run_rechecks"))
        self.assertEqual(resp.status_code, 403)
        drain.assert_not_called()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_views.CronRechecksTests -v 2`
Expected: FAIL — no `core:run_rechecks` route; `core.views.recheck` not imported.

- [ ] **Step 4: Update imports in `core/views.py`**

Replace the service imports (lines 19-21) with:

```python
from .models import Profile
from .services import strava, recheck
from .services.sync import process_account, ensure_fresh_token
```

- [ ] **Step 4: Rewrite the webhook sync block**

In `core/views.py` `webhook`, replace the post-throttle block (the
`profile.last_webhook_at = ...` through the `process_account(...)` try/except,
lines 167-172) with:

```python
            profile.last_webhook_at = dj_timezone.now()
            profile.save(update_fields=["last_webhook_at"])
            try:
                # Resolve the window once; reuse it for the sync and (if no data
                # lands) the cached recheck row, so retries make no Strava read.
                access = ensure_fresh_token(profile)
                activity = strava.get_activity(access, event["object_id"])
                updated = process_account(profile, activities=[activity])
                recheck.reconcile(
                    profile, activity, found=activity.identifier in updated
                )
            except Exception:
                logger.exception(
                    "Webhook processing failed for athlete %s", event.get("owner_id")
                )
```

- [ ] **Step 5: Add the cron view**

In `core/views.py`, append:

```python
def run_rechecks(request):
    # Vercel Cron sends `Authorization: Bearer <CRON_SECRET>`. Reject anything
    # else so the endpoint can't be used to drive Strava traffic by outsiders.
    expected = settings.CRON_SECRET
    if not expected:
        if not settings.DEBUG:
            return HttpResponseForbidden("cron not configured")
    else:
        header = request.headers.get("Authorization", "")
        if not secrets.compare_digest(header, f"Bearer {expected}"):
            return HttpResponseForbidden("bad cron secret")
    processed = recheck.run_due_rechecks()
    return JsonResponse({"status": "ok", "processed": processed})
```

- [ ] **Step 6: Add the URL**

In `core/urls.py`, add before the `webhook` line:

```python
    path("cron/rechecks", views.run_rechecks, name="run_rechecks"),
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_views -v 2`
Expected: PASS (cron tests, new webhook test, and all existing view tests).

- [ ] **Step 8: Commit**

```bash
git add core/views.py core/urls.py core/tests/test_views.py
git commit -m "feat: webhook queues rechecks; authenticated cron drain endpoint"
```

---

### Task 5: Config + docs — `CRON_SECRET`, Vercel cron, README

**Files:**
- Modify: `roadrunner/settings.py:72-76`
- Modify: `.env.example`
- Modify: `vercel.json`
- Modify: `README.md`

**Interfaces:**
- Produces: `settings.CRON_SECRET`; a Vercel cron pointed at `/cron/rechecks`.

No automated test — these are config/doc files. Verified by the full suite still
passing (Step 5) and by the cron-auth tests in Task 4 reading `settings.CRON_SECRET`.

- [ ] **Step 1: Add the setting**

In `roadrunner/settings.py`, add to the app-config block (after the
`STRAVA_WEBHOOK_VERIFY_TOKEN` line, ~line 76):

```python
CRON_SECRET = os.environ.get("CRON_SECRET", "")
```

- [ ] **Step 2: Document the env var**

Append to `.env.example`:

```
CRON_SECRET=
```

- [ ] **Step 3: Register the Vercel cron**

Replace `vercel.json` with:

```json
{
  "builds": [{ "src": "api/index.py", "use": "@vercel/python" }],
  "routes": [{ "src": "/(.*)", "dest": "api/index.py" }],
  "crons": [{ "path": "/cron/rechecks", "schedule": "0 * * * *" }]
}
```

- [ ] **Step 4: Update the README**

In `README.md`, add a `CRON_SECRET` row to the env-var table (after the
`STRAVA_WEBHOOK_VERIFY_TOKEN` row):

```markdown
| `CRON_SECRET` | Secret string used to authenticate Vercel Cron calls to `/cron/rechecks`; Vercel sends it as `Authorization: Bearer <CRON_SECRET>` |
```

Replace the **Deferred re-check for late checklists** roadmap bullet (lines 73-81)
with a "Scheduled re-checks" section describing the shipped feature:

```markdown
## Scheduled Re-checks

When a Strava activity is created/updated, the webhook checks for an overlapping
eBird/iNaturalist observation *at that moment*. Because birders often save their
checklists hours later, an activity with no match is queued for re-checks **2, 4,
and 8 hours later**. A Vercel Cron job hits `/cron/rechecks` (hourly) and drains
due re-checks; the moment a checklist is found the species are written and the
queue entry is dropped, and entries are discarded after the 8-hour attempt.

The endpoint is authenticated with `CRON_SECRET` — set it in both the Vercel
environment and (Vercel injects it into the cron request automatically). Note: on
Vercel's **Hobby** plan cron jobs run at most once per day; the hourly schedule
needs the **Pro** plan. Adjust the `crons` schedule in `vercel.json` to taste.
```

- [ ] **Step 5: Run the full suite**

Run: `python manage.py test -v 2`
Expected: PASS (every test across the suite).

- [ ] **Step 6: Commit**

```bash
git add roadrunner/settings.py .env.example vercel.json README.md
git commit -m "config: CRON_SECRET, Vercel cron for /cron/rechecks, docs"
```

---

## Self-Review

**Spec coverage:**
- 2/4/8h triggers after webhook → `ActivityRecheck.OFFSETS` (Task 1) + `schedule`/`run_due_rechecks` (Task 3). ✓
- Updated initially or during a hook → not carried to later triggers → `reconcile`/delete-on-match (Tasks 3, 4). ✓
- Fail fast for the common no-data case → cached window + `activities=` param (no Strava read), per-profile batch, bounded drain (Tasks 2, 3). ✓
- Serverless scheduler → Vercel Cron + authenticated `/cron/rechecks` (Tasks 4, 5). ✓
- Auth/secret handling → `CRON_SECRET`, constant-time compare, deny-when-unconfigured (Tasks 4, 5). ✓

**Placeholder scan:** none — every code step shows full code; every run step shows command + expected result.

**Type consistency:** `schedule`/`reconcile` take `IdDates`; `clear` takes `activity_id: int`; `run_due_rechecks` builds `IdDates(identifier, start, end)` and calls `process_account(profile, activities=...)` matching Task 2's new parameter; `OFFSETS` indexed by `tier` consistently in Tasks 1, 3. The webhook (Task 4) compares `activity.identifier in updated` where `updated` is `list[int]` from `process_account` and `activity.identifier` is the Strava id (int). Consistent.
