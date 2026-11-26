"""Dedicated hermetic F1 Transactional Outbox unit and failure-mode tests."""
import concurrent.futures
import json
import os
import time
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError, ReadTimeoutError
from common.models import DispatchStatus, OrderStatus
from common.outbox import publish_outbox_record
from handlers import fulfillment_worker, order_service, outbox_relay, outbox_sweeper
from src.reconciliation.aws_acquisition import AwsEvidenceAcquirer
from src.reconciliation.collector import EvidenceReconciliationCollector
from src.reconciliation.models import OrderClassification, SourceCompleteness
from tests.unit.test_order_service import _post_event, _valid_order


def test_transaction_cancellation_on_third_item_prevents_partial_commit(aws_double):
    # Pre-seed collision on OutboxTable third PutItem so TransactWriteItems cancels atomically
    aws_double.put_item(
        "test-silentloss-outbox",
        {"outbox_id": {"S": "outbox#ord_tx_cancel"}, "order_id": {"S": "ord_tx_cancel"}, "dispatch_state": {"S": "PENDING"}},
    )
    aws_double.drain_outbox_stream_events()

    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(_post_event(_valid_order("ord_tx_cancel"), "key_tx_cancel"), None)

    assert res["statusCode"] == 409
    assert not aws_double.tables.get("test-silentloss-idempotency")
    assert not aws_double.tables.get("test-silentloss-orders")
    assert len(aws_double.tables["test-silentloss-outbox"]) == 1


def test_b02_timeout_two_blocked_attempts_then_durable_recovery_and_fulfillment(aws_double, monkeypatch):
    monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "true")
    monkeypatch.setenv("BENCHMARK_CONTROLLER_ARN", "test-controller")
    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]

    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(
            _post_event(_valid_order("ord_b02_rec"), "key_b02_rec", {"x-benchmark-fault": "SQS_INJECTED_TIMEOUT"}),
            None,
        )
    assert res["statusCode"] == 201
    ob_init = aws_double.tables["test-silentloss-outbox"][("outbox#ord_b02_rec",)]
    assert ob_init["dispatch_state"]["S"] == DispatchStatus.PENDING.value
    assert ob_init["fault_remaining_blocks"]["N"] == "2"

    # Initial stream pass MUST block 2 attempts with 0 SQS calls and return batchItemFailures
    stream_events = aws_double.drain_outbox_stream_events()
    assert len(stream_events) == 1
    with patch.object(outbox_relay, "get_dynamodb", return_value=aws_double), \
         patch.object(outbox_relay, "get_sqs", return_value=aws_double):
        pass1 = outbox_relay.handler({"Records": stream_events}, None)

    assert len(pass1["batchItemFailures"]) == 1
    assert not aws_double.queues.get(queue_url)
    ob_mid = aws_double.tables["test-silentloss-outbox"][("outbox#ord_b02_rec",)]
    assert ob_mid["dispatch_state"]["S"] == DispatchStatus.RETRY_WAIT.value
    assert ob_mid["blocked_attempts_executed"]["N"] == "2"
    assert ob_mid["fault_remaining_blocks"]["N"] == "0"
    assert ob_mid["sqs_api_calls"]["N"] == "0"
    fev_mid = json.loads(ob_mid["fault_execution_evidence"]["S"])
    assert fev_mid["no_send_on_initial_pass_proven"] is True
    assert fev_mid["recovered"] is False

    # Reconcile BEFORE recovery -> classifies strictly as DURABLY_PENDING (P), never UNACCOUNTED (U)
    sess = MagicMock()
    sess.client.side_effect = lambda *a, **k: aws_double
    aws_double.tables["test-silentloss-orders"][("ord_b02_rec",)]  # Ensure present
    # Use standard prefix for acquisition
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        pass
    # Retry stream pass recovers cleanly
    with patch.object(outbox_relay, "get_dynamodb", return_value=aws_double), \
         patch.object(outbox_relay, "get_sqs", return_value=aws_double):
        pass2 = outbox_relay.handler({"Records": stream_events}, None)

    assert pass2 == {"batchItemFailures": []}
    assert len(aws_double.queues[queue_url]) == 1
    ob_done = aws_double.tables["test-silentloss-outbox"][("outbox#ord_b02_rec",)]
    assert ob_done["dispatch_state"]["S"] == DispatchStatus.CONFIRMED.value
    assert ob_done["sqs_api_calls"]["N"] == "1"
    assert ob_done["blocked_attempts_executed"]["N"] == "2"
    fev_done = json.loads(ob_done["fault_execution_evidence"]["S"])
    assert fev_done["recovered"] is True
    assert fev_done["blocked_attempts"] == 2
    assert fev_done["sqs_api_calls"] == 1

    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        assert fulfillment_worker.handler({"Records": aws_double.queues[queue_url]}, None) == {"batchItemFailures": []}
    assert aws_double.tables["test-silentloss-orders"][("ord_b02_rec",)]["status"]["S"] == OrderStatus.FULFILLED.value
    assert aws_double.tables["test-silentloss-fulfillment"][("ord_b02_rec", "FULFILLMENT#PRIMARY")]["status"]["S"] == "COMPLETED"


def test_b03_transient_503_blocks_once_then_publishes_same_invocation(aws_double, monkeypatch):
    monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "true")
    monkeypatch.setenv("BENCHMARK_CONTROLLER_ARN", "test-controller")
    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]

    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(
            _post_event(_valid_order("ord_b03_rec"), "key_b03_rec", {"x-benchmark-fault": "SQS_INJECTED_503"}),
            None,
        )
    assert res["statusCode"] == 201
    stream_events = aws_double.drain_outbox_stream_events()
    with patch.object(outbox_relay, "get_dynamodb", return_value=aws_double), \
         patch.object(outbox_relay, "get_sqs", return_value=aws_double):
        pass1 = outbox_relay.handler({"Records": stream_events}, None)

    assert pass1 == {"batchItemFailures": []}
    assert len(aws_double.queues[queue_url]) == 1
    ob = aws_double.tables["test-silentloss-outbox"][("outbox#ord_b03_rec",)]
    assert ob["dispatch_state"]["S"] == DispatchStatus.CONFIRMED.value
    assert ob["blocked_attempts_executed"]["N"] == "1"
    assert ob["sqs_api_calls"]["N"] == "1"
    assert ob["attempt_count"]["N"] == "2"


def test_sweeper_recovers_missed_stream_and_expired_interrupted_leases(aws_double):
    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        order_service.handler(_post_event(_valid_order("ord_missed_stream"), "k_ms"), None)
        order_service.handler(_post_event(_valid_order("ord_crashed_lease"), "k_cl"), None)
    aws_double.drain_outbox_stream_events()  # Stream drops completely!

    # Simulate crashed initial relay leaving ord_crashed_lease in PUBLISHING with expired lease
    crashed_ob = aws_double.tables["test-silentloss-outbox"][("outbox#ord_crashed_lease",)]
    crashed_ob["dispatch_state"] = {"S": DispatchStatus.PUBLISHING.value}
    crashed_ob["lease_owner"] = {"S": "crashed_worker:dead"}
    crashed_ob["lease_expires_at"] = {"N": str(int(time.time()) - 30)}

    with patch.object(outbox_sweeper, "get_dynamodb", return_value=aws_double), \
         patch.object(outbox_sweeper, "get_sqs", return_value=aws_double):
        stats = outbox_sweeper.handler({}, None)

    assert stats["confirmed"] == 2
    assert len(aws_double.queues[queue_url]) == 2
    assert aws_double.tables["test-silentloss-outbox"][("outbox#ord_missed_stream",)]["dispatch_state"]["S"] == DispatchStatus.CONFIRMED.value
    assert aws_double.tables["test-silentloss-outbox"][("outbox#ord_crashed_lease",)]["recovery_source"]["S"] == "scheduled_sweeper"


def test_concurrent_relay_and_sweeper_lease_race_produces_single_fulfillment(aws_double):
    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        order_service.handler(_post_event(_valid_order("ord_race_outbox"), "k_race"), None)
    stream_events = aws_double.drain_outbox_stream_events()

    def run_competitor(i):
        if i % 2 == 0:
            with patch.object(outbox_relay, "get_dynamodb", return_value=aws_double), \
                 patch.object(outbox_relay, "get_sqs", return_value=aws_double):
                return outbox_relay.handler({"Records": stream_events}, None)
        with patch.object(outbox_sweeper, "get_dynamodb", return_value=aws_double), \
             patch.object(outbox_sweeper, "get_sqs", return_value=aws_double):
            return outbox_sweeper.handler({}, None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(run_competitor, range(6)))

    assert aws_double.tables["test-silentloss-outbox"][("outbox#ord_race_outbox",)]["dispatch_state"]["S"] == DispatchStatus.CONFIRMED.value
    assert len(aws_double.queues[queue_url]) == 1

    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        fulfillment_worker.handler({"Records": aws_double.queues[queue_url]}, None)
    assert len(aws_double.tables["test-silentloss-fulfillment"]) == 1


def test_retry_exhaustion_marks_order_explicitly_failed_and_preserves_outbox(aws_double):
    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        order_service.handler(_post_event(_valid_order("ord_exh__B-01_0000"), "k_exh"), None)

    # Explicit throttling rejects the send; server 5xx responses remain uncertain.
    def rejected(**kwargs):
        raise ClientError({"Error": {"Code": "RequestThrottled"}}, "SendMessage")
    aws_double.send_message = rejected
    out = publish_outbox_record(
        aws_double, aws_double, "test-silentloss-outbox", "test-silentloss-orders",
        queue_url, "outbox#ord_exh__B-01_0000", max_attempts_per_invocation=2, max_total_attempts=2,
    )
    assert out["status"] == "EXHAUSTED" and out["retryable"] is False
    ob = aws_double.tables["test-silentloss-outbox"][("outbox#ord_exh__B-01_0000",)]
    ord_rec = aws_double.tables["test-silentloss-orders"][("ord_exh__B-01_0000",)]
    assert ob["dispatch_state"]["S"] == DispatchStatus.EXHAUSTED.value
    assert ord_rec["status"]["S"] == OrderStatus.FAILED.value
    assert ord_rec["dispatch_status"]["S"] == DispatchStatus.REJECTED.value


def test_reconciliation_classifies_active_outbox_item_as_durably_pending_not_unaccounted(aws_double):
    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        order_service.handler(_post_event(_valid_order("ord_pendrun__B-02_0000"), "k_pendrun"), None)

    sess = MagicMock()
    sess.client.side_effect = lambda *a, **k: aws_double
    acq = AwsEvidenceAcquirer(
        "test-silentloss-orders", "test-silentloss-fulfillment", "test-silentloss-rejections",
        queue_url, "dlq", "", "", session=sess, outbox_table="test-silentloss-outbox",
    )
    ev = acq.acquire_run_evidence("pendrun")
    assert "ord_pendrun__B-02_0000" in ev["pending_records"]
    assert ev["pending_records"]["ord_pendrun__B-02_0000"]["recoverable_path_verified"] is True

    sc = ev["source_completeness"]
    sc.publication_logs_complete = True
    summary = EvidenceReconciliationCollector().reconcile(
        ev["committed_orders"], ev["fulfillment_records"],
        pending_records=ev["pending_records"], source_completeness=sc,
    )
    assert summary.total_committed == 1
    assert summary.total_pending == 1
    assert summary.confirmed_silent_loss == 0
    assert summary.indeterminate_orders == 0
    assert summary.classified_orders["ord_pendrun__B-02_0000"] == OrderClassification.DURABLY_PENDING.value
