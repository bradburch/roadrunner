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
