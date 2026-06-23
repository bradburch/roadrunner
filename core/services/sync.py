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
