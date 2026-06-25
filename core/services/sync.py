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
