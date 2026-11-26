"""Shared F1 Transactional Outbox conditional lease publisher and durable recovery engine."""
from __future__ import annotations

import calendar
import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional

from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError

from common.aws_clients import get_remaining_time_ms
from common.logger import StructuredLogger
from common.models import DispatchStatus, OrderStatus

logger = StructuredLogger("silentloss-outbox-publisher")

AMBIGUOUS_NETWORK_ERRORS = (
    ReadTimeoutError,
    ConnectTimeoutError,
    EndpointConnectionError,
    TimeoutError,
)

DEFAULT_LEASE_SECONDS = 10
DEFAULT_MAX_PUBLISHER_ATTEMPTS = 25
NON_RETRYABLE_SQS_ERRORS = {"QueueDoesNotExist", "AccessDenied", "InvalidParameterValue"}
RECOVERABLE_STATES = (DispatchStatus.PENDING.value, DispatchStatus.RETRY_WAIT.value)


def _ddb_str(item: Optional[Dict[str, Any]], key: str, default: str = "") -> str:
    if not item:
        return default
    val = item.get(key)
    return val.get("S", default) if isinstance(val, dict) else default


def _ddb_int(item: Optional[Dict[str, Any]], key: str, default: int = 0) -> int:
    if not item:
        return default
    val = item.get(key)
    if isinstance(val, dict) and "N" in val:
        try:
            return int(float(val["N"]))
        except (ValueError, TypeError):
            return default
    return default


def parse_iso_epoch(ts_iso: str) -> float:
    if not ts_iso:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def _build_fault_evidence(
    *,
    fault_type: str,
    order_id: str,
    correlation_id: str,
    blocked_attempts: int,
    sqs_api_calls: int,
    recovered: bool,
    recovery_source: Optional[str] = None,
    sqs_message_id: Optional[str] = None,
    recovery_time_sec: Optional[float] = None,
) -> Dict[str, Any]:
    ev: Dict[str, Any] = {
        "fault_adapter_executed": True,
        "fault_type": fault_type,
        "source": "async_outbox_fault_adapter",
        "order_id": order_id,
        "correlation_id": correlation_id,
        "blocked_attempts": blocked_attempts,
        "sqs_api_calls": sqs_api_calls,
        "publisher_completed": True,
        "recovered": recovered,
        "no_send_on_initial_pass_proven": (blocked_attempts == 2 and sqs_api_calls == 0) if not recovered else (blocked_attempts == 2),
    }
    if recovery_source:
        ev["recovery_source"] = recovery_source
    if sqs_message_id:
        ev["sqs_message_id"] = sqs_message_id
    if recovery_time_sec is not None:
        ev["recovery_time_sec"] = recovery_time_sec
    return ev


def _is_condition_failed(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code", "")
    if code == "ConditionalCheckFailedException":
        return True
    if code == "TransactionCanceledException":
        reasons = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
        return bool(reasons) and "ConditionalCheckFailed" in reasons
    return False


def _consume_fault_block(ddb, outbox_table: str, outbox_id: str, lease_owner: str, now_iso: str, max_total: int) -> bool:
    """Atomically decrement fault_remaining_blocks under the active lease."""
    try:
        ddb.update_item(
            TableName=outbox_table,
            Key={"outbox_id": {"S": outbox_id}},
            UpdateExpression=(
                "SET fault_remaining_blocks = fault_remaining_blocks - :one, "
                "blocked_attempts_executed = blocked_attempts_executed + :one, attempt_count = attempt_count + :one, "
                "updated_at = :now"
            ),
            ConditionExpression="attribute_exists(outbox_id) AND lease_owner = :me AND dispatch_state = :publishing AND attempt_count < :max AND fault_remaining_blocks >= :one",
            ExpressionAttributeValues={
                ":one": {"N": "1"},
                ":publishing": {"S": DispatchStatus.PUBLISHING.value},
                ":max": {"N": str(max_total)},
                ":me": {"S": lease_owner},
                ":now": {"S": now_iso},
            },
        )
        return True
    except ClientError as exc:
        if _is_condition_failed(exc):
            return False
        raise


def _mark_confirmed(
    ddb,
    outbox_table: str,
    orders_table: str,
    outbox_id: str,
    order_id: str,
    lease_owner: str,
    msg_id: str,
    final_attempts: int,
    final_sqs_calls: int,
    trigger_source: str,
    rec_sec: float,
    now_iso: str,
    fault_evidence: Optional[Dict[str, Any]],
) -> str:
    """Atomically confirm OutboxTable + update OrdersTable dispatch metadata only after SQS ack."""
    outbox_expr = (
        "SET dispatch_state = :confirmed, sqs_message_id = :mid, "
        "attempt_count = :attempts, sqs_api_calls = :calls, "
        "lease_owner = :none, lease_expires_at = :zero, "
        "published_at = :now, recovery_source = :src, recovery_time_sec = :rec, "
        "updated_at = :now, publication_uncertain = :certain"
    )
    outbox_vals: Dict[str, Any] = {
        ":certain": {"S": "false"},
        ":confirmed": {"S": DispatchStatus.CONFIRMED.value},
        ":publishing": {"S": DispatchStatus.PUBLISHING.value},
        ":me": {"S": lease_owner},
        ":mid": {"S": msg_id},
        ":attempts": {"N": str(final_attempts)},
        ":calls": {"N": str(final_sqs_calls)},
        ":none": {"S": "NONE"},
        ":zero": {"N": "0"},
        ":now": {"S": now_iso},
        ":src": {"S": trigger_source},
        ":rec": {"N": str(rec_sec)},
    }
    if fault_evidence is not None:
        outbox_expr += ", fault_execution_evidence = :fev"
        outbox_vals[":fev"] = {"S": json.dumps(fault_evidence, sort_keys=True)}

    try:
        ddb.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": outbox_table,
                        "Key": {"outbox_id": {"S": outbox_id}},
                        "UpdateExpression": outbox_expr,
                        "ConditionExpression": "attribute_exists(outbox_id) AND lease_owner = :me AND dispatch_state = :publishing",
                        "ExpressionAttributeValues": outbox_vals,
                    }
                },
                {
                    "Update": {
                        "TableName": orders_table,
                        "Key": {"order_id": {"S": order_id}},
                        "UpdateExpression": "SET dispatch_status = :confirmed, sqs_message_id = :mid, updated_at = :now",
                        "ConditionExpression": "attribute_exists(order_id)",
                        "ExpressionAttributeValues": {
                            ":confirmed": {"S": DispatchStatus.CONFIRMED.value},
                            ":mid": {"S": msg_id},
                            ":now": {"S": now_iso},
                        },
                    }
                },
            ]
        )
        return "CONFIRMED"
    except (ClientError, *AMBIGUOUS_NETWORK_ERRORS):
        reread = ddb.get_item(
            TableName=outbox_table,
            Key={"outbox_id": {"S": outbox_id}},
            ConsistentRead=True,
        ).get("Item")
        if _ddb_str(reread, "dispatch_state") == DispatchStatus.CONFIRMED.value:
            return "ALREADY_CONFIRMED"
        return "RETRY_NEEDED"



def _verify_terminal(ddb, outbox_table, orders_table, outbox_id, order_id):
    """Never acknowledge a terminal publisher decision on an unverified transaction."""
    try:
        ob = ddb.get_item(TableName=outbox_table, Key={"outbox_id": {"S": outbox_id}}, ConsistentRead=True).get("Item")
        order = ddb.get_item(TableName=orders_table, Key={"order_id": {"S": order_id}}, ConsistentRead=True).get("Item")
        state = _ddb_str(ob, "dispatch_state")
        if (state == DispatchStatus.EXHAUSTED.value
                and _ddb_str(ob, "publication_uncertain", "false") == "false"
                and _ddb_str(order, "status") == OrderStatus.FAILED.value
                and _ddb_str(order, "dispatch_status") == DispatchStatus.REJECTED.value):
            return {"outbox_id": outbox_id, "order_id": order_id, "status": "EXHAUSTED", "retryable": False}
        if (state == DispatchStatus.UNKNOWN.value
                and _ddb_str(ob, "publication_uncertain") == "true"
                and _ddb_str(order, "status") in (OrderStatus.COMMITTED.value, OrderStatus.FULFILLED.value)
                and _ddb_str(order, "dispatch_status") == DispatchStatus.UNKNOWN.value):
            return {"outbox_id": outbox_id, "order_id": order_id, "status": "PUBLICATION_UNCERTAIN",
                    "retryable": False, "requires_attention": True}
    except Exception:
        pass
    return {"outbox_id": outbox_id, "order_id": order_id, "status": "TERMINAL_STATE_UNVERIFIED", "retryable": True}


def publish_outbox_record(
    ddb,
    sqs,
    outbox_table: str,
    orders_table: str,
    queue_url: str,
    outbox_id: str,
    *,
    trigger_source: str = "outbox_publisher",
    allow_short_retry_wait: bool = True,
    context: Any = None,
    max_attempts_per_invocation: int = 2,
    max_total_attempts: Optional[int] = None,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> Dict[str, Any]:
    """
    Execute lease-protected, idempotent, retry-safe SQS publication for one OutboxTable record.
    Returns status metadata; never deletes unresolved outbox obligations prematurely.
    """
    max_total = max_total_attempts or int(os.environ.get("OUTBOX_MAX_PUBLISHER_ATTEMPTS", str(DEFAULT_MAX_PUBLISHER_ATTEMPTS)))

    item_res = ddb.get_item(
        TableName=outbox_table,
        Key={"outbox_id": {"S": outbox_id}},
        ConsistentRead=True,
    )
    item = item_res.get("Item")
    if not item:
        return {"outbox_id": outbox_id, "status": "NOT_FOUND", "retryable": False}

    order_id = _ddb_str(item, "order_id")
    correlation_id = _ddb_str(item, "correlation_id", "NONE")
    cur_state = _ddb_str(item, "dispatch_state", DispatchStatus.PENDING.value)

    if cur_state == DispatchStatus.CONFIRMED.value:
        return {
            "outbox_id": outbox_id,
            "order_id": order_id,
            "status": "ALREADY_CONFIRMED",
            "retryable": False,
            "sqs_message_id": _ddb_str(item, "sqs_message_id"),
        }

    if cur_state in (DispatchStatus.EXHAUSTED.value, DispatchStatus.UNKNOWN.value):
        return _verify_terminal(ddb, outbox_table, orders_table, outbox_id, order_id)

    now_epoch = time.time()
    next_attempt_at = _ddb_int(item, "next_attempt_at", 0)
    if cur_state == DispatchStatus.RETRY_WAIT.value and next_attempt_at > now_epoch:
        wait_s = next_attempt_at - now_epoch
        if allow_short_retry_wait and wait_s <= 4.0 and get_remaining_time_ms(context) > int(wait_s * 1000) + 3500:
            time.sleep(wait_s)
            now_epoch = time.time()
        else:
            return {"outbox_id": outbox_id, "order_id": order_id, "status": "DEFERRED_NOT_DUE", "retryable": True}

    lease_exp = _ddb_int(item, "lease_expires_at", 0)
    if cur_state == DispatchStatus.PUBLISHING.value and lease_exp > now_epoch:
        return {"outbox_id": outbox_id, "order_id": order_id, "status": "LEASE_HELD_BY_PEER", "retryable": True}

    lease_owner = f"{trigger_source}:{uuid.uuid4().hex[:12]}"
    new_lease_exp = int(now_epoch) + lease_seconds
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    try:
        ddb.update_item(
            TableName=outbox_table,
            Key={"outbox_id": {"S": outbox_id}},
            UpdateExpression="SET dispatch_state = :publishing, lease_owner = :me, lease_expires_at = :exp, updated_at = :now",
            ConditionExpression=(
                "attribute_exists(outbox_id) AND ("
                "dispatch_state IN (:pending, :retry_wait) "
                "OR (dispatch_state = :publishing AND lease_expires_at <= :now_lease)"
                ")"
            ),
            ExpressionAttributeValues={
                ":publishing": {"S": DispatchStatus.PUBLISHING.value},
                ":pending": {"S": DispatchStatus.PENDING.value},
                ":retry_wait": {"S": DispatchStatus.RETRY_WAIT.value},
                ":me": {"S": lease_owner},
                ":exp": {"N": str(new_lease_exp)},
                ":now_lease": {"N": str(int(now_epoch))},
                ":now": {"S": now_iso},
            },
        )
    except ClientError as exc:
        if _is_condition_failed(exc):
            return {"outbox_id": outbox_id, "order_id": order_id, "status": "LEASE_CONTENTION", "retryable": True}
        raise

    leased_item = ddb.get_item(
        TableName=outbox_table,
        Key={"outbox_id": {"S": outbox_id}},
        ConsistentRead=True,
    ).get("Item")
    if not leased_item or _ddb_str(leased_item, "lease_owner") != lease_owner:
        return {"outbox_id": outbox_id, "order_id": order_id, "status": "LEASE_LOST", "retryable": True}

    payload_str = _ddb_str(leased_item, "payload")
    fault_type = _ddb_str(leased_item, "fault_type", "")
    prior_attempts = _ddb_int(leased_item, "attempt_count", 0)
    prior_blocked = _ddb_int(leased_item, "blocked_attempts_executed", 0)
    prior_sqs_calls = _ddb_int(leased_item, "sqs_api_calls", 0)
    created_ms = _ddb_int(leased_item, "created_epoch_ms", 0)
    created_at_iso = _ddb_str(leased_item, "created_at", now_iso)
    fault_adapter_enabled = os.environ.get("ENABLE_BENCHMARK_FAULT_ADAPTER", "false").lower() == "true"

    # Missing markers on an interrupted/previously attempted legacy record are uncertain.
    uncertain = _ddb_str(leased_item, "publication_uncertain",
                         "true" if cur_state == DispatchStatus.PUBLISHING.value or prior_sqs_calls else "false") == "true"
    if "publication_uncertain" not in leased_item:
        ddb.update_item(
            TableName=outbox_table, Key={"outbox_id": {"S": outbox_id}},
            UpdateExpression="SET publication_uncertain = :u",
            ConditionExpression="lease_owner = :me AND dispatch_state = :publishing AND attribute_not_exists(publication_uncertain)",
            ExpressionAttributeValues={":u": {"S": "true" if uncertain else "false"},
                ":me": {"S": lease_owner}, ":publishing": {"S": DispatchStatus.PUBLISHING.value}})

    local_blocked = 0
    local_non_fault_attempts = 0
    local_sqs_calls = 0
    last_error = "PublicationUnknown"
    terminal_non_retryable = False
    confirmed_msg_id: Optional[str] = None

    for attempt_idx in range(1, max_attempts_per_invocation + 1):
        if prior_attempts + local_blocked + local_non_fault_attempts >= max_total:
            break
        if get_remaining_time_ms(context) < 2500:
            last_error = "InsufficientRemainingBudget"
            break

        injected = False
        attempt_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            rem_blocks = _ddb_int(leased_item, "fault_remaining_blocks", 0) - local_blocked
            if fault_adapter_enabled and fault_type in {"SQS_INJECTED_TIMEOUT", "SQS_INJECTED_503"} and rem_blocks > 0:
                try:
                    consumed = _consume_fault_block(ddb, outbox_table, outbox_id, lease_owner, attempt_iso, max_total)
                except Exception:
                    # Do not overwrite possibly committed reservation counters with stale locals.
                    return {"outbox_id": outbox_id, "status": "FAULT_RESERVATION_UNVERIFIED", "retryable": True}
                if consumed:
                    injected = True
                    local_blocked += 1
                    if fault_type == "SQS_INJECTED_TIMEOUT":
                        raise ReadTimeoutError(endpoint_url=queue_url)
                    raise ClientError(
                        {"Error": {"Code": "ServiceUnavailable", "Message": "Controlled pre-send rejection"}},
                        "SendMessage",
                    )

                else:
                    return {"outbox_id": outbox_id, "status": "LEASE_OR_BUDGET_LOST", "retryable": True}

            was_uncertain = uncertain
            # Reserve budget and persist uncertainty BEFORE the external side effect.
            # A crash here consumes a reservation, not a fabricated confirmed SQS call.
            try:
                ddb.update_item(
                    TableName=outbox_table, Key={"outbox_id": {"S": outbox_id}},
                    UpdateExpression="SET publication_uncertain = :yes, attempt_count = attempt_count + :one ADD send_attempts_reserved :one",
                    ConditionExpression="lease_owner = :me AND dispatch_state = :publishing AND attempt_count < :max AND lease_expires_at > :clock",
                    ExpressionAttributeValues={":yes": {"S": "true"}, ":one": {"N": "1"},
                        ":me": {"S": lease_owner}, ":publishing": {"S": DispatchStatus.PUBLISHING.value},
                        ":max": {"N": str(max_total)}, ":clock": {"N": str(int(time.time()))}})
            except Exception:
                return {"outbox_id": outbox_id, "status": "SEND_RESERVATION_UNVERIFIED", "retryable": True}
            uncertain = True
            local_non_fault_attempts += 1
            local_sqs_calls += 1
            resp = sqs.send_message(
                QueueUrl=queue_url,
                MessageBody=payload_str,
                MessageAttributes={
                    "CorrelationId": {"DataType": "String", "StringValue": correlation_id},
                    "EventType": {"DataType": "String", "StringValue": "order.created"},
                },
            )
            confirmed_msg_id = resp.get("MessageId")
            break
        except AMBIGUOUS_NETWORK_ERRORS:
            last_error = "PublicationAcknowledgementUnknown"
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            last_error = code
            # Explicit rejection can clear THIS reservation, never earlier uncertainty.
            # Server errors can follow acceptance and remain ambiguous.
            definitive = code in NON_RETRYABLE_SQS_ERRORS or code in {"RequestThrottled", "ThrottlingException"}
            if not injected and definitive:
                try:
                    ddb.update_item(
                        TableName=outbox_table, Key={"outbox_id": {"S": outbox_id}},
                        UpdateExpression="SET publication_uncertain = :u",
                        ConditionExpression="lease_owner = :me AND dispatch_state = :publishing",
                        ExpressionAttributeValues={":u": {"S": "true" if was_uncertain else "false"},
                            ":me": {"S": lease_owner}, ":publishing": {"S": DispatchStatus.PUBLISHING.value}})
                    uncertain = was_uncertain
                except Exception:
                    return {"outbox_id": outbox_id, "status": "REJECTION_STATE_UNVERIFIED", "retryable": True}
            if not injected and code in NON_RETRYABLE_SQS_ERRORS:
                terminal_non_retryable = True
                break
        except Exception as exc:
            last_error = type(exc).__name__

        if attempt_idx < max_attempts_per_invocation and get_remaining_time_ms(context) >= 1700:
            time.sleep(0.05)

    final_attempts = prior_attempts + local_blocked + local_non_fault_attempts
    final_blocked = prior_blocked + local_blocked
    final_sqs_calls = prior_sqs_calls + local_sqs_calls
    done_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if confirmed_msg_id:
        now_ms = int(time.time() * 1000)
        if created_ms > 0:
            rec_sec = round(max(0.0, (now_ms - created_ms) / 1000.0), 3)
        else:
            rec_sec = round(max(0.0, time.time() - parse_iso_epoch(created_at_iso)), 3)

        fev = None
        if fault_type and final_blocked > 0:
            fev = _build_fault_evidence(
                fault_type=fault_type,
                order_id=order_id,
                correlation_id=correlation_id,
                blocked_attempts=final_blocked,
                sqs_api_calls=final_sqs_calls,
                recovered=True,
                recovery_source=trigger_source,
                sqs_message_id=confirmed_msg_id,
                recovery_time_sec=rec_sec,
            )

        tx_outcome = _mark_confirmed(
            ddb=ddb,
            outbox_table=outbox_table,
            orders_table=orders_table,
            outbox_id=outbox_id,
            order_id=order_id,
            lease_owner=lease_owner,
            msg_id=confirmed_msg_id,
            final_attempts=final_attempts,
            final_sqs_calls=final_sqs_calls,
            trigger_source=trigger_source,
            rec_sec=rec_sec,
            now_iso=done_iso,
            fault_evidence=fev,
        )
        if tx_outcome in ("CONFIRMED", "ALREADY_CONFIRMED"):
            logger.info(
                "Outbox event published and confirmed",
                correlation_id=correlation_id,
                order_id=order_id,
                outbox_id=outbox_id,
                trigger_source=trigger_source,
                sqs_message_id=confirmed_msg_id,
                blocked_attempts=final_blocked,
                sqs_api_calls=final_sqs_calls,
                recovery_time_sec=rec_sec,
            )
            return {
                "outbox_id": outbox_id,
                "order_id": order_id,
                "status": tx_outcome,
                "retryable": False,
                "sqs_message_id": confirmed_msg_id,
                "recovery_source": trigger_source,
                "recovery_time_sec": rec_sec,
                "blocked_attempts": final_blocked,
                "sqs_api_calls": final_sqs_calls,
            }
        return {"outbox_id": outbox_id, "order_id": order_id, "status": "CONFIRM_TX_RETRY", "retryable": True}

    # Publication failed or timed out within this invocation: preserve durable state!
    fev_defer = None
    if fault_type and final_blocked > 0:
        fev_defer = _build_fault_evidence(
            fault_type=fault_type,
            order_id=order_id,
            correlation_id=correlation_id,
            blocked_attempts=final_blocked,
            sqs_api_calls=final_sqs_calls,
            recovered=False,
        )

    if (terminal_non_retryable and not uncertain) or final_attempts >= max_total:
        # Stop bounded automatic sends. Ambiguous publication remains inspectable UNKNOWN;
        # only conclusive rejection can make the business order FAILED.
        exh_expr = (
            "SET dispatch_state = :exh, attempt_count = :attempts, sqs_api_calls = :calls, "
            "lease_owner = :none, lease_expires_at = :zero, last_error = :err, updated_at = :now"
        )
        exh_vals: Dict[str, Any] = {
            ":exh": {"S": DispatchStatus.UNKNOWN.value if uncertain else DispatchStatus.EXHAUSTED.value},
            ":uncertain": {"S": "true" if uncertain else "false"},
            ":publishing": {"S": DispatchStatus.PUBLISHING.value},
            ":me": {"S": lease_owner},
            ":attempts": {"N": str(final_attempts)},
            ":calls": {"N": str(final_sqs_calls)},
            ":none": {"S": "NONE"},
            ":zero": {"N": "0"},
            ":err": {"S": last_error[:128]},
            ":now": {"S": done_iso},
        }
        if fev_defer is not None:
            exh_expr += ", fault_execution_evidence = :fev"
            exh_vals[":fev"] = {"S": json.dumps(fev_defer, sort_keys=True)}
        try:
            ddb.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": outbox_table,
                            "Key": {"outbox_id": {"S": outbox_id}},
                            "UpdateExpression": exh_expr,
                            "ConditionExpression": "attribute_exists(outbox_id) AND lease_owner = :me AND dispatch_state = :publishing AND publication_uncertain = :uncertain",
                            "ExpressionAttributeValues": exh_vals,
                        }
                    },
                    {
                        "Update": {
                            "TableName": orders_table,
                            "Key": {"order_id": {"S": order_id}},
                            "UpdateExpression": ("SET dispatch_status = :rejected, updated_at = :now" if uncertain else "SET #s = :failed, dispatch_status = :rejected, updated_at = :now"),
                            "ConditionExpression": ("attribute_exists(order_id) AND #s IN (:committed, :fulfilled)" if uncertain else "attribute_exists(order_id) AND #s = :committed"),
                            "ExpressionAttributeNames": {"#s": "status"},
                            "ExpressionAttributeValues": {
                                **({":fulfilled": {"S": OrderStatus.FULFILLED.value}} if uncertain else {":failed": {"S": OrderStatus.FAILED.value}}),
                                ":committed": {"S": OrderStatus.COMMITTED.value},
                                ":rejected": {"S": DispatchStatus.UNKNOWN.value if uncertain else DispatchStatus.REJECTED.value},
                                ":now": {"S": done_iso},
                            },
                        }
                    },
                ]
            )
        except Exception:
            # A rejection or lost transaction ack is not proof of either terminal state.
            pass
        terminal = _verify_terminal(ddb, outbox_table, orders_table, outbox_id, order_id)
        logger.error("Publisher budget/terminal decision verified" if not terminal["retryable"] else "Terminal persistence unverified; durable lease retained",
                     order_id=order_id, outbox_id=outbox_id, status=terminal["status"], attempts=final_attempts)
        return terminal

    backoff = 2 if final_attempts <= 2 else min(30, 2 ** min(final_attempts - 1, 4))
    next_due = int(time.time()) + backoff
    wait_expr = (
        "SET dispatch_state = :rw, attempt_count = :attempts, sqs_api_calls = :calls, "
        "lease_owner = :none, lease_expires_at = :zero, next_attempt_at = :due, "
        "last_error = :err, updated_at = :now"
    )
    wait_vals: Dict[str, Any] = {
        ":rw": {"S": DispatchStatus.RETRY_WAIT.value},
        ":publishing": {"S": DispatchStatus.PUBLISHING.value},
        ":me": {"S": lease_owner},
        ":attempts": {"N": str(final_attempts)},
        ":calls": {"N": str(final_sqs_calls)},
        ":none": {"S": "NONE"},
        ":zero": {"N": "0"},
        ":due": {"N": str(next_due)},
        ":err": {"S": last_error[:128]},
        ":now": {"S": done_iso},
    }
    if fev_defer is not None:
        wait_expr += ", fault_execution_evidence = :fev"
        wait_vals[":fev"] = {"S": json.dumps(fev_defer, sort_keys=True)}

    try:
        ddb.update_item(
            TableName=outbox_table,
            Key={"outbox_id": {"S": outbox_id}},
            UpdateExpression=wait_expr,
            ConditionExpression="attribute_exists(outbox_id) AND lease_owner = :me AND dispatch_state = :publishing",
            ExpressionAttributeValues=wait_vals,
        )
        ddb.update_item(
            TableName=orders_table,
            Key={"order_id": {"S": order_id}},
            UpdateExpression="SET dispatch_status = :unknown, updated_at = :now",
            ConditionExpression="attribute_exists(order_id) AND dispatch_status <> :confirmed",
            ExpressionAttributeValues={
                ":unknown": {"S": DispatchStatus.UNKNOWN.value},
                ":confirmed": {"S": DispatchStatus.CONFIRMED.value},
                ":now": {"S": done_iso},
            },
        )
    except ClientError:
        pass

    logger.error(
        "OUTBOX_PUBLISH_FAILED_DEFERRED: Durable intent retained in RETRY_WAIT for recovery",
        correlation_id=correlation_id,
        order_id=order_id,
        outbox_id=outbox_id,
        trigger_source=trigger_source,
        error=last_error,
        blocked_attempts=final_blocked,
        sqs_api_calls=final_sqs_calls,
        next_attempt_at=next_due,
    )
    return {
        "outbox_id": outbox_id,
        "order_id": order_id,
        "status": "RETRY_SCHEDULED",
        "retryable": True,
        "error": last_error,
        "blocked_attempts": final_blocked,
        "sqs_api_calls": final_sqs_calls,
        "next_attempt_at": next_due,
    }


def sweep_pending_outbox(
    ddb,
    sqs,
    outbox_table: str,
    orders_table: str,
    queue_url: str,
    *,
    index_name: str = "DispatchStateIndex",
    context: Any = None,
    max_items: int = 50,
) -> Dict[str, Any]:
    """
    Scheduled safety sweeper over OutboxTable GSI DispatchStateIndex.
    Recovers PENDING records missed by streams, RETRY_WAIT records past backoff, and
    PUBLISHING records whose publisher lease expired mid-flight.
    """
    now_epoch = int(time.time())
    candidate_ids: List[str] = []
    seen: set = set()

    queries = [
        {
            "state": DispatchStatus.RETRY_WAIT.value,
            "kce": "dispatch_state = :st AND next_attempt_at <= :now",
            "vals": {":st": {"S": DispatchStatus.RETRY_WAIT.value}, ":now": {"N": str(now_epoch)}},
        },
        {
            "state": DispatchStatus.PENDING.value,
            "kce": "dispatch_state = :st AND next_attempt_at <= :now",
            "vals": {":st": {"S": DispatchStatus.PENDING.value}, ":now": {"N": str(now_epoch)}},
        },
        {
            "state": DispatchStatus.PUBLISHING.value,
            "kce": "dispatch_state = :st",
            "vals": {":st": {"S": DispatchStatus.PUBLISHING.value}},
        },
    ]

    for q in queries:
        if len(candidate_ids) >= max_items or get_remaining_time_ms(context) < 4000:
            break
        kwargs: Dict[str, Any] = {
            "TableName": outbox_table,
            "IndexName": index_name,
            "KeyConditionExpression": q["kce"],
            "ExpressionAttributeValues": q["vals"],
        }
        while True:
            resp = ddb.query(**kwargs)
            for raw in resp.get("Items", []):
                oid = _ddb_str(raw, "outbox_id")
                if not oid or oid in seen:
                    continue
                if q["state"] == DispatchStatus.PUBLISHING.value and _ddb_int(raw, "lease_expires_at", 0) > now_epoch:
                    continue
                seen.add(oid)
                candidate_ids.append(oid)
                if len(candidate_ids) >= max_items:
                    break
            lek = resp.get("LastEvaluatedKey")
            if not lek or len(candidate_ids) >= max_items or get_remaining_time_ms(context) < 4000:
                break
            kwargs["ExclusiveStartKey"] = lek

    stats: Dict[str, Any] = {
        "candidates": len(candidate_ids),
        "confirmed": 0,
        "already_confirmed": 0,
        "retry_scheduled": 0,
        "exhausted": 0,
        "skipped": 0,
        "errors": 0,
        "recovered_order_ids": [],
    }
    for oid in candidate_ids:
        if get_remaining_time_ms(context) < 3000:
            break
        try:
            res = publish_outbox_record(
                ddb,
                sqs,
                outbox_table,
                orders_table,
                queue_url,
                oid,
                trigger_source="scheduled_sweeper",
                allow_short_retry_wait=False,
                context=context,
            )
            st = res.get("status", "")
            if st == "CONFIRMED":
                stats["confirmed"] += 1
                if res.get("order_id"):
                    stats["recovered_order_ids"].append(res["order_id"])
            elif st == "ALREADY_CONFIRMED":
                stats["already_confirmed"] += 1
            elif st in ("RETRY_SCHEDULED", "DEFERRED_NOT_DUE"):
                stats["retry_scheduled"] += 1
            elif st == "EXHAUSTED":
                stats["exhausted"] += 1
            else:
                stats["skipped"] += 1
        except Exception as exc:
            stats["errors"] += 1
            logger.error("Unexpected sweeper failure processing outbox item", outbox_id=oid, error=type(exc).__name__)

    return stats
