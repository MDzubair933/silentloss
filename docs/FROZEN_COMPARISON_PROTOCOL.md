# SilentLoss — Frozen Before / After Comparison Protocol

**Status:** **FROZEN PRIOR TO OFFICIAL BEFORE-STATE BENCHMARK**  
**Date:** 2026-11-26  
**Workspace:** `silentloss/`  
**Target Region / Account:** `us-east-1` / `<redacted-account-id>`  
**Reference Baseline:** Baseline V3 (`template.yaml` + `src/lambdas/` unmodified; `ENABLE_BENCHMARK_FAULT_ADAPTER=false` on ordinary `/orders`, enabled strictly on isolated controller route `/experiments/orders` with `BenchmarkControllerArn=arn:aws:iam::<redacted-account-id>:user/<redacted-iam-user>`).

---

## 1. Benchmark Tooling Modification Disclosure (Non-Architectural)

As authorized in Phase 5 Section 3, only client-side benchmark/evidence collector networking configuration was adjusted to match observed host-to-`us-east-1` TLS+API latency (`~1.37s` cold connect+scan RTT measured in Phase 3):

| Component | Before (Phase 3) | Fixed for Phase 5 & Phase 7 | Preserved Unchanged |
|---|---|---|---|
| `src/reconciliation/aws_acquisition.py` | Fresh client per call; `connect_timeout=0.2s`, `read_timeout=0.4s`, `total_max_attempts=1`, default scan timeout `10s` | Per-service client cache (`self._clients`); `connect_timeout=5.0s`, `read_timeout=10.0s`, `total_max_attempts=2`, default scan timeout `20s`, strict shared loop deadlines preserved | Scan filtering (`ord_<run_id>__`), zero `sqs:ReceiveMessage`, fail-closed `SourceCompleteness` defaults (`False`), zero repair writes |
| `scripts/run_benchmark.py` | `SqsDuplicateControl` used `0.2s/0.4s/1` | Uses `connect_timeout=5.0s`, `read_timeout=10.0s`, `total_max_attempts=2` and passes same to `AwsEvidenceAcquirer` | CLI options, scenario plans, exit behavior |
| `src/benchmark/transport.py` | Unsynchronized lazy `boto3.Session()` in `SigV4HttpTransport` | Added `threading.Lock()` around lazy `_session` initialization | `8.0s` HTTP timeout, SigV4 signing, sanitized header logging |
| **Application Lambda Runtime** (`src/lambdas/common/aws_clients.py` + handlers) | `0.2s/0.4s`, `total_max_attempts=1`, 2 explicit app attempts, SQS worker timeout `30s`, queue visibility `180s` | **UNMODIFIED** (`.aws-sam/final-build/` matches `src/lambdas/` byte-for-byte) | 100% identical runtime retry semantics |

This exact benchmark tooling configuration is frozen for identical reuse in the future **Phase 7 After-State Benchmark**.

---

## 2. Frozen Campaign Structure & Workload (Before == After)

Both the Phase 5 Before-State Benchmark (`silentloss-baseline-exp`, `Environment=HackathonBeforeExp`) and the future Phase 7 After-State Benchmark (`silentloss-remediated-exp`) MUST execute the exact unmodified V3 scenarios ([src/benchmark/scenarios.py](file:///silentloss//src/benchmark/scenarios.py)) across two deterministic seeds (`160` unique committed orders, `192` HTTP/SQS steps, within the `100–300` target):

| Replicate | Run ID | Seed | Concurrency | Convergence Window (`s`) | Scenarios Executed | Standard Unique Orders (`C` Target) | Scheduled Total Steps |
|---|---|---:|---:|---:|---|---:|---:|
| **Replicate 1** | `bef26r1` *(Before)* / equivalent `aftr1` | `20261126` | `2` | `60.0` | `B-01`, `B-02`, `B-03`, `B-04` | `80` (`20` per scenario) | `96` (`28+20+20+28`) |
| **Replicate 2** | `bef26r2` *(Before)* / equivalent `aftr2` | `20261127` | `2` | `60.0` | `B-01`, `B-02`, `B-03`, `B-04` | `80` (`20` per scenario) | `96` (`28+20+20+28`) |
| **Combined Campaign** | `bef26r1 + bef26r2` | `20261126`, `20261127` | `2` (max cap `4`; ESM max `2`) | `60.0` per scenario | `B-01`..`B-04` × 2 | **`160` unique committed orders** | **`192` total requests/controls** |

*(Note: Removed scenario `B-05` and removed `B-04` poison-pill/DLQ claims remain permanently excluded per V3 `BENCHMARK_PROTOCOL.md`.)*

### Per-Scenario Logical Mapping

1. **`B-01` — Healthy Traffic + Controls (Reqs `A`):**
   - `20` valid `STANDARD` orders (`fault=None`, `client_retry_on_ambiguous=True`), `4` `NEGATIVE_CONTROL` invalid requests (expected `HTTP 400`, zero DB commits), `4` `REPLAY_CONTROL` identical API replays (expected `HTTP 200`, `is_idempotent_replay=true`, zero duplicate fulfillment).
2. **`B-02` — Controlled Post-Commit, Pre-Publication Failure (`25%` = `5/20` orders per replicate, Reqs `B`):**
   - `20` valid `STANDARD` orders; `5` selected by seeded RNG `random.Random(f"{seed}:B-02").sample(range(20), 5)` inject `x-benchmark-fault: SQS_INJECTED_TIMEOUT` (`client_retry_on_ambiguous=False`).
   - Exact indices (`seed=20261126`): deterministic subset of `5` indices; exact indices (`seed=20261127`): independent deterministic subset of `5` indices, each persisted in `.B-02.plan.json` **before** traffic starts.
3. **`B-03` — Temporary Publisher Unavailability + Attempt-2 Recovery (`20%` = `4/20` orders per replicate, Reqs `C`):**
   - `20` valid `STANDARD` orders; `4` selected by seeded RNG receive `x-benchmark-fault: SQS_INJECTED_503`, failing publisher attempt 1 with `ServiceUnavailable` (`503`) and succeeding on publisher attempt 2 (`blocked_attempts=1, sqs_api_calls=1`, `HTTP 201`).
4. **`B-04` — Explicit Duplicate Domain Event & API Idempotency Controls (Reqs `D`):**
   - `20` valid `STANDARD` orders + `4` `REPLAY_CONTROL` API replays (`HTTP 200`) + `4` `DUPLICATE_EVENT` controls republishing the exact `published_event` payload returned by `OrderServiceFunction` to `OrderFulfillmentQueue` via `SqsDuplicateControl`.

---

## 3. Equivalent Publication-Boundary Fault Semantics (Before vs. Future After)

To maintain strict scientific parity between the Phase 5 Before-State and future Phase 7 After-State benchmarks:

- **Fault Boundary & Trigger:** In both Before and After stacks, `x-benchmark-fault: SQS_INJECTED_TIMEOUT` simulates an application-layer dependency failure immediately at the direct synchronous SQS `SendMessage` publish call **after** the authoritative synchronous DynamoDB `transact_write_items` transaction commits (`2` blocked pre-send attempts returning `ReadTimeoutError` with `sqs_api_calls=0`, `publisher_completed=true`, `no_send_proven=true`, HTTP `504` on the initial API call).
- **What Remains Prohibited:**
  - Never trigger account-wide AWS outages or deny queue permissions across unrelated stacks.
  - Never alter `fault_rate_percent` (`25%` in `B-02`, `20%` in `B-03`), `client_retry_on_ambiguous` (`False` in `B-02`, `True` in `B-01`), or fault intensity (`2` blocked pre-send attempts for `SQS_INJECTED_TIMEOUT`; `1` pre-send attempt for `SQS_INJECTED_503`) between Before and After runs.
  - Under the future After architecture (F1: durable dispatch intent persisted atomically with the order in the initial DB transaction), the exact same synchronous eager-send failure still fails the synchronous SQS attempt with `SQS_INJECTED_TIMEOUT`, leaving the durable dispatch intent to be published and fulfilled asynchronously by the recovery publisher within the **same `60s` convergence window**. Thus, **Before** yields `U = 5` stranded orders per `20`-order `B-02` run (`U=10/160` overall), whereas **After** must eliminate unaccounted losses (`U = 0`, `C = F` or `F + P` within deadline, `D = 0`).

---

## 4. Reconciliation & Classification Rules

- Strict mathematical invariant: $C = F + P + E + U + I$ (`EvidenceReconciliationCollector`).
- Silent loss rate: $U / C \times 100\%$.
- Every classification uses authoritative DynamoDB (`ConsistentRead=True`) evidence and hash-verified closed controller traces (`.plan.json`, `.jsonl`, `.closed.json`), **never** relying on HTTP error codes alone, stored `dispatch_status` metadata alone, or approximate SQS counts.
- Interrupted, truncated, or unclosed fault evidence remains `INDETERMINATE (I)`, never `UNACCOUNTED (U)`.
- Approximate SQS queue attributes serve strictly as operational quiescence checks; no destructive `sqs:ReceiveMessage` calls occur in the collector.
