"""Exact Phase 8 probes and targeted uncertainty/terminal-persistence regressions."""
import json
import os
import time
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from common.outbox import publish_outbox_record
from handlers import order_service, fulfillment_worker, outbox_relay
from tests.unit.test_order_service import _post_event, _valid_order

OUTBOX = "test-silentloss-outbox"
ORDERS = "test-silentloss-orders"


def seed(aws, oid="ord_phase8_ack"):
    with patch.object(order_service, "get_dynamodb", return_value=aws):
        assert order_service.handler(_post_event(_valid_order(oid), oid), None)["statusCode"] == 201
    return "outbox#" + oid


def publish(aws, ob, **kwargs):
    return publish_outbox_record(aws, aws, OUTBOX, ORDERS, os.environ["FULFILLMENT_QUEUE_URL"], ob, **kwargs)


def item(aws, ob):
    return aws.tables[OUTBOX][(ob,)]


def due(aws, ob):
    item(aws, ob)["next_attempt_at"] = {"N": "0"}
    item(aws, ob)["lease_expires_at"] = {"N": "0"}


def denied(**kwargs):
    raise ClientError({"Error": {"Code": "AccessDenied"}}, "SendMessage")


def fulfill(aws):
    with patch.object(fulfillment_worker, "get_dynamodb", return_value=aws):
        for message in next(iter(aws.queues.values())):
            assert fulfillment_worker.handler({"Records": [message]}, None) == {"batchItemFailures": []}
    assert len(aws.tables["test-silentloss-fulfillment"]) == 1


def test_original_accepted_lost_ack_then_access_denied_remains_eligible(aws_double):
    aws = aws_double
    ob = seed(aws)
    send = aws.send_message
    calls = []
    def mixed(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            send(**kwargs)
            raise ReadTimeoutError(endpoint_url="https://example.invalid")
        return denied(**kwargs)
    with patch.object(aws, "send_message", side_effect=mixed):
        result = publish(aws, ob)
    assert result["status"] == "RETRY_SCHEDULED" and result["retryable"] is True
    assert len(calls) == 2 and len(next(iter(aws.queues.values()))) == 1
    assert item(aws, ob)["publication_uncertain"] == {"S": "true"}
    assert item(aws, ob)["dispatch_state"]["S"] == "RETRY_WAIT"
    assert aws.tables[ORDERS][("ord_phase8_ack",)]["status"]["S"] == "COMMITTED"
    fulfill(aws)
    assert aws.tables[ORDERS][("ord_phase8_ack",)]["status"]["S"] == "FULFILLED"
    assert not aws.tables.get("test-silentloss-rejections")


@pytest.mark.parametrize("crash", [False, True])
def test_uncertainty_survives_invocation_handoff_and_budget_stop(aws_double, crash):
    aws = aws_double
    ob = seed(aws)
    send = aws.send_message
    class Interrupted(BaseException):
        pass
    def lost(**kwargs):
        send(**kwargs)
        if crash:
            raise Interrupted()
        raise ReadTimeoutError(endpoint_url="https://example.invalid")
    with patch.object(aws, "send_message", side_effect=lost):
        if crash:
            with pytest.raises(Interrupted):
                publish(aws, ob, max_attempts_per_invocation=1, max_total_attempts=2)
        else:
            assert publish(aws, ob, max_attempts_per_invocation=1, max_total_attempts=2)["retryable"]
    assert item(aws, ob)["attempt_count"]["N"] == "1"
    assert item(aws, ob)["send_attempts_reserved"]["N"] == "1"
    assert item(aws, ob)["publication_uncertain"]["S"] == "true"
    due(aws, ob)
    with patch.object(aws, "send_message", side_effect=denied) as sends:
        result = publish(aws, ob, max_total_attempts=2)
        assert sends.call_count == 1
        again = publish(aws, ob, max_total_attempts=2)
        assert sends.call_count == 1  # Parked UNKNOWN does not exceed the send budget.
    assert result["status"] == again["status"] == "PUBLICATION_UNCERTAIN"
    assert result["requires_attention"] and result["retryable"] is False
    assert item(aws, ob)["dispatch_state"]["S"] == "UNKNOWN"
    assert item(aws, ob)["attempt_count"]["N"] == "2"
    assert aws.tables[ORDERS][("ord_phase8_ack",)]["status"]["S"] == "COMMITTED"
    fulfill(aws)


@pytest.mark.parametrize("failure", ["timeout", "rejection", "conflict", "readback"])
def test_original_terminal_transaction_not_committed_stays_retryable(aws_double, failure):
    aws = aws_double
    ob = seed(aws, "ord_phase8_tx")
    transaction = aws.transact_write_items
    get = aws.get_item
    writes = []
    def fail(**kwargs):
        writes.append(kwargs)
        if failure in ("timeout", "readback"):
            raise ReadTimeoutError(endpoint_url="https://example.invalid")
        if failure == "conflict":
            raise ClientError({"Error": {"Code": "TransactionCanceledException"}, "CancellationReasons": [{"Code": "ConditionalCheckFailed"}]}, "TransactWriteItems")
        raise ClientError({"Error": {"Code": "AccessDenied"}}, "TransactWriteItems")
    def reads(**kwargs):
        if failure == "readback" and writes:
            raise ReadTimeoutError(endpoint_url="https://example.invalid")
        return get(**kwargs)
    with patch.object(aws, "send_message", side_effect=denied), patch.object(aws, "transact_write_items", side_effect=fail), patch.object(aws, "get_item", side_effect=reads):
        result = publish(aws, ob)
    assert result["status"] == "TERMINAL_STATE_UNVERIFIED" and result["retryable"] is True
    assert item(aws, ob)["dispatch_state"]["S"] == "PUBLISHING"
    assert aws.tables[ORDERS][("ord_phase8_tx",)]["status"]["S"] == "COMMITTED"
    due(aws, ob)
    # A later owner can verify a real terminal transaction; prior uncertainty was cleared by a rejection.
    with patch.object(aws, "send_message", side_effect=denied):
        assert publish(aws, ob)["status"] == "EXHAUSTED"
    assert aws.tables[ORDERS][("ord_phase8_tx",)]["status"]["S"] == "FAILED"


@pytest.mark.parametrize("uncertain", [False, True])
def test_terminal_commit_lost_ack_confirmed_with_consistent_reads(aws_double, uncertain):
    aws = aws_double
    ob = seed(aws)
    aws.fail_next_ddb_transact_commit_then_timeout = 1
    get = aws.get_item
    reads = []
    send = aws.send_message
    def ambiguous(**kwargs):
        send(**kwargs)
        raise ReadTimeoutError(endpoint_url="https://example.invalid")
    def track(**kwargs):
        reads.append(kwargs)
        return get(**kwargs)
    with patch.object(aws, "send_message", side_effect=ambiguous if uncertain else denied), patch.object(aws, "get_item", side_effect=track):
        result = publish(aws, ob, max_total_attempts=2)
    assert result["status"] == ("PUBLICATION_UNCERTAIN" if uncertain else "EXHAUSTED") and not result["retryable"]
    assert reads[-1]["TableName"] == ORDERS and reads[-2]["TableName"] == OUTBOX
    assert all(r["ConsistentRead"] is True for r in reads)
    if uncertain:
        fulfill(aws)  # Both accepted copies still produce only one completed effect.


def test_lost_reservation_ack_does_not_send_and_preserves_budget(aws_double):
    aws = aws_double
    ob = seed(aws)
    update = aws.update_item
    def interrupted(**kwargs):
        result = update(**kwargs)
        if "send_attempts_reserved" in kwargs["UpdateExpression"]:
            raise ReadTimeoutError(endpoint_url="https://example.invalid")
        return result
    with patch.object(aws, "update_item", side_effect=interrupted), patch.object(aws, "send_message") as sends:
        result = publish(aws, ob, max_total_attempts=1)
        sends.assert_not_called()
    assert result["status"] == "SEND_RESERVATION_UNVERIFIED" and result["retryable"]
    assert item(aws, ob)["publication_uncertain"]["S"] == "true"
    assert item(aws, ob)["attempt_count"]["N"] == "1"
    due(aws, ob)
    with patch.object(aws, "send_message") as sends:
        assert publish(aws, ob, max_total_attempts=1)["status"] == "PUBLICATION_UNCERTAIN"
        sends.assert_not_called()


def test_crash_after_reservation_before_send_is_durable_and_bounded(aws_double):
    aws = aws_double
    ob = seed(aws)
    class Interrupted(BaseException):
        pass
    with patch.object(aws, "send_message", side_effect=Interrupted()):
        with pytest.raises(Interrupted):
            publish(aws, ob, max_total_attempts=1)
    assert not aws.queues
    assert item(aws, ob)["publication_uncertain"]["S"] == "true"
    assert item(aws, ob)["attempt_count"]["N"] == "1"
    assert item(aws, ob)["sqs_api_calls"]["N"] == "0"  # No fabricated completed-invocation call count.
    due(aws, ob)
    with patch.object(aws, "send_message") as sends:
        assert publish(aws, ob, max_total_attempts=1)["status"] == "PUBLICATION_UNCERTAIN"
        sends.assert_not_called()


def test_server_error_budget_exhaustion_is_uncertain_not_business_failure(aws_double):
    aws = aws_double
    ob = seed(aws)
    aws.fail_next_sqs_503 = 10
    result = publish(aws, ob, max_total_attempts=2)
    assert result["status"] == "PUBLICATION_UNCERTAIN" and result["requires_attention"]
    assert item(aws, ob)["attempt_count"]["N"] == "2"
    assert item(aws, ob)["publication_uncertain"]["S"] == "true"
    assert aws.tables[ORDERS][("ord_phase8_ack",)]["status"]["S"] == "COMMITTED"
    assert not aws.queues


def test_ambiguous_fault_reservation_preserves_durable_counter(aws_double, monkeypatch):
    aws = aws_double
    ob = seed(aws)
    monkeypatch.setenv("ENABLE_BENCHMARK_FAULT_ADAPTER", "true")
    item(aws, ob).update(fault_type={"S": "SQS_INJECTED_TIMEOUT"},
                         fault_remaining_blocks={"N": "2"}, blocked_attempts_executed={"N": "0"})
    update = aws.update_item
    def lost(**kwargs):
        result = update(**kwargs)
        if "fault_remaining_blocks = fault_remaining_blocks" in kwargs["UpdateExpression"]:
            raise ReadTimeoutError(endpoint_url="https://example.invalid")
        return result
    with patch.object(aws, "update_item", side_effect=lost), patch.object(aws, "send_message") as sends:
        result = publish(aws, ob, max_total_attempts=1)
        sends.assert_not_called()
    assert result["status"] == "FAULT_RESERVATION_UNVERIFIED" and result["retryable"]
    assert item(aws, ob)["attempt_count"]["N"] == "1"
    assert item(aws, ob)["blocked_attempts_executed"]["N"] == "1"
    assert item(aws, ob)["fault_remaining_blocks"]["N"] == "1"


def test_lease_takeover_during_send_does_not_clear_peer_uncertainty(aws_double):
    aws = aws_double
    ob = seed(aws)
    def takeover(**kwargs):
        item(aws, ob)["lease_owner"] = {"S": "peer"}
        return denied(**kwargs)
    with patch.object(aws, "send_message", side_effect=takeover):
        result = publish(aws, ob)
    assert result["retryable"] and result["status"] == "REJECTION_STATE_UNVERIFIED"
    assert item(aws, ob)["lease_owner"]["S"] == "peer"
    assert item(aws, ob)["publication_uncertain"]["S"] == "true"
    assert aws.tables[ORDERS][("ord_phase8_ack",)]["status"]["S"] == "COMMITTED"


def test_relay_reports_unverified_terminal_transaction_as_batch_failure(aws_double):
    aws = aws_double
    seed(aws)
    events = aws.drain_outbox_stream_events()
    with patch.object(outbox_relay, "get_dynamodb", return_value=aws), patch.object(outbox_relay, "get_sqs", return_value=aws), patch.object(aws, "send_message", side_effect=denied), patch.object(aws, "transact_write_items", side_effect=ReadTimeoutError(endpoint_url="https://example.invalid")):
        result = outbox_relay.handler({"Records": events}, None)
    assert result == {"batchItemFailures": [{"itemIdentifier": events[0]["dynamodb"]["SequenceNumber"]}]}
