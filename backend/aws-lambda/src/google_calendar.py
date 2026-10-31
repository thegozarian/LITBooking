"""Small Google Calendar API client for the booking Lambda."""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import boto3


SSM = boto3.client("ssm")
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
HTTP_TIMEOUT_SECONDS = 8
_access_token: str | None = None
_access_token_expires_at = 0.0


class GoogleCalendarError(Exception):
    """A sanitized configuration or Google Calendar API error."""


@lru_cache(maxsize=1)
def _oauth_credentials() -> dict[str, str]:
    parameter_name = os.environ.get("GOOGLE_CALENDAR_OAUTH_PARAMETER", "")
    if not parameter_name:
        raise GoogleCalendarError("Google Calendar OAuth is not configured")

    response = SSM.get_parameter(Name=parameter_name, WithDecryption=True)
    try:
        credentials = json.loads(response["Parameter"]["Value"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise GoogleCalendarError("Google Calendar OAuth configuration is invalid") from exc

    if not isinstance(credentials, dict) or not all(
        isinstance(credentials.get(key), str) and credentials[key]
        for key in ("client_id", "client_secret", "refresh_token")
    ):
        raise GoogleCalendarError("Google Calendar OAuth configuration is incomplete")
    return credentials


def _post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    *,
    conflict_event_id: str | None = None,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read())
    except HTTPError as exc:
        if exc.code == 409 and conflict_event_id:
            return {"id": conflict_event_id}
        raise GoogleCalendarError(
            f"Google Calendar request failed with HTTP {exc.code}"
        ) from None
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GoogleCalendarError("Google Calendar request could not be completed") from exc

    if not isinstance(result, dict):
        raise GoogleCalendarError("Google Calendar returned an invalid response")
    return result


def _get_access_token() -> str:
    global _access_token, _access_token_expires_at

    now = time.monotonic()
    if _access_token and now < _access_token_expires_at - 60:
        return _access_token

    credentials = _oauth_credentials()
    body = urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": credentials["client_id"],
            "client_secret": credentials["client_secret"],
            "refresh_token": credentials["refresh_token"],
        }
    ).encode("utf-8")
    request = Request(
        TOKEN_URL,
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            token_response = json.loads(response.read())
    except HTTPError as exc:
        raise GoogleCalendarError(
            f"Google OAuth token refresh failed with HTTP {exc.code}"
        ) from None
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GoogleCalendarError("Google OAuth token refresh could not be completed") from exc

    token = token_response.get("access_token") if isinstance(token_response, dict) else None
    expires_in = token_response.get("expires_in", 3600) if isinstance(token_response, dict) else 0
    if not isinstance(token, str) or not token:
        raise GoogleCalendarError("Google OAuth returned no access token")

    _access_token = token
    _access_token_expires_at = time.monotonic() + int(expires_in)
    return token


def _calendar_url(calendar_id: str, collection: str) -> str:
    return f"{CALENDAR_API}/calendars/{quote(calendar_id, safe='')}/{collection}"


def create_calendar(summary: str, time_zone: str = "Europe/Dublin") -> str:
    """Create an app-owned secondary calendar and return its ID."""
    response = _post_json(
        f"{CALENDAR_API}/calendars",
        {"summary": summary, "timeZone": time_zone},
        {"authorization": f"Bearer {_get_access_token()}"},
    )
    calendar_id = response.get("id")
    if not isinstance(calendar_id, str) or not calendar_id:
        raise GoogleCalendarError("Google Calendar returned no calendar ID")
    return calendar_id


def get_busy_periods(time_min: str, time_max: str) -> list[dict[str, str]]:
    """Return busy intervals without exposing event titles or descriptions."""
    booking_calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    configured_ids = os.environ.get("GOOGLE_CALENDAR_AVAILABILITY_IDS", "")
    if configured_ids:
        calendar_ids = list(dict.fromkeys(
            calendar_id.strip()
            for calendar_id in configured_ids.split(",")
            if calendar_id.strip()
        ))
    else:
        calendar_ids = ["primary"]
        if booking_calendar_id != "primary":
            calendar_ids.append(booking_calendar_id)

    response = _post_json(
        f"{CALENDAR_API}/freeBusy",
        {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": calendar_id} for calendar_id in calendar_ids],
        },
        {"authorization": f"Bearer {_get_access_token()}"},
    )

    calendars = response.get("calendars", {})
    if not isinstance(calendars, dict):
        raise GoogleCalendarError("Google Calendar returned an invalid calendar list")

    busy_periods = []
    for calendar_id in calendar_ids:
        calendar = calendars.get(calendar_id)
        if not isinstance(calendar, dict):
            raise GoogleCalendarError("Google Calendar omitted a requested calendar")
        if calendar.get("errors"):
            raise GoogleCalendarError("Google Calendar could not check a requested calendar")
        busy = calendar.get("busy", [])
        if not isinstance(busy, list):
            raise GoogleCalendarError("Google Calendar returned an invalid busy-period list")
        busy_periods.extend(
            {"calendarId": calendar_id, "start": item["start"], "end": item["end"]}
            for item in busy
            if isinstance(item, dict)
            and isinstance(item.get("start"), str)
            and isinstance(item.get("end"), str)
        )
    return busy_periods


def create_event(event: dict[str, Any], event_id: str) -> dict[str, str]:
    """Create a calendar event and return only its stable ID."""
    if not isinstance(event_id, str) or len(event_id) != 64 or any(char not in "0123456789abcdef" for char in event_id):
        raise GoogleCalendarError("Google Calendar event ID is invalid")
    calendar_id = os.environ.get("GOOGLE_CALENDAR_ID", "primary")
    event = {**event, "id": event_id}
    response = _post_json(
        f"{_calendar_url(calendar_id, 'events')}?{urlencode({'sendUpdates': 'all'})}",
        event,
        {"authorization": f"Bearer {_get_access_token()}"},
        conflict_event_id=event_id,
    )
    event_id = response.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise GoogleCalendarError("Google Calendar returned no event ID")
    return {"id": event_id}
