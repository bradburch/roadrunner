from datetime import datetime, timezone
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from core.models import Profile


class OAuthViewTests(TestCase):
    def test_connect_redirects_to_strava(self):
        resp = self.client.get(reverse("core:connect"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("strava.com/oauth/authorize", resp["Location"])
        self.assertIn("state=", resp["Location"])

    @patch("core.views.strava.exchange_code")
    def test_callback_creates_profile_and_logs_in(self, exchange):
        exchange.return_value = {
            "access_token": "a", "refresh_token": "r",
            "expires_at": int(datetime.now(tz=timezone.utc).timestamp()) + 3600,
            "athlete": {"id": 7, "firstname": "Wile", "lastname": "Coyote"},
        }
        # seed state into the session the way connect would
        session = self.client.session
        session["oauth_state"] = "xyz"
        session.save()
        resp = self.client.get(reverse("core:callback"), {"code": "C", "state": "xyz"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Profile.objects.filter(strava_athlete_id=7).exists())
        self.assertIn("_auth_user_id", self.client.session)

    @patch("core.views.strava.exchange_code")
    def test_callback_consumes_state(self, exchange):
        exchange.return_value = {
            "access_token": "a", "refresh_token": "r",
            "expires_at": int(datetime.now(tz=timezone.utc).timestamp()) + 3600,
            "athlete": {"id": 42, "firstname": "Road", "lastname": "Runner"},
        }
        session = self.client.session
        session["oauth_state"] = "abc"
        session.save()
        self.client.get(reverse("core:callback"), {"code": "C", "state": "abc"})
        self.assertNotIn("oauth_state", self.client.session)

    def test_callback_handles_user_cancel(self):
        resp = self.client.get(reverse("core:callback"), {"error": "access_denied"})
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse("core:landing"))
        self.assertFalse(Profile.objects.exists())

    def test_callback_rejects_bad_state(self):
        session = self.client.session
        session["oauth_state"] = "right"
        session.save()
        resp = self.client.get(reverse("core:callback"), {"code": "C", "state": "wrong"})
        self.assertEqual(resp.status_code, 400)
