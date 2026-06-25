from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase
from core.services import inaturalist
from core.services.timespan import IdDates


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    m.status_code = 200
    return m


def _activity():
    base = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
    return IdDates(99, base, base + timedelta(hours=1))


class CollectSpeciesTests(SimpleTestCase):
    @patch("core.services.inaturalist.requests.get")
    def test_filters_observations_to_window(self, get):
        get.return_value = _resp({"results": [
            {"time_observed_at": "2026-06-01T07:30:00+00:00",
             "taxon": {"preferred_common_name": "Western Fence Lizard",
                       "name": "Sceloporus occidentalis"}},
            {"time_observed_at": "2026-06-01T09:30:00+00:00",  # outside the window
             "taxon": {"preferred_common_name": "Mallard", "name": "Anas platyrhynchos"}},
        ]})
        self.assertEqual(
            inaturalist.collect_species("me", [_activity()]),
            {99: {"Western Fence Lizard": ""}},
        )

    @patch("core.services.inaturalist.requests.get")
    def test_falls_back_to_scientific_name(self, get):
        get.return_value = _resp({"results": [
            {"time_observed_at": "2026-06-01T07:30:00+00:00",
             "taxon": {"name": "Sceloporus occidentalis"}},
        ]})
        self.assertEqual(
            inaturalist.collect_species("me", [_activity()]),
            {99: {"Sceloporus occidentalis": ""}},
        )

    @patch("core.services.inaturalist.requests.get")
    def test_skips_observation_without_time(self, get):
        get.return_value = _resp({"results": [
            {"observed_on": "2026-06-01", "taxon": {"name": "Sceloporus occidentalis"}},
        ]})
        self.assertEqual(inaturalist.collect_species("me", [_activity()]), {})

    @patch("core.services.inaturalist.requests.get")
    def test_no_activities_makes_no_request(self, get):
        self.assertEqual(inaturalist.collect_species("me", []), {})
        get.assert_not_called()
