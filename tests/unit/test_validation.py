import json
from common.models import compute_payload_hash, to_minor_units
from common.validation import (
    MAX_PAYLOAD_BYTES,
    parse_strict_json_body,
    validate_create_order_payload,
    validate_fulfillment_event,
)
import pytest


def test_valid_usd_decimal_precision():
    raw = json.dumps({
        "customer_id": "cust_123456",
        "items": [
            {"sku": "SKU-001", "quantity": 2, "unit_price": "10.50"},
            {"sku": "SKU-002", "quantity": 1, "unit_price": "5.00"},
        ],
        "total_amount": "26.00",
        "currency": "USD",
    })
    parsed, err = parse_strict_json_body(raw)
    assert err is None
    ok, verr, norm = validate_create_order_payload(parsed)
    assert ok is True
    assert verr is None
    assert norm["total_amount_minor"] == 2600
    assert norm["total_amount_str"] == "26.00"


def test_valid_jpy_zero_decimal():
    raw = json.dumps({
        "customer_id": "cust_jpy_1",
        "items": [{"sku": "SKU-JP", "quantity": 3, "unit_price": 150}],
        "total_amount": 450,
        "currency": "JPY",
    })
    parsed, _ = parse_strict_json_body(raw)
    ok, verr, norm = validate_create_order_payload(parsed)
    assert ok is True
    assert norm["total_amount_minor"] == 450


@pytest.mark.parametrize("bad_qty", [True, False, 1.5, 0, -2, "2", 999999])
def test_reject_invalid_quantities(bad_qty):
    parsed, _ = parse_strict_json_body(json.dumps({
        "customer_id": "cust_001",
        "items": [{"sku": "SKU-1", "quantity": bad_qty, "unit_price": 10}],
        "total_amount": 10,
        "currency": "USD",
    }))
    ok, err, _ = validate_create_order_payload(parsed)
    assert ok is False
    assert "quantity" in err


@pytest.mark.parametrize("bad_price", [True, False, 0, -5.0, "10.999", "abc"])
def test_reject_invalid_or_overprecise_prices(bad_price):
    parsed, _ = parse_strict_json_body(json.dumps({
        "customer_id": "cust_001",
        "items": [{"sku": "SKU-1", "quantity": 1, "unit_price": bad_price}],
        "total_amount": 10,
        "currency": "USD",
    }))
    ok, _, _ = validate_create_order_payload(parsed)
    assert ok is False


def test_reject_jpy_fractional_precision():
    parsed, _ = parse_strict_json_body('{"customer_id":"cust_1","items":[{"sku":"SKU-1","quantity":1,"unit_price":10.5}],"total_amount":10.5,"currency":"JPY"}')
    ok, err, _ = validate_create_order_payload(parsed)
    assert ok is False
    assert "decimal places" in err


@pytest.mark.parametrize("bad_json", [
    '{"customer_id":"cust_1","items":[{"sku":"SKU-1","quantity":1,"unit_price":NaN}],"total_amount":10,"currency":"USD"}',
    '{"customer_id":"cust_1","items":[{"sku":"SKU-1","quantity":1,"unit_price":Infinity}],"total_amount":10,"currency":"USD"}',
    '{"customer_id":"cust_1","items":[{"sku":"SKU-1","quantity":1,"unit_price":-Infinity}],"total_amount":10,"currency":"USD"}',
    "not json at all",
    "[]",
    "",
])
def test_reject_nan_infinity_and_malformed_json(bad_json):
    parsed, err = parse_strict_json_body(bad_json)
    assert parsed is None or err is not None


def test_reject_oversized_payload():
    huge = '{"customer_id":"cust_1","pad":"' + ("a" * (MAX_PAYLOAD_BYTES + 100)) + '"}'
    parsed, err = parse_strict_json_body(huge)
    assert parsed is None
    assert "maximum allowed size" in err


def test_reject_float_precision_one_cent_mismatch():
    # Previously 0.05 tolerance allowed silent 1-4 cent discrepancies!
    raw = '{"customer_id":"cust_1","items":[{"sku":"SKU-1","quantity":1,"unit_price":10.00}],"total_amount":10.02,"currency":"USD"}'
    parsed, _ = parse_strict_json_body(raw)
    ok, err, _ = validate_create_order_payload(parsed)
    assert ok is False
    assert "exact item sum" in err


@pytest.mark.parametrize("bad_id", [123, "", "ab", "bad id with spaces", "bad/slash", "x" * 70])
def test_reject_invalid_identifiers(bad_id):
    parsed, _ = parse_strict_json_body(json.dumps({
        "customer_id": bad_id,
        "items": [{"sku": "SKU-1", "quantity": 1, "unit_price": 10}],
        "total_amount": 10,
        "currency": "USD",
    }))
    ok, err, _ = validate_create_order_payload(parsed)
    assert ok is False
    assert "customer_id" in err


def test_reject_unsupported_currency():
    parsed, _ = parse_strict_json_body('{"customer_id":"cust_1","items":[{"sku":"SKU-1","quantity":1,"unit_price":10}],"total_amount":10,"currency":"INR"}')
    ok, err, _ = validate_create_order_payload(parsed)
    assert ok is False
    assert "currency" in err


def test_canonical_payload_hash_no_float():
    h1 = compute_payload_hash("cust_1", "USD", 1050, [{"sku": "A", "quantity": 1, "unit_price_minor": 1050}])
    h2 = compute_payload_hash("cust_1", "USD", 1050, [{"sku": "A", "quantity": 1, "unit_price_minor": 1050}])
    h3 = compute_payload_hash("cust_1", "USD", 1051, [{"sku": "A", "quantity": 1, "unit_price_minor": 1051}])
    assert h1 == h2
    assert h1 != h3


def test_validate_fulfillment_event_envelope():
    valid_ev = json.dumps({
        "event_version": "1.0",
        "event_type": "order.created",
        "event_id": "evt_12345",
        "correlation_id": "corr_1",
        "timestamp": "2026-11-26T12:00:00Z",
        "data": {"order_id": "ord_12345"},
    })
    ok, err, body = validate_fulfillment_event(valid_ev)
    assert ok is True

    # Bad version
    bad_ver = valid_ev.replace('"1.0"', '"2.0"')
    assert validate_fulfillment_event(bad_ver)[0] is False

    # Bad type
    bad_type = valid_ev.replace('"order.created"', '"order.deleted"')
    assert validate_fulfillment_event(bad_type)[0] is False

    # Missing data.order_id
    no_oid = json.dumps({"event_version": "1.0", "event_type": "order.created", "event_id": "evt_1", "correlation_id": "c1", "timestamp": "t1", "data": {}})
    assert validate_fulfillment_event(no_oid)[0] is False
