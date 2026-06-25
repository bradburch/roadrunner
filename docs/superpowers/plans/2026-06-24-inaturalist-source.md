# iNaturalist all-taxa source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add iNaturalist as a second, optional, all-taxa observation source alongside eBird, with both fetched concurrently and merged into one Strava activity description block.

**Architecture:** Each source exposes `collect_species(source_id, activities) -> dict[int, dict[str, str]]` (species keyed by Strava activity id). `sync.process_account` fans the linked sources out concurrently via `concurrent.futures.ThreadPoolExecutor`, isolates each behind a try/except, and merges results with `matching.add_dict`. iNaturalist does point-in-window filtering itself (its observations are timestamped points, not timed checklists like eBird).

**Tech Stack:** Django 6.0, Python 3.12, `requests`, stdlib `concurrent.futures`. No new dependencies.

## Global Constraints

- Django >= 6.0; no new third-party dependencies (concurrency uses stdlib `concurrent.futures.ThreadPoolExecutor`).
- HTTP calls use `timeout=30` (existing convention).
- Block header text is exactly `Nature seen during activity:` (was `Birds seen during activity:`).
- iNaturalist species carry no count: their dict value is the empty string `""`.
- All iNaturalist quality grades are included (no `quality_grade` filter).
- Source-id validation regex: eBird `[A-Za-z0-9_-]{4,64}`, iNaturalist `[A-Za-z0-9_-]{1,64}`.
- iNaturalist reads need no auth/API key.
- Tests: `SimpleTestCase` for pure logic (no DB), `TestCase` when a `Profile` row is needed. Run with `python manage.py test <dotted.path> -v 2`.

---

### Task 1: `Profile.inaturalist_user_id` field + migration

**Files:**
- Modify: `core/models.py:12`
- Test: `core/tests/test_models.py`

**Interfaces:**
- Produces: `Profile.inaturalist_user_id: str` (CharField, `blank=True`, default `""`).

- [ ] **Step 1: Write the failing test**

Add to `core/tests/test_models.py`, inside `ProfileTests`:

```python
    def test_inaturalist_user_id_defaults_blank(self):
        self.assertEqual(self._profile().inaturalist_user_id, "")

    def test_inaturalist_user_id_persists(self):
        p = self._profile(inaturalist_user_id="naturelover")
        p.refresh_from_db()
        self.assertEqual(p.inaturalist_user_id, "naturelover")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python manage.py test core.tests.test_models -v 2`
Expected: FAIL — `TypeError`/`FieldError` for unknown `inaturalist_user_id`.

- [ ] **Step 3: Add the field**

In `core/models.py`, add after line 12 (`ebird_profile_id = ...`):

```python
    inaturalist_user_id = models.CharField(max_length=64, blank=True)
```

- [ ] **Step 4: Generate the migration**

Run: `python manage.py makemigrations core`
Expected: creates `core/migrations/000X_profile_inaturalist_user_id.py` adding the field.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_models -v 2`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add core/models.py core/migrations/ core/tests/test_models.py
git commit -m "feat: add Profile.inaturalist_user_id field"
```

---

### Task 2: `matching.py` — all-taxa header, no-count rendering, empty-count merge

**Files:**
- Modify: `core/services/matching.py:8`, `:15-18`, `:27-41`
- Test: `core/tests/test_matching.py`

**Interfaces:**
- Produces:
  - `BLOCK_HEADER == "Nature seen during activity:"`
  - `create_bird_description(species_num: dict) -> str` renders `"<key>\n"` when value is falsy, else `"<value> <key>\n"`.
  - `add_dict(current, new)` — a real count beats `""`; `""` never overwrites a real count.
  - `_BLOCK_RE` matches both legacy `Birds seen during activity:` and new `Nature seen during activity:` blocks.

- [ ] **Step 1: Write the failing tests**

In `core/tests/test_matching.py`, update the existing `DescriptionTests` and `AddDictTests` and `UpsertBlockTests` and add new cases. Replace `class DescriptionTests` with:

```python
class DescriptionTests(SimpleTestCase):
    def test_description_lines(self):
        self.assertEqual(matching.create_bird_description({"Robin": "3"}), "3 Robin\n")

    def test_no_count_renders_bare_name(self):
        self.assertEqual(
            matching.create_bird_description({"Western Fence Lizard": ""}),
            "Western Fence Lizard\n",
        )

    def test_mixed_counts(self):
        self.assertEqual(
            matching.create_bird_description({"Robin": "3", "Lizard": ""}),
            "3 Robin\nLizard\n",
        )
```

Add to `class AddDictTests`:

```python
    def test_count_wins_over_empty(self):
        self.assertEqual(matching.add_dict({"Lizard": ""}, {"Lizard": "2"}), {"Lizard": "2"})

    def test_empty_does_not_overwrite_count(self):
        self.assertEqual(matching.add_dict({"Robin": "3"}, {"Robin": ""}), {"Robin": "3"})

    def test_both_empty_stays_empty(self):
        self.assertEqual(matching.add_dict({"Lizard": ""}, {"Lizard": ""}), {"Lizard": ""})
```

In `class UpsertBlockTests`, change every literal `"Birds seen during activity:"` to `"Nature seen during activity:"` (in `test_empty_description_gets_block_only`, `test_existing_text_is_preserved_and_block_appended`, `test_resync_replaces_block_not_duplicates`). Then add:

```python
    def test_resync_replaces_legacy_birds_block(self):
        legacy = (
            "My ride.\n\nBirds seen during activity:\n3 Robin\n\n"
            "Generated by Roadrunner\n" + matching.ROADRUNNER_URL
        )
        out = matching.upsert_block(legacy, "5 Robin\n")
        self.assertNotIn("Birds seen during activity:", out)
        self.assertIn("Nature seen during activity:", out)
        self.assertEqual(out.count("seen during activity:"), 1)
        self.assertIn("5 Robin", out)
        self.assertNotIn("3 Robin", out)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_matching -v 2`
Expected: FAIL — old header asserted, no-count not handled, legacy block not replaced.

- [ ] **Step 3: Implement the changes**

In `core/services/matching.py`, change line 8:

```python
BLOCK_HEADER = "Nature seen during activity:"
```

Replace the `_BLOCK_RE` definition (lines 15-18) with an alternation that also matches the legacy header:

```python
_BLOCK_RE = re.compile(
    r"(?:Birds|Nature) seen during activity:" + r".*?"
    + re.escape(BLOCK_FOOTER) + r"(?:\n\S+)?",
    re.DOTALL,
)
```

Replace `add_dict` (lines 27-37) with:

```python
def add_dict(current: dict, new: dict) -> dict:
    merged = current.copy()
    for k, v in new.items():
        if k not in merged:
            merged[k] = v
        elif not v:                                   # iNat has no count — keep existing
            continue
        elif not merged[k]:                           # existing had no count — take the count
            merged[k] = v
        elif v.isnumeric() and merged[k].isnumeric():
            merged[k] = str(int(v) + int(merged[k]))
        else:
            merged[k] = "X"
    return merged
```

Replace `create_bird_description` (lines 40-41) with:

```python
def create_bird_description(species_num: dict) -> str:
    return "".join(
        f"{value} {key}\n" if value else f"{key}\n"
        for key, value in species_num.items()
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_matching -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/services/matching.py core/tests/test_matching.py
git commit -m "feat: all-taxa block header, no-count rendering, empty-count merge"
```

---

### Task 3: `core/services/inaturalist.py` — `collect_species`

**Files:**
- Create: `core/services/inaturalist.py`
- Test: `core/tests/test_inaturalist.py`

**Interfaces:**
- Consumes: `IdDates` (from `core.services.timespan`).
- Produces: `collect_species(user_id: str, activities: list[IdDates]) -> dict[int, dict[str, str]]` — for each activity, `{species_name: ""}` for observations whose `time_observed_at` falls in `[start_date, end_date]`. No request when `activities` is empty.

- [ ] **Step 1: Write the failing tests**

Create `core/tests/test_inaturalist.py`:

```python
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase
from core.services import inaturalist
from core.services.timespan import IdDates


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    m.status_code = 200
    return m


def _activity():
    base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
    return IdDates(99, base, base + timedelta(hours=1))


class CollectSpeciesTests(SimpleTestCase):
    @patch("core.services.inaturalist.requests.get")
    def test_filters_observations_to_window(self, get):
        get.return_value = _resp({"results": [
            {"time_observed_at": "2026-06-01T07:30:00+00:00",
             "taxon": {"preferred_common_name": "Western Fence Lizard",
                       "name": "Sceloporus occidentalis"}},
            {"time_observed_at": "2026-06-01T09:30:00+00:00",  # outside the window
             "taxon": {"preferred_common_name": "Mallard", "name": "Anas platyrhynchos"}},
        ]})
        self.assertEqual(
            inaturalist.collect_species("me", [_activity()]),
            {99: {"Western Fence Lizard": ""}},
        )

    @patch("core.services.inaturalist.requests.get")
    def test_falls_back_to_scientific_name(self, get):
        get.return_value = _resp({"results": [
            {"time_observed_at": "2026-06-01T07:30:00+00:00",
             "taxon": {"name": "Sceloporus occidentalis"}},
        ]})
        self.assertEqual(
            inaturalist.collect_species("me", [_activity()]),
            {99: {"Sceloporus occidentalis": ""}},
        )

    @patch("core.services.inaturalist.requests.get")
    def test_skips_observation_without_time(self, get):
        get.return_value = _resp({"results": [
            {"observed_on": "2026-06-01", "taxon": {"name": "Sceloporus occidentalis"}},
        ]})
        self.assertEqual(inaturalist.collect_species("me", [_activity()]), {})

    @patch("core.services.inaturalist.requests.get")
    def test_no_activities_makes_no_request(self, get):
        self.assertEqual(inaturalist.collect_species("me", []), {})
        get.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_inaturalist -v 2`
Expected: FAIL — `ModuleNotFoundError: core.services.inaturalist`.

- [ ] **Step 3: Implement the service**

Create `core/services/inaturalist.py`:

```python
from datetime import datetime
import requests
from .timespan import IdDates

_API = "https://api.inaturalist.org/v1/observations"


def collect_species(user_id: str, activities: list[IdDates]) -> dict[int, dict[str, str]]:
    if not activities:
        return {}
    d1 = min(a.start_date for a in activities).date().isoformat()
    d2 = max(a.end_date for a in activities).date().isoformat()
    # No auth: iNaturalist reads are public. All quality grades included.
    resp = requests.get(
        _API,
        params={"user_id": user_id, "d1": d1, "d2": d2,
                "per_page": 200, "order_by": "observed_on"},
        timeout=30,
    )
    # ponytail: single page (200) covers ~5 recent activities; paginate if windows ever span more
    out: dict[int, dict[str, str]] = {}
    for obs in resp.json().get("results", []):
        ts = obs.get("time_observed_at")
        taxon = obs.get("taxon")
        if not ts or not taxon:
            continue
        observed = datetime.fromisoformat(ts)
        name = taxon.get("preferred_common_name") or taxon.get("name")
        if not name:
            continue
        for activity in activities:
            if activity.start_date <= observed <= activity.end_date:
                out.setdefault(activity.identifier, {})[name] = ""
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_inaturalist -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/services/inaturalist.py core/tests/test_inaturalist.py
git commit -m "feat: iNaturalist collect_species adapter"
```

---

### Task 4: `ebird.collect_species` — extract the match loop from sync

**Files:**
- Modify: `core/services/ebird.py:1-4` (imports), add `collect_species`
- Test: `core/tests/test_ebird.py`

**Interfaces:**
- Consumes: `IdDates`; existing `get_recent_checklists`, `get_dates_observation`, `build_bird_dict`; `matching.compare`, `matching.add_dict`.
- Produces: `collect_species(profile_id: str, activities: list[IdDates]) -> dict[int, dict[str, str]]` — same shape as `inaturalist.collect_species`, built from checklists that overlap an activity window. Checklists with no recorded duration are skipped.

- [ ] **Step 1: Write the failing tests**

Add to `core/tests/test_ebird.py` (add `timedelta` to the datetime import line: `from datetime import datetime, timedelta, timezone`):

```python
class CollectSpeciesTests(SimpleTestCase):
    def _activity(self):
        base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
        return IdDates(99, base, base + timedelta(hours=1))

    @patch("core.services.ebird.build_bird_dict", return_value={"American Robin": "3"})
    @patch("core.services.ebird.get_dates_observation")
    @patch("core.services.ebird.get_recent_checklists")
    def test_overlapping_checklist_yields_species(self, lists, dates, build):
        base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
        checklist = IdDates("S1", base + timedelta(minutes=10))
        lists.return_value = [checklist]
        dates.return_value = (base + timedelta(hours=1), [{"speciesCode": "amerob"}])
        self.assertEqual(
            ebird.collect_species("PROF", [self._activity()]),
            {99: {"American Robin": "3"}},
        )

    @patch("core.services.ebird.get_dates_observation", return_value=(None, None))
    @patch("core.services.ebird.get_recent_checklists")
    def test_checklist_without_duration_skipped(self, lists, dates):
        lists.return_value = [IdDates("S1", datetime(2026, 6, 1, 7, tzinfo=timezone.utc))]
        self.assertEqual(ebird.collect_species("PROF", [self._activity()]), {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_ebird -v 2`
Expected: FAIL — `AttributeError: module 'core.services.ebird' has no attribute 'collect_species'`.

- [ ] **Step 3: Implement `collect_species`**

In `core/services/ebird.py`, add `matching` to the imports (line 4 area):

```python
from . import matching
```

Add at the end of the file:

```python
def collect_species(profile_id: str, activities: list[IdDates]) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for checklist in get_recent_checklists(profile_id):
        end, obs = get_dates_observation(checklist)
        if end is None:
            continue
        checklist.end_date, checklist.obs = end, obs
        for activity in activities:
            if matching.compare(activity, checklist):
                species = build_bird_dict(checklist.obs)
                existing = out.get(activity.identifier)
                out[activity.identifier] = (
                    matching.add_dict(existing, species) if existing else species
                )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_ebird -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/services/ebird.py core/tests/test_ebird.py
git commit -m "feat: ebird.collect_species adapter"
```

---

### Task 5: `sync.process_account` — concurrent fan-out + per-source isolation

**Files:**
- Modify: `core/services/sync.py:1-4` (imports), `:18-50` (rewrite body)
- Test: `core/tests/test_sync.py`

**Interfaces:**
- Consumes: `ebird.collect_species`, `inaturalist.collect_species`, `strava.*`, `matching.add_dict`, `matching.create_bird_description`, `matching.upsert_block`.
- Produces: `process_account(profile, activity_ids=None) -> list[int]` — unchanged signature; now merges all linked sources, each isolated so one failing source still writes the other's species.

- [ ] **Step 1: Write the failing tests**

Replace the entire `class ProcessAccountTests` in `core/tests/test_sync.py` with:

```python
class ProcessAccountTests(TestCase):
    def _activity(self):
        base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
        return IdDates(99, base, base + timedelta(hours=1))

    @patch("core.services.sync.strava.get_activity_raw_description", return_value="My ride.")
    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.inaturalist.collect_species")
    @patch("core.services.sync.ebird.collect_species")
    @patch("core.services.sync.strava.get_recent_activities")
    def test_merges_both_sources(self, acts, ebird_c, inat_c, update, desc_get):
        acts.return_value = [self._activity()]
        ebird_c.return_value = {99: {"Robin": "3"}}
        inat_c.return_value = {99: {"Western Fence Lizard": ""}}
        p = _profile()
        p.inaturalist_user_id = "me"
        p.save(update_fields=["inaturalist_user_id"])
        updated = sync.process_account(p)
        self.assertEqual(updated, [99])
        desc = update.call_args.kwargs.get("description") or update.call_args.args[2]
        self.assertIn("3 Robin", desc)
        self.assertIn("Western Fence Lizard", desc)
        self.assertIn("Nature seen during activity:", desc)

    @patch("core.services.sync.strava.get_activity_raw_description", return_value="")
    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.inaturalist.collect_species")
    @patch("core.services.sync.ebird.collect_species", return_value={99: {"Robin": "3"}})
    @patch("core.services.sync.strava.get_recent_activities")
    def test_ebird_only(self, acts, ebird_c, inat_c, update, desc_get):
        acts.return_value = [self._activity()]
        self.assertEqual(sync.process_account(_profile()), [99])
        inat_c.assert_not_called()  # iNat not linked

    @patch("core.services.sync.strava.get_activity_raw_description", return_value="")
    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.inaturalist.collect_species", return_value={99: {"Lizard": ""}})
    @patch("core.services.sync.ebird.collect_species")
    @patch("core.services.sync.strava.get_recent_activities")
    def test_inaturalist_only(self, acts, ebird_c, inat_c, update, desc_get):
        acts.return_value = [self._activity()]
        p = _profile()
        p.ebird_profile_id = ""
        p.inaturalist_user_id = "me"
        p.save(update_fields=["ebird_profile_id", "inaturalist_user_id"])
        updated = sync.process_account(p)
        self.assertEqual(updated, [99])
        ebird_c.assert_not_called()
        desc = update.call_args.kwargs.get("description") or update.call_args.args[2]
        self.assertIn("Lizard", desc)

    @patch("core.services.sync.strava.get_activity_raw_description", return_value="")
    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.inaturalist.collect_species", return_value={99: {"Lizard": ""}})
    @patch("core.services.sync.ebird.collect_species", side_effect=Exception("eBird down"))
    @patch("core.services.sync.strava.get_recent_activities")
    def test_one_source_failure_still_writes_other(self, acts, ebird_c, inat_c, update, desc_get):
        acts.return_value = [self._activity()]
        p = _profile()
        p.inaturalist_user_id = "me"
        p.save(update_fields=["inaturalist_user_id"])
        with self.assertLogs("core.services.sync", level="ERROR"):
            updated = sync.process_account(p)
        self.assertEqual(updated, [99])
        desc = update.call_args.kwargs.get("description") or update.call_args.args[2]
        self.assertIn("Lizard", desc)

    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.inaturalist.collect_species", return_value={})
    @patch("core.services.sync.ebird.collect_species", return_value={})
    @patch("core.services.sync.strava.get_recent_activities")
    def test_no_match_updates_nothing(self, acts, ebird_c, inat_c, update):
        acts.return_value = [self._activity()]
        self.assertEqual(sync.process_account(_profile()), [])
        update.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_sync -v 2`
Expected: FAIL — `sync` has no `inaturalist`; old inline eBird flow still present.

- [ ] **Step 3: Rewrite imports and body**

Replace `core/services/sync.py` lines 1-4 with:

```python
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from django.utils import timezone as djtz
from ..models import Profile
from . import ebird, inaturalist, strava, matching

logger = logging.getLogger(__name__)


def _safe_collect(collect, source_id, activities) -> dict:
    try:
        return collect(source_id, activities)
    except Exception:
        logger.exception("Source %s failed", collect.__module__)
        return {}
```

Keep `ensure_fresh_token` as-is. Replace `process_account` (lines 18-50) with:

```python
def process_account(profile: Profile, activity_ids: list[int] | None = None) -> list[int]:
    access = ensure_fresh_token(profile)

    if activity_ids is not None:
        activities = [strava.get_activity(access, i) for i in activity_ids]
    else:
        activities = strava.get_recent_activities(access)

    sources = []
    if profile.ebird_profile_id:
        sources.append((ebird.collect_species, profile.ebird_profile_id))
    if profile.inaturalist_user_id:
        sources.append((inaturalist.collect_species, profile.inaturalist_user_id))

    # Fan out concurrently; requests releases the GIL during I/O so the calls
    # overlap. Each source is isolated — a failing one yields {} and never blocks
    # the others. Token refresh already ran on this thread; collectors do no ORM.
    activity_species: dict[int, dict] = {}
    if sources:
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            results = list(pool.map(
                lambda s: _safe_collect(s[0], s[1], activities), sources
            ))
        for per_activity in results:
            for activity_id, species in per_activity.items():
                existing = activity_species.get(activity_id)
                activity_species[activity_id] = (
                    matching.add_dict(existing, species) if existing else species
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_sync -v 2`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/services/sync.py core/tests/test_sync.py
git commit -m "feat: concurrent multi-source sync with per-source isolation"
```

---

### Task 6: Views, URL, dashboard — link iNaturalist and broaden gating

**Files:**
- Modify: `core/views.py` (add `inaturalist_profile`; broaden `sync_now` and `webhook` gates; reword no-match message)
- Modify: `core/urls.py`
- Modify: `core/templates/core/dashboard.html`
- Test: `core/tests/test_views.py`

**Interfaces:**
- Consumes: `Profile.inaturalist_user_id`.
- Produces: route `core:inaturalist_profile` (POST), view `inaturalist_profile`; `sync_now`/`webhook` run when *either* source is linked.

- [ ] **Step 1: Write the failing tests**

Add to `class DashboardTests` in `core/tests/test_views.py`:

```python
    def test_dashboard_shows_inaturalist_panel(self):
        self._login()
        resp = self.client.get(reverse("core:dashboard"))
        self.assertContains(resp, "iNaturalist")

    def test_save_inaturalist_profile(self):
        self._login()
        resp = self.client.post(
            reverse("core:inaturalist_profile"), {"inaturalist_user_id": "naturelover"}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            Profile.objects.get(strava_athlete_id=7).inaturalist_user_id, "naturelover"
        )

    def test_save_inaturalist_profile_extracts_login_from_url(self):
        self._login()
        self.client.post(
            reverse("core:inaturalist_profile"),
            {"inaturalist_user_id": "https://www.inaturalist.org/people/naturelover"},
        )
        self.assertEqual(
            Profile.objects.get(strava_athlete_id=7).inaturalist_user_id, "naturelover"
        )

    def test_save_inaturalist_profile_rejects_invalid(self):
        self._login()
        resp = self.client.post(
            reverse("core:inaturalist_profile"),
            {"inaturalist_user_id": "not a name!"}, follow=True,
        )
        self.assertEqual(Profile.objects.get(strava_athlete_id=7).inaturalist_user_id, "")
        self.assertContains(resp, "valid iNaturalist username")

    @patch("core.views.process_account", return_value=[99])
    def test_sync_runs_with_only_inaturalist(self, proc):
        user = self._login()
        user.profile.inaturalist_user_id = "me"
        user.profile.save(update_fields=["inaturalist_user_id"])
        resp = self.client.post(reverse("core:sync"))
        self.assertEqual(resp.status_code, 302)
        proc.assert_called_once()
```

Replace `test_sync_skips_when_no_ebird_id` with a neither-source version:

```python
    @patch("core.views.process_account")
    def test_sync_skips_when_no_source_linked(self, proc):
        self._login()
        resp = self.client.post(reverse("core:sync"))
        self.assertEqual(resp.status_code, 302)
        proc.assert_not_called()
```

Add a webhook test (in `class WebhookTests`):

```python
    @patch("core.views.process_account", return_value=[99])
    def test_post_processes_inaturalist_only_owner(self, proc):
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

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_views -v 2`
Expected: FAIL — no `core:inaturalist_profile` route; dashboard lacks iNaturalist panel; sync gated on eBird only.

- [ ] **Step 3: Add the view**

In `core/views.py`, add after `ebird_profile` (after line 103):

```python
@login_required
@require_POST
def inaturalist_profile(request):
    profile = request.user.profile
    raw = request.POST.get("inaturalist_user_id", "").strip()
    # Accept a pasted profile URL by keeping only the login after /people/.
    if "/people/" in raw:
        raw = raw.split("/people/", 1)[1]
    user_id = raw.strip("/").split("?", 1)[0].split("/", 1)[0].strip()
    if user_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
        messages.error(request, "That doesn't look like a valid iNaturalist username.")
        return redirect("core:dashboard")
    profile.inaturalist_user_id = user_id
    profile.save(update_fields=["inaturalist_user_id"])
    messages.success(request, "iNaturalist profile saved.")
    return redirect("core:dashboard")
```

- [ ] **Step 4: Broaden the gates**

In `core/views.py` `sync_now`, replace lines 110-112:

```python
    if not (profile.ebird_profile_id or profile.inaturalist_user_id):
        messages.error(request, "Link an eBird or iNaturalist profile first.")
        return redirect("core:dashboard")
```

And replace the no-match message (line 126):

```python
        messages.info(request, "No matching observations found for recent activities.")
```

In `webhook`, replace the gate (line 143):

```python
        if profile and (profile.ebird_profile_id or profile.inaturalist_user_id):
```

- [ ] **Step 5: Add the URL**

In `core/urls.py`, add after the `ebird-profile` line:

```python
    path("inaturalist-profile", views.inaturalist_profile, name="inaturalist_profile"),
```

- [ ] **Step 6: Add the dashboard panel and make eBird optional**

In `core/templates/core/dashboard.html`, remove ` required` from the eBird `<input>` (line 175). After the closing `</form>` of the eBird panel (line 187), add:

```html
<form method="post" action="{% url 'core:inaturalist_profile' %}" class="panel">
  {% csrf_token %}
  <p class="eyebrow">Your iNaturalist profile</p>
  <div class="field">
    <label for="inaturalist_user_id">iNaturalist username</label>
    <input class="input" id="inaturalist_user_id" name="inaturalist_user_id"
           value="{{ profile.inaturalist_user_id }}" placeholder="e.g. naturelover">
  </div>
  <button class="btn btn--ghost" type="submit">Save</button>
  {% if profile.inaturalist_user_id %}<a class="note" style="margin-left:.7rem" href="https://www.inaturalist.org/people/{{ profile.inaturalist_user_id }}" target="_blank" rel="noopener">View your iNaturalist profile&nbsp;↗</a>{% endif %}
  <p class="note" style="margin:.7rem 0 0">
    Your username from your iNaturalist profile URL, after <code>/people/</code> —
    e.g. <code>inaturalist.org/people/<b>naturelover</b></code>.
  </p>
  <p class="note" style="margin:.7rem 0 0">
    Link eBird, iNaturalist, or both — synced activities merge species from
    whichever you connect.
  </p>
</form>
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_views -v 2`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add core/views.py core/urls.py core/templates/core/dashboard.html core/tests/test_views.py
git commit -m "feat: link iNaturalist profile and gate sync on either source"
```

---

### Task 7: All-taxa copy (landing, about, base title, README)

**Files:**
- Modify: `core/templates/core/base.html:36`
- Modify: `core/templates/core/landing.html`
- Modify: `core/templates/core/_about.html`
- Modify: `README.md`
- Test: `core/tests/test_views.py`

**Interfaces:** none (copy only).

- [ ] **Step 1: Write the failing tests**

Add to `class LandingTests` in `core/tests/test_views.py`:

```python
    def test_landing_mentions_inaturalist(self):
        resp = self.client.get(reverse("core:landing"))
        self.assertContains(resp, "iNaturalist")

    def test_landing_demo_uses_new_header(self):
        resp = self.client.get(reverse("core:landing"))
        self.assertContains(resp, "Nature seen during activity:")
        self.assertNotContains(resp, "Birds seen during activity:")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test core.tests.test_views.LandingTests -v 2`
Expected: FAIL — landing still says eBird/birds only and the old header.

- [ ] **Step 3: Update `base.html`**

Change the `<title>` (line 36) to:

```html
  <title>Roadrunner — where nature meets your miles</title>
```

- [ ] **Step 4: Update `landing.html`**

Replace the intro `<div>` (lines 198-204) with:

```html
<div>
  <p class="eyebrow">eBird · iNaturalist × Strava</p>
  <h1 class="lede">Where <em>nature</em> meets your miles.</h1>
  <p class="subhead">Roadrunner writes the species you logged on eBird or
    iNaturalist straight into the Strava activity you saw them on.</p>
  <a class="btn btn--strava" href="{% url 'core:connect' %}">Connect with Strava</a>
</div>
```

Replace the demo block body (lines 215-228, the `Birds seen during activity:` text through the last species line) so the header changes and a couple of non-bird, no-count lines appear:

```html
    <div style="white-space:pre-line;font-size:.97rem">Nature seen during activity:
100 Surf Scoter
2 Red-breasted Merganser
4 Black Oystercatcher
10 Western Gull
1 Brown Pelican
1 Turkey Vulture
Western Fence Lizard
California Poppy
Monarch</div>
```

(The footer `Generated by Roadrunner` and link line below it stay unchanged.)

- [ ] **Step 5: Update `_about.html`**

Replace the first paragraph (lines 5-10) with:

```html
  <p style="margin:.3rem 0">
    Roadrunner adds the species you logged on eBird or iNaturalist to the Strava
    activity you logged them during. When a Strava activity's time window overlaps
    your observations, your activity description gets a tidy list of the species —
    with counts from eBird checklists, and names from iNaturalist.
  </p>
```

Replace the first `<li>` (line 12) with:

```html
    <li>It reads your most recent Strava activities, recent eBird checklists, and recent iNaturalist observations.</li>
```

Replace the `<em>“Birds seen during activity”</em>` reference (line 14) with `<em>“Nature seen during activity”</em>`.

Replace the eBird duration caveat paragraph (lines 18-21) with one that scopes it and adds the iNaturalist note:

```html
  <p class="note" style="margin:.7rem 0 0">
    Heads up: only eBird checklists <strong>with a recorded duration</strong> can
    be matched (incidental observations without a duration are skipped), and
    iNaturalist observations need a <strong>recorded time</strong> to place them in
    an activity.
  </p>
```

- [ ] **Step 6: Update `README.md`**

Change the opening description line to:

```markdown
A multi-user web app that writes the species from your eBird and iNaturalist observations into your overlapping Strava activity descriptions. Users log in with Strava and link an eBird profile ID, an iNaturalist username, or both (iNaturalist reads need no API key).
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python manage.py test core.tests.test_views.LandingTests -v 2`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `python manage.py test -v 2`
Expected: PASS (all tests across the suite).

- [ ] **Step 9: Commit**

```bash
git add core/templates/core/base.html core/templates/core/landing.html core/templates/core/_about.html README.md core/tests/test_views.py
git commit -m "copy: broaden product to all taxa and two sources"
```

---

## Self-Review

**Spec coverage:**
- iNaturalist adapter (point-in-window, no count, all grades, name fallback, no-time skip) → Task 3. ✓
- eBird collector extraction / symmetric interface → Task 4. ✓
- Concurrent fan-out + per-source failure isolation + thread-safety (refresh before fan-out) → Task 5. ✓
- `matching` header rename, legacy-header regex, no-count render, empty-count merge → Task 2. ✓
- `Profile.inaturalist_user_id` + migration → Task 1. ✓
- Views/URL/dashboard: link iNat, optional eBird, either-source gating for sync + webhook → Task 6. ✓
- All-taxa copy (landing, about, base title, README) → Task 7. ✓

**Placeholder scan:** none — every code step shows full code; every run step shows command + expected result.

**Type consistency:** both collectors are `collect_species(str, list[IdDates]) -> dict[int, dict[str, str]]`; `sync` consumes that uniformly; `matching.add_dict`/`create_bird_description` handle `""` values produced by the iNat collector. Consistent across Tasks 2–6.
