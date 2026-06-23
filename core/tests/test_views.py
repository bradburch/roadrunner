from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.contrib.auth.models import User
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


class DashboardTests(TestCase):
    def _login(self):
        user = User.objects.create(username="7")
        Profile.objects.create(
            user=user, strava_athlete_id=7, access_token="a", refresh_token="r",
            expires_at=dj_timezone.now() + timedelta(hours=1),
        )
        self.client.force_login(user)
        return user

    def test_dashboard_requires_login(self):
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 302)  # redirected to login/landing

    def test_dashboard_shows_for_logged_in(self):
        self._login()
        resp = self.client.get(reverse("core:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "eBird")

    def test_save_ebird_profile(self):
        self._login()
        resp = self.client.post(reverse("core:ebird_profile"), {"ebird_profile_id": "ABC123"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Profile.objects.get(strava_athlete_id=7).ebird_profile_id, "ABC123")

    @patch("core.views.process_account", return_value=[99])
    def test_sync_invokes_process_account(self, proc):
        user = self._login()
        user.profile.ebird_profile_id = "ABC123"
        user.profile.save(update_fields=["ebird_profile_id"])
        resp = self.client.post(reverse("core:sync"))
        self.assertEqual(resp.status_code, 302)
        proc.assert_called_once()

    def test_ebird_profile_rejects_get(self):
        self._login()
        resp = self.client.get(reverse("core:ebird_profile"))
        self.assertEqual(resp.status_code, 405)

    def test_sync_rejects_get(self):
        self._login()
        resp = self.client.get(reverse("core:sync"))
        self.assertEqual(resp.status_code, 405)

    @patch("core.views.process_account")
    def test_sync_skips_when_no_ebird_id(self, proc):
        self._login()
        resp = self.client.post(reverse("core:sync"))
        self.assertEqual(resp.status_code, 302)
        proc.assert_not_called()


import json


class WebhookTests(TestCase):
    def test_get_verification_echoes_challenge(self):
        with self.settings(STRAVA_WEBHOOK_VERIFY_TOKEN="vt"):
            resp = self.client.get(reverse("core:webhook"), {
                "hub.mode": "subscribe", "hub.verify_token": "vt", "hub.challenge": "abc",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"hub.challenge": "abc"})

    def test_get_verification_rejects_bad_token(self):
        with self.settings(STRAVA_WEBHOOK_VERIFY_TOKEN="vt"):
            resp = self.client.get(reverse("core:webhook"), {
                "hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "abc",
            })
        self.assertEqual(resp.status_code, 403)

    @patch("core.views.process_account", return_value=[99])
    def test_post_create_event_processes_owner(self, proc):
        user = User.objects.create(username="7")
        Profile.objects.create(
            user=user, strava_athlete_id=7, access_token="a", refresh_token="r",
            expires_at=dj_timezone.now() + timedelta(hours=1), ebird_profile_id="P",
        )
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 99, "owner_id": 7}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        proc.assert_called_once()
        self.assertEqual(list(proc.call_args.args[1]), [99])

    @patch("core.views.process_account")
    def test_post_unknown_owner_is_noop_200(self, proc):
        body = {"object_type": "activity", "aspect_type": "create",
                "object_id": 1, "owner_id": 999}
        resp = self.client.post(reverse("core:webhook"), data=json.dumps(body),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        proc.assert_not_called()
