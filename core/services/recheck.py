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
