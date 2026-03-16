from id_dates import IdDates
from requests import Response
from utils import connection

import configparser
import datetime

config = configparser.ConfigParser()
config.read('config.ini')
strava_config = config['strava']

def refresh() -> None:

    path = "oauth/token"
    params = {
        "client_id": strava_config.get('strava_client_id'),
        "client_secret": strava_config.get('strava_client_secret'),
        "refresh_token": strava_config.get('strava_refresh_token'),
        "grant_type": "refresh_token"
    }

    url = __create_url(path, params)
    resp = connection("POST", url)
    __update_config(resp.json())


def get_recent_activities() -> dict[datetime.date, list[IdDates]]:

    path = "activities"
    params = {
        "per_page": "5",
        "page": "1",
    }
    headers = {"Authorization": f"Bearer {strava_config.get('strava_access_token')}"}

    url = __create_url(path, params)
    resp = connection("GET", url, headers=headers)
    respJson = resp.json()

    activity_list = __create_activity_list(respJson)
    
    return activity_list


def update_activity(id: str, bird_list: str) -> Response:

    title = "Birds seen during activity:"
    description = f"{title}\n" + bird_list

    data = {
        "description": description
    }

    path = "activities"
    headers = {"Authorization": f"Bearer {strava_config.get('strava_access_token')}"}

    url = __create_url(path, {}, id)
    resp = connection("PUT", url, headers=headers, data=data)
    
    return resp


def __create_activity_list(activities: list) -> dict[datetime.date, list[IdDates]]:
    
    start_activity = {}

    for activity in activities:
        id = activity["id"]
        start_date_local = activity["start_date_local"]
        strava_start_date = datetime.datetime.fromisoformat(start_date_local)
        elapsed_time = activity["elapsed_time"]
        end_date = __calculate_end_time(strava_start_date, elapsed_time)
        act = IdDates(id, strava_start_date, end_date)

        start_activity.setdefault(strava_start_date.date(), []).append(act)

    return start_activity


def __calculate_end_time(start_date, elapsed_time) -> datetime.datetime:

    delta = datetime.timedelta(seconds=elapsed_time)
    end_date = start_date + delta
    
    return end_date


def __create_url(path: str, params: dict, id: str = None) -> str:

    strava_api_url = "https://www.strava.com/api/v3/"
    params_list = "&".join("{}={}".format(key, value) for key, value in params.items())

    url = f"{strava_api_url}{path}"
    if id: 
        url = f"{url}/{id}?{params_list}"
    else:
        url = f"{url}?{params_list}"

    return url


def __update_config(respJson: dict) -> None:

    config.set('strava', 'strava_access_token', respJson["access_token"])
    config.set('strava', 'strava_refresh_token', respJson["refresh_token"])

    with open('config.ini', 'w') as configfile:
        config.write(configfile)
