"""Regression and F1 remediation verification tests."""
import json
import os
import time
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from common.aws_clients import DYNAMODB_CONFIG, SQS_CONFIG, WORKER_DYNAMODB_CONFIG
from common.outbox import publish_outbox_record
from handlers import fulfillment_worker, order_service, outbox_relay
from src.benchmark.evidence import load_publication_evidence
from src.benchmark.harness import BenchmarkHarness
from src.benchmark.transport import SqsDuplicateControl
from src.reconciliation.aws_acquisition import AwsEvidenceAcquirer
from src.reconciliation.collector import EvidenceReconciliationCollector
from src.reconciliation.models import SourceCompleteness
from tests.unit.test_fulfillment_worker import _seed_committed_order, _sqs_record
from tests.unit.test_order_service import _post_event, _valid_order


@pytest.mark.parametrize("reasons", [[], [{"Code": "None"}], [{"Code": "TransactionConflict"}], [{"Code": "ProvisionedThroughputExceeded"}]])
def test_retryable_transaction_cancellation_returns_batch_failure(aws_double, reasons):
    _seed_committed_order(aws_double)
    error = ClientError({"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": reasons}, "TransactWriteItems")
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double), \
         patch.object(aws_double, "transact_write_items", side_effect=error):
        result = fulfillment_worker.handler({"Records": [_sqs_record("transient")]}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": "transient"}]}
    assert not aws_double.tables.get("test-silentloss-rejections")
    assert not aws_double.tables.get("test-silentloss-fulfillment")


def test_outbox_lost_sqs_ack_retains_retry_wait_and_recovers_without_duplicate_effect(aws_double):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        res = order_service.handler(_post_event(_valid_order("ord_ack"), "key_ack"), None)
    assert res["statusCode"] == 201
    real_send = aws_double.send_message
    calls = []

    def send(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            real_send(**kwargs)
            raise ReadTimeoutError(endpoint_url="https://example.invalid")
        if len(calls) == 2:
            raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "SendMessage")
        return real_send(**kwargs)

    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]
    with patch.object(aws_double, "send_message", side_effect=send):
        out = publish_outbox_record(
            aws_double, aws_double, "test-silentloss-outbox", "test-silentloss-orders",
            queue_url, "outbox#ord_ack", trigger_source="test", allow_short_retry_wait=False,
        )
    assert out["status"] == "RETRY_SCHEDULED" and out["retryable"] is True
    assert aws_double.tables["test-silentloss-outbox"][("outbox#ord_ack",)]["dispatch_state"]["S"] == "RETRY_WAIT"
    assert len(calls) == 2

    # Force retry window due & recover cleanly on next publish attempt -> worker suppresses duplicate cleanly!
    aws_double.tables["test-silentloss-outbox"][("outbox#ord_ack",)]["next_attempt_at"] = {"N": str(int(time.time()) - 1)}
    out2 = publish_outbox_record(
        aws_double, aws_double, "test-silentloss-outbox", "test-silentloss-orders",
        queue_url, "outbox#ord_ack", trigger_source="sweeper_test",
    )
    assert out2["status"] == "CONFIRMED"
    records = aws_double.queues[queue_url]
    assert len(records) == 2
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        for r in records:
            assert fulfillment_worker.handler({"Records": [r]}, None) == {"batchItemFailures": []}
    assert len(aws_double.tables["test-silentloss-fulfillment"]) == 1


def test_maximum_idempotency_key_generates_worker_compatible_order(aws_double):
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        result = order_service.handler(_post_event(_valid_order(), "a" * 64), None)
    assert result["statusCode"] == 201
    assert len(json.loads(result["body"])["order_id"]) <= 64
    stream_events = aws_double.drain_outbox_stream_events()
    with patch.object(outbox_relay, "get_dynamodb", return_value=aws_double), \
         patch.object(outbox_relay, "get_sqs", return_value=aws_double):
        assert outbox_relay.handler({"Records": stream_events}, None) == {"batchItemFailures": []}
    record = next(iter(aws_double.queues.values()))[0]
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
        assert fulfillment_worker.handler({"Records": [record]}, None) == {"batchItemFailures": []}
    assert len(aws_double.tables["test-silentloss-fulfillment"]) == 1


@pytest.mark.parametrize("value", ["1e30", "1e999999", "9999999999999999999999999999999999999"])
def test_oversized_finite_decimal_returns_validation_error(value):
    payload = _valid_order()
    payload["items"][0]["unit_price"] = value
    payload["total_amount"] = value
    assert order_service.handler(_post_event(payload, "large"), None)["statusCode"] == 400


def test_sdk_total_attempts_are_one():
    for config in (DYNAMODB_CONFIG, SQS_CONFIG, WORKER_DYNAMODB_CONFIG):
        assert config.retries["total_max_attempts"] == 1
        assert "max_attempts" not in config.retries
        assert config.connect_timeout + config.read_timeout <= 0.61


def test_fault_controller_is_distinct_and_unknown_fault_fails_closed(aws_double, monkeypatch):
    monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "true")
    monkeypatch.setenv("BENCHMARK_CONTROLLER_ARN", "controller")
    event = _post_event(_valid_order("ord_denied"), "key_denied", {"x-benchmark-fault": "SQS_INJECTED_TIMEOUT"})
    assert order_service.handler(event, None)["statusCode"] == 403
    event["requestContext"]["authorizer"] = {"iam": {"userArn": "controller"}}
    event["headers"]["x-benchmark-fault"] = "UNSUPPORTED"
    assert order_service.handler(event, None)["statusCode"] == 400
    assert not aws_double.tables and not aws_double.queues


@pytest.mark.parametrize("field", ["event_type", "event_version"])
def test_untrusted_rejection_values_never_persist_or_log(aws_double, field):
    rec = _sqs_record("sensitive")
    event = json.loads(rec["body"])
    event[field] = "untrusted-sensitive-marker"
    rec["body"] = json.dumps(event)
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double), \
         patch.object(fulfillment_worker.logger, "error") as log:
        assert fulfillment_worker.handler({"Records": [rec]}, None) == {"batchItemFailures": []}
    persisted = [item for table in aws_double.tables.values() for item in table.values()]
    assert "untrusted-sensitive-marker" not in json.dumps(persisted)
    assert "untrusted-sensitive-marker" not in str(log.call_args_list)


@pytest.mark.parametrize("meta", [{}, {"age_seconds": "bad"}, {"recoverable_path_verified": True, "age_seconds": 1}])
def test_empty_or_unverified_pending_is_indeterminate(meta):
    summary = EvidenceReconciliationCollector().reconcile({"ord_1": {"status": "COMMITTED"}}, [], pending_records={"ord_1": meta})
    assert summary.indeterminate_orders == 1 and summary.total_pending == 0
    assert not summary.clean_success_verified


def test_stale_metadata_and_approximate_zero_never_prove_absence(aws_double):
    aws_double.put_item("orders", {"order_id": {"S": "ord_stale__B-01_0000"}, "status": {"S": "COMMITTED"}, "dispatch_status": {"S": "NOT_ATTEMPTED"}})
    session = MagicMock()
    session.client.side_effect = lambda *a, **k: aws_double
    acquirer = AwsEvidenceAcquirer("orders", "effects", "rejects", "q", "dlq", "", "", session)
    data = acquirer.acquire_run_evidence("stale")
    assert data["quiescence_signal"]["appears_quiescent"]
    assert not data["source_completeness"].dlq_evidence_complete
    assert not data["source_completeness"].publication_logs_complete
    summary = EvidenceReconciliationCollector().reconcile(data["committed_orders"], [], source_completeness=data["source_completeness"])
    assert summary.indeterminate_orders == 1 and summary.confirmed_silent_loss == 0


def test_local_four_scenarios_f1_outbox_recovers_all_80_orders_with_zero_silent_loss(aws_double, monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "true")
    monkeypatch.setenv("BENCHMARK_CONTROLLER_ARN", "controller")
    trace = str(tmp_path / "trace.jsonl")
    queue = os.environ["FULFILLMENT_QUEUE_URL"]

    def observe_f1(run_id, timeout):
        stream_events = aws_double.drain_outbox_stream_events()
        with patch.object(outbox_relay, "get_dynamodb", return_value=aws_double), \
             patch.object(outbox_relay, "get_sqs", return_value=aws_double):
            res = outbox_relay.handler({"Records": stream_events}, None)
            if res["batchItemFailures"]:
                failed_seqs = {f["itemIdentifier"] for f in res["batchItemFailures"]}
                retry_batch = [e for e in stream_events if e["dynamodb"]["SequenceNumber"] in failed_seqs]
                res2 = outbox_relay.handler({"Records": retry_batch}, None)
                assert res2["batchItemFailures"] == []
        records = aws_double.queues[queue]
        with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
            for record in records:
                assert fulfillment_worker.handler({"Records": [record]}, None) == {"batchItemFailures": []}
        session = MagicMock()
        session.client.side_effect = lambda *a, **k: aws_double
        return AwsEvidenceAcquirer(
            "test-silentloss-orders", "test-silentloss-fulfillment", "test-silentloss-rejections",
            queue, "dlq", "", "", session=session, outbox_table="test-silentloss-outbox",
        ).acquire_run_evidence(run_id)

    harness = BenchmarkHarness(
        handler_invoker=order_service.handler,
        run_id="afterprobe",
        controller_arn="controller",
        duplicate_sender=SqsDuplicateControl(queue, aws_double),
        observer=observe_f1,
    )
    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        results = {sid: harness.run_scenario(sid, incremental_jsonl_path=trace) for sid in ("B-01", "B-02", "B-03", "B-04")}

    assert results["B-01"]["negative_control_rejections"] == 4
    assert results["B-01"]["replay_control_hits"] == 4
    assert results["B-02"]["confirmed_executed_faults"] == 5
    assert results["B-03"]["confirmed_executed_faults"] == 4
    assert results["B-04"]["duplicate_events_sent"] == 4
    assert all(r["benchmark_verified"] is True for r in results.values())

    records = aws_double.queues[queue]
    assert len(records) == 84  # All 80 unique outbox events + 4 duplicate domain-event controls
    originals = [r for r in results["B-04"]["execution_log"] if r["kind"] == "STANDARD"]
    duplicates = [r for r in results["B-04"]["execution_log"] if r["kind"] == "DUPLICATE_EVENT"]
    assert duplicates[0]["message_body"] == originals[0]["response_body"]["published_event"]
    assert len(aws_double.tables["test-silentloss-fulfillment"]) == 80
    _, _ = load_publication_evidence(trace + ".B-02.plan.json", trace, trace + ".B-02.closed.json")

    orders = {k[0]: {"status": v["status"]["S"]} for k, v in aws_double.tables["test-silentloss-orders"].items()}
    effects = [{"order_id": k[0], "fulfillment_id": k[1], "status": "COMPLETED"} for k in aws_double.tables["test-silentloss-fulfillment"]]
    sc = SourceCompleteness(
        orders_table_complete=True,
        fulfillment_table_complete=True,
        rejection_audit_complete=True,
        pagination_complete=True,
        publication_logs_complete=True,
        run_scope="afterprobe",
        collection_boundaries="Hermetic in-memory F1 outbox tables and closed controller trace",
    )
    summary = EvidenceReconciliationCollector().reconcile(
        orders, effects, source_completeness=sc, duplicate_delivery_attempts=4,
    )
    assert (summary.total_committed, summary.total_fulfilled, summary.confirmed_silent_loss, summary.indeterminate_orders) == (80, 80, 0, 0)
    assert summary.duplicate_completed_effects == 0 and summary.clean_success_verified is True


def test_transport_timeout_keeps_complete_request_trace(tmp_path):
    harness = BenchmarkHarness(handler_invoker=lambda *a: (_ for _ in ()).throw(TimeoutError()), run_id="timeout")
    trace = str(tmp_path / "timeout.jsonl")
    result = harness.run_scenario("B-01", incremental_jsonl_path=trace)
    assert len(result["execution_log"]) == 28
    assert all(r["status_code"] == 0 for r in result["execution_log"])
    assert len(open(trace).readlines()) == 28
    assert not result["benchmark_verified"]


def test_scan_collection_deadline_stops_infinite_pagination():
    ddb = MagicMock()
    ddb.scan.return_value = {"Items": [], "LastEvaluatedKey": {"order_id": {"S": "loop"}}}
    session = MagicMock()
    session.client.return_value = ddb
    acquirer = AwsEvidenceAcquirer("o", "f", "r", "q", "d", "", "", session)
    start = time.monotonic()
    data = acquirer.acquire_run_evidence("bounded", collection_timeout_seconds=0.01)
    assert time.monotonic() - start < 0.5
    assert not data["source_completeness"].pagination_complete


def test_run_filter_has_delimiter_and_namespace_validation():
    assert not "ord_run1234__B-01_0000".startswith("ord_run123__")
    with pytest.raises(ValueError):
        BenchmarkHarness(run_id="bad/run")


def test_convergence_window_is_bounded_and_never_claims_partial_success(aws_double):
    aws_double.put_item("orders", {"order_id": {"S": "ord_window__B-01_0000"}, "status": {"S": "COMMITTED"}})
    session = MagicMock()
    session.client.side_effect = lambda *a, **k: aws_double
    result = AwsEvidenceAcquirer("orders", "f", "r", "q", "d", "", "", session).await_convergence("window", timeout_seconds=0.02, poll_seconds=0.005)
    assert result["observation_window_complete"] and not result["convergence_observed"]
    assert result["observation_elapsed_seconds"] < 0.5
