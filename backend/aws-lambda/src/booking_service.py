"""Business rules and atomic time-slot holds for LIT Booking."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from datetime import date, datetime, time as day_time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from google_calendar import GoogleCalendarError, get_busy_periods


TIMEZONE_NAME = "Europe/Dublin"
LOCAL_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
SLOT_MINUTES = 30
DDB = boto3.client("dynamodb")
SERIALIZER = TypeSerializer()
DESERIALIZER = TypeDeserializer()


class BookingServiceError(Exception):
    """A sanitized booking configuration or storage failure."""


class InvalidBookingRequest(BookingServiceError):
    """A requested booking type or slot does not satisfy policy."""


class SlotUnavailable(BookingServiceError):
    """The chosen slot was taken by another booking or hold."""


@lru_cache(maxsize=1)
def _policy() -> dict[str, Any]:
    try:
        with Path(__file__).with_name("booking-policy.json").open(encoding="utf-8") as source:
            policy = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise BookingServiceError("Booking policy is unavailable") from exc

    if policy.get("timezone") != TIMEZONE_NAME:
        raise BookingServiceError("Booking policy timezone is invalid")
    return policy


@lru_cache(maxsize=1)
def _table_name() -> str:
    name = os.environ.get("BOOKINGS_TABLE", "")
    if not name:
        raise BookingServiceError("Booking state storage is not configured")
    return name


def _parse_datetime(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidBookingRequest("Invalid date or time") from exc
    if result.tzinfo is None:
        raise InvalidBookingRequest("A timezone is required")
    return result.astimezone(timezone.utc)


def _easter_sunday(year: int) -> date:
    """Return Gregorian Easter Sunday using the Meeus/Jones/Butcher algorithm."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _first_monday(year: int, month: int) -> date:
    current = date(year, month, 1)
    return current + timedelta(days=(7 - current.weekday()) % 7)


def _last_monday(year: int, month: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day - timedelta(days=last_day.weekday())


def _irish_public_holidays(year: int) -> set[date]:
    st_brigids = _first_monday(year, 2)
    february_first = date(year, 2, 1)
    if february_first.weekday() == 4:
        st_brigids = february_first

    return {
        date(year, 1, 1),
        st_brigids,
        date(year, 3, 17),
        _easter_sunday(year) + timedelta(days=1),
        _first_monday(year, 5),
        _first_monday(year, 6),
        _first_monday(year, 8),
        _last_monday(year, 10),
        date(year, 12, 25),
        date(year, 12, 26),
    }


def _booking_type_config(booking_type: str) -> dict[str, Any]:
    booking_types = _policy().get("booking_types", {})
    details = booking_types.get(booking_type) if isinstance(booking_types, dict) else None
    if not isinstance(details, dict):
        raise InvalidBookingRequest("Unknown booking type")
    return details


def _local_candidates(day: date, booking_type: str) -> list[datetime]:
    policy = _policy()
    availability = policy.get("availability", {})
    if day.weekday() >= 5:
        return []
    if (
        availability.get("excluded_holiday_region") == "IE"
        and day in _irish_public_holidays(day.year)
    ):
        return []

    details = _booking_type_config(booking_type)
    allowed_starts = details.get("allowed_start_times")
    if isinstance(allowed_starts, list):
        local_times = [day_time.fromisoformat(value) for value in allowed_starts]
    else:
        opened = day_time.fromisoformat(availability["open_time"])
        closed = day_time.fromisoformat(availability["close_time"])
        duration = int(details["duration_minutes"]) + int(details.get("buffer_minutes", 0))
        day_start = opened.hour * 60 + opened.minute
        latest = closed.hour * 60 + closed.minute - duration
        local_times = [
            day_time(hour=minutes // 60, minute=minutes % 60)
            for minutes in range(day_start, latest + 1, SLOT_MINUTES)
        ]

    return [datetime.combine(day, local_time, LOCAL_TIMEZONE) for local_time in local_times]


def _busy_intervals(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    try:
        periods = get_busy_periods(
            start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
    except GoogleCalendarError as exc:
        raise BookingServiceError("Calendar availability could not be read") from exc
    return [
        (_parse_datetime(item["start"]), _parse_datetime(item["end"]))
        for item in periods
    ]


def _lock_keys(start_local: datetime, blocked_minutes: int) -> list[dict[str, str]]:
    keys = []
    for offset in range(0, blocked_minutes, SLOT_MINUTES):
        segment = start_local + timedelta(minutes=offset)
        keys.append(
            {
                "pk": f"SLOT#{segment.date().isoformat()}",
                "sk": f"TIME#{segment:%H:%M}",
            }
        )
    return keys


def _batch_get(keys: list[dict[str, str]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    unique_keys = list({(item["pk"], item["sk"]): item for item in keys}.values())
    remaining = unique_keys
    attempts = 0
    while remaining:
        attempts += 1
        response = DDB.batch_get_item(
            RequestItems={
                _table_name(): {
                    "Keys": [
                        {key: SERIALIZER.serialize(value) for key, value in item.items()}
                        for item in remaining[:100]
                    ],
                    "ConsistentRead": True,
                }
            }
        )
        found.extend(
            {
                key: DESERIALIZER.deserialize(value)
                for key, value in item.items()
            }
            for item in response.get("Responses", {}).get(_table_name(), [])
        )
        remaining = [
            {
                key: DESERIALIZER.deserialize(value)
                for key, value in item.items()
            }
            for item in response.get("UnprocessedKeys", {}).get(_table_name(), {}).get("Keys", [])
        ]
        if remaining and attempts >= 5:
            raise BookingServiceError("Booking availability could not be read")
        if remaining:
            time.sleep(0.05 * (2 ** (attempts - 1)))
    return found


def _active_lock_items(keys: list[dict[str, str]], now_epoch: int) -> list[dict[str, Any]]:
    if not keys:
        return []
    try:
        return [
            item
            for item in _batch_get(keys)
            if not item.get("expiresAt") or int(item["expiresAt"]) > now_epoch
        ]
    except ClientError as exc:
        raise BookingServiceError("Booking availability could not be read") from exc


def get_available_slots(
    booking_type: str,
    time_min: str,
    time_max: str,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return every in-policy candidate with its current availability state."""
    policy = _policy()
    availability = policy.get("availability", {})
    start = _parse_datetime(time_min)
    end = _parse_datetime(time_max)
    if end <= start or end - start > timedelta(days=int(availability.get("calendar_view_days", 7)) + 1):
        raise InvalidBookingRequest("Invalid availability range")

    details = _booking_type_config(booking_type)
    duration = int(details["duration_minutes"])
    buffer = int(details.get("buffer_minutes", 0))
    blocked_minutes = duration + buffer
    if duration <= 0 or blocked_minutes % SLOT_MINUTES:
        raise BookingServiceError("Booking duration must align to the slot interval")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    minimum_start = current + timedelta(hours=int(availability.get("minimum_notice_hours", 24)))
    maximum_start = current + timedelta(days=int(availability.get("advance_booking_days", 30)))
    local_first_day = start.astimezone(LOCAL_TIMEZONE).date()
    local_last_day = (end - timedelta(microseconds=1)).astimezone(LOCAL_TIMEZONE).date()

    candidates: list[tuple[datetime, datetime, datetime, list[dict[str, str]]]] = []
    day = local_first_day
    while day <= local_last_day:
        for local_start in _local_candidates(day, booking_type):
            start_utc = local_start.astimezone(timezone.utc)
            end_local = local_start + timedelta(minutes=duration)
            block_end_local = local_start + timedelta(minutes=blocked_minutes)
            block_end_utc = block_end_local.astimezone(timezone.utc)
            if start_utc < minimum_start or start_utc > maximum_start:
                continue
            if start_utc < start or block_end_utc > end:
                continue
            candidates.append((local_start, end_local, block_end_local, _lock_keys(local_start, blocked_minutes)))
        day += timedelta(days=1)

    if not candidates:
        return []

    query_start = min(item[0] for item in candidates).astimezone(timezone.utc)
    query_end = max(item[2] for item in candidates).astimezone(timezone.utc)
    busy = _busy_intervals(query_start, query_end)

    all_slot_keys = [key for candidate in candidates for key in candidate[3]]
    lock_items = _active_lock_items(all_slot_keys, int(current.timestamp()))
    locked_keys = {(item["pk"], item["sk"]) for item in lock_items}

    site_capacity: set[tuple[str, str]] = set()
    if booking_type == "site_visit":
        capacity_keys = [
            {"pk": f"CAPACITY#{candidate[0].date().isoformat()}", "sk": f"SITE_VISIT#{index}"}
            for candidate in candidates
            for index in (1, 2)
        ]
        capacity_items = _active_lock_items(capacity_keys, int(current.timestamp()))
        site_capacity = {(item["pk"], item["sk"]) for item in capacity_items}

    slots: list[dict[str, Any]] = []
    for local_start, local_end, block_end, slot_keys in candidates:
        start_utc = local_start.astimezone(timezone.utc)
        block_end_utc = block_end.astimezone(timezone.utc)
        overlaps_calendar = any(
            start_utc < busy_end and block_end_utc > busy_start
            for busy_start, busy_end in busy
        )
        overlaps_hold = any((key["pk"], key["sk"]) in locked_keys for key in slot_keys)
        site_capacity_full = False
        if booking_type == "site_visit":
            capacity_pk = f"CAPACITY#{local_start.date().isoformat()}"
            site_capacity_full = all(
                (capacity_pk, f"SITE_VISIT#{index}") in site_capacity
                for index in (1, 2)
            )
        slots.append(
            {
                "start": local_start.isoformat(),
                "end": local_end.isoformat(),
                "available": not (overlaps_calendar or overlaps_hold or site_capacity_full),
            }
        )
    return slots


def _serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: SERIALIZER.serialize(value) for key, value in item.items()}


def _hold_token(secret: str, shop: str, hold_id: str, expires_at: int) -> str:
    message = f"{shop}|{hold_id}|{expires_at}".encode("utf-8")
    import hmac

    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _existing_hold(shop: str, request_hash: str, secret: str, now_epoch: int) -> dict[str, str] | None:
    try:
        response = DDB.get_item(
            TableName=_table_name(),
            Key=_serialize_item({"pk": f"REQUEST#{request_hash}", "sk": "META"}),
            ConsistentRead=True,
        )
        request_item = response.get("Item")
        if not request_item:
            return None
        request_item = {key: DESERIALIZER.deserialize(value) for key, value in request_item.items()}
        hold_id = request_item.get("holdId", "")
        hold_response = DDB.get_item(
            TableName=_table_name(),
            Key=_serialize_item({"pk": f"HOLD#{hold_id}", "sk": "META"}),
            ConsistentRead=True,
        )
        raw_hold = hold_response.get("Item")
        if not raw_hold:
            return None
        hold = {key: DESERIALIZER.deserialize(value) for key, value in raw_hold.items()}
        expires_at = int(hold.get("expiresAt", 0))
        if (
            expires_at <= now_epoch
            or hold.get("shop") != shop
            or hold.get("status") != "HELD"
        ):
            return None
        return {
            "hold_id": hold_id,
            "hold_token": _hold_token(secret, shop, hold_id, expires_at),
            "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat().replace("+00:00", "Z"),
        }
    except ClientError as exc:
        raise BookingServiceError("Could not check the existing booking hold") from exc


def create_hold(
    booking_type: str,
    start_value: str,
    shop: str,
    request_id: str,
    secret: str,
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    if not shop or not re.fullmatch(r"[A-Za-z0-9._-]{16,80}", request_id):
        raise InvalidBookingRequest("Invalid booking request")

    try:
        start_utc = _parse_datetime(start_value)
    except InvalidBookingRequest:
        raise
    start_local = start_utc.astimezone(LOCAL_TIMEZONE)
    if start_local.second or start_local.microsecond or start_local.minute not in (0, 30):
        raise InvalidBookingRequest("The selected time is not on a booking interval")

    policy = _policy()
    details = _booking_type_config(booking_type)
    duration = int(details["duration_minutes"])
    buffer = int(details.get("buffer_minutes", 0))
    blocked_minutes = duration + buffer
    block_end_local = start_local + timedelta(minutes=blocked_minutes)
    if block_end_local.date() != start_local.date():
        raise InvalidBookingRequest("The selected time crosses a calendar day")

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_epoch = int(current.timestamp())
    expected_start = start_local.isoformat()
    request_hash = hashlib.sha256(
        f"{shop}|{request_id}|{booking_type}|{expected_start}".encode("utf-8")
    ).hexdigest()
    previous = _existing_hold(shop, request_hash, secret, now_epoch)
    if previous:
        return previous

    time_min = start_local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    time_max = (start_local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()
    try:
        offered = get_available_slots(
            booking_type,
            time_min,
            time_max,
            now=current,
        )
    except GoogleCalendarError as exc:
        raise BookingServiceError("Calendar availability could not be checked") from exc

    if not any(
        slot["start"] == expected_start and slot.get("available") is True
        for slot in offered
    ):
        raise SlotUnavailable("That time is no longer available")

    expires_at = now_epoch + int(policy.get("holds", {}).get("reservation_minutes", 15)) * 60
    hold_id = uuid.uuid4().hex
    slot_keys = _lock_keys(start_local, blocked_minutes)
    request_item = {
        "pk": f"REQUEST#{request_hash}",
        "sk": "META",
        "holdId": hold_id,
        "expiresAt": expires_at,
    }
    hold_item = {
        "pk": f"HOLD#{hold_id}",
        "sk": "META",
        "holdId": hold_id,
        "shop": shop,
        "requestHash": request_hash,
        "bookingType": booking_type,
        "start": expected_start,
        "end": (start_local + timedelta(minutes=duration)).isoformat(),
        "status": "HELD",
        "expiresAt": expires_at,
        "lockKeys": slot_keys,
        "requestKey": {"pk": f"REQUEST#{request_hash}", "sk": "META"},
    }
    transaction = [
        {
            "Put": {
                "TableName": _table_name(),
                "Item": _serialize_item(request_item),
                "ConditionExpression": "attribute_not_exists(#pk)",
                "ExpressionAttributeNames": {"#pk": "pk"},
            }
        },
        {
            "Put": {
                "TableName": _table_name(),
                "Item": _serialize_item(hold_item),
                "ConditionExpression": "attribute_not_exists(#pk)",
                "ExpressionAttributeNames": {"#pk": "pk"},
            }
        },
    ]
    for key in slot_keys:
        transaction.append(
            {
                "Put": {
                    "TableName": _table_name(),
                    "Item": _serialize_item(
                        {
                            **key,
                            "holdId": hold_id,
                            "status": "HELD",
                            "expiresAt": expires_at,
                        }
                    ),
                    "ConditionExpression": (
                        "attribute_not_exists(#pk) OR "
                        "(attribute_exists(#expiry) AND #expiry <= :now)"
                    ),
                    "ExpressionAttributeNames": {"#pk": "pk", "#expiry": "expiresAt"},
                    "ExpressionAttributeValues": {":now": SERIALIZER.serialize(now_epoch)},
                }
            }
        )

    capacity_slot: dict[str, str] | None = None
    if booking_type == "site_visit":
        capacity_pk = f"CAPACITY#{start_local.date().isoformat()}"
        capacity_slot = {"pk": capacity_pk, "sk": "SITE_VISIT#1"}
        hold_item["capacityKey"] = capacity_slot
        transaction[1]["Put"]["Item"] = _serialize_item(hold_item)
        transaction.append(
            {
                "Put": {
                    "TableName": _table_name(),
                    "Item": _serialize_item(
                        {**capacity_slot, "holdId": hold_id, "status": "HELD", "expiresAt": expires_at}
                    ),
                    "ConditionExpression": (
                        "attribute_not_exists(#pk) OR "
                        "(attribute_exists(#expiry) AND #expiry <= :now)"
                    ),
                    "ExpressionAttributeNames": {"#pk": "pk", "#expiry": "expiresAt"},
                    "ExpressionAttributeValues": {":now": SERIALIZER.serialize(now_epoch)},
                }
            }
        )

    try:
        DDB.transact_write_items(
            TransactItems=transaction,
            ClientRequestToken=request_hash[:32],
        )
    except DDB.exceptions.TransactionCanceledException:
        if booking_type == "site_visit" and capacity_slot:
            second_slot = {"pk": capacity_slot["pk"], "sk": "SITE_VISIT#2"}
            hold_item["capacityKey"] = second_slot
            transaction[1]["Put"]["Item"] = _serialize_item(hold_item)
            transaction[-1]["Put"]["Item"] = _serialize_item(
                {**second_slot, "holdId": hold_id, "status": "HELD", "expiresAt": expires_at}
            )
            try:
                DDB.transact_write_items(
                    TransactItems=transaction,
                    ClientRequestToken=f"{request_hash[:31]}2",
                )
            except DDB.exceptions.TransactionCanceledException:
                previous = _existing_hold(shop, request_hash, secret, now_epoch)
                if previous:
                    return previous
                raise SlotUnavailable("That time is no longer available") from None
            except ClientError as exc:
                raise BookingServiceError("Could not reserve the selected time") from exc
        else:
            previous = _existing_hold(shop, request_hash, secret, now_epoch)
            if previous:
                return previous
            raise SlotUnavailable("That time is no longer available") from None
    except ClientError as exc:
        raise BookingServiceError("Could not reserve the selected time") from exc

    return {
        "hold_id": hold_id,
        "hold_token": _hold_token(secret, shop, hold_id, expires_at),
        "expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def release_hold(shop: str, hold_id: str, token: str, secret: str) -> None:
    if not shop or not re.fullmatch(r"[a-f0-9]{32}", hold_id) or not re.fullmatch(r"[a-f0-9]{64}", token):
        raise InvalidBookingRequest("Invalid booking hold")

    try:
        response = DDB.get_item(
            TableName=_table_name(),
            Key=_serialize_item({"pk": f"HOLD#{hold_id}", "sk": "META"}),
            ConsistentRead=True,
        )
    except ClientError as exc:
        raise BookingServiceError("Could not read the booking hold") from exc
    raw_hold = response.get("Item")
    if not raw_hold:
        return
    hold = {key: DESERIALIZER.deserialize(value) for key, value in raw_hold.items()}
    expires_at = int(hold.get("expiresAt", 0))
    if hold.get("shop") != shop:
        raise InvalidBookingRequest("Invalid booking hold")
    expected_token = _hold_token(secret, shop, hold_id, expires_at)
    import hmac

    if not hmac.compare_digest(token, expected_token):
        raise InvalidBookingRequest("Invalid booking hold")
    if hold.get("status") != "HELD" or expires_at <= int(time.time()):
        return

    lock_keys = list(hold.get("lockKeys", []))
    capacity_key = hold.get("capacityKey")
    if isinstance(capacity_key, dict):
        lock_keys.append(capacity_key)
    transaction = [
        {
            "Delete": {
                "TableName": _table_name(),
                "Key": _serialize_item(key),
                "ConditionExpression": "#holdId = :holdId",
                "ExpressionAttributeNames": {"#holdId": "holdId"},
                "ExpressionAttributeValues": {":holdId": SERIALIZER.serialize(hold_id)},
            }
        }
        for key in lock_keys
    ]
    transaction.append(
        {
            "Update": {
                "TableName": _table_name(),
                "Key": _serialize_item({"pk": f"HOLD#{hold_id}", "sk": "META"}),
                "UpdateExpression": "SET #status = :released",
                "ConditionExpression": "#status = :held AND expiresAt > :now",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": {
                    ":released": SERIALIZER.serialize("RELEASED"),
                    ":held": SERIALIZER.serialize("HELD"),
                    ":now": SERIALIZER.serialize(int(time.time())),
                },
            }
        }
    )
    try:
        DDB.transact_write_items(TransactItems=transaction)
    except DDB.exceptions.TransactionCanceledException:
        # A concurrent release or expiry is safe; callers can discard the token.
        return
    except ClientError as exc:
        raise BookingServiceError("Could not release the booking hold") from exc


def _read_hold(hold_id: str) -> dict[str, Any] | None:
    try:
        response = DDB.get_item(
            TableName=_table_name(),
            Key=_serialize_item({"pk": f"HOLD#{hold_id}", "sk": "META"}),
            ConsistentRead=True,
        )
    except ClientError as exc:
        raise BookingServiceError("Could not read the booking hold") from exc
    raw = response.get("Item")
    return ({key: DESERIALIZER.deserialize(value) for key, value in raw.items()} if raw else None)


def prepare_order_booking(shop: str, hold_id: str, token: str, order_id: str, secret: str) -> dict[str, Any]:
    """Atomically keep a new order's slot locked while its calendar event is created."""
    if not shop or not re.fullmatch(r"[a-f0-9]{32}", hold_id) or not re.fullmatch(r"[a-f0-9]{64}", token):
        raise InvalidBookingRequest("Invalid booking hold")
    if not order_id or len(order_id) > 128:
        raise InvalidBookingRequest("Invalid Shopify order")
    hold = _read_hold(hold_id)
    if not hold or hold.get("shop") != shop:
        raise InvalidBookingRequest("Booking hold was not found")
    expected_token = _hold_token(secret, shop, hold_id, int(hold.get("expiresAt", 0)))
    if not hmac.compare_digest(token, expected_token):
        raise InvalidBookingRequest("Invalid booking hold")
    if hold.get("status") == "BOOKED" and hold.get("orderId") == order_id:
        return hold
    if hold.get("status") == "CONFIRMING" and hold.get("orderId") == order_id:
        return hold
    if hold.get("status") != "HELD":
        raise InvalidBookingRequest("Booking hold is no longer available")

    start = _parse_datetime(str(hold["start"]))
    end = _parse_datetime(str(hold["end"]))
    try:
        busy = get_busy_periods(
            start.isoformat().replace("+00:00", "Z"),
            end.isoformat().replace("+00:00", "Z"),
        )
    except GoogleCalendarError as exc:
        raise BookingServiceError("Calendar availability could not be checked") from exc
    if any(
        start < _parse_datetime(period["end"]) and end > _parse_datetime(period["start"])
        for period in busy
    ):
        raise SlotUnavailable("The appointment time is no longer available")

    now_epoch = int(time.time())
    lock_keys = list(hold.get("lockKeys", []))
    if isinstance(hold.get("capacityKey"), dict):
        lock_keys.append(hold["capacityKey"])
    transaction = [{
        "Update": {
            "TableName": _table_name(),
            "Key": _serialize_item({"pk": f"HOLD#{hold_id}", "sk": "META"}),
            "UpdateExpression": "SET #status = :confirming, #orderId = :orderId REMOVE #expiry",
            "ConditionExpression": "#shop = :shop AND (#status = :held OR (#status = :confirming AND #orderId = :orderId))",
            "ExpressionAttributeNames": {"#status": "status", "#orderId": "orderId", "#shop": "shop", "#expiry": "expiresAt"},
            "ExpressionAttributeValues": {
                ":confirming": SERIALIZER.serialize("CONFIRMING"),
                ":held": SERIALIZER.serialize("HELD"),
                ":orderId": SERIALIZER.serialize(order_id),
                ":shop": SERIALIZER.serialize(shop),
            },
        }
    }]
    for key in lock_keys:
        transaction.append({
            "Put": {
                "TableName": _table_name(),
                "Item": _serialize_item({**key, "holdId": hold_id, "status": "CONFIRMING"}),
                "ConditionExpression": "attribute_not_exists(#pk) OR #holdId = :holdId OR (attribute_exists(#expiry) AND #expiry <= :now)",
                "ExpressionAttributeNames": {"#pk": "pk", "#holdId": "holdId", "#expiry": "expiresAt"},
                "ExpressionAttributeValues": {":holdId": SERIALIZER.serialize(hold_id), ":now": SERIALIZER.serialize(now_epoch)},
            }
        })
    try:
        DDB.transact_write_items(TransactItems=transaction)
    except DDB.exceptions.TransactionCanceledException as exc:
        current = _read_hold(hold_id)
        if current and current.get("status") == "CONFIRMING" and current.get("orderId") == order_id:
            return current
        raise SlotUnavailable("The appointment time was reserved by another order") from exc
    except ClientError as exc:
        raise BookingServiceError("Could not confirm the appointment time") from exc
    hold.update({"status": "CONFIRMING", "orderId": order_id})
    return hold


def complete_order_booking(hold_id: str, order_id: str, event_id: str) -> None:
    try:
        DDB.update_item(
            TableName=_table_name(),
            Key=_serialize_item({"pk": f"HOLD#{hold_id}", "sk": "META"}),
            UpdateExpression="SET #status = :booked, #eventId = :eventId",
            ConditionExpression="#status = :confirming AND #orderId = :orderId",
            ExpressionAttributeNames={"#status": "status", "#eventId": "eventId", "#orderId": "orderId"},
            ExpressionAttributeValues={
                ":booked": SERIALIZER.serialize("BOOKED"),
                ":confirming": SERIALIZER.serialize("CONFIRMING"),
                ":orderId": SERIALIZER.serialize(order_id),
                ":eventId": SERIALIZER.serialize(event_id),
            },
        )
    except DDB.exceptions.ConditionalCheckFailedException:
        current = _read_hold(hold_id)
        if current and current.get("status") == "BOOKED" and current.get("orderId") == order_id and current.get("eventId") == event_id:
            return
        raise BookingServiceError("Could not finish appointment booking") from None
    except ClientError as exc:
        raise BookingServiceError("Could not finish appointment booking") from exc
