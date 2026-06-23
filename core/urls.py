from django.urls import path
from . import views

app_name = "core"
urlpatterns = [
    path("", views.landing, name="landing"),
    path("strava/connect", views.connect, name="connect"),
    path("strava/callback", views.callback, name="callback"),
]
