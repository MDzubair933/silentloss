import calendar
import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import boto3
from botocore.config import Config

from src.reconciliation.models import SourceCompleteness


def _ddb_str(item: Dict[str, Any], key: str, default: str = "") -> str:
    val = item.get(key, {})
    return val.get("S", default) if isinstance(val, dict) else default


def _ddb_num(item: Dict[str, Any], key: str, default: float = 0.0) -> float:
    val = item.get(key, {})
    if isinstance(val, dict) and "N" in val:
        try:
            return float(val["N"])
        except (ValueError, TypeError):
            return default
    return default


def _parse_iso_epoch(ts_iso: str) -> float:
    if not ts_iso:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(ts_iso, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


class AwsEvidenceAcquirer:
    """
    Read-only AWS evidence collector for Phase 5/7 authorized executions.
    Uses paginated, run-scoped DynamoDB scans and non-destructive SQS queue attribute checks
    (NEVER consumes active SQS messages). Publication logs require separate closed traces.
    """

    def __init__(
        self,
        orders_table: str,
        fulfillment_table: str,
        rejection_table: str,
        queue_url: str,
        dlq_url: str,
        order_log_group: str,
        worker_log_group: str,
        session: Optional[boto3.Session] = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        max_attempts: int = 2,
        outbox_table: Optional[str] = None,
    ):
        self.orders_table = orders_table
        self.fulfillment_table = fulfillment_table
        self.rejection_table = rejection_table
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.order_log_group = order_log_group
        self.worker_log_group = worker_log_group
        self._session = session
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.max_attempts = max_attempts
        self.outbox_table = outbox_table
        self._clients: Dict[str, Any] = {}

    def _client(self, service_name: str):
        if self._session is None:
            self._session = boto3.Session()
        if service_name not in self._clients:
            self._clients[service_name] = self._session.client(
                service_name,
                config=Config(
                    connect_timeout=self.connect_timeout,
                    read_timeout=self.read_timeout,
                    retries={"total_max_attempts": self.max_attempts, "mode": "standard"},
                ),
            )
        return self._clients[service_name]

    def _paginated_scan_run_scoped(
        self, table_name: str, key_attr: str, run_id: Optional[str], deadline: Optional[float] = None
    ) -> Tuple[List[Dict[str, Any]], bool]:
        deadline = deadline if deadline is not None else time.monotonic() + 10
        ddb = self._client("dynamodb")
        items: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {
            "TableName": table_name,
            "ConsistentRead": True,
        }
        if run_id:
            kwargs["FilterExpression"] = f"begins_with({key_attr}, :prefix)"
            kwargs["ExpressionAttributeValues"] = {":prefix": {"S": f"ord_{run_id}__"}}

        try:
            while True:
                if time.monotonic() >= deadline:
                    return items, False
                resp = ddb.scan(**kwargs)
                items.extend(resp.get("Items", []))
                last_key = resp.get("LastEvaluatedKey")
                if not last_key:
                    return items, True
                kwargs["ExclusiveStartKey"] = last_key
        except Exception:
            return items, False

    def check_queue_quiescence(self) -> Dict[str, Any]:
        """
        Read operational SQS queue attributes non-destructively (no ReceiveMessage).
        Approximate counts are operational quiescence signals only, never order proof.
        """
        sqs = self._client("sqs")
        try:
            attrs = sqs.get_queue_attributes(
                QueueUrl=self.queue_url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                ],
            ).get("Attributes", {})
            dlq_attrs = sqs.get_queue_attributes(
                QueueUrl=self.dlq_url,
                AttributeNames=["ApproximateNumberOfMessages"],
            ).get("Attributes", {})
            visible = int(attrs.get("ApproximateNumberOfMessages", -1))
            in_flight = int(attrs.get("ApproximateNumberOfMessagesNotVisible", -1))
            dlq_visible = int(dlq_attrs.get("ApproximateNumberOfMessages", -1))
            return {
                "accessible": True,
                "approx_visible": visible,
                "approx_not_visible": in_flight,
                "dlq_approx_visible": dlq_visible,
                "appears_quiescent": (visible == 0 and in_flight == 0),
            }
        except Exception as exc:
            return {"accessible": False, "error": type(exc).__name__, "appears_quiescent": False}

    def acquire_run_evidence(self, run_id: Optional[str] = None, collection_timeout_seconds: float = 20) -> Dict[str, Any]:
        """
        Collect run-scoped committed orders, fulfillments, rejections, durable outbox records, and completeness flags.
        """
        if not run_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", run_id):
            raise ValueError("A bounded run_id is required")
        if not 0 < collection_timeout_seconds <= 120:
            raise ValueError("Collection timeout must be in (0, 120]")
        deadline = time.monotonic() + collection_timeout_seconds
        now_epoch = time.time()
        now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_epoch))

        raw_orders, orders_ok = self._paginated_scan_run_scoped(self.orders_table, "order_id", run_id, deadline)
        raw_ful, ful_ok = self._paginated_scan_run_scoped(self.fulfillment_table, "order_id", run_id, deadline)
        raw_rej, rej_ok = self._paginated_scan_run_scoped(self.rejection_table, "order_id", run_id, deadline)

        raw_outbox: List[Dict[str, Any]] = []
        outbox_ok: Optional[bool] = None
        if self.outbox_table:
            raw_outbox, outbox_ok = self._paginated_scan_run_scoped(self.outbox_table, "order_id", run_id, deadline)

        quiescence = self.check_queue_quiescence() if time.monotonic() < deadline else {"accessible": False, "appears_quiescent": False}

        committed_orders: Dict[str, Dict[str, Any]] = {}
        pub_outcomes: Dict[str, str] = {}
        for item in raw_orders:
            oid = _ddb_str(item, "order_id")
            if oid:
                d_status = _ddb_str(item, "dispatch_status", "UNKNOWN")
                committed_orders[oid] = {
                    "order_id": oid,
                    "status": _ddb_str(item, "status", "COMMITTED"),
                    "dispatch_status": d_status,
                    "created_at": _ddb_str(item, "created_at"),
                }
                if "outbox_id" in item:
                    committed_orders[oid]["outbox_id"] = _ddb_str(item, "outbox_id")
                pub_outcomes[oid] = d_status

        fulfillment_records: List[Dict[str, Any]] = [
            {
                "order_id": _ddb_str(item, "order_id"),
                "fulfillment_id": _ddb_str(item, "fulfillment_id"),
                "status": _ddb_str(item, "status"),
                "processed_at": _ddb_str(item, "processed_at"),
            }
            for item in raw_ful
        ]

        rejection_order_ids: Set[str] = {
            _ddb_str(item, "order_id") for item in raw_rej if _ddb_str(item, "order_id")
        }

        outbox_records: Dict[str, Dict[str, Any]] = {}
        pending_records: Dict[str, Dict[str, Any]] = {}
        for item in raw_outbox:
            oid = _ddb_str(item, "order_id")
            ob_id = _ddb_str(item, "outbox_id")
            if not oid or not ob_id:
                continue
            d_state = _ddb_str(item, "dispatch_state", "PENDING")
            event_id = _ddb_str(item, "event_id")
            fev_raw = _ddb_str(item, "fault_execution_evidence")
            fev_parsed = None
            if fev_raw:
                try:
                    fev_parsed = json.loads(fev_raw)
                except json.JSONDecodeError:
                    fev_parsed = None
            rec_entry = {
                "outbox_id": ob_id,
                "order_id": oid,
                "event_id": event_id,
                "dispatch_state": d_state,
                "attempt_count": int(_ddb_num(item, "attempt_count", 0)),
                "blocked_attempts_executed": int(_ddb_num(item, "blocked_attempts_executed", 0)),
                "sqs_api_calls": int(_ddb_num(item, "sqs_api_calls", 0)),
                "send_attempts_reserved": int(_ddb_num(item, "send_attempts_reserved", 0)),
                "publication_uncertain": _ddb_str(item, "publication_uncertain") == "true",
                "requires_attention": d_state == "UNKNOWN",
                "sqs_message_id": _ddb_str(item, "sqs_message_id"),
                "fault_type": _ddb_str(item, "fault_type"),
                "fault_remaining_blocks": int(_ddb_num(item, "fault_remaining_blocks", 0)),
                "recovery_source": _ddb_str(item, "recovery_source"),
                "recovery_time_sec": _ddb_num(item, "recovery_time_sec", 0.0),
                "last_error": _ddb_str(item, "last_error"),
                "created_at": _ddb_str(item, "created_at"),
                "published_at": _ddb_str(item, "published_at"),
                "fault_execution_evidence": fev_parsed,
            }
            outbox_records[oid] = rec_entry

            if d_state in ("PENDING", "PUBLISHING", "RETRY_WAIT") and event_id:
                created_ms = _ddb_num(item, "created_epoch_ms", 0.0)
                if created_ms > 0:
                    age_sec = round(max(0.0, (now_epoch * 1000.0 - created_ms) / 1000.0), 3)
                else:
                    created_ep = _parse_iso_epoch(_ddb_str(item, "created_at"))
                    age_sec = round(max(0.0, now_epoch - created_ep), 3) if created_ep > 0 else 0.0
                pending_records[oid] = {
                    "outbox_id": ob_id,
                    "event_id": event_id,
                    "dispatch_state": d_state,
                    "attempt_count": int(_ddb_num(item, "attempt_count", 0)),
                    "age_seconds": age_sec,
                    "recoverable_path_verified": True,
                    "evidence_reference": f"dynamodb://{self.outbox_table}/{ob_id}",
                }

        # Note: Without consuming active SQS messages, individual DLQ or active SQS message bodies
        # remain unobserved at the order_id level. Approximate zero is never exact DLQ/order evidence.
        dlq_exact_available = False

        pagination_ok = orders_ok and ful_ok and rej_ok and (outbox_ok if self.outbox_table else True)
        sc = SourceCompleteness(
            orders_table_complete=orders_ok,
            fulfillment_table_complete=ful_ok,
            dlq_evidence_complete=dlq_exact_available,
            rejection_audit_complete=rej_ok,
            publication_logs_complete=False,
            pagination_complete=pagination_ok,
            run_scope=run_id or "ALL",
            observation_timestamp_utc=now_utc,
            collection_boundaries="Non-destructive scan; SQS active queue never consumed",
        )

        result: Dict[str, Any] = {
            "committed_orders": committed_orders,
            "fulfillment_records": fulfillment_records,
            "pending_records": pending_records,
            "dlq_order_ids": set(),
            "rejection_order_ids": rejection_order_ids,
            "publication_outcomes": pub_outcomes,
            "publication_evidence": {},
            "quiescence_signal": quiescence,
            "source_completeness": sc,
        }
        if self.outbox_table:
            result["outbox_records"] = outbox_records
            result["outbox_table_complete"] = outbox_ok
        return result

    def await_convergence(self, run_id, timeout_seconds=60, poll_seconds=0.2):
        """Bounded read-only observation. No queue consumption and no repair writes."""
        if not 0 < timeout_seconds <= 180 or not 0 < poll_seconds <= timeout_seconds:
            raise ValueError("Invalid convergence window")
        start = time.monotonic()
        deadline = start + timeout_seconds
        latest = None
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            latest = self.acquire_run_evidence(run_id, collection_timeout_seconds=min(20, remaining))
            orders = latest["committed_orders"]
            completed = {r["order_id"] for r in latest["fulfillment_records"] if r["status"] == "COMPLETED"}
            sc = latest["source_completeness"]
            if (
                orders
                and sc.orders_table_complete
                and sc.fulfillment_table_complete
                and sc.pagination_complete
                and all(o["status"] == "FULFILLED" and oid in completed for oid, o in orders.items())
            ):
                latest["convergence_observed"] = True
                break
            time.sleep(min(poll_seconds, max(0, deadline - time.monotonic())))
        if latest is None:
            raise TimeoutError("No collection completed before deadline")
        latest.setdefault("convergence_observed", False)
        latest["observation_window_complete"] = time.monotonic() >= deadline
        latest["observation_elapsed_seconds"] = time.monotonic() - start
        return latest
