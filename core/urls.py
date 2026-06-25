from django.urls import path
from . import views

app_name = "core"
urlpatterns = [
    path("", views.landing, name="landing"),
    path("strava/connect", views.connect, name="connect"),
    path("strava/callback", views.callback, name="callback"),
    path("dashboard", views.dashboard, name="dashboard"),
    path("ebird-profile", views.ebird_profile, name="ebird_profile"),
    path("inaturalist-profile", views.inaturalist_profile, name="inaturalist_profile"),
    path("sync", views.sync_now, name="sync"),
    path("webhook", views.webhook, name="webhook"),
]
