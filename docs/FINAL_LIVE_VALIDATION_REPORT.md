# SilentLoss — Final Corrected Deployment and Live Revalidation Report

**Final Verdict:** **READY FOR PRESENTATION AND PUBLICATION PREPARATION**  
**Date (UTC):** 2026-11-26 (`12:27Z`–`12:41Z`)  
**Authoritative Workspace:** `silentloss/`  
**Target Environment:** `silentloss-remediated-exp` (`Environment=HackathonAfterExp`, `us-east-1`, Account `<redacted-account-id>`)  
**HTTP API Endpoint:** `https://<redacted-api-id>.execute-api.us-east-1.amazonaws.com`  
**Corrected Build Deployed:** `.aws-sam/corrected-build/template.yaml`  
**Preserved Protocol Reference:** [FROZEN_COMPARISON_PROTOCOL.md](FROZEN_COMPARISON_PROTOCOL.md)  

---

## 1. Deployed Revision Identity & Controlled Publisher Cutover

All Phase 8 defect fixes (atomic pre-send `publication_uncertain` / `send_attempts_reserved` reservation and strongly consistent terminal persistence verification in [src/lambdas/common/outbox.py](src/lambdas/common/outbox.py)) were deployed exclusively from `.aws-sam/corrected-build/template.yaml` (never `.aws-sam/final-build`):

- **Controlled Cutover Sequence ([pre_cutover_snapshot.json](evidence/phase-09-final-validation/pre-cutover/pre_cutover_snapshot.json), [corrected_deploy_sanitized.txt](evidence/phase-09-final-validation/deployment/corrected_deploy_sanitized.txt), [post_cutover_verification.json](evidence/phase-09-final-validation/deployment/post_cutover_verification.json)):**
  1. Verified zero unresolved obligations pre-cutover (`164/164` historical `OutboxTable` items in `CONFIRMED`, `0/0` SQS messages visible or in-flight).
  2. Disabled both publishers (`OutboxRecoveryScheduleRule` -> `DISABLED`, `OutboxStreamMapping` -> `Disabled`) and quiesced prior to updating Lambda functions, ensuring old and corrected publisher revisions never ran concurrently on the same outbox.
  3. Updated stack `silentloss-remediated-exp` via SAM CLI `1.174.1` (`UPDATE_COMPLETE`, `LogLevel=INFO`, `LambdaRuntime=python3.14`, `EnableFaultAdapter=true`, `BenchmarkControllerArn=arn:aws:iam::<redacted-account-id>:user/<redacted-iam-user>`).
  4. Downloaded and hashed every deployed Lambda ZIP against `.aws-sam/corrected-build/` **before** re-enabling triggers.
- **Exact Deployed Binary & Config Verification:**
  - All `5` ARM64 (`python3.14`, `256 MB`) functions updated cleanly from pre-cutover `CodeSha256: 8gYhHf1My/jHIKvT81KYALHG6t+06/V3qHXCLJ4T7hk=` to **`u/Rb9bW20rQ5u848H4FE0v8mmpxZEOdPqDWxgOVigsk=`**.
  - All `12` packaged runtime files inside every deployed Lambda ZIP matched `.aws-sam/corrected-build/` byte-for-byte, including `common/outbox.py` (`409a7309182376067f6b41292b770ac3e4cd65700db713c141552044def95e14`).
  - Live IAM policies verified unchanged and least-privilege: `0` attached managed policies, `0` wildcard resources, `OrderServiceRole` and `ExperimentServiceRole` hold **zero `sqs:*` permissions**, and only `OutboxRelayRole` / `OutboxRecoverySweeperRole` hold `sqs:SendMessage` scoped to `OrderFulfillmentQueue`.
  - EventBridge `OutboxRecoveryScheduleRule` (`rate(1 minute)`, `ENABLED`), `OutboxStreamMapping` (`Enabled`, `BatchSize=5`, `ParallelizationFactor=1`, `MaximumRetryAttempts=2`, `ReportBatchItemFailures`, `INSERT` filter), `FulfillmentWorkerFunctionSQSEvent` (`Enabled`, `MaxConcurrency=2`), and `OutboxTable` `DispatchStateIndex` + `NEW_AND_OLD_IMAGES` stream all verified active.
- **Baseline Stack Preservation:** `silentloss-baseline` (`CREATE_COMPLETE`) and `silentloss-baseline-exp` (`UPDATE_COMPLETE`) remained completely unmodified (`MatchesPreCutover: true`) and recorded **0 CloudWatch log events and 0 Lambda invocations** across the entire Phase 9 validation window.

---

## 2. Targeted Live Smoke Verification (`finpref`) vs. Hermetic-Only Scope

Bounded pre-campaign verification against the newly deployed stack ([final_smoke_tests.json](evidence/phase-09-final-validation/smoke/final_smoke_tests.json)):
- **Auth & Experiment Isolation:** Unsigned `POST /orders` and `POST /experiments/orders` both denied (`HTTP 403`). Authorized `POST /experiments/orders` with `x-benchmark-fault: UNKNOWN_FAULT` returned `HTTP 400` (`UnsupportedBenchmarkFault`). Ordinary `POST /orders` with `x-benchmark-fault: SQS_INJECTED_TIMEOUT` (`ord_finpref__ok_001`) returned `HTTP 201` (`db_outcome="CONFIRMED"`, `queue_outcome="PENDING"`, `fault_execution_evidence: null`), confirming fault header isolation.
- **Normal Fulfillment & Readback:** `ord_finpref__ok_001` converged to `FULFILLED` / `CONFIRMED` (`GET /orders/ord_finpref__ok_001` returned `HTTP 200`, `is_fulfilled=true`), with `OutboxTable` recording `attempt_count=1`, `send_attempts_reserved=1`, `publication_uncertain=false`.
- **Controlled Timeout & Transient `503` Recovery:** `ord_finpref__to_001` (`SQS_INJECTED_TIMEOUT`) blocked `2` attempts and recovered asynchronously (`attempt_count=3`, `send_attempts_reserved=1`, `publication_uncertain=false`, `recovery_time_sec=18.246s` including first post-deploy ARM64 cold start); `ord_finpref__s503_001` (`SQS_INJECTED_503`) blocked `1` attempt and recovered (`attempt_count=2`, `send_attempts_reserved=1`, `publication_uncertain=false`, `recovery_time_sec=15.906s`).
- **Idempotent Replay & Duplicate Suppression:** API replays of `ord_finpref__ok_001` and `ord_finpref__to_001` returned `HTTP 200` (`is_idempotent_replay=true`). Direct duplicate SQS delivery (`174142a4-29b9-485a-bc7f-a79a1bc5d8f4`) left `FulfillmentTable` at `3` items (`1` per order, `D=0`), with SQS queue and DLQ quiescent (`0/0/0`).
- **Distinction — Live AWS vs. Local Hermetic Defect Tests:** Live AWS smoke and campaign runs exercised the live conditional `send_attempts_reserved` & `publication_uncertain` transitions, async fault blocks, stream recovery, scheduled EventBridge sweeper confirmation (`ord_fin26r1__B-02_0009`), duplicate suppression, and auth isolation on ARM64 Python 3.14. Conversely, cross-invocation `AccessDenied` after a lost SQS acknowledgement (`UNKNOWN` parking), interrupted reservation writes, terminal transaction timeout/conflict/readback failure (`TERMINAL_STATE_UNVERIFIED`), and retry budget exhaustion are verified strictly in the local `109`-test hermetic suite ([test_publication_correction.py](tests/unit/test_publication_correction.py), [hermetic-tests.txt](evidence/phase-08-correction/hermetic-tests.txt)) without injecting destructive account-wide failures.

---

## 3. Final After Benchmark Results & Before vs. Corrected Live Comparison

Using identical frozen seeds (`20261126` -> `fin26r1`, `20261127` -> `fin26r2`), scenarios `B-01..B-04`, fault rates (`25%` `B-02`, `20%` `B-03`), client concurrency (`2`), and SQS worker max concurrency (`2`), machine-readable results are sealed in [FINAL_BENCHMARK_RESULTS.json](FINAL_BENCHMARK_RESULTS.json), [FINAL_BENCHMARK_RESULTS.csv](FINAL_BENCHMARK_RESULTS.csv), and [before_after_dataset.json](evidence/phase-09-final-validation/publication-safe/before_after_dataset.json):

| Metric | Before (`silentloss-baseline-exp`) | Corrected Live After (`silentloss-remediated-exp`, Phase 9) | Delta | Notes |
|---|---:|---:|---:|---|
| **Unique Committed Orders ($C$)** | 160 | **160** | `0` | Exact parity (`80` × `2` seeds) |
| **Fulfilled Orders ($F$)** | 150 | **160** | **`+10`** | All `10` timeout-injected orders fulfilled |
| **Durably Pending ($P$)** | 0 | **0** | `0` | All outbox intents confirmed within `< 60s` |
| **Explicitly Failed ($E$)** | 0 | **0** | `0` | Zero terminal failures |
| **Confirmed Silent Loss ($U$)** | **10** | **0** | **`-10`** | **100% elimination of silent loss** |
| **Indeterminate ($I$)** | 0 | **0** | `0` | `100%` determinate order classifications |
| **Duplicate Business Effects ($D$)** | 0 | **0** | `0` | Clean suppression across `8` SQS duplicates + `16` API replays |
| **Overall Silent Loss Rate ($U / C$)** | **6.25%** | **0.00%** | **`-6.25 pp`** | Historical Phase 7 revision also measured `0.00%` |
| **`B-02` Scenario Loss Rate** | **25.00%** (`10/40`) | **0.00%** (`0/40`) | **`-25.00 pp`** | `10/10` designated timeout orders recovered |
| **Fulfillment Completion Rate ($F / C$)** | **93.75%** | **100.00%** | **`+6.25 pp`** | Full synthetic effect completion |
| **`B-02` Recovery Time (min / avg / max)** | Unrecovered (`inf`) | **`1.667s / 2.058s / 2.800s`** | `<= 2.80s` | Includes pre-send conditional reservation write (Phase 7 max was `2.818s`) |
| **`B-03` Recovery Time (min / avg / max)** | Inline retry (`1` block) | **`0.220s / 0.390s / 0.570s`** | `8/8` recovered | Async `SQS_INJECTED_503` recovery |
| **Ingestion Lambda Duration Avg / p95** | `71.28 ms` / `98.85 ms` | **`37.88 ms` / `43.91 ms`** | `-46.9%` avg | API handler writes 3-table TX with zero SQS wait |
| **Client HTTP Standard RTT Avg / p50 / p95** | `951.67 / 924.21 / 1155.96 ms` | `965.32 / 902.21 / 1143.13 ms` | `+13.65 / -22.00 / -12.83 ms` | Closely comparable client RTT across runs |
| **HTTP `429` / Lambda Errors / Throttles** | `0 / 0 / 0` | **`0 / 0 / 0`** | `0` | Peak concurrency `3` well within quota `10` |
| **Campaign Wall-Clock Duration** | `484.0s` | **`138.033s`** (`62.602s` + `75.431s`) | `-345.967s` | Before waited 60s on unrecovered `B-02`; not a proportional throughput speedup |
| **Gross On-Demand Window Cost (USD)** | `$0.002112` | **`$0.002901`** | `+$0.000789` | Reflects extra `OutboxTable` pre-send reservation WCUs (`3,128` vs `2,586` in Phase 7) + relay/sweeper |

### Per-Replicate & Per-Scenario Breakdown

| Replicate (Seed) | Scenario | Steps | $C$ | $F$ | $P$ | $E$ | $U$ | $I$ | $D$ | Dup Att | Max Rec (s) | Exec Faults | Clean Success |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| **`fin26r1`** (`20261126`) | `B-01` Healthy + Controls | 28 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000 | `0/0` | `True` |
| **`fin26r1`** (`20261126`) | `B-02` Timeout + Recovery | 20 | 20 | **20** | 0 | 0 | **0** | 0 | 0 | 0 | **2.583** | `5/5` | **`True`** |
| **`fin26r1`** (`20261126`) | `B-03` Transient `503` Recovery | 20 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0.570 | `4/4` | `True` |
| **`fin26r1`** (`20261126`) | `B-04` Duplicate Controls | 28 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 4 | 0.000 | `0/0` | `True` |
| **Replicate 1 Subtotal** | `ALL_80` (`12:31:51Z`–`12:32:54Z`, `62.602s`) | **96** | **80** | **80** | **0** | **0** | **0** | **0** | **0** | **4** | **2.583** | **`9/9`** | **`True`** |
| **`fin26r2`** (`20261127`) | `B-01` Healthy + Controls | 28 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000 | `0/0` | `True` |
| **`fin26r2`** (`20261127`) | `B-02` Timeout + Recovery | 20 | 20 | **20** | 0 | 0 | **0** | 0 | 0 | 0 | **2.800** | `5/5` | **`True`** |
| **`fin26r2`** (`20261127`) | `B-03` Transient `503` Recovery | 20 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0.471 | `4/4` | `True` |
| **`fin26r2`** (`20261127`) | `B-04` Duplicate Controls | 28 | 20 | 20 | 0 | 0 | 0 | 0 | 0 | 4 | 0.000 | `0/0` | `True` |
| **Replicate 2 Subtotal** | `ALL_80` (`12:33:00Z`–`12:34:16Z`, `75.431s`) | **96** | **80** | **80** | **0** | **0** | **0** | **0** | **0** | **4** | **2.800** | **`9/9`** | **`True`** |
| **Combined Campaign** | **`fin26r1 + fin26r2`** | **192** | **160** | **160** | **0** | **0** | **0** | **0** | **0** | **8** | **2.800** | **`18/18`** | **`True`** |

Independent CLI reconciliations ([scripts/reconcile_evidence.py](scripts/reconcile_evidence.py)) confirmed `C=20, F=20, U=0, I=0, D=0` (`83.33%` source completeness, `invariant_holds=True`) on all `8` scenario runs and `C=80, F=80, U=0, I=0, D=0` (`66.67%` without single-scenario trace flags; `83.33%` with merged proofs in [merged_80_with_proofs.json](evidence/phase-09-final-validation/reconciliation/fin26r1/merged_80_with_proofs.json) and [combined_160_reconciliation.json](evidence/phase-09-final-validation/reconciliation/combined_160_reconciliation.json)).

---

## 4. Affirmative `10 / 10` `B-02` Timeout Recovery Traces & Scheduled Sweeper Observation

All `10` timeout-injected orders ([b02_recovery_traces.json](evidence/phase-09-final-validation/publication-safe/b02_recovery_traces.json)) blocked `2` attempts (`sqs_api_calls=0` on initial pass), transitioned cleanly via single pre-send reservation (`send_attempts_reserved=1`, `publication_uncertain=false`, `attempt_count=3`), and completed `FULFILLED` / `COMPLETED`:

| Order ID | API Commit UTC (`201`) | Blocked / SQS Calls | Uncert / Resv | Recovery Source & Time | Confirmed SQS Message ID | Final State |
|---|---|---:|---:|---|---|---|
| `ord_fin26r1__B-02_0003` | `12:32:14Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`1.981s`) | `dae064f5-8165-4b41-8496-ff65e920077d` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r1__B-02_0010` | `12:32:17Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`1.667s`) | `c3dee202-7ba5-4232-9603-65e255e67177` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r1__B-02_0011` | `12:32:17Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`2.583s`) | `6e88c2cc-527d-4349-abbd-14cc450a40c3` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r1__B-02_0012` | `12:32:18Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`1.841s`) | `e17089af-fa64-4856-9254-bbffcf4845ed` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r1__B-02_0018` | `12:32:20Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`2.280s`) | `7a605d4c-d06a-43c8-ab39-724d2a669539` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r2__B-02_0003` | `12:33:27Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`1.793s`) | `99e81168-a414-4ff3-9feb-ffa340ff63b5` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r2__B-02_0005` | `12:33:28Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`2.800s`) | `8098b45c-0a1d-408d-83f2-a36c886a2718` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r2__B-02_0008` | `12:33:30Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`2.021s`) | `fefad830-dfe0-45df-ad5f-676ec6125244` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r2__B-02_0012` | `12:33:32Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`1.859s`) | `93dfa47d-3521-4d65-866a-5ed31ff577eb` | `FULFILLED` / `COMPLETED` |
| `ord_fin26r2__B-02_0015` | `12:33:33Z` | `2 / 1` | `False / 1` | `dynamodb_stream` (`1.755s`) | `a511e5f4-cc83-4c70-a1ac-f6dcb8f2fd99` | `FULFILLED` / `COMPLETED` |

**Scheduled Sweeper Live Activity:** During `fin26r1`, healthy order `ord_fin26r1__B-02_0009` landed in `OutboxTable` (`PENDING`) concurrently with the `12:32:16Z` EventBridge `OutboxRecoverySweeperFunction` tick. Live CloudWatch logs ([cwlogs_silentloss-outbox-sweeper-HackathonAfterExp.json](evidence/phase-09-final-validation/raw-private/cwlogs_silentloss-outbox-sweeper-HackathonAfterExp.json)) recorded `candidates: 1`, `confirmed: 1`, `trigger_source: scheduled_sweeper`, `sqs_message_id: d587fffc-ff90-4e1e-8a92-ea0de606bf0c`, `recovery_time_sec: 0.474`, and `errors: 0` with zero duplicate fulfillment effects. Note carefully that this was a freshly committed healthy `PENDING` item picked up during normal tick scheduling; intentional missed-stream and expired-lease crash recovery remain proven hermetically in [test_f1_outbox.py](tests/unit/test_f1_outbox.py).

---

## 5. CloudWatch Telemetry, Table Consistency & Remaining Material Limitations

- **CloudWatch Metrics & Logs (`12:31:00Z`–`12:36:00Z`, [cloudwatch_logs_summary.json](evidence/phase-09-final-validation/cloudwatch/cloudwatch_logs_summary.json), [cloudwatch_metrics_summary.json](evidence/phase-09-final-validation/cloudwatch/cloudwatch_metrics_summary.json)):**
  - API Gateway (`<redacted-api-id>`): `Count=184`, `4xx=8`, **`5xx=0`**, average API latency `88.80 ms`.
  - Lambda (`507` log-reported campaign invocations; `509` CW Metrics sum): `ExperimentOrderService` `184` inv (`max conc 2`, `0` errors/throttles), `OutboxRelay` `155` inv (`max conc 3`, `159` confirms, `10` deferrals, `0` errors/throttles), `FulfillmentWorker` `165` inv (`max conc 2`, `160` fulfills, `8` duplicates ignored, `0` errors/throttles), `OutboxRecoverySweeper` `3` campaign ticks (`12` post-deploy total ticks, `0` errors/throttles).
  - SQS (`silentloss-fulfillment-queue-HackathonAfterExp`): `Sent=168`, `Received=168`, `Deleted=168`; post-campaign queue and DLQ both `0 / 0`.
  - DynamoDB post-campaign full scans: `327 : 327 : 327 : 327 : 0` across Orders, Outbox, Fulfillment, Idempotency, and Rejections (`164` historical + `3` `finpref` + `80` `fin26r1` + `80` `fin26r2`), `unconfirmed_outbox_items_count=0`, `uncertain_outbox_items_count=0`, `max_fulfillments_per_order=1`, and `160/160` `fin26r1+fin26r2` items verified with `publication_uncertain=false` and `send_attempts_reserved=1`.
- **Remaining Material Limitations (Transparent Disclosure):**
  1. **DLQ Inspection Boundary:** `dlq_evidence_complete` stays `False` (`83.33%` source completeness) intentionally because `AwsEvidenceAcquirer` uses `GetQueueAttributes` without calling `sqs:ReceiveMessage`.
  2. **Synthetic Effect & Application Fault Scope:** Demonstrated business effect is a synthetic DynamoDB fulfillment record (`FulfillmentTable`), and faults are application-layer pre-send rejections/timeouts rather than AWS regional service outages.
  3. **Hermetic-Only Exceptional Failure Sequences:** Lost-ack-then-`AccessDenied` (`UNKNOWN` parking), interrupted reservation writes, terminal transaction timeout/conflict/readback failures (`TERMINAL_STATE_UNVERIFIED`), exhausted-retry parking, and missed-stream/expired-lease sweeper failover are proven hermetically ([test_publication_correction.py](tests/unit/test_publication_correction.py), [test_f1_outbox.py](tests/unit/test_f1_outbox.py)), not by fault-injecting account-wide SQS denials or crashing live Lambda containers.
  4. **Historical Preserved Baseline:** All `PHASE_5_EVIDENCE_MANIFEST.sha256` and `PHASE_6_7_EVIDENCE_MANIFEST.sha256` artifacts remain untouched; `166` historical Phase 6/7 entries remain verified alongside `FINAL_LIVE_EVIDENCE_MANIFEST.sha256`.
