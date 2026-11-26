"""AWS Lambda handler for scheduled Outbox pending-event recovery sweep."""
from __future__ import annotations

import os
from typing import Any, Dict

from common.aws_clients import create_dynamodb_client, create_sqs_client
from common.logger import StructuredLogger
from common.outbox import sweep_pending_outbox

logger = StructuredLogger("silentloss-outbox-sweeper")

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


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Sweep PENDING, due RETRY_WAIT, and expired-lease PUBLISHING records from OutboxTable.
    Recovers orders left behind if stream relay fails or stream retention expires.
    """
    outbox_table = os.environ.get("OUTBOX_TABLE_NAME", "silentloss-outbox")
    orders_table = os.environ.get("ORDERS_TABLE_NAME", "silentloss-orders")
    queue_url = os.environ.get("FULFILLMENT_QUEUE_URL", "")
    index_name = os.environ.get("OUTBOX_STATE_INDEX_NAME", "DispatchStateIndex")

    ddb = get_dynamodb()
    sqs = get_sqs()

    stats = sweep_pending_outbox(
        ddb,
        sqs,
        outbox_table,
        orders_table,
        queue_url,
        index_name=index_name,
        context=context,
    )
    logger.info("Outbox recovery sweep completed", **stats)
    return stats
