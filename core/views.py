import json
import logging
import re
import secrets
from datetime import datetime, timezone
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponseBadRequest, JsonResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone as dj_timezone
from django.utils.html import format_html, format_html_join
from django.views.decorators.csrf import csrf_exempt
from urllib.parse import urlencode
from .models import Profile
from .services import strava
from .services.sync import process_account

logger = logging.getLogger(__name__)

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
WEBHOOK_COOLDOWN_SECONDS = 30


def landing(request):
    return render(request, "core/landing.html")


def connect(request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    params = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "redirect_uri": request.build_absolute_uri(reverse("core:callback")),
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
    # Strava returns the athlete's name in the token response; keep it fresh.
    user.first_name = (athlete.get("firstname") or "")[:150]
    user.last_name = (athlete.get("lastname") or "")[:150]
    user.save(update_fields=["first_name", "last_name"])
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
    raw = request.POST.get("ebird_profile_id", "").strip()
    # Accept a pasted profile URL by keeping only the ID segment after /profile/.
    if "/profile/" in raw:
        raw = raw.split("/profile/", 1)[1]
    profile_id = raw.strip("/").split("?", 1)[0].split("/", 1)[0].strip()
    # eBird profile IDs are case-sensitive base64-ish tokens — validate without
    # altering case.
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", profile_id):
        messages.error(request, "That doesn't look like a valid eBird profile ID.")
        return redirect("core:dashboard")
    profile.ebird_profile_id = profile_id
    profile.save(update_fields=["ebird_profile_id"])
    messages.success(request, "eBird profile saved.")
    return redirect("core:dashboard")


@login_required
@require_POST
def inaturalist_profile(request):
    profile = request.user.profile
    raw = request.POST.get("inaturalist_user_id", "").strip()
    # Accept a pasted profile URL by keeping only the login after /people/.
    if "/people/" in raw:
        raw = raw.split("/people/", 1)[1]
    user_id = raw.strip("/").split("?", 1)[0].split("/", 1)[0].strip()
    if user_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", user_id):
        messages.error(request, "That doesn't look like a valid iNaturalist username.")
        return redirect("core:dashboard")
    profile.inaturalist_user_id = user_id
    profile.save(update_fields=["inaturalist_user_id"])
    messages.success(request, "iNaturalist profile saved.")
    return redirect("core:dashboard")


@login_required
@require_POST
def sync_now(request):
    profile = request.user.profile
    if not (profile.ebird_profile_id or profile.inaturalist_user_id):
        messages.error(request, "Link an eBird or iNaturalist profile first.")
        return redirect("core:dashboard")
    updated = process_account(profile)
    if updated:
        # base.html renders messages with |safe, so HTML must come from
        # format_html (escapes args); never pass raw user input into a message.
        links = format_html_join(
            ", ",
            '<a href="https://www.strava.com/activities/{0}" target="_blank" rel="noopener">{0}</a>',
            ((activity_id,) for activity_id in updated),
        )
        messages.success(
            request, format_html("Updated {} activity(ies): {}", len(updated), links)
        )
    else:
        messages.info(request, "No matching observations found for recent activities.")
    return redirect("core:dashboard")


@csrf_exempt
def webhook(request):
    if request.method == "GET":
        if request.GET.get("hub.verify_token") != settings.STRAVA_WEBHOOK_VERIFY_TOKEN:
            return HttpResponseForbidden("bad verify token")
        return JsonResponse({"hub.challenge": request.GET.get("hub.challenge")})

    try:
        event = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        event = {}
    if event.get("object_type") == "activity" and event.get("aspect_type") in ("create", "update"):
        profile = Profile.objects.filter(strava_athlete_id=event.get("owner_id")).first()
        if profile and (profile.ebird_profile_id or profile.inaturalist_user_id):
            if (
                profile.last_webhook_at is not None
                and (dj_timezone.now() - profile.last_webhook_at).total_seconds() < WEBHOOK_COOLDOWN_SECONDS
            ):
                return JsonResponse({"status": "throttled"})
            profile.last_webhook_at = dj_timezone.now()
            profile.save(update_fields=["last_webhook_at"])
            try:
                process_account(profile, [event["object_id"]])
            except Exception:
                logger.exception("Webhook processing failed for athlete %s", event.get("owner_id"))
    return JsonResponse({"status": "ok"})
