from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List


class OrderClassification(str, Enum):
    FULFILLED = "FULFILLED"                 # F: Verified atomic fulfillment effect
    DURABLY_PENDING = "DURABLY_PENDING"     # P: Independently verified, durably recoverable pending path
    EXPLICITLY_FAILED = "EXPLICITLY_FAILED" # E: Verified explicit terminal failure (DLQ or RejectionAudit)
    UNACCOUNTED = "UNACCOUNTED"             # U: Confirmed unaccounted committed order (Silent Loss)
    INDETERMINATE = "INDETERMINATE"         # I: Incomplete, stale, or conflicting evidence


@dataclass
class SourceCompleteness:
    orders_table_complete: bool = False
    fulfillment_table_complete: bool = False
    dlq_evidence_complete: bool = False
    rejection_audit_complete: bool = False
    publication_logs_complete: bool = False
    pagination_complete: bool = False
    run_scope: str = "ALL"
    observation_timestamp_utc: str = ""
    collection_boundaries: str = "Unverified acquisition"

    def all_complete(self) -> bool:
        return all(
            [
                self.orders_table_complete,
                self.fulfillment_table_complete,
                self.dlq_evidence_complete,
                self.rejection_audit_complete,
                self.publication_logs_complete,
                self.pagination_complete,
            ]
        )

    def source_completeness_percent(self) -> float:
        flags = [
            self.orders_table_complete,
            self.fulfillment_table_complete,
            self.dlq_evidence_complete,
            self.rejection_audit_complete,
            self.publication_logs_complete,
            self.pagination_complete,
        ]
        return round((sum(1 for f in flags if f) / len(flags)) * 100.0, 2)


@dataclass
class ReconciliationSummary:
    demonstrated_business_effect: str = (
        "Single synthetic order fulfillment record written atomically with "
        "OrdersTable state transition to FULFILLED"
    )
    total_committed: int = 0                  # C
    total_fulfilled: int = 0                  # F
    total_pending: int = 0                    # P
    total_explicitly_failed: int = 0          # E
    confirmed_silent_loss: int = 0            # U
    indeterminate_orders: int = 0             # I

    silent_loss_rate_percent: float = 0.0     # U / C * 100
    fulfillment_completion_rate: float = 0.0  # F / C * 100
    duplicate_completed_effects: int = 0      # D (only distinct completed effects > 1 per order)
    duplicate_delivery_attempts: int = 0      # Replay/redelivery attempts observed
    explicit_failures: int = 0                # E
    pending_past_deadline: int = 0            # Pending obligations exceeding observation deadline
    unresolved_obligations: int = 0           # U + pending_past_deadline + I
    recovery_time_sec: float = 0.0

    source_completeness_percent: float = 100.0
    classification_completeness_percent: float = 100.0
    evidence_completeness_percent: float = 100.0
    clean_success_verified: bool = False      # Strictly True only when C>0, U==0, I==0, D==0, pending_past_deadline==0
    invariant_holds: bool = False             # C == F + P + E + U + I

    source_completeness: Dict[str, Any] = field(default_factory=dict)
    classified_orders: Dict[str, str] = field(default_factory=dict)
    indeterminate_reasons: Dict[str, str] = field(default_factory=dict)
    state_inconsistencies: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
