# iNaturalist as a second, all-taxa observation source

**Date:** 2026-06-24
**Status:** Approved design

## Goal

Add iNaturalist alongside eBird as a source of species observations that get
written into overlapping Strava activity descriptions. Broaden the product from
birds-only to **all taxa** (animals, plants, fungi). Both sources are optional
and additive: a user may link eBird, iNaturalist, or both, and a synced activity
shows the merged set of species from whichever sources are linked.

Explicitly **out of scope**: Observation.org, GBIF, Merlin, BirdWeather and other
nature apps (no fit or redundant with iNaturalist); iNaturalist OAuth/writes;
per-species counts for iNaturalist; storing a taxon group; separate per-source
description blocks.

## Key structural difference

eBird returns **timed checklists**: a start time plus `durationHrs`, i.e. a time
window, with a species list. iNaturalist returns **individual observations**,
each carrying its own `time_observed_at` timestamp — not grouped into timed
sessions.

The existing matcher `matching.compare(a, b)` overlaps two windows with a strict
`earliest_end > latest_start`. A zero-width observation window (start == end)
fails that test, so observations cannot be fed through `compare()`. Instead the
iNaturalist adapter does **point-in-window filtering itself**: keep each
observation whose `time_observed_at` falls inside an activity's `[start, end]`.

The two sources therefore converge at `activity_species: dict[int, dict]` in
`sync.process_account` — *after* matching — not at the `IdDates` level before it.
This is correct precisely because the sources disagree on input shape (windows vs
points) but agree on output ("species seen during activity N").

## Components

### New: `core/services/inaturalist.py` (~50 lines)

```
collect_species(user_id: str, activities: list[IdDates]) -> dict[int, dict[str, str]]
```

- Single request: `GET https://api.inaturalist.org/v1/observations` with params
  `user_id`, `d1`/`d2` (min/max activity date), `per_page=200`,
  `order_by=observed_on`. No auth — iNaturalist reads are public.
  - `# ponytail: single page (200) covers ~5 recent activities; paginate if windows ever span more`
- Per observation:
  - Skip any without `time_observed_at` (only a `observed_on` date — can't place
    it in a window).
  - Species name = `taxon.preferred_common_name or taxon.name` (scientific-name
    fallback when no common name exists).
  - Assign the name to every activity whose `[start_date, end_date]` contains the
    observation timestamp.
- Returned dict values are `""` (empty) — iNaturalist has no reliable count, so
  species render as bare names.
- All quality grades included (research-grade, needs-ID, casual): the user saw it
  on their activity regardless of community confirmation.

### `core/services/sync.py`

`process_account` currently calls `ebird.get_recent_checklists` unconditionally.
Changes:

- Guard the eBird loop with `if profile.ebird_profile_id:` (eBird is now optional).
- After it, add a parallel iNaturalist block, gated on
  `if profile.inaturalist_user_id:`, that calls `inaturalist.collect_species` and
  merges each activity's species into `activity_species` via `matching.add_dict`.

### `core/services/matching.py`

- `BLOCK_HEADER` → `"Nature seen during activity:"`.
- Broaden `_BLOCK_RE` header to match `(?:Birds|Nature) seen during activity:` so
  existing "Birds seen during activity:" blocks already written to users' Strava
  descriptions are still found and replaced on re-sync (no orphaned old block).
- `create_bird_description`: render a no-count entry as the bare name —
  `f"{value} {key}" if value else key`.
- `add_dict`: treat `""` as "unknown count". When merging a species present in
  both a counted source (eBird) and an uncounted one (iNaturalist), the known
  count wins; `""` never overwrites a real value, and a real value replaces `""`.

### `core/models.py`

- Add `inaturalist_user_id = models.CharField(max_length=64, blank=True)`.
- One migration.

### `core/views.py` and `core/templates/core/dashboard.html`

- New view `inaturalist_profile` mirroring `ebird_profile`: accept a pasted
  `inaturalist.org/people/<login>` URL or a bare login, validate against
  `[A-Za-z0-9_-]{1,64}`, save to `profile.inaturalist_user_id`. New URL route.
- Dashboard: a second panel for the iNaturalist login, mirroring the eBird panel.
  Remove `required` from the eBird input (now that either source suffices).
- `sync_now`: gate on `ebird_profile_id or inaturalist_user_id`; reword the empty
  result message from "No matching checklists found" to "No matching observations
  found".
- `webhook`: broaden the `profile.ebird_profile_id` gate to either source.

## Data flow

```
process_account(profile, activity_ids?)
  ├─ activities = recent (or specified) Strava activities  [windows]
  ├─ if ebird_profile_id:
  │     for each checklist window overlapping an activity (compare):
  │       merge ebird.build_bird_dict(obs)  →  activity_species[act]
  ├─ if inaturalist_user_id:
  │     inaturalist.collect_species(user_id, activities)    [point-in-window]
  │       merge per-activity species (value "")  →  activity_species[act]
  └─ for each activity in activity_species:
        create_bird_description → upsert_block (merged, one block) → Strava update
```

## Error handling

- iNaturalist request uses a timeout (matching existing 30s convention). A failed
  request should not break a sync that also has eBird data — wrap the iNat block
  so an exception is logged and skipped, the eBird-derived species still write.
  (The webhook path already swallows and logs exceptions at the top level.)
- Observations lacking `time_observed_at` are skipped, not errored.
- Missing common name falls back to scientific name; an observation with no
  `taxon` at all is skipped.

## Testing

- New `core/tests/test_inaturalist.py` (mock `requests`): species filtered to the
  correct activity window; common-name vs scientific-name fallback; observations
  without `time_observed_at` skipped; empty/`None` user handled.
- `core/tests/test_matching.py`: no-count entries render as bare names; counted
  and uncounted entries coexist; `add_dict` count-wins-over-empty; `_BLOCK_RE`
  still matches and replaces an old "Birds seen during activity:" block.
- `core/tests/test_sync.py`: eBird-only, iNat-only, and both-linked paths; merged
  block contains species from both sources.
- `core/tests/test_views.py`: saving an iNaturalist login (bare and URL forms);
  sync gating when only one source is linked.
