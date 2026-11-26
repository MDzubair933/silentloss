import json
import os
from unittest.mock import MagicMock, patch
import pytest

from handlers import fulfillment_worker, order_service, outbox_relay
from src.benchmark.harness import BenchmarkHarness
from src.benchmark.scenarios import STANDARD_SCENARIOS, validate_concurrency
from src.benchmark.transport import SigV4HttpTransport, sanitize_headers_for_logging
from src.reconciliation.aws_acquisition import AwsEvidenceAcquirer


def test_concurrency_validation_enforces_account_headroom():
    assert validate_concurrency(2) == 2
    assert validate_concurrency(4) == 4
    with pytest.raises(ValueError):
        validate_concurrency(8)
    with pytest.raises(ValueError):
        validate_concurrency(0)
    with pytest.raises(ValueError):
        validate_concurrency(True)


def test_exact_fault_percentage_and_seeded_determinism():
    h1 = BenchmarkHarness(seed=42, run_id="r1")
    h2 = BenchmarkHarness(seed=42, run_id="r1")
    s = STANDARD_SCENARIOS["B-02"]

    plan1 = h1.build_deterministic_plan(s)
    plan2 = h2.build_deterministic_plan(s)
    assert plan1 == plan2
    faults = [p["index"] for p in plan1 if p["scheduled_fault"] is not None]
    assert len(faults) == 5


def test_sigv4_signing_and_sanitized_headers(monkeypatch):
    transport = SigV4HttpTransport("https://example.execute-api.us-east-1.amazonaws.com", region="us-east-1")
    fake_creds = MagicMock()
    fake_creds.access_key = "AKIAEXAMPLE123"
    fake_creds.secret_key = "SECRETEXAMPLE123"
    fake_creds.token = None
    fake_frozen = MagicMock()
    fake_frozen.get_frozen_credentials.return_value = fake_creds

    mock_session = MagicMock()
    mock_session.get_credentials.return_value = fake_frozen
    transport._session = mock_session

    captured_headers = {}

    class FakeResp:
        status = 201
        headers = {"Apigw-Requestid": "req-abc"}
        def read(self):
            return b'{"order_id":"ord_1","status":"COMMITTED"}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=8.0):
        captured_headers.update(dict(req.headers))
        return FakeResp()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        code, body, meta = transport.request("POST", "/orders", {"Content-Type": "application/json"}, "{}")

    assert code == 201
    assert meta["aws_apigw_request_id"] == "req-abc"
    assert any(k.lower() == "authorization" and "AWS4-HMAC-SHA256" in v for k, v in captured_headers.items())

    clean = sanitize_headers_for_logging(captured_headers)
    assert not any(k.lower() == "authorization" for k in clean.keys())


def test_end_to_end_local_benchmark_f1_outbox_with_jsonl(aws_double, tmp_path, monkeypatch):
    monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "true")
    monkeypatch.setenv("BENCHMARK_CONTROLLER_ARN", "test-controller")
    jsonl_path = tmp_path / "run.jsonl"
    queue_url = os.environ["FULFILLMENT_QUEUE_URL"]

    def observe_outbox_recovery(run_id, timeout):
        stream_events = aws_double.drain_outbox_stream_events()
        with patch.object(outbox_relay, "get_dynamodb", return_value=aws_double), \
             patch.object(outbox_relay, "get_sqs", return_value=aws_double):
            res = outbox_relay.handler({"Records": stream_events}, None)
            if res["batchItemFailures"]:
                failed_seqs = {f["itemIdentifier"] for f in res["batchItemFailures"]}
                retry_batch = [e for e in stream_events if e["dynamodb"]["SequenceNumber"] in failed_seqs]
                # Fast-forward due timer by expiring next_attempt_at via time advance mock isn't needed;
                # allow_short_retry_wait waits <=2s, OR expire timer cleanly in double:
                res2 = outbox_relay.handler({"Records": retry_batch}, None)
                assert res2["batchItemFailures"] == []
        records = aws_double.queues.get(queue_url, [])
        with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws_double):
            for rec in records:
                assert fulfillment_worker.handler({"Records": [rec]}, None) == {"batchItemFailures": []}
        mock_sess = MagicMock()
        mock_sess.client.side_effect = lambda *a, **k: aws_double
        return AwsEvidenceAcquirer(
            "test-silentloss-orders", "test-silentloss-fulfillment", "test-silentloss-rejections",
            queue_url, "dlq", "", "", session=mock_sess, outbox_table="test-silentloss-outbox",
        ).acquire_run_evidence(run_id)

    with patch.object(order_service, "get_dynamodb", return_value=aws_double):
        harness = BenchmarkHarness(
            handler_invoker=order_service.handler, seed=101, run_id="local_e2e",
            controller_arn="test-controller", observer=observe_outbox_recovery,
        )
        b01 = harness.run_scenario("B-01", incremental_jsonl_path=str(jsonl_path))
        b02 = harness.run_scenario("B-02", incremental_jsonl_path=str(jsonl_path))

    assert b01["standard_requests"] == 20
    assert b01["negative_control_rejections"] == 4
    assert b01["replay_control_hits"] == 4
    assert b01["scheduled_faults"] == 0
    assert b01["benchmark_verified"] is True

    assert b02["scheduled_faults"] == 5
    assert b02["confirmed_executed_faults"] == 5
    assert b02["reconciliation"]["total_fulfilled"] == 20
    assert b02["reconciliation"]["confirmed_silent_loss"] == 0
    assert b02["benchmark_verified"] is True
    assert os.path.exists(jsonl_path)
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == b01["total_requests"] + b02["total_requests"]
