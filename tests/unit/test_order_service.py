import concurrent.futures
import json
from unittest.mock import patch
from common.models import DispatchStatus, OrderStatus
from handlers import order_service


def _post_event(body_dict, idempotency_key=None, extra_headers=None):
    headers = {"x-correlation-id": "corr-test"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if extra_headers:
        headers.update(extra_headers)
    return {
        "requestContext": {"http": {"method": "POST"}, "authorizer": {"iam": {"userArn": "test-controller"}}},
        "rawPath": "/orders",
        "headers": headers,
        "body": json.dumps(body_dict),
    }


def _valid_order(order_id=None, total="100.00"):
    d = {
        "customer_id": "cust_001",
        "items": [{"sku": "SKU-A", "quantity": 2, "unit_price": "50.00"}],
        "total_amount": total,
        "currency": "USD",
    }
    if order_id:
        d["order_id"] = order_id
    return d


def test_create_order_happy_path_atomic_outbox_stateful(aws_double):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(_post_event(_valid_order("ord_happy_1"), "key_happy_1"), None)
        assert res["statusCode"] == 201
        body = json.loads(res["body"])
        assert body["order_id"] == "ord_happy_1"
        assert body["status"] == OrderStatus.COMMITTED.value
        assert body["db_outcome"] == "CONFIRMED"
        assert body["queue_outcome"] == DispatchStatus.PENDING.value
        assert body["outbox_id"] == "outbox#ord_happy_1"
        assert body["total_amount_minor"] == 10000
        assert len(aws_double.tables["test-silentloss-orders"]) == 1
        assert len(aws_double.tables["test-silentloss-idempotency"]) == 1
        assert len(aws_double.tables["test-silentloss-outbox"]) == 1
        assert not aws_double.queues  # Order ingestion never calls SQS directly!


def test_idempotency_cannot_be_bypassed_by_conflicting_order_id(aws_double):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        r1 = order_service.handler(_post_event(_valid_order("ord_alpha"), "shared_key_1"), None)
        assert r1["statusCode"] == 201

        r2 = order_service.handler(_post_event(_valid_order("ord_beta"), "shared_key_1"), None)
        assert r2["statusCode"] == 409
        assert json.loads(r2["body"])["error"] == "IdempotencyKeyConflict"

        r3 = order_service.handler(_post_event(_valid_order("ord_alpha"), "other_key_2"), None)
        assert r3["statusCode"] == 409
        assert json.loads(r3["body"])["error"] == "OrderIdConflict"


def test_concurrent_identical_requests_resolve_to_same_order_and_single_outbox(aws_double):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        def send_identical(_):
            return order_service.handler(_post_event(_valid_order(), "race_key_identical"), None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
            responses = list(ex.map(send_identical, range(8)))

        status_codes = [r["statusCode"] for r in responses]
        order_ids = {json.loads(r["body"])["order_id"] for r in responses}

        assert status_codes.count(201) == 1
        assert status_codes.count(200) == 7
        assert len(order_ids) == 1
        assert len(aws_double.tables["test-silentloss-orders"]) == 1
        assert len(aws_double.tables["test-silentloss-outbox"]) == 1


def test_concurrent_conflicting_requests_reject_conflicts(aws_double):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        def send_variant(i):
            qty = 2 if i % 2 == 0 else 3
            body = {
                "customer_id": "cust_001",
                "items": [{"sku": "SKU-A", "quantity": qty, "unit_price": "50.00"}],
                "total_amount": f"{qty * 50}.00",
                "currency": "USD",
            }
            return order_service.handler(_post_event(body, "race_key_conflict"), None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            responses = list(ex.map(send_variant, range(6)))

        codes = [r["statusCode"] for r in responses]
        assert codes.count(201) + codes.count(200) >= 1
        assert 409 in codes
        assert len(aws_double.tables["test-silentloss-orders"]) == 1
        assert len(aws_double.tables["test-silentloss-outbox"]) == 1


def test_ambiguous_db_commit_lost_ack_recovers_on_reread_including_outbox(aws_double):
    aws_double.fail_next_ddb_transact_commit_then_timeout = 1

    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(_post_event(_valid_order("ord_lost_dback"), "key_lost_db"), None)
        assert res["statusCode"] == 201
        body = json.loads(res["body"])
        assert body["db_outcome"] == "CONFIRMED"
        assert body["queue_outcome"] == DispatchStatus.PENDING.value
        assert len(aws_double.tables["test-silentloss-outbox"]) == 1


def test_ambiguous_db_commit_unverified_returns_504_unknown(aws_double):
    aws_double.fail_next_ddb_transact_timeout = 1

    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(_post_event(_valid_order("ord_timeout_db"), "key_timeout_db"), None)
        assert res["statusCode"] == 504
        body = json.loads(res["body"])
        assert body["db_outcome"] == "UNKNOWN"
        assert body["queue_outcome"] == DispatchStatus.NOT_ATTEMPTED.value
        assert not aws_double.tables.get("test-silentloss-outbox")


def test_bounded_execution_aborts_when_budget_low(aws_double):
    class LowBudgetContext:
        def get_remaining_time_in_millis(self):
            return 1500

    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(_post_event(_valid_order("ord_budget"), "key_budget"), LowBudgetContext())
        assert res["statusCode"] == 503
        assert not aws_double.tables
        assert not aws_double.queues


def test_fault_adapter_disabled_by_default_and_persisted_when_authorized(aws_double, monkeypatch):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res1 = order_service.handler(
            _post_event(_valid_order("ord_fault_off"), "key_fault_off", {"x-benchmark-fault": "SQS_INJECTED_TIMEOUT"}),
            None,
        )
        assert res1["statusCode"] == 201
        ob1 = aws_double.tables["test-silentloss-outbox"][("outbox#ord_fault_off",)]
        assert "fault_type" not in ob1

        monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "true")
        monkeypatch.setenv("BENCHMARK_CONTROLLER_ARN", "test-controller")
        res2 = order_service.handler(
            _post_event(_valid_order("ord_fault_on"), "key_fault_on", {"x-benchmark-fault": "SQS_INJECTED_TIMEOUT"}),
            None,
        )
        assert res2["statusCode"] == 201
        b2 = json.loads(res2["body"])
        assert b2["queue_outcome"] == DispatchStatus.PENDING.value
        assert b2["fault_scheduled"] == "SQS_INJECTED_TIMEOUT"
        ob2 = aws_double.tables["test-silentloss-outbox"][("outbox#ord_fault_on",)]
        assert ob2["fault_type"]["S"] == "SQS_INJECTED_TIMEOUT"
        assert ob2["fault_remaining_blocks"]["N"] == "2"

        replay = order_service.handler(_post_event(_valid_order("ord_fault_on"), "key_fault_on"), None)
        assert replay["statusCode"] == 200
        assert json.loads(replay["body"])["is_idempotent_replay"] is True


def test_get_order_existing_and_missing(aws_double):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        order_service.handler(_post_event(_valid_order("ord_get_1"), "key_get_1"), None)

        get_ok = order_service.handler(
            {
                "requestContext": {"http": {"method": "GET"}},
                "rawPath": "/orders/ord_get_1",
                "pathParameters": {"order_id": "ord_get_1"},
            },
            None,
        )
        assert get_ok["statusCode"] == 200
        assert json.loads(get_ok["body"])["order"]["order_id"] == "ord_get_1"
        assert json.loads(get_ok["body"])["order"]["outbox_id"] == "outbox#ord_get_1"

        get_404 = order_service.handler(
            {
                "requestContext": {"http": {"method": "GET"}},
                "rawPath": "/orders/ord_missing_999",
                "pathParameters": {"order_id": "ord_missing_999"},
            },
            None,
        )
        assert get_404["statusCode"] == 404
