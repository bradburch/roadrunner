from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone as djtz
from core.models import ActivityRecheck, Profile
from core.services import recheck
from core.services.timespan import IdDates


def _profile(athlete_id=42):
    user = User.objects.create(username=str(athlete_id))
    return Profile.objects.create(
        user=user, strava_athlete_id=athlete_id, access_token="a", refresh_token="r",
        expires_at=djtz.now() + timedelta(hours=1), ebird_profile_id="PROF",
    )


def _activity(identifier=99):
    base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
    return IdDates(identifier, base, base + timedelta(hours=1))


class ScheduleTests(TestCase):
    def test_schedule_creates_tier0_row_due_in_2h(self):
        p = _profile()
        before = djtz.now()
        recheck.schedule(p, _activity())
        row = ActivityRecheck.objects.get(profile=p, activity_id=99)
        self.assertEqual(row.tier, 0)
        self.assertEqual(row.start_date, _activity().start_date)
        self.assertEqual(row.end_date, _activity().end_date)
        delta = row.due_at - before
        self.assertGreater(delta, timedelta(hours=2) - timedelta(minutes=1))
        self.assertLess(delta, timedelta(hours=2) + timedelta(minutes=1))

    def test_schedule_is_idempotent(self):
        p = _profile()
        recheck.schedule(p, _activity())
        first = ActivityRecheck.objects.get(profile=p, activity_id=99)
        first.tier = 2  # simulate the ladder having advanced
        first.save(update_fields=["tier"])
        recheck.schedule(p, _activity())  # second webhook for same activity
        self.assertEqual(ActivityRecheck.objects.filter(activity_id=99).count(), 1)
        self.assertEqual(ActivityRecheck.objects.get(activity_id=99).tier, 2)

    def test_reconcile_found_clears(self):
        p = _profile()
        recheck.schedule(p, _activity())
        recheck.reconcile(p, _activity(), found=True)
        self.assertFalse(ActivityRecheck.objects.filter(activity_id=99).exists())

    def test_reconcile_not_found_schedules(self):
        p = _profile()
        recheck.reconcile(p, _activity(), found=False)
        self.assertTrue(ActivityRecheck.objects.filter(activity_id=99).exists())


class RunDueRechecksTests(TestCase):
    def _row(self, profile, identifier, tier=0, due_offset=timedelta(hours=-1)):
        now = djtz.now()
        a = _activity(identifier)
        return ActivityRecheck.objects.create(
            profile=profile, activity_id=identifier,
            start_date=a.start_date, end_date=a.end_date,
            created_at=now - timedelta(hours=2), tier=tier,
            due_at=now + due_offset,
        )

    @patch("core.services.recheck.process_account", return_value=[])
    def test_no_match_advances_tier(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        self.assertEqual(recheck.run_due_rechecks(), 1)
        row = ActivityRecheck.objects.get(activity_id=99)
        self.assertEqual(row.tier, 1)
        # due_at re-anchored to created_at + OFFSETS[1] (4h)
        self.assertEqual(row.due_at, row.created_at + ActivityRecheck.OFFSETS[1])

    @patch("core.services.recheck.process_account", return_value=[99])
    def test_match_deletes_row(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        recheck.run_due_rechecks()
        self.assertFalse(ActivityRecheck.objects.filter(activity_id=99).exists())

    @patch("core.services.recheck.process_account", return_value=[])
    def test_last_tier_no_match_deletes_row(self, proc):
        p = _profile()
        self._row(p, 99, tier=2)  # 8h tier was the one just run
        recheck.run_due_rechecks()
        self.assertFalse(ActivityRecheck.objects.filter(activity_id=99).exists())

    @patch("core.services.recheck.process_account", return_value=[])
    def test_future_rows_untouched(self, proc):
        p = _profile()
        self._row(p, 99, tier=0, due_offset=timedelta(hours=1))  # not due yet
        self.assertEqual(recheck.run_due_rechecks(), 0)
        proc.assert_not_called()
        self.assertEqual(ActivityRecheck.objects.get(activity_id=99).tier, 0)

    @patch("core.services.recheck.process_account", return_value=[])
    def test_batches_activities_per_profile(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        self._row(p, 100, tier=0)
        recheck.run_due_rechecks()
        proc.assert_called_once()
        activities = proc.call_args.kwargs["activities"]
        self.assertEqual({a.identifier for a in activities}, {99, 100})

    @patch("core.services.recheck.process_account", side_effect=Exception("boom"))
    def test_failed_batch_leaves_rows(self, proc):
        p = _profile()
        self._row(p, 99, tier=0)
        with self.assertLogs("core.services.recheck", level="ERROR"):
            recheck.run_due_rechecks()
        self.assertTrue(ActivityRecheck.objects.filter(activity_id=99).exists())
        self.assertEqual(ActivityRecheck.objects.get(activity_id=99).tier, 0)
