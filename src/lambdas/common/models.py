from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Tuple
import uuid


CURRENCY_EXPONENTS: Dict[str, int] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CAD": 2,
    "JPY": 0,
}


class OrderStatus(str, Enum):
    COMMITTED = "COMMITTED"
    FULFILLED = "FULFILLED"
    FAILED = "FAILED"


class DispatchStatus(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    PENDING = "PENDING"
    PUBLISHING = "PUBLISHING"
    RETRY_WAIT = "RETRY_WAIT"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXHAUSTED = "EXHAUSTED"
    UNKNOWN = "UNKNOWN"


class FulfillmentStatus(str, Enum):
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


def deterministic_event_id(order_id: str, event_type: str = "order.created") -> str:
    """Derive a stable deterministic UUIDv5 event identifier per order + event_type."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"silentloss:{event_type}:{order_id}"))


def to_minor_units(amount: Any, currency: str) -> Tuple[int, str]:
    """
    Convert a validated numeric/Decimal monetary value into exact integer minor units
    and canonical decimal string representation without any floating-point conversion.
    Raises ValueError on invalid types, NaN, Infinity, or precision overflow.
    """
    if isinstance(amount, bool):
        raise ValueError("Monetary value cannot be boolean")
    if isinstance(amount, float):
        raise ValueError("Floating-point monetary input must be parsed as Decimal")
    if not isinstance(amount, (int, Decimal, str)):
        raise ValueError(f"Unsupported monetary value type: {type(amount).__name__}")

    if currency not in CURRENCY_EXPONENTS:
        raise ValueError(f"Unsupported currency: {currency}")

    exponent = CURRENCY_EXPONENTS[currency]
    try:
        dec = amount if isinstance(amount, Decimal) else Decimal(str(amount))
    except InvalidOperation as exc:
        raise ValueError("Malformed decimal monetary value") from exc

    if not dec.is_finite():
        raise ValueError("Non-finite monetary value (NaN or Infinity) is prohibited")
    if dec <= 0:
        raise ValueError("Monetary value must be strictly positive")
    # Bound magnitude BEFORE Decimal multiplication/quantization or int conversion.
    if dec > Decimal(100_000_000) / (Decimal(10) ** exponent):
        raise ValueError("Monetary value exceeds the supported limit")

    # Check fractional decimal digits against currency precision exponent
    exp = dec.as_tuple().exponent
    if isinstance(exp, int) and exp < -exponent:
        raise ValueError(
            f"Currency {currency} allows at most {exponent} decimal places"
        )

    scale = Decimal(10) ** exponent
    minor_dec = dec * scale
    if minor_dec != int(minor_dec):
        raise ValueError("Monetary value does not align to integer minor units")

    quantized = dec.quantize(Decimal(10) ** -exponent) if exponent > 0 else Decimal(int(dec))
    return int(minor_dec), str(quantized)


def compute_payload_hash(
    customer_id: str,
    currency: str,
    total_amount_minor: int,
    items_normalized: list,
    explicit_order_id: str = "",
) -> str:
    """
    Compute deterministic SHA-256 hash using exact integer minor currency units
    and canonical JSON ordering. Never uses floats.
    """
    canonical = {
        "customer_id": customer_id,
        "currency": currency,
        "total_amount_minor": total_amount_minor,
        "explicit_order_id": explicit_order_id or "",
        "items": sorted(
            items_normalized,
            key=lambda x: (str(x["sku"]), int(x["quantity"]), int(x["unit_price_minor"])),
        ),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
