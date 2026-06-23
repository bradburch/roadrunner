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
