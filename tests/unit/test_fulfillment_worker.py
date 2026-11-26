import concurrent.futures
import json
from unittest.mock import patch
from common.models import OrderStatus
from handlers import fulfillment_worker


def _seed_committed_order(aws_double, order_id="ord_w_1", customer_id="cust_1", total_minor=5000, status="COMMITTED"):
    aws_double.put_item(
        TableName="test-silentloss-orders",
        Item={
            "order_id": {"S": order_id},
            "customer_id": {"S": customer_id},
            "currency": {"S": "USD"},
            "total_amount": {"S": "50.00"},
            "total_amount_minor": {"N": str(total_minor)},
            "status": {"S": status},
        },
    )


def _sqs_record(msg_id, order_id="ord_w_1", customer_id="cust_1", total_minor=5000, currency="USD"):
    return {
        "messageId": msg_id,
        "body": json.dumps({
            "event_version": "1.0",
            "event_type": "order.created",
            "event_id": f"evt_{msg_id}",
            "timestamp": "2026-11-26T12:00:00Z",
            "correlation_id": f"corr_{msg_id}",
            "data": {
                "order_id": order_id,
                "customer_id": customer_id,
                "currency": currency,
                "total_amount_minor": total_minor,
            },
        }),
    }


def test_atomic_fulfillment_transition_success(aws_double):
    _seed_committed_order(aws_double, "ord_atom_1")
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        res = fulfillment_worker.handler({"Records": [_sqs_record("m1", "ord_atom_1")]}, None)
        assert res["batchItemFailures"] == []
        # Verify BOTH tables updated atomically
        o = aws_double.tables["test-silentloss-orders"][("ord_atom_1",)]
        f = aws_double.tables["test-silentloss-fulfillment"][("ord_atom_1", "FULFILLMENT#PRIMARY")]
        assert o["status"]["S"] == OrderStatus.FULFILLED.value
        assert f["status"]["S"] == "COMPLETED"
        assert f["total_amount_minor"]["N"] == "5000"


def test_concurrent_duplicate_deliveries_no_double_effect(aws_double):
    _seed_committed_order(aws_double, "ord_dup_conc")
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        def deliver(i):
            return fulfillment_worker.handler({"Records": [_sqs_record(f"m_dup_{i}", "ord_dup_conc")]}, None)

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(deliver, range(6)))

        assert all(r["batchItemFailures"] == [] for r in results)
        # Strictly ONE fulfillment record exists
        assert len(aws_double.tables["test-silentloss-fulfillment"]) == 1


def test_lost_transaction_ack_resolved_via_reread(aws_double):
    _seed_committed_order(aws_double, "ord_lost_tx")
    aws_double.fail_next_ddb_transact_commit_then_timeout = 1

    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        res = fulfillment_worker.handler({"Records": [_sqs_record("m_lost", "ord_lost_tx")]}, None)
        # Committed atomically despite lost ack -> verified on reread -> no retry needed!
        assert res["batchItemFailures"] == []
        assert ("ord_lost_tx", "FULFILLMENT#PRIMARY") in aws_double.tables["test-silentloss-fulfillment"]


def test_partial_failure_prevention_on_transaction_cancel(aws_double):
    # Order already in FAILED state -> transaction must cancel atomically without writing FulfillmentTable!
    _seed_committed_order(aws_double, "ord_failed_state", status="FAILED")
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        res = fulfillment_worker.handler({"Records": [_sqs_record("m_fail", "ord_failed_state")]}, None)
        assert res["batchItemFailures"] == []
        assert len(aws_double.tables.get("test-silentloss-fulfillment", {})) == 0
        # Durable rejection persisted
        assert ("rej_m_fail",) in aws_double.tables["test-silentloss-rejections"]


def test_stale_or_conflicting_event_values_rejected_and_audited(aws_double):
    _seed_committed_order(aws_double, "ord_mismatch", customer_id="cust_real", total_minor=5000)
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        # Event claims different total_amount_minor (tampered/stale)
        bad_rec = _sqs_record("m_stale", "ord_mismatch", customer_id="cust_real", total_minor=9999)
        res = fulfillment_worker.handler({"Records": [bad_rec]}, None)
        assert res["batchItemFailures"] == []
        assert len(aws_double.tables.get("test-silentloss-fulfillment", {})) == 0
        rej = aws_double.tables["test-silentloss-rejections"][("rej_m_stale",)]
        assert rej["error_code"]["S"] == "StaleOrConflictingEvent"


def test_malformed_messages_preserved_without_crashing_batch_siblings(aws_double, caplog):
    _seed_committed_order(aws_double, "ord_sibling_ok")
    records = [
        {"messageId": "m_bad_json", "body": "{bad json secret_customer_pii"},
        {"messageId": "m_bad_ver", "body": json.dumps({"event_version": "9.9", "event_type": "order.created", "event_id": "e1", "correlation_id": "c1", "timestamp": "t1", "data": {"order_id": "o1"}})},
        {"messageId": "m_missing_order", "body": _sqs_record("m_missing_order", "ord_does_not_exist")["body"]},
        _sqs_record("m_good_sibling", "ord_sibling_ok"),
    ]
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        res = fulfillment_worker.handler({"Records": records}, None)
        # All 3 permanent failures durably audited, 0 retry loop, healthy sibling fulfilled!
        assert res["batchItemFailures"] == []
        assert ("ord_sibling_ok", "FULFILLMENT#PRIMARY") in aws_double.tables["test-silentloss-fulfillment"]
        assert ("rej_m_bad_json",) in aws_double.tables["test-silentloss-rejections"]
        assert ("rej_m_bad_ver",) in aws_double.tables["test-silentloss-rejections"]
        assert ("rej_m_missing_order",) in aws_double.tables["test-silentloss-rejections"]
        # Raw sensitive body never leaked in logs
        assert "secret_customer_pii" not in caplog.text


def test_transient_transaction_timeout_isolated_to_failing_item(aws_double):
    _seed_committed_order(aws_double, "ord_tx_1")
    _seed_committed_order(aws_double, "ord_tx_2")
    aws_double.fail_next_ddb_transact_timeout = 1

    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        res = fulfillment_worker.handler(
            {"Records": [_sqs_record("m_timeout", "ord_tx_1"), _sqs_record("m_ok", "ord_tx_2")]},
            None,
        )
        assert res["batchItemFailures"] == [{"itemIdentifier": "m_timeout"}]
        assert ("ord_tx_2", "FULFILLMENT#PRIMARY") in aws_double.tables["test-silentloss-fulfillment"]
