import secrets
from datetime import datetime, timezone
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from urllib.parse import urlencode
from .models import Profile
from .services import strava

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"


def landing(request):
    return render(request, "core/landing.html")


def connect(request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "redirect_uri": request.build_absolute_uri("/strava/callback"),
        "response_type": "code",
        "scope": "activity:read_all,activity:write",
        "state": state,
    }
    return redirect(f"{STRAVA_AUTH_URL}?{urlencode(params)}")


def callback(request):
    if request.GET.get("state") != request.session.get("oauth_state"):
        return HttpResponseBadRequest("Invalid OAuth state")
    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing code")

    data = strava.exchange_code(code)
    athlete = data["athlete"]
    user, _ = User.objects.get_or_create(username=str(athlete["id"]))
    Profile.objects.update_or_create(
        strava_athlete_id=athlete["id"],
        defaults={
            "user": user,
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": datetime.fromtimestamp(data["expires_at"], tz=timezone.utc),
        },
    )
    login(request, user)
    return redirect("core:landing")
