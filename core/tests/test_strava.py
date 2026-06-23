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
