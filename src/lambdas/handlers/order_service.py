import hashlib
import json
import os
import time
import uuid
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError

from common.aws_clients import create_dynamodb_client, get_remaining_time_ms
from common.logger import StructuredLogger
from common.models import DispatchStatus, OrderStatus, compute_payload_hash, deterministic_event_id
from common.validation import (
    parse_strict_json_body,
    validate_create_order_payload,
    validate_identifier,
)

logger = StructuredLogger("silentloss-order-service")

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
        _dynamodb = create_dynamodb_client(worker=False)
    return _dynamodb


def _json_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _header_get(headers: dict, name: str, default: str = None):
    if not headers:
        return default
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v
    return default


def handler(event, context):
    """
    AWS Lambda entrypoint for Order Service HTTP API (IAM-authenticated).
    Writes order, idempotency lock, and durable Outbox dispatch intent atomically
    into DynamoDB; performs ZERO direct synchronous SQS publication.
    """
    http_method = (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod", "")
    )
    raw_path = event.get("rawPath") or event.get("path", "")
    headers = event.get("headers") or {}
    correlation_id = _header_get(headers, "x-correlation-id") or str(uuid.uuid4())

    logger.info("Received request", correlation_id=correlation_id, method=http_method, path=raw_path)

    if http_method == "POST" and (raw_path.endswith("/orders") or raw_path.endswith("/orders/")):
        return create_order(event, context, correlation_id)
    if http_method == "GET" and "/orders/" in raw_path:
        path_params = event.get("pathParameters") or {}
        order_id = path_params.get("order_id") or raw_path.rstrip("/").split("/")[-1]
        return get_order(order_id, correlation_id)

    return _json_response(
        404,
        {"error": "NotFound", "message": f"Route not found: {http_method} {raw_path}"},
    )


def _fetch_existing_by_idempotency(ddb, idemp_table: str, orders_table: str, idempotency_key: str):
    res = ddb.get_item(
        TableName=idemp_table,
        Key={"idempotency_key": {"S": idempotency_key}},
        ConsistentRead=True,
    )
    if "Item" not in res:
        return None, None
    idemp_item = res["Item"]
    order_id = idemp_item["order_id"]["S"]
    order_res = ddb.get_item(
        TableName=orders_table,
        Key={"order_id": {"S": order_id}},
        ConsistentRead=True,
    )
    return idemp_item, order_res.get("Item")


def _build_replay_response(order_id: str, order_item: dict, correlation_id: str) -> dict:
    status_val = order_item.get("status", {}).get("S", OrderStatus.COMMITTED.value) if order_item else OrderStatus.COMMITTED.value
    dispatch_val = order_item.get("dispatch_status", {}).get("S", DispatchStatus.PENDING.value) if order_item else DispatchStatus.PENDING.value
    body = {
        "order_id": order_id,
        "status": status_val,
        "db_outcome": "CONFIRMED",
        "queue_outcome": dispatch_val,
        "is_idempotent_replay": True,
        "correlation_id": correlation_id,
    }
    if order_item and "outbox_id" in order_item:
        body["outbox_id"] = order_item["outbox_id"]["S"]
    return _json_response(200, body)


def create_order(event, context, correlation_id: str):
    orders_table_name = os.environ.get("ORDERS_TABLE_NAME", "silentloss-orders")
    idempotency_table_name = os.environ.get("IDEMPOTENCY_TABLE_NAME", "silentloss-idempotency")
    outbox_table_name = os.environ.get("OUTBOX_TABLE_NAME", "silentloss-outbox")

    raw_body = event.get("body")
    parsed_body, parse_err = parse_strict_json_body(raw_body)
    if parse_err or parsed_body is None:
        return _json_response(400, {"error": "InvalidJSON", "message": parse_err})

    is_valid, validation_error, normalized = validate_create_order_payload(parsed_body)
    if not is_valid or normalized is None:
        logger.warn("Validation failed", correlation_id=correlation_id, error=validation_error)
        return _json_response(400, {"error": "ValidationError", "message": validation_error})

    headers = event.get("headers") or {}
    experimental = os.environ.get("ENABLE_BENCHMARK_FAULT_ADAPTER", "false").lower() == "true"
    requested_fault = _header_get(headers, "x-benchmark-fault") or _header_get(headers, "x-fault-injection")
    supported_faults = {"SQS_INJECTED_TIMEOUT", "SQS_INJECTED_503"}
    if requested_fault and requested_fault not in supported_faults:
        return _json_response(400, {"error": "UnsupportedBenchmarkFault"})
    if experimental:
        caller = (event.get("requestContext", {}).get("authorizer", {}).get("iam", {}).get("userArn"))
        controller = os.environ.get("BENCHMARK_CONTROLLER_ARN", "")
        if not controller or caller != controller:
            return _json_response(403, {"error": "BenchmarkControllerRequired"})

    raw_idemp_key = _header_get(headers, "idempotency-key") or parsed_body.get("idempotency_key")
    explicit_order_id = normalized["explicit_order_id"]

    if raw_idemp_key is not None:
        err = validate_identifier(raw_idemp_key, "Idempotency-Key")
        if err:
            return _json_response(400, {"error": "ValidationError", "message": err})
        idempotency_key = raw_idemp_key
    elif explicit_order_id is not None:
        idempotency_key = explicit_order_id
    else:
        generated = f"ord_{uuid.uuid4().hex[:16]}"
        idempotency_key = generated
        explicit_order_id = None

    canonical_order_id = explicit_order_id or (
        idempotency_key if idempotency_key.startswith("ord_") else
        "ord_" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:48]
    )

    payload_hash = compute_payload_hash(
        customer_id=normalized["customer_id"],
        currency=normalized["currency"],
        total_amount_minor=normalized["total_amount_minor"],
        items_normalized=normalized["items"],
        explicit_order_id=explicit_order_id or "",
    )

    if get_remaining_time_ms(context) < 5000:
        return _json_response(
            503,
            {
                "error": "InsufficientExecutionBudget",
                "db_outcome": "NOT_ATTEMPTED",
                "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
            },
        )
    ddb = get_dynamodb()

    # Step 1a: Pre-check existing idempotency lock (strongly consistent)
    try:
        idemp_item, order_item = _fetch_existing_by_idempotency(
            ddb, idempotency_table_name, orders_table_name, idempotency_key
        )
        if idemp_item is not None:
            stored_hash = idemp_item.get("payload_hash", {}).get("S", "")
            if stored_hash != payload_hash:
                logger.warn(
                    "Idempotency conflict detected",
                    correlation_id=correlation_id,
                    idempotency_key=idempotency_key,
                )
                return _json_response(
                    409,
                    {
                        "error": "IdempotencyKeyConflict",
                        "message": "Idempotency key reused with conflicting request payload",
                    },
                )
            return _build_replay_response(idemp_item["order_id"]["S"], order_item, correlation_id)
    except AMBIGUOUS_NETWORK_ERRORS as exc:
        logger.error("Timeout checking idempotency key", correlation_id=correlation_id, error=str(exc))
        return _json_response(
            504,
            {
                "error": "DatabaseOutcomeUnknown",
                "db_outcome": "UNKNOWN",
                "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
                "message": "Timed out checking idempotency state; safe to retry with identical Idempotency-Key",
                "idempotency_key": idempotency_key,
                "correlation_id": correlation_id,
            },
        )
    except ClientError as exc:
        logger.error("DynamoDB error checking idempotency key", correlation_id=correlation_id, error=str(exc))
        return _json_response(
            503,
            {
                "error": "DatabaseUnavailable",
                "db_outcome": "REJECTED",
                "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
                "message": "Database temporarily unavailable before commit",
                "correlation_id": correlation_id,
            },
        )

    now_epoch = int(time.time())
    now_epoch_ms = int(time.time() * 1000)
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))
    stable_event_id = deterministic_event_id(canonical_order_id, "order.created")
    outbox_id = f"outbox#{canonical_order_id}"

    event_payload = {
        "event_version": "1.0",
        "event_type": "order.created",
        "event_id": stable_event_id,
        "timestamp": now_iso,
        "correlation_id": correlation_id,
        "data": {
            "order_id": canonical_order_id,
            "customer_id": normalized["customer_id"],
            "total_amount": normalized["total_amount_str"],
            "total_amount_minor": normalized["total_amount_minor"],
            "currency": normalized["currency"],
            "items": normalized["items"],
        },
    }

    order_item_ddb = {
        "order_id": {"S": canonical_order_id},
        "idempotency_key": {"S": idempotency_key},
        "outbox_id": {"S": outbox_id},
        "event_id": {"S": stable_event_id},
        "customer_id": {"S": normalized["customer_id"]},
        "total_amount": {"S": normalized["total_amount_str"]},
        "total_amount_minor": {"N": str(normalized["total_amount_minor"])},
        "currency": {"S": normalized["currency"]},
        "items": {"S": json.dumps(normalized["items"])},
        "status": {"S": OrderStatus.COMMITTED.value},
        "dispatch_status": {"S": DispatchStatus.PENDING.value},
        "created_at": {"S": now_iso},
        "updated_at": {"S": now_iso},
        "payload_hash": {"S": payload_hash},
        "correlation_id": {"S": correlation_id},
        "version": {"N": "1"},
    }
    idemp_item_ddb = {
        "idempotency_key": {"S": idempotency_key},
        "order_id": {"S": canonical_order_id},
        "outbox_id": {"S": outbox_id},
        "payload_hash": {"S": payload_hash},
        "created_at": {"S": now_iso},
    }
    outbox_item_ddb = {
        "outbox_id": {"S": outbox_id},
        "order_id": {"S": canonical_order_id},
        "event_id": {"S": stable_event_id},
        "event_version": {"S": "1.0"},
        "event_type": {"S": "order.created"},
        "dispatch_state": {"S": DispatchStatus.PENDING.value},
        "payload": {"S": json.dumps(event_payload)},
        "correlation_id": {"S": correlation_id},
        "attempt_count": {"N": "0"},
        "blocked_attempts_executed": {"N": "0"},
        "sqs_api_calls": {"N": "0"},
        "lease_owner": {"S": "NONE"},
        "lease_expires_at": {"N": "0"},
        "next_attempt_at": {"N": str(now_epoch)},
        "created_epoch_ms": {"N": str(now_epoch_ms)},
        "created_at": {"S": now_iso},
        "updated_at": {"S": now_iso},
    }
    if experimental and requested_fault:
        outbox_item_ddb["fault_type"] = {"S": requested_fault}
        outbox_item_ddb["fault_remaining_blocks"] = {"N": "2" if requested_fault == "SQS_INJECTED_TIMEOUT" else "1"}

    # Step 1b: Single Atomic Transaction committing Idempotency + Order + Durable Outbox Intent
    try:
        ddb.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": idempotency_table_name,
                        "Item": idemp_item_ddb,
                        "ConditionExpression": "attribute_not_exists(idempotency_key)",
                    }
                },
                {
                    "Put": {
                        "TableName": orders_table_name,
                        "Item": order_item_ddb,
                        "ConditionExpression": "attribute_not_exists(order_id)",
                    }
                },
                {
                    "Put": {
                        "TableName": outbox_table_name,
                        "Item": outbox_item_ddb,
                        "ConditionExpression": "attribute_not_exists(outbox_id)",
                    }
                },
            ]
        )
        logger.info(
            "Order and durable outbox intent committed atomically",
            correlation_id=correlation_id,
            order_id=canonical_order_id,
            outbox_id=outbox_id,
            event_id=stable_event_id,
        )

    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "TransactionCanceledException":
            reasons = [r.get("Code") for r in exc.response.get("CancellationReasons", [])]
            if (
                not reasons
                or "ConditionalCheckFailed" not in reasons
                or any(r not in ("None", "ConditionalCheckFailed") for r in reasons)
            ):
                return _json_response(
                    503,
                    {
                        "error": "RetryableTransactionCancellation",
                        "db_outcome": "UNKNOWN",
                        "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
                    },
                )
            try:
                raced_idemp, raced_order = _fetch_existing_by_idempotency(
                    ddb, idempotency_table_name, orders_table_name, idempotency_key
                )
                if raced_idemp is not None:
                    if raced_idemp.get("payload_hash", {}).get("S") == payload_hash:
                        return _build_replay_response(raced_idemp["order_id"]["S"], raced_order, correlation_id)
                    return _json_response(
                        409,
                        {
                            "error": "IdempotencyKeyConflict",
                            "message": "Concurrent conflicting request on same Idempotency-Key",
                        },
                    )
                return _json_response(
                    409,
                    {
                        "error": "OrderIdConflict",
                        "message": f"Order ID '{canonical_order_id}' already exists under another idempotency key",
                    },
                )
            except Exception as reread_exc:
                logger.error("Failed strongly consistent reread after race", correlation_id=correlation_id, error=str(reread_exc))
                return _json_response(
                    503,
                    {
                        "error": "DatabaseOutcomeUnknown",
                        "db_outcome": "UNKNOWN",
                        "message": "Concurrent conflict occurred and state verification failed",
                    },
                )

        logger.error("DynamoDB transaction rejected", correlation_id=correlation_id, code=code)
        return _json_response(
            500,
            {
                "error": "DatabaseCommitError",
                "db_outcome": "REJECTED",
                "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
                "message": "Failed to persist order and outbox transaction to database",
                "correlation_id": correlation_id,
            },
        )

    except AMBIGUOUS_NETWORK_ERRORS as exc:
        logger.warn("Ambiguous DB commit timeout; checking consistent state", correlation_id=correlation_id, error=str(exc))
        if get_remaining_time_ms(context) > 3000:
            try:
                check_idemp, check_order = _fetch_existing_by_idempotency(
                    ddb, idempotency_table_name, orders_table_name, idempotency_key
                )
                outbox_res = ddb.get_item(
                    TableName=outbox_table_name,
                    Key={"outbox_id": {"S": outbox_id}},
                    ConsistentRead=True,
                )
                if check_idemp is not None and check_order is not None and "Item" in outbox_res:
                    if check_idemp.get("payload_hash", {}).get("S") != payload_hash:
                        return _json_response(409, {"error": "IdempotencyKeyConflict"})
                    logger.info("Confirmed DB+outbox commit succeeded after lost ack", correlation_id=correlation_id, order_id=canonical_order_id)
                else:
                    return _json_response(
                        504,
                        {
                            "error": "DatabaseCommitOutcomeUnknown",
                            "db_outcome": "UNKNOWN",
                            "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
                            "message": "Database transaction timed out and commit was not verified on reread",
                            "idempotency_key": idempotency_key,
                            "correlation_id": correlation_id,
                        },
                    )
            except Exception:
                return _json_response(
                    504,
                    {
                        "error": "DatabaseCommitOutcomeUnknown",
                        "db_outcome": "UNKNOWN",
                        "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
                        "message": "Database transaction outcome unknown after lost acknowledgement",
                        "idempotency_key": idempotency_key,
                        "correlation_id": correlation_id,
                    },
                )
        else:
            return _json_response(
                504,
                {
                    "error": "DatabaseCommitOutcomeUnknown",
                    "db_outcome": "UNKNOWN",
                    "queue_outcome": DispatchStatus.NOT_ATTEMPTED.value,
                    "message": "Database transaction timed out with insufficient remaining budget",
                    "idempotency_key": idempotency_key,
                    "correlation_id": correlation_id,
                },
            )

    # Order + outbox intent are durably committed; asynchronous relay handles SQS publication.
    resp_body = {
        "order_id": canonical_order_id,
        "idempotency_key": idempotency_key,
        "outbox_id": outbox_id,
        "event_id": stable_event_id,
        "customer_id": normalized["customer_id"],
        "status": OrderStatus.COMMITTED.value,
        "db_outcome": "CONFIRMED",
        "queue_outcome": DispatchStatus.PENDING.value,
        "total_amount": normalized["total_amount_str"],
        "total_amount_minor": normalized["total_amount_minor"],
        "currency": normalized["currency"],
        "created_at": now_iso,
        "correlation_id": correlation_id,
    }
    if experimental:
        resp_body["published_event"] = event_payload
        if requested_fault:
            resp_body["fault_scheduled"] = requested_fault
    return _json_response(201, resp_body)


def get_order(order_id: str, correlation_id: str):
    orders_table_name = os.environ.get("ORDERS_TABLE_NAME", "silentloss-orders")
    fulfillment_table_name = os.environ.get("FULFILLMENT_TABLE_NAME", "silentloss-fulfillment")

    err = validate_identifier(order_id, "order_id")
    if err:
        return _json_response(400, {"error": "ValidationError", "message": err})

    ddb = get_dynamodb()
    try:
        res = ddb.get_item(
            TableName=orders_table_name,
            Key={"order_id": {"S": order_id}},
            ConsistentRead=True,
        )
        if "Item" not in res:
            return _json_response(404, {"error": "NotFound", "message": f"Order {order_id} not found"})

        item = res["Item"]
        order_data = {
            "order_id": item.get("order_id", {}).get("S"),
            "outbox_id": item.get("outbox_id", {}).get("S"),
            "customer_id": item.get("customer_id", {}).get("S"),
            "total_amount": item.get("total_amount", {}).get("S"),
            "total_amount_minor": int(item.get("total_amount_minor", {}).get("N", 0)),
            "currency": item.get("currency", {}).get("S"),
            "status": item.get("status", {}).get("S"),
            "dispatch_status": item.get("dispatch_status", {}).get("S", DispatchStatus.PENDING.value),
            "created_at": item.get("created_at", {}).get("S"),
            "updated_at": item.get("updated_at", {}).get("S"),
        }

        fulfillment_res = ddb.query(
            TableName=fulfillment_table_name,
            KeyConditionExpression="order_id = :oid",
            ExpressionAttributeValues={":oid": {"S": order_id}},
            ConsistentRead=True,
        )
        fulfillments = [
            {
                "fulfillment_id": f.get("fulfillment_id", {}).get("S"),
                "status": f.get("status", {}).get("S"),
                "processed_at": f.get("processed_at", {}).get("S"),
            }
            for f in fulfillment_res.get("Items", [])
        ]

        return _json_response(
            200,
            {
                "order": order_data,
                "fulfillments": fulfillments,
                "is_fulfilled": any(f["status"] == "COMPLETED" for f in fulfillments),
            },
        )
    except ClientError as exc:
        logger.error("Failed to retrieve order", correlation_id=correlation_id, order_id=order_id, error=str(exc))
        return _json_response(500, {"error": "InternalServerError", "message": "Failed to query database"})
