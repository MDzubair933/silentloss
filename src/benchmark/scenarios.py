from dataclasses import dataclass
from typing import Dict, List, Optional

ACCOUNT_CONCURRENCY_QUOTA = 10
DEFAULT_CONCURRENCY = 2
MAX_SAFE_CONCURRENCY = 4  # Leaves shared headroom inside account-wide limit of 10


@dataclass(frozen=True)
class BenchmarkScenario:
    scenario_id: str
    name: str
    description: str
    order_count: int
    concurrency: int
    fault_type: Optional[str] = None
    fault_rate_percent: float = 0.0
    poison_pill_count: int = 0
    invalid_negative_control_count: int = 0
    duplicate_replay_count: int = 0
    client_retry_on_ambiguous: bool = False
    convergence_window_sec: int = 60
    can_run_local_mock: bool = True
    requires_live_aws_for_real_queue: bool = True
    duplicate_event_count: int = 0


def validate_concurrency(requested: int) -> int:
    """Centrally enforce safe benchmark concurrency against the verified account quota."""
    if isinstance(requested, bool) or not isinstance(requested, int) or requested < 1:
        raise ValueError("Concurrency must be a positive integer >= 1")
    if requested > MAX_SAFE_CONCURRENCY:
        raise ValueError(
            f"Requested concurrency {requested} exceeds safe limit {MAX_SAFE_CONCURRENCY} "
            f"(account-wide Lambda concurrency quota is {ACCOUNT_CONCURRENCY_QUOTA})"
        )
    return requested


STANDARD_SCENARIOS: Dict[str, BenchmarkScenario] = {
    "B-01": BenchmarkScenario(
        scenario_id="B-01",
        name="Healthy Traffic + Controls",
        description="Positive control healthy orders, negative control invalid orders, and idempotent client replays.",
        order_count=20,
        concurrency=DEFAULT_CONCURRENCY,
        fault_type=None,
        fault_rate_percent=0.0,
        invalid_negative_control_count=4,
        client_retry_on_ambiguous=True,
        duplicate_replay_count=4,
    ),
    "B-02": BenchmarkScenario(
        scenario_id="B-02",
        name="Post-Commit Publication Ambiguous Timeout",
        description="Controlled dependency timeout injected on SQS dispatch immediately after durable DB commit.",
        order_count=20,
        concurrency=DEFAULT_CONCURRENCY,
        fault_type="SQS_INJECTED_TIMEOUT",
        fault_rate_percent=25.0,
        client_retry_on_ambiguous=False,
    ),
    "B-03": BenchmarkScenario(
        scenario_id="B-03",
        name="Temporary Publisher Unavailability (503)",
        description="Controlled dependency 503 rejection simulating publisher unavailability.",
        order_count=20,
        concurrency=DEFAULT_CONCURRENCY,
        fault_type="SQS_INJECTED_503",
        fault_rate_percent=20.0,
    ),
    "B-04": BenchmarkScenario(
        scenario_id="B-04",
        name="Explicit Duplicate Domain Event Control",
        description="Republishes identical published events to SQS; also verifies API idempotency. No poison/DLQ claim.",
        order_count=20,
        concurrency=DEFAULT_CONCURRENCY,
        duplicate_event_count=4,
        duplicate_replay_count=4,
    ),
}
