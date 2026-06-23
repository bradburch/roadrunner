from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from core.models import Profile


class ProfileTests(TestCase):
    def _profile(self, **kw):
        user = User.objects.create(username=str(kw.get("strava_athlete_id", 1)))
        defaults = dict(
            user=user, strava_athlete_id=1, access_token="a",
            refresh_token="r", expires_at=timezone.now() + timedelta(hours=1),
        )
        defaults.update(kw)
        return Profile.objects.create(**defaults)

    def test_token_not_expired_when_future(self):
        self.assertFalse(self._profile().token_expired())

    def test_token_expired_when_past(self):
        p = self._profile(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertTrue(p.token_expired())
