import math
from typing import Any, Dict, List, Optional, Set

from src.reconciliation.models import OrderClassification, ReconciliationSummary, SourceCompleteness


class EvidenceReconciliationCollector:
    """
    Independent, read-only evidence reconciliation collector for SilentLoss.
    Strictly enforces the mutually exclusive reliability invariant:
        C = F + P + E + U + I
    Never modifies or repairs the workload being measured.
    """

    def __init__(self, observation_deadline_seconds: int = 120):
        self.observation_deadline_seconds = observation_deadline_seconds

    def reconcile(
        self,
        committed_orders: Dict[str, Dict[str, Any]],
        fulfillment_records: List[Dict[str, Any]],
        pending_records: Optional[Dict[str, Dict[str, Any]]] = None,
        dlq_records: Optional[Set[str]] = None,
        rejection_records: Optional[Set[str]] = None,
        publication_outcomes: Optional[Dict[str, str]] = None,
        publication_evidence: Optional[Dict[str, Dict[str, Any]]] = None,
        duplicate_delivery_attempts: int = 0,
        recovery_time_sec: float = 0.0,
        source_completeness: Optional[SourceCompleteness] = None,
        corrupted_record_ids: Optional[Set[str]] = None,
    ) -> ReconciliationSummary:
        sc = source_completeness or SourceCompleteness()
        summary = ReconciliationSummary(
            total_committed=len(committed_orders),
            duplicate_delivery_attempts=duplicate_delivery_attempts,
            recovery_time_sec=recovery_time_sec,
            source_completeness={
                "orders_table_complete": sc.orders_table_complete,
                "fulfillment_table_complete": sc.fulfillment_table_complete,
                "dlq_evidence_complete": sc.dlq_evidence_complete,
                "rejection_audit_complete": sc.rejection_audit_complete,
                "publication_logs_complete": sc.publication_logs_complete,
                "pagination_complete": sc.pagination_complete,
                "run_scope": sc.run_scope,
                "observation_timestamp_utc": sc.observation_timestamp_utc,
                "collection_boundaries": sc.collection_boundaries,
            },
        )
        summary.source_completeness_percent = sc.source_completeness_percent()

        if summary.total_committed == 0:
            summary.invariant_holds = True
            summary.evidence_completeness_percent = summary.source_completeness_percent
            return summary

        pending_map = pending_records or {}
        dlq_set = dlq_records or set()
        rejection_set = rejection_records or set()
        # Stored dispatch metadata is diagnostic, never evidence of queue absence.
        proof_map = publication_evidence or {}
        corrupted_set = corrupted_record_ids or set()
        terminal_failed_set = {oid for oid in dlq_set if committed_orders.get(oid, {}).get("status") == "FAILED"}

        # Deduplicate completed fulfillment records by stable identity (order_id, fulfillment_id)
        # Rejected attempts NEVER count as completed effects.
        completed_by_order: Dict[str, Set[str]] = {}
        for rec in fulfillment_records:
            oid = rec.get("order_id")
            fid = rec.get("fulfillment_id") or rec.get("sqs_message_id") or "FULFILLMENT#PRIMARY"
            status = rec.get("status")
            if oid and status in ("COMPLETED", "FULFILLED"):
                completed_by_order.setdefault(oid, set()).add(fid)

        # Count duplicate COMPLETED business effects (only when >1 distinct completed effect for an order)
        dup_completed_effects = sum(max(0, len(fids) - 1) for fids in completed_by_order.values())
        summary.duplicate_completed_effects = dup_completed_effects

        for order_id, order_data in committed_orders.items():
            order_status = order_data.get("status", "COMMITTED")
            has_completed_ful = order_id in completed_by_order
            has_terminal_fail = order_id in terminal_failed_set
            has_pending = order_id in pending_map

            # 1. Corrupted trace -> INDETERMINATE
            if order_id in corrupted_set:
                summary.indeterminate_orders += 1
                summary.classified_orders[order_id] = OrderClassification.INDETERMINATE.value
                summary.indeterminate_reasons[order_id] = "Corrupted or unreadable evidence record"
                continue

            # 2. State inconsistency detection (B14)
            if order_status == "FULFILLED" and not has_completed_ful and sc.fulfillment_table_complete:
                summary.indeterminate_orders += 1
                summary.classified_orders[order_id] = OrderClassification.INDETERMINATE.value
                reason = "OrdersTable reports FULFILLED but FulfillmentTable has no COMPLETED record"
                summary.indeterminate_reasons[order_id] = reason
                summary.state_inconsistencies.append({"order_id": order_id, "reason": reason})
                continue

            if has_completed_ful and order_status == "FAILED":
                summary.indeterminate_orders += 1
                summary.classified_orders[order_id] = OrderClassification.INDETERMINATE.value
                reason = "FulfillmentTable shows COMPLETED while OrdersTable reports FAILED"
                summary.indeterminate_reasons[order_id] = reason
                summary.state_inconsistencies.append({"order_id": order_id, "reason": reason})
                continue

            if has_completed_ful and has_terminal_fail:
                summary.indeterminate_orders += 1
                summary.classified_orders[order_id] = OrderClassification.INDETERMINATE.value
                reason = "Conflicting evidence: order has both COMPLETED fulfillment and terminal DLQ/Rejection record"
                summary.indeterminate_reasons[order_id] = reason
                summary.state_inconsistencies.append({"order_id": order_id, "reason": reason})
                continue

            # 3. Verified Fulfilled Orders (F)
            if has_completed_ful and order_status != "FULFILLED":
                summary.indeterminate_orders += 1
                summary.classified_orders[order_id] = OrderClassification.INDETERMINATE.value
                summary.state_inconsistencies.append({"order_id": order_id, "reason": "Completed effect without atomic FULFILLED order state"})
                continue

            if has_completed_ful:
                if not sc.fulfillment_table_complete:
                    # Even if found, mark F because completed record is affirmatively present
                    pass
                summary.total_fulfilled += 1
                summary.classified_orders[order_id] = OrderClassification.FULFILLED.value
                continue

            # 4. Verified Explicit Terminal Failures (E)
            if has_terminal_fail:
                summary.total_explicitly_failed += 1
                summary.classified_orders[order_id] = OrderClassification.EXPLICITLY_FAILED.value
                continue

            # 5. Durably Recoverable Pending Orders (P)
            if has_pending:
                pend_meta = pending_map[order_id] if isinstance(pending_map[order_id],dict) else {}
                try:
                    age_sec = float(pend_meta.get("age_seconds", -1))
                except (ValueError, TypeError, OverflowError):
                    age_sec = -1
                is_recoverable = (pend_meta.get("recoverable_path_verified") is True
                                  and bool(pend_meta.get("evidence_reference"))
                                  and math.isfinite(age_sec) and age_sec >= 0)
                if not is_recoverable:
                    summary.indeterminate_orders += 1
                    summary.classified_orders[order_id] = OrderClassification.INDETERMINATE.value
                    summary.indeterminate_reasons[order_id] = "Pending path recoverability not independently verified"
                    continue
                if age_sec > self.observation_deadline_seconds:
                    summary.pending_past_deadline += 1
                summary.total_pending += 1
                summary.classified_orders[order_id] = OrderClassification.DURABLY_PENDING.value
                continue

            # U is only demonstrated by a complete controlled pre-send execution,
            # bound to this order and a closed run. Metadata/approximate counts cannot prove U.
            proof = proof_map.get(order_id, {})
            no_send = (proof.get("order_id") == order_id
                       and proof.get("source") == "application_fault_adapter"
                       and proof.get("fault_type") == "SQS_INJECTED_TIMEOUT"
                       and proof.get("publisher_completed") is True
                       and proof.get("no_send_proven") is True
                       and proof.get("blocked_attempts") == 2
                       and proof.get("sqs_api_calls") == 0
                       and proof.get("run_closed") is True
                       and bool(proof.get("evidence_reference")))
            complete = (sc.orders_table_complete and sc.fulfillment_table_complete
                        and sc.rejection_audit_complete and sc.pagination_complete
                        and sc.publication_logs_complete)
            if complete and no_send and order_status == "COMMITTED" and order_id not in rejection_set:
                summary.confirmed_silent_loss += 1
                summary.classified_orders[order_id] = OrderClassification.UNACCOUNTED.value
            else:
                summary.indeterminate_orders += 1
                summary.classified_orders[order_id] = OrderClassification.INDETERMINATE.value
                summary.indeterminate_reasons[order_id] = "No independently verified terminal state or publication exclusion proof"

        c = summary.total_committed
        summary.explicit_failures = summary.total_explicitly_failed
        summary.silent_loss_rate_percent = round((summary.confirmed_silent_loss / c) * 100.0, 4)
        summary.fulfillment_completion_rate = round((summary.total_fulfilled / c) * 100.0, 4)
        summary.unresolved_obligations = (
            summary.confirmed_silent_loss + summary.total_pending + summary.indeterminate_orders
        )

        determinate_count = (
            summary.total_fulfilled
            + summary.total_pending
            + summary.total_explicitly_failed
            + summary.confirmed_silent_loss
        )
        summary.classification_completeness_percent = round((determinate_count / c) * 100.0, 2)
        summary.evidence_completeness_percent = round(
            min(summary.source_completeness_percent, summary.classification_completeness_percent), 2
        )

        sum_components = (
            summary.total_fulfilled
            + summary.total_pending
            + summary.total_explicitly_failed
            + summary.confirmed_silent_loss
            + summary.indeterminate_orders
        )
        summary.invariant_holds = (c == sum_components)

        # Clean success requires ZERO unaccounted AND ZERO indeterminate AND ZERO duplicates AND ZERO past-deadline pending
        summary.clean_success_verified = (
            c > 0
            and summary.invariant_holds
            and summary.confirmed_silent_loss == 0
            and summary.indeterminate_orders == 0
            and summary.duplicate_completed_effects == 0
            and summary.total_pending == 0
            and summary.total_explicitly_failed == 0
            and summary.total_fulfilled == c
            and sc.orders_table_complete and sc.fulfillment_table_complete
            and sc.rejection_audit_complete and sc.pagination_complete
            and len(summary.state_inconsistencies) == 0
        )

        return summary
