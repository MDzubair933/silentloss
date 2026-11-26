import hashlib
import json
import os
import time
from botocore.exceptions import ClientError, ReadTimeoutError, ConnectTimeoutError, EndpointConnectionError

from common.aws_clients import create_dynamodb_client, get_remaining_time_ms
from common.logger import StructuredLogger
from common.models import FulfillmentStatus, OrderStatus
from common.validation import validate_fulfillment_event

logger = StructuredLogger("silentloss-fulfillment-worker")

_dynamodb = None

AMBIGUOUS_NETWORK_ERRORS = (
    ReadTimeoutError,
    ConnectTimeoutError,
    EndpointConnectionError,
    TimeoutError,
)


def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = create_dynamodb_client(worker=True)
    return _dynamodb


def handler(event, context):
    """
    AWS Lambda entrypoint for SQS Fulfillment Worker.
    Isolates per-record exceptions and supports SQS partial batch response (ReportBatchItemFailures).
    """
    records = event.get("Records") or []
    batch_item_failures = []

    logger.info("Fulfillment worker triggered", batch_size=len(records))

    for record in records:
        message_id = record.get("messageId", "UNKNOWN")
        if get_remaining_time_ms(context) < 3500:
            batch_item_failures.append({"itemIdentifier": message_id})
            continue
        try:
            success, is_retryable, error_code, error_msg = process_message(record)
        except Exception as exc:
            # Unexpected unhandled failure must not crash sibling messages in the batch
            logger.error(
                "Unhandled exception processing SQS record",
                message_id=message_id,
                error_type=type(exc).__name__,
            )
            success = False
            is_retryable = True
            error_code = "UnhandledWorkerException"
            error_msg = type(exc).__name__

        if not success:
            if is_retryable:
                logger.warn(
                    "Retryable fulfillment failure; returning itemIdentifier for SQS retry",
                    message_id=message_id,
                    error_code=error_code,
                )
                batch_item_failures.append({"itemIdentifier": message_id})
            else:
                # Durable rejection recorded inside process_message; never silently discarded
                logger.error(
                    "Permanent validation/state rejection durably audited",
                    message_id=message_id,
                    error_code=error_code,
                    reason=error_code,
                )

    return {"batchItemFailures": batch_item_failures}


def _record_durable_rejection(
    ddb,
    rejection_table_name: str,
    message_id: str,
    raw_body: str,
    error_code: str,
    reason: str,
    order_id: str = None,
    correlation_id: str = None,
) -> bool:
    """
    Persist sanitized permanent rejection record so rejected/malformed messages
    are never silently discarded and raw sensitive payloads are never logged.
    """
    payload_sha256 = hashlib.sha256((raw_body or "").encode("utf-8", errors="replace")).hexdigest()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    item = {
        "rejection_id": {"S": f"rej_{message_id}"},
        "sqs_message_id": {"S": str(message_id)},
        "error_code": {"S": error_code},
        # Diagnostic codes are controlled by this module; payload text is not stored.
        "reason": {"S": error_code},
        "payload_sha256": {"S": payload_sha256},
        "payload_bytes": {"N": str(len((raw_body or "").encode("utf-8", errors="replace")))},
        "rejected_at": {"S": now_iso},
        "correlation_id": {"S": correlation_id or "NONE"},
    }
    if order_id:
        item["order_id"] = {"S": order_id}

    try:
        ddb.put_item(TableName=rejection_table_name, Item=item)
        return True
    except Exception as exc:
        logger.error(
            "Failed to persist durable rejection record; falling back to DLQ redrive",
            message_id=message_id,
            error_type=type(exc).__name__,
        )
        return False


def _verify_fulfillment_state(ddb, orders_table: str, fulfillment_table: str, order_id: str):
    """Consistent reread of both tables to resolve duplicate deliveries or ambiguous transactions."""
    order_res = ddb.get_item(
        TableName=orders_table,
        Key={"order_id": {"S": order_id}},
        ConsistentRead=True,
    )
    ful_res = ddb.get_item(
        TableName=fulfillment_table,
        Key={"order_id": {"S": order_id}, "fulfillment_id": {"S": "FULFILLMENT#PRIMARY"}},
        ConsistentRead=True,
    )
    return order_res.get("Item"), ful_res.get("Item")


def process_message(record: dict):
    """
    Validate SQS event envelope, cross-check authoritative order, and perform
    atomic DynamoDB transaction for fulfillment + order state transition.
    Returns: (success: bool, is_retryable: bool, error_code: str|None, error_message: str|None)
    """
    message_id = record.get("messageId", "UNKNOWN")
    orders_table_name = os.environ.get("ORDERS_TABLE_NAME", "silentloss-orders")
    fulfillment_table_name = os.environ.get("FULFILLMENT_TABLE_NAME", "silentloss-fulfillment")
    rejection_table_name = os.environ.get("REJECTION_AUDIT_TABLE_NAME", "silentloss-rejections")

    raw_body = record.get("body", "")
    ddb = get_dynamodb()

    # Step 1: Validate strict event envelope (no raw payload logged on failure)
    is_valid, err_msg, body = validate_fulfillment_event(raw_body)
    if not is_valid or body is None:
        persisted = _record_durable_rejection(
            ddb,
            rejection_table_name,
            message_id,
            raw_body,
            "InvalidEventEnvelope",
            err_msg or "Malformed event",
        )
        # If durable audit succeeded, prevent pointless retries; otherwise allow SQS DLQ redrive
        return False, (not persisted), "InvalidEventEnvelope", "Invalid event envelope"

    correlation_id = body["correlation_id"]
    event_data = body["data"]
    order_id = event_data["order_id"]

    # Step 2: Fetch authoritative order record from OrdersTable
    try:
        order_res = ddb.get_item(
            TableName=orders_table_name,
            Key={"order_id": {"S": order_id}},
            ConsistentRead=True,
        )
    except (ClientError, *AMBIGUOUS_NETWORK_ERRORS) as exc:
        logger.warn("Transient error reading authoritative order", correlation_id=correlation_id, order_id=order_id)
        return False, True, "TransientReadError", type(exc).__name__

    if "Item" not in order_res:
        persisted = _record_durable_rejection(
            ddb,
            rejection_table_name,
            message_id,
            raw_body,
            "OrderNotFound",
            f"Referenced order {order_id} missing in OrdersTable",
            order_id=order_id,
            correlation_id=correlation_id,
        )
        return False, (not persisted), "OrderNotFound", f"Referenced order {order_id} missing"

    auth_order = order_res["Item"]
    auth_customer_id = auth_order.get("customer_id", {}).get("S", "")
    auth_currency = auth_order.get("currency", {}).get("S", "")
    auth_total_str = auth_order.get("total_amount", {}).get("S", "")
    auth_total_minor = int(auth_order.get("total_amount_minor", {}).get("N", 0))
    auth_status = auth_order.get("status", {}).get("S", "")

    # Step 3: Cross-check event fields against authoritative order values (B6)
    if "customer_id" in event_data and event_data["customer_id"] != auth_customer_id:
        persisted = _record_durable_rejection(
            ddb,
            rejection_table_name,
            message_id,
            raw_body,
            "StaleOrConflictingEvent",
            "Event customer_id mismatches authoritative order",
            order_id=order_id,
            correlation_id=correlation_id,
        )
        return False, (not persisted), "StaleOrConflictingEvent", "customer_id mismatch"

    if "currency" in event_data and event_data["currency"] != auth_currency:
        persisted = _record_durable_rejection(
            ddb,
            rejection_table_name,
            message_id,
            raw_body,
            "StaleOrConflictingEvent",
            "Event currency mismatches authoritative order",
            order_id=order_id,
            correlation_id=correlation_id,
        )
        return False, (not persisted), "StaleOrConflictingEvent", "currency mismatch"

    if "total_amount_minor" in event_data:
        ev_minor = event_data["total_amount_minor"]
        if isinstance(ev_minor, bool) or not isinstance(ev_minor, int) or ev_minor != auth_total_minor:
            persisted = _record_durable_rejection(
                ddb,
                rejection_table_name,
                message_id,
                raw_body,
                "StaleOrConflictingEvent",
                "Event monetary total mismatches authoritative order",
                order_id=order_id,
                correlation_id=correlation_id,
            )
            return False, (not persisted), "StaleOrConflictingEvent", "monetary total mismatch"

    # Reject non-eligible order states (unless already FULFILLED, handled as replay)
    if auth_status == OrderStatus.FULFILLED.value:
        # Check fulfillment table consistently
        try:
            _, existing_ful = _verify_fulfillment_state(ddb, orders_table_name, fulfillment_table_name, order_id)
            if existing_ful and existing_ful.get("status", {}).get("S") == FulfillmentStatus.COMPLETED.value:
                logger.info("Duplicate SQS delivery confirmed already fulfilled", correlation_id=correlation_id, order_id=order_id)
                return True, False, None, None
            # Conflicting state: order says FULFILLED, fulfillment record missing!
            persisted = _record_durable_rejection(
                ddb,
                rejection_table_name,
                message_id,
                raw_body,
                "InconsistentOrderState",
                "Order marked FULFILLED without matching FulfillmentTable record",
                order_id=order_id,
                correlation_id=correlation_id,
            )
            return False, (not persisted), "InconsistentOrderState", "Conflicting FULFILLED order state"
        except (ClientError, *AMBIGUOUS_NETWORK_ERRORS) as exc:
            return False, True, "TransientVerifyError", type(exc).__name__

    if auth_status != OrderStatus.COMMITTED.value:
        persisted = _record_durable_rejection(
            ddb,
            rejection_table_name,
            message_id,
            raw_body,
            "IneligibleOrderState",
            f"Order status '{auth_status}' is not eligible for fulfillment",
            order_id=order_id,
            correlation_id=correlation_id,
        )
        return False, (not persisted), "IneligibleOrderState", f"Status {auth_status} ineligible"

    # Step 4: Atomic DynamoDB Transaction (B5)
    # Using strictly authoritative order attributes!
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fulfillment_item = {
        "order_id": {"S": order_id},
        "fulfillment_id": {"S": "FULFILLMENT#PRIMARY"},
        "status": {"S": FulfillmentStatus.COMPLETED.value},
        "sqs_message_id": {"S": message_id},
        "event_id": {"S": body["event_id"]},
        "customer_id": {"S": auth_customer_id},
        "currency": {"S": auth_currency},
        "total_amount": {"S": auth_total_str},
        "total_amount_minor": {"N": str(auth_total_minor)},
        "processed_at": {"S": now_iso},
        "correlation_id": {"S": correlation_id},
    }

    try:
        ddb.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": fulfillment_table_name,
                        "Item": fulfillment_item,
                        "ConditionExpression": "attribute_not_exists(order_id) AND attribute_not_exists(fulfillment_id)",
                    }
                },
                {
                    "Update": {
                        "TableName": orders_table_name,
                        "Key": {"order_id": {"S": order_id}},
                        "UpdateExpression": "SET #s = :fulfilled, updated_at = :now ADD version :one",
                        "ConditionExpression": "attribute_exists(order_id) AND #s = :committed",
                        "ExpressionAttributeNames": {"#s": "status"},
                        "ExpressionAttributeValues": {
                            ":fulfilled": {"S": OrderStatus.FULFILLED.value},
                            ":committed": {"S": OrderStatus.COMMITTED.value},
                            ":now": {"S": now_iso},
                            ":one": {"N": "1"},
                        },
                    }
                },
            ]
        )
        logger.info("Atomic fulfillment transaction committed", correlation_id=correlation_id, order_id=order_id, message_id=message_id)
        return True, False, None, None

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "TransactionCanceledException":
            reasons = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
            # Absence of reasons is uncertainty, never proof of terminal conflict.
            if (not reasons or "ConditionalCheckFailed" not in reasons
                    or any(r not in ("None", "ConditionalCheckFailed") for r in reasons)):
                return False, True, "RetryableTransactionCancellation", "Transaction retry required"
            # Inspect consistent state to distinguish legitimate duplicate vs conflict
            try:
                cur_order, cur_ful = _verify_fulfillment_state(
                    ddb, orders_table_name, fulfillment_table_name, order_id
                )
                if (
                    cur_order
                    and cur_ful
                    and cur_order.get("status", {}).get("S") == OrderStatus.FULFILLED.value
                    and cur_ful.get("status", {}).get("S") == FulfillmentStatus.COMPLETED.value
                ):
                    logger.info("Concurrent duplicate delivery safely reconciled as already fulfilled", correlation_id=correlation_id, order_id=order_id)
                    return True, False, None, None

                # Otherwise genuine state conflict
                persisted = _record_durable_rejection(
                    ddb,
                    rejection_table_name,
                    message_id,
                    raw_body,
                    "TransactionConflict",
                    "Atomic fulfillment transaction canceled due to conflicting state",
                    order_id=order_id,
                    correlation_id=correlation_id,
                )
                return False, (not persisted), "TransactionConflict", "Conflicting state prevented fulfillment"
            except (ClientError, *AMBIGUOUS_NETWORK_ERRORS):
                return False, True, "TransientVerifyError", "Failed consistent reread after transaction cancellation"

        logger.warn("Transient DynamoDB error during fulfillment transaction", correlation_id=correlation_id, order_id=order_id, code=code)
        return False, True, "TransientTransactionError", code

    except AMBIGUOUS_NETWORK_ERRORS as exc:
        # Lost transaction acknowledgement: verify whether transaction actually committed!
        logger.warn("Lost acknowledgement on fulfillment transaction; verifying state", correlation_id=correlation_id, order_id=order_id)
        try:
            cur_order, cur_ful = _verify_fulfillment_state(
                ddb, orders_table_name, fulfillment_table_name, order_id
            )
            if (
                cur_order
                and cur_ful
                and cur_order.get("status", {}).get("S") == OrderStatus.FULFILLED.value
                and cur_ful.get("status", {}).get("S") == FulfillmentStatus.COMPLETED.value
            ):
                logger.info("Confirmed fulfillment transaction actually committed despite lost ack", correlation_id=correlation_id, order_id=order_id)
                return True, False, None, None
        except Exception:
            pass
        return False, True, "AmbiguousTransactionOutcome", type(exc).__name__
