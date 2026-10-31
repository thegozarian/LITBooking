"""Shopify App Proxy and order webhook handlers for LIT Booking.

The App Proxy validates Shopify's HMAC and timestamp, reads busy periods without
exposing event details, and creates atomic temporary holds. The signed
orders/create webhook turns a valid hold into a stable Google Calendar event.
"""

from __future__ import annotations

import hashlib
import hmac
import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from urllib.parse import parse_qs

import boto3

from booking_service import (
    BookingServiceError,
    InvalidBookingRequest,
    SlotUnavailable,
    complete_order_booking,
    create_hold,
    get_available_slots,
    prepare_order_booking,
    release_hold,
)
from google_calendar import GoogleCalendarError, create_event, get_busy_periods


MAX_PROXY_AGE_SECONDS = 300
MAX_AVAILABILITY_WINDOW = timedelta(days=31)
SSM = boto3.client("ssm")
LOGGER = logging.getLogger(__name__)


def _response(status_code: int, payload: dict[str, object]) -> dict[str, object]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
        },
        "body": json.dumps(payload, separators=(",", ":")),
    }


def _query_values(event: dict[str, object]) -> dict[str, list[str]]:
    raw_query = event.get("rawQueryString")
    if isinstance(raw_query, str):
        return parse_qs(raw_query, keep_blank_values=True)

    # Useful for local invocation formats that don't include rawQueryString.
    params = event.get("queryStringParameters")
    if not isinstance(params, dict):
        return {}
    return {str(key): [str(value)] for key, value in params.items() if value is not None}


def _valid_shopify_proxy_signature(
    query: dict[str, list[str]], secret: str, now: int | None = None
) -> bool:
    signatures = query.get("signature", [])
    timestamps = query.get("timestamp", [])
    if (
        len(signatures) != 1
        or len(timestamps) != 1
        or not query.get("shop", [""])[0]
        or not query.get("path_prefix", [""])[0]
    ):
        return False

    received_signature = signatures[0]
    if len(received_signature) != 64:
        return False

    try:
        timestamp = int(timestamps[0])
    except (TypeError, ValueError):
        return False

    current_time = int(time.time()) if now is None else now
    if abs(current_time - timestamp) > MAX_PROXY_AGE_SECONDS:
        return False

    unsigned_items = ((key, values) for key, values in query.items() if key != "signature")
    canonical_params = "".join(
        sorted(f"{key}={','.join(values)}" for key, values in unsigned_items)
    )
    expected_signature = hmac.new(
        secret.encode("utf-8"), canonical_params.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(received_signature.lower(), expected_signature)


@lru_cache(maxsize=1)
def _shopify_app_secret() -> str:
    """Load the encrypted Shopify secret once per Lambda execution environment."""
    parameter_name = os.environ.get("SHOPIFY_APP_SECRET_PARAMETER", "")
    if not parameter_name:
        return ""

    result = SSM.get_parameter(Name=parameter_name, WithDecryption=True)
    return result["Parameter"]["Value"]


def _availability_window(query: dict[str, list[str]]) -> tuple[str, str] | None:
    starts = query.get("start", [])
    ends = query.get("end", [])
    if len(starts) != 1 or len(ends) != 1 or not starts[0] or not ends[0]:
        return None

    try:
        start = datetime.fromisoformat(starts[0].replace("Z", "+00:00"))
        end = datetime.fromisoformat(ends[0].replace("Z", "+00:00"))
    except ValueError:
        return None

    if start.tzinfo is None or end.tzinfo is None:
        return None
    if end <= start or end - start > MAX_AVAILABILITY_WINDOW:
        return None

    start_utc = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    end_utc = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return start_utc, end_utc


def _availability_response(query: dict[str, list[str]]) -> dict[str, object]:
    window = _availability_window(query)
    if window is None:
        return _response(400, {"error": "invalid_time_window"})

    try:
        periods = get_busy_periods(*window)
    except GoogleCalendarError:
        return _response(502, {"error": "calendar_unavailable"})

    return _response(
        200,
        {
            "busy": [
                {"start": period["start"], "end": period["end"]}
                for period in periods
            ]
        },
    )


def _single_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key, [])
    if len(values) != 1 or not values[0]:
        return None
    return values[0]


def _slots_response(query: dict[str, list[str]]) -> dict[str, object]:
    booking_type = _single_query_value(query, "booking_type")
    window = _availability_window(query)
    if not booking_type or window is None:
        return _response(400, {"error": "invalid_booking_request"})

    try:
        slots = get_available_slots(booking_type, *window)
    except InvalidBookingRequest:
        return _response(400, {"error": "invalid_booking_request"})
    except (BookingServiceError, GoogleCalendarError):
        return _response(503, {"error": "availability_temporarily_unavailable"})

    return _response(200, {"slots": slots, "timezone": "Europe/Dublin"})


def _hold_response(query: dict[str, list[str]], secret: str) -> dict[str, object]:
    booking_type = _single_query_value(query, "booking_type")
    start = _single_query_value(query, "start")
    request_id = _single_query_value(query, "request_id")
    shop = _single_query_value(query, "shop")
    if not booking_type or not start or not request_id or not shop:
        return _response(400, {"error": "invalid_booking_request"})

    try:
        hold = create_hold(booking_type, start, shop, request_id, secret)
    except InvalidBookingRequest:
        return _response(400, {"error": "invalid_booking_request"})
    except SlotUnavailable:
        return _response(409, {"error": "slot_unavailable"})
    except (BookingServiceError, GoogleCalendarError):
        return _response(503, {"error": "booking_temporarily_unavailable"})

    return _response(201, hold)


def _release_response(query: dict[str, list[str]], secret: str) -> dict[str, object]:
    shop = _single_query_value(query, "shop")
    hold_id = _single_query_value(query, "hold_id")
    hold_token = _single_query_value(query, "hold_token")
    if not shop or not hold_id or not hold_token:
        return _response(400, {"error": "invalid_booking_request"})

    try:
        release_hold(shop, hold_id, hold_token, secret)
    except InvalidBookingRequest:
        return _response(400, {"error": "invalid_booking_request"})
    except BookingServiceError:
        return _response(503, {"error": "booking_temporarily_unavailable"})
    return _response(200, {"released": True})


def _header(event: dict[str, object], name: str) -> str:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return ""
    return next((str(value) for key, value in headers.items() if str(key).lower() == name.lower()), "")


def _raw_body(event: dict[str, object]) -> bytes:
    body = event.get("body", "")
    if not isinstance(body, str):
        return b""
    if event.get("isBase64Encoded") is True:
        try:
            return base64.b64decode(body, validate=True)
        except ValueError:
            return b""
    return body.encode("utf-8")


def _valid_shopify_webhook(event: dict[str, object], body: bytes, secret: str) -> bool:
    supplied = _header(event, "x-shopify-hmac-sha256")
    if not supplied:
        return False
    computed = base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")
    return hmac.compare_digest(supplied, computed)


def _line_properties(line_item: dict[str, object]) -> dict[str, str]:
    raw = line_item.get("properties", [])
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items() if value is not None}
    if isinstance(raw, list):
        return {
            str(item["name"]): str(item["value"])
            for item in raw
            if isinstance(item, dict) and item.get("name") is not None and item.get("value") is not None
        }
    return {}


def _process_order_webhook(event: dict[str, object], secret: str) -> dict[str, object]:
    body = _raw_body(event)
    if not _valid_shopify_webhook(event, body, secret):
        return _response(401, {"error": "invalid_webhook_signature"})
    if _header(event, "x-shopify-topic").lower() != "orders/create":
        return _response(400, {"error": "unsupported_webhook_topic"})
    shop = _header(event, "x-shopify-shop-domain").lower()
    if not shop or not shop.endswith(".myshopify.com"):
        return _response(400, {"error": "invalid_shop"})
    try:
        order = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _response(400, {"error": "invalid_webhook_body"})
    if not isinstance(order, dict) or not order.get("id"):
        return _response(400, {"error": "invalid_order"})
    order_id = str(order["id"])
    order_label = str(order.get("name") or f"Order {order_id}")
    customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    billing = order.get("billing_address") if isinstance(order.get("billing_address"), dict) else {}
    email = order.get("email") or order.get("contact_email") or customer.get("email") or billing.get("email")
    if not isinstance(email, str) or "@" not in email:
        email = ""
    lines = order.get("line_items", [])
    if not isinstance(lines, list):
        return _response(400, {"error": "invalid_order_items"})

    processed = 0
    try:
        for line_item in lines:
            if not isinstance(line_item, dict):
                continue
            properties = _line_properties(line_item)
            hold_id = properties.get("_Booking hold id", "")
            token = properties.get("_Booking hold token", "")
            booking_type = properties.get("_Booking type", "")
            if not hold_id and not token and not booking_type:
                continue
            if not hold_id or not token or not booking_type:
                raise InvalidBookingRequest("Booking details are incomplete")

            hold = prepare_order_booking(shop, hold_id, token, order_id, secret)
            if hold.get("bookingType") != booking_type:
                raise InvalidBookingRequest("Booking type does not match the reservation")
            event_id = hashlib.sha256(f"{shop}|{order_id}|{hold_id}".encode("utf-8")).hexdigest()
            details = {
                "video_chat": "Video chat with engineer",
                "phone_call": "Phone call with engineer",
                "remote_service": "Remote service appointment",
                "site_visit": "Site visit",
            }
            calendar_event = {
                "summary": f"LIT Booking — {details.get(booking_type, 'Appointment')}",
                "description": f"Shopify booking {order_label}",
                "start": {"dateTime": str(hold["start"]), "timeZone": "Europe/Dublin"},
                "end": {"dateTime": str(hold["end"]), "timeZone": "Europe/Dublin"},
            }
            if email:
                calendar_event["attendees"] = [{"email": email}]
            created = create_event(calendar_event, event_id)
            complete_order_booking(hold_id, order_id, created["id"])
            processed += 1
    except (InvalidBookingRequest, SlotUnavailable, BookingServiceError, GoogleCalendarError):
        LOGGER.exception("Shopify order webhook booking processing failed")
        return _response(500, {"error": "booking_processing_failed"})
    return _response(200, {"processed": processed})


def lambda_handler(event: dict[str, object], context: object) -> dict[str, object]:
    """Handle Shopify App Proxy requests and the order-created webhook."""
    request_context = event.get("requestContext")
    http_context = request_context.get("http", {}) if isinstance(request_context, dict) else {}
    method = str(http_context.get("method", "")).upper()
    if method not in {"GET", "POST"}:
        return _response(405, {"error": "method_not_allowed"})

    secret = _shopify_app_secret()
    if not secret:
        return _response(503, {"error": "service_not_configured"})

    raw_path = str(event.get("rawPath", ""))
    if method == "POST" and raw_path.rstrip("/") == "/webhooks/orders/create":
        return _process_order_webhook(event, secret)

    query = _query_values(event)
    if not _valid_shopify_proxy_signature(query, secret):
        return _response(401, {"error": "invalid_app_proxy_request"})

    actions = query.get("action", [])
    if actions:
        if len(actions) != 1:
            return _response(400, {"error": "invalid_action"})
        if actions[0] == "availability":
            if method != "GET":
                return _response(405, {"error": "method_not_allowed"})
            return _availability_response(query)
        if actions[0] == "slots":
            if method != "GET":
                return _response(405, {"error": "method_not_allowed"})
            return _slots_response(query)
        if actions[0] == "hold":
            if method != "POST":
                return _response(405, {"error": "method_not_allowed"})
            return _hold_response(query, secret)
        if actions[0] == "release":
            if method != "POST":
                return _response(405, {"error": "method_not_allowed"})
            return _release_response(query, secret)
        return _response(404, {"error": "unsupported_action"})

    return _response(
        200,
        {
            "probe": "lit-booking-aws",
            "version": 1,
            "method": method,
            "shopifyRequestVerified": True,
            "hasShop": bool(query.get("shop", [""])[0]),
            "hasPathPrefix": bool(query.get("path_prefix", [""])[0]),
        },
    )
