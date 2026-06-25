from datetime import datetime, timezone
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
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        name = taxon.get("preferred_common_name") or taxon.get("name")
        if not name:
            continue
        for activity in activities:
            if activity.start_date <= observed <= activity.end_date:
                out.setdefault(activity.identifier, {})[name] = ""
    return out
