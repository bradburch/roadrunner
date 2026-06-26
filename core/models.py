from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    strava_athlete_id = models.BigIntegerField(unique=True)
    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    ebird_profile_id = models.CharField(max_length=64, blank=True)
    inaturalist_user_id = models.CharField(max_length=64, blank=True)
    last_webhook_at = models.DateTimeField(null=True, blank=True)

    def token_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def __str__(self) -> str:
        return f"Profile(athlete={self.strava_athlete_id})"


class ActivityRecheck(models.Model):
    """A queued late-checklist re-check for one Strava activity.

    Created only when a webhook sync found no overlapping observation. A Vercel
    Cron job drains due rows (see core.services.recheck). `tier` is the index of
    the next offset to fire; the row is deleted on success or after the last
    offset, so most rows live at most 8 hours.
    """

    # 2/4/8h ladder, measured from `created_at` (when we first saw no data).
    OFFSETS = [timedelta(hours=2), timedelta(hours=4), timedelta(hours=8)]

    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    activity_id = models.BigIntegerField()
    # Cached activity window so rechecks need no Strava read (fail fast).
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
    tier = models.PositiveSmallIntegerField(default=0)
    due_at = models.DateTimeField(db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "activity_id"], name="uniq_pending_recheck"
            )
        ]

    def __str__(self) -> str:
        return f"ActivityRecheck(activity={self.activity_id}, tier={self.tier})"
