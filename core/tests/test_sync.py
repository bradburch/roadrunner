from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone as djtz
from core.models import Profile
from core.services import sync
from core.services.timespan import IdDates


def _profile(expired=False):
    user = User.objects.create(username="42")
    return Profile.objects.create(
        user=user, strava_athlete_id=42, access_token="old", refresh_token="r",
        expires_at=djtz.now() + (timedelta(hours=-1) if expired else timedelta(hours=1)),
        ebird_profile_id="PROF",
    )


class EnsureFreshTokenTests(TestCase):
    @patch("core.services.sync.strava.refresh_token")
    def test_refreshes_and_persists_when_expired(self, refresh):
        refresh.return_value = {"access_token": "new", "refresh_token": "r2",
                                "expires_at": int(djtz.now().timestamp()) + 3600}
        p = _profile(expired=True)
        token = sync.ensure_fresh_token(p)
        self.assertEqual(token, "new")
        p.refresh_from_db()
        self.assertEqual(p.access_token, "new")
        self.assertEqual(p.refresh_token, "r2")

    @patch("core.services.sync.strava.refresh_token")
    def test_no_refresh_when_valid(self, refresh):
        token = sync.ensure_fresh_token(_profile(expired=False))
        self.assertEqual(token, "old")
        refresh.assert_not_called()


class ProcessAccountTests(TestCase):
    def _windows(self):
        base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
        activity = IdDates(99, base, base + timedelta(hours=1))
        checklist = IdDates("S1", base + timedelta(minutes=10))
        return activity, checklist

    @patch("core.services.sync.strava.get_activity_raw_description", return_value="My ride.")
    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.ebird.build_bird_dict", return_value={"Robin": "3"})
    @patch("core.services.sync.ebird.get_dates_observation")
    @patch("core.services.sync.ebird.get_recent_checklists")
    @patch("core.services.sync.strava.get_recent_activities")
    def test_match_updates_activity(self, acts, lists, dates, build, update, desc_get):
        activity, checklist = self._windows()
        acts.return_value = [activity]
        lists.return_value = [checklist]
        dates.return_value = (checklist.start_date + timedelta(hours=1), [{"speciesCode": "amerob", "howManyStr": "3"}])
        updated = sync.process_account(_profile())
        self.assertEqual(updated, [99])
        # description written contains the marker block + bird line
        desc = update.call_args.kwargs.get("description") or update.call_args.args[2]
        self.assertIn("3 Robin", desc)
        self.assertIn("<!-- roadrunner -->", desc)

    @patch("core.services.sync.strava.update_description", return_value=200)
    @patch("core.services.sync.ebird.get_dates_observation")
    @patch("core.services.sync.ebird.get_recent_checklists", return_value=[])
    @patch("core.services.sync.strava.get_recent_activities")
    def test_no_match_updates_nothing(self, acts, lists, dates, update):
        activity, _ = self._windows()
        acts.return_value = [activity]
        self.assertEqual(sync.process_account(_profile()), [])
        update.assert_not_called()
