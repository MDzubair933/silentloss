from decimal import Decimal
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from common.models import CURRENCY_EXPONENTS, to_minor_units

MAX_PAYLOAD_BYTES = 65536
MAX_ITEMS_COUNT = 100
MAX_ITEM_QUANTITY = 10000
MAX_ORDER_TOTAL_MINOR = 100_000_000  # 1,000,000.00 in 2-decimal currency

IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")
SKU_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{2,64}$")
EVENT_VERSION = "1.0"
SUPPORTED_EVENT_TYPE = "order.created"


def _reject_non_finite_constant(value: str):
    raise ValueError(f"Non-finite JSON numeric constant prohibited: {value}")


def parse_strict_json_body(raw_body: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Parse HTTP or event JSON body strictly:
      - Enforces 64KB size cap
      - Parses floats as Decimal to avoid IEEE-754 drift
      - Rejects NaN, Infinity, -Infinity constants
    """
    if isinstance(raw_body, dict):
        encoded = json.dumps(raw_body, default=str).encode("utf-8")
        if len(encoded) > MAX_PAYLOAD_BYTES:
            return None, f"Payload exceeds maximum allowed size of {MAX_PAYLOAD_BYTES} bytes"
        # Re-parse via Decimal so any accidental float in dict is safely checked
        try:
            parsed = json.loads(
                json.dumps(raw_body),
                parse_float=Decimal,
                parse_constant=_reject_non_finite_constant,
            )
            return parsed, None
        except ValueError as exc:
            return None, str(exc)

    if not isinstance(raw_body, str) or not raw_body.strip():
        return None, "Request body must be a non-empty JSON string"

    if len(raw_body.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return None, f"Payload exceeds maximum allowed size of {MAX_PAYLOAD_BYTES} bytes"

    try:
        parsed = json.loads(
            raw_body,
            parse_float=Decimal,
            parse_constant=_reject_non_finite_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"Invalid JSON payload: {exc}"

    if not isinstance(parsed, dict):
        return None, "JSON payload root must be an object"

    return parsed, None


def validate_identifier(value: Any, field_name: str) -> Optional[str]:
    if not isinstance(value, str) or not IDENTIFIER_PATTERN.match(value):
        return f"Invalid '{field_name}': must be 3-64 alphanumeric, '_' or '-' characters"
    return None


def validate_create_order_payload(
    data: Any,
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Strictly validate POST /orders payload and return normalized business representation
    with integer minor currency units.
    """
    if not isinstance(data, dict):
        return False, "Payload must be a JSON object", None

    # Validate customer_id
    customer_id = data.get("customer_id")
    err = validate_identifier(customer_id, "customer_id")
    if err:
        return False, err, None

    # Validate optional explicit order_id
    explicit_order_id = data.get("order_id")
    if explicit_order_id is not None:
        err = validate_identifier(explicit_order_id, "order_id")
        if err:
            return False, err, None

    # Validate currency
    currency = data.get("currency")
    if not isinstance(currency, str) or currency not in CURRENCY_EXPONENTS:
        return False, f"Invalid 'currency'. Supported: {sorted(CURRENCY_EXPONENTS.keys())}", None

    # Validate items
    items = data.get("items")
    if not isinstance(items, list) or len(items) == 0:
        return False, "'items' must be a non-empty list", None
    if len(items) > MAX_ITEMS_COUNT:
        return False, f"'items' count exceeds maximum of {MAX_ITEMS_COUNT}", None

    normalized_items: List[Dict[str, Any]] = []
    calculated_total_minor = 0

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return False, f"Item at index {idx} must be an object", None

        sku = item.get("sku")
        if not isinstance(sku, str) or not SKU_PATTERN.match(sku):
            return False, f"Item at index {idx} missing valid 'sku'", None

        qty = item.get("quantity")
        if isinstance(qty, bool) or not isinstance(qty, int):
            return False, f"Item at index {idx} 'quantity' must be an integer (not boolean or float)", None
        if qty <= 0 or qty > MAX_ITEM_QUANTITY:
            return False, f"Item at index {idx} 'quantity' must be between 1 and {MAX_ITEM_QUANTITY}", None

        raw_price = item.get("unit_price")
        try:
            unit_price_minor, unit_price_str = to_minor_units(raw_price, currency)
        except ValueError as exc:
            return False, f"Item at index {idx} invalid 'unit_price': {exc}", None

        line_total_minor = qty * unit_price_minor
        calculated_total_minor += line_total_minor
        normalized_items.append(
            {
                "sku": sku,
                "quantity": qty,
                "unit_price_minor": unit_price_minor,
                "unit_price": unit_price_str,
            }
        )

    # Validate total_amount
    raw_total = data.get("total_amount")
    try:
        declared_total_minor, declared_total_str = to_minor_units(raw_total, currency)
    except ValueError as exc:
        return False, f"Invalid 'total_amount': {exc}", None

    if declared_total_minor > MAX_ORDER_TOTAL_MINOR:
        return False, "Order total exceeds maximum allowed limit", None

    if calculated_total_minor != declared_total_minor:
        return (
            False,
            f"Declared total_amount ({declared_total_minor} minor units) does not match exact item sum ({calculated_total_minor} minor units)",
            None,
        )

    return True, None, {
        "customer_id": customer_id,
        "explicit_order_id": explicit_order_id,
        "currency": currency,
        "items": normalized_items,
        "total_amount_minor": declared_total_minor,
        "total_amount_str": declared_total_str,
    }


def validate_fulfillment_event(raw_body: str) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
    """
    Strictly validate incoming SQS fulfillment event envelope and payload.
    """
    body, err = parse_strict_json_body(raw_body)
    if err or body is None:
        return False, err or "Malformed event JSON", None

    if body.get("event_version") != EVENT_VERSION:
        return False, "Unsupported event_version", None

    if body.get("event_type") != SUPPORTED_EVENT_TYPE:
        return False, "Unsupported event_type", None

    event_id = body.get("event_id")
    if not isinstance(event_id, str) or len(event_id.strip()) < 3:
        return False, "Event missing valid 'event_id'", None

    correlation_id = body.get("correlation_id")
    if not isinstance(correlation_id, str) or len(correlation_id.strip()) == 0:
        return False, "Event missing valid 'correlation_id'", None

    timestamp = body.get("timestamp")
    if not isinstance(timestamp, str) or len(timestamp.strip()) == 0:
        return False, "Event missing valid 'timestamp'", None

    data = body.get("data")
    if not isinstance(data, dict):
        return False, "Event missing valid 'data' object", None

    order_id = data.get("order_id")
    err = validate_identifier(order_id, "data.order_id")
    if err:
        return False, err, None

    customer_id = data.get("customer_id")
    if customer_id is not None:
        err = validate_identifier(customer_id, "data.customer_id")
        if err:
            return False, err, None

    return True, None, body
