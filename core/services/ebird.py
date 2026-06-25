from datetime import datetime, timezone, timedelta
import requests
from django.conf import settings
from .timespan import IdDates
from . import matching

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
    if not observation:
        return {}
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
