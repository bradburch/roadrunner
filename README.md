# eBird-Strava

Automatically updates your Strava activity descriptions with the birds observed during a corresponding eBird checklist. When the date and time of a Strava activity overlaps with an eBird checklist, the activity description is updated with the species and counts from that checklist.

## How It Works

1. Fetches your recent eBird checklists
2. Fetches your 5 most recent Strava activities
3. Matches them by date and time overlap
4. For each match, updates the Strava activity description with the observed species and counts

Multiple eBird checklists can match a single Strava activity (e.g. two short walks on the same day). In that case, the species counts are merged. Checklists without a recorded duration are skipped.

## Prerequisites

- [Docker](https://www.docker.com/)
- An [eBird API token](https://ebird.org/data/download)
- A [Strava API application](https://developers.strava.com/docs/) with a refresh token

## Setup

Create a `config.ini` file in the project root. This file is excluded from version control — do not commit it.

```ini
[ebird]
ebird_api_token =
ebird_profile_id =

[strava]
strava_refresh_token =
strava_access_token =
strava_client_id =
strava_client_secret =
```

### eBird credentials

- **`ebird_api_token`** — Request a free token at https://ebird.org/data/download
- **`ebird_profile_id`** — Found in your eBird profile URL, directly after `/profile/`. Your profile must be set to public.

  Example: `https://ebird.org/profile/`**`MzkyNjAwNA`**

### Strava credentials

- **`strava_client_id`** and **`strava_client_secret`** — Create an API application at https://www.strava.com/settings/api
- **`strava_refresh_token`** and **`strava_access_token`** — Follow the [Strava OAuth flow](https://developers.strava.com/docs/authentication/) to obtain these tokens. The app will automatically refresh and persist the access token on each run.

## Running

### Docker (recommended)

Build the Docker image:

```bash
docker build -t ebird-strava .
```

Run it:

```bash
docker run -v $(pwd)/config.ini:/usr/app/src/config.ini ebird-strava
```

Mounting `config.ini` as a volume ensures the refreshed Strava access token is saved back to your local file between runs.

### Python

```bash
pip install -r requirements.txt
python main.py
```

## Output

On a successful match you will see the bird list printed to the console, followed by a confirmation:

```
3 American Robin
2 Black-capped Chickadee
1 White-breasted Nuthatch

Updated Strava activity 12345678: https://www.strava.com/activities/12345678
```

The Strava activity description is written as:

```
Birds seen during activity:
3 American Robin
2 Black-capped Chickadee
1 White-breasted Nuthatch
```

If no eBird checklists overlap with any recent Strava activities, the script will print:

```
No matching Strava activities and eBird checklists!
```
