from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from django.test import SimpleTestCase
from core.services import ebird
from core.services.timespan import IdDates


def _resp(json_data):
    m = MagicMock()
    m.json.return_value = json_data
    m.status_code = 200
    return m


class EbirdTests(SimpleTestCase):
    @patch("core.services.ebird.requests.request")
    def test_get_recent_checklists_parses_ids_and_dates(self, req):
        req.return_value = _resp([
            {"subId": "S1", "isoObsDate": "2026-06-01 07:00"},
        ])
        result = ebird.get_recent_checklists("PROF")
        self.assertEqual(result[0].identifier, "S1")
        self.assertEqual(result[0].start_date.tzinfo, timezone.utc)

    @patch("core.services.ebird.requests.request")
    def test_get_dates_observation_returns_none_without_duration(self, req):
        req.return_value = _resp({"obs": []})  # no durationHrs
        idd = IdDates("S1", datetime(2026, 6, 1, 7, tzinfo=timezone.utc))
        self.assertEqual(ebird.get_dates_observation(idd), (None, None))

    @patch("core.services.ebird.requests.request")
    def test_get_dates_observation_computes_end(self, req):
        req.return_value = _resp({"durationHrs": 1.0, "obs": [{"x": 1}]})
        start = datetime(2026, 6, 1, 7, tzinfo=timezone.utc)
        end, obs = ebird.get_dates_observation(IdDates("S1", start))
        self.assertEqual((end - start).total_seconds(), 3600)
        self.assertEqual(obs, [{"x": 1}])

    @patch("core.services.ebird.requests.get")
    def test_build_bird_dict_maps_codes_to_names(self, get):
        get.return_value = _resp([{"speciesCode": "amerob", "comName": "American Robin"}])
        obs = [{"speciesCode": "amerob", "howManyStr": "3"}]
        self.assertEqual(ebird.build_bird_dict(obs), {"American Robin": "3"})

    @patch("core.services.ebird.requests.get")
    def test_build_bird_dict_empty_returns_empty(self, get):
        result = ebird.build_bird_dict([])
        self.assertEqual(result, {})
        get.assert_not_called()
