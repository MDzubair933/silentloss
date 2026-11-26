"""AWS Lambda handler for DynamoDB Streams Outbox Relay."""
from __future__ import annotations

import os
from typing import Any, Dict, List

from common.aws_clients import create_dynamodb_client, create_sqs_client, get_remaining_time_ms
from common.logger import StructuredLogger
from common.outbox import publish_outbox_record

logger = StructuredLogger("silentloss-outbox-relay")

_dynamodb = None
_sqs = None


def get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = create_dynamodb_client(worker=False)
    return _dynamodb


def get_sqs():
    global _sqs
    if _sqs is None:
        _sqs = create_sqs_client()
    return _sqs


def _extract_outbox_id(record: Dict[str, Any]) -> str:
    ddb_part = record.get("dynamodb") or {}
    keys = ddb_part.get("Keys") or {}
    if "outbox_id" in keys and "S" in keys["outbox_id"]:
        return keys["outbox_id"]["S"]
    new_img = ddb_part.get("NewImage") or {}
    return new_img.get("outbox_id", {}).get("S", "")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, List[Dict[str, str]]]:
    """
    Process DynamoDB Stream INSERT records from OutboxTable and publish durably to SQS.
    Supports partial batch failures (ReportBatchItemFailures) using sequence numbers.
    """
    records = event.get("Records") or []
    outbox_table = os.environ.get("OUTBOX_TABLE_NAME", "silentloss-outbox")
    orders_table = os.environ.get("ORDERS_TABLE_NAME", "silentloss-orders")
    queue_url = os.environ.get("FULFILLMENT_QUEUE_URL", "")

    ddb = get_dynamodb()
    sqs = get_sqs()
    batch_item_failures: List[Dict[str, str]] = []

    logger.info("Outbox relay triggered", batch_size=len(records))

    for record in records:
        seq = (record.get("dynamodb") or {}).get("SequenceNumber") or record.get("eventID", "UNKNOWN")
        if record.get("eventName") != "INSERT":
            continue
        if get_remaining_time_ms(context) < 3000:
            batch_item_failures.append({"itemIdentifier": seq})
            continue

        outbox_id = _extract_outbox_id(record)
        if not outbox_id:
            continue

        try:
            res = publish_outbox_record(
                ddb,
                sqs,
                outbox_table,
                orders_table,
                queue_url,
                outbox_id,
                trigger_source="dynamodb_stream",
                allow_short_retry_wait=True,
                context=context,
            )
            if res.get("retryable"):
                batch_item_failures.append({"itemIdentifier": seq})
        except Exception as exc:
            logger.error(
                "Unhandled error relaying outbox stream record",
                outbox_id=outbox_id,
                sequence_number=seq,
                error_type=type(exc).__name__,
            )
            batch_item_failures.append({"itemIdentifier": seq})

    return {"batchItemFailures": batch_item_failures}
