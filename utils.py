from id_dates import IdDates
from requests import request, Response


def connection(method: str, url: str, headers: dict | None = None, data: dict | None = None) -> Response:
    headers = headers or {}
    data = data or {}

    response = request(method, url, headers=headers, data=data, timeout=30)

    if response.status_code == 200:
        return response
    else:
        print('ERROR')
        print(response.json())
        return response


def compare(strava: IdDates, ebird: IdDates) -> bool:

    latest_start = max(strava.start_date, ebird.start_date)
    earliest_end = min(strava.end_date, ebird.end_date)

    return earliest_end > latest_start


def add_dict(current: dict, new: dict):

    new_dict = current.copy()

    for k, v in new.items():
        if k in new_dict:
            if v.isnumeric() and new_dict[k].isnumeric():
                new_value = int(v) + int(new_dict[k])
                new_dict[k] = str(new_value)
            elif not v.isnumeric() or not new_dict[k].isnumeric():
                new_dict[k] = 'X'
        else:
            new_dict[k] = v

    return new_dict
