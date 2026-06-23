import secrets
from datetime import datetime, timezone
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from urllib.parse import urlencode
from .models import Profile
from .services import strava
from .services.sync import process_account

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
    if request.GET.get("error"):
        messages.error(request, "Strava authorization was cancelled.")
        return redirect("core:landing")

    if request.GET.get("state") != request.session.get("oauth_state"):
        return HttpResponseBadRequest("Invalid OAuth state")

    request.session.pop("oauth_state", None)

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
    return redirect("core:dashboard")


@login_required
def dashboard(request):
    profile = request.user.profile
    return render(request, "core/dashboard.html", {"profile": profile})


@login_required
@require_POST
def ebird_profile(request):
    profile = request.user.profile
    profile.ebird_profile_id = request.POST.get("ebird_profile_id", "").strip()
    profile.save(update_fields=["ebird_profile_id"])
    messages.success(request, "eBird profile saved.")
    return redirect("core:dashboard")


@login_required
@require_POST
def sync_now(request):
    profile = request.user.profile
    if not profile.ebird_profile_id:
        messages.error(request, "Set your eBird profile ID first.")
        return redirect("core:dashboard")
    updated = process_account(profile)
    if updated:
        messages.success(request, f"Updated {len(updated)} activity(ies).")
    else:
        messages.info(request, "No matching checklists found for recent activities.")
    return redirect("core:dashboard")


import json as _json
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def webhook(request):
    if request.method == "GET":
        if request.GET.get("hub.verify_token") != settings.STRAVA_WEBHOOK_VERIFY_TOKEN:
            return HttpResponseForbidden("bad verify token")
        return JsonResponse({"hub.challenge": request.GET.get("hub.challenge")})

    event = _json.loads(request.body or b"{}")
    if event.get("object_type") == "activity" and event.get("aspect_type") in ("create", "update"):
        profile = Profile.objects.filter(strava_athlete_id=event.get("owner_id")).first()
        if profile and profile.ebird_profile_id:
            process_account(profile, [event["object_id"]])
    return JsonResponse({"status": "ok"})
