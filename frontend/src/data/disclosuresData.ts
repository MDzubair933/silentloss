export interface DisclosureItem {
  id: string;
  title: string;
  category: "Methodology" | "Scope" | "Cost & Telemetry" | "Verification";
  detail: string;
}

export const ENGINEERING_DISCLOSURES: DisclosureItem[] = [
  {
    id: "disc-1",
    title: "Controlled Application-Layer Pre-Send Faults",
    category: "Methodology",
    detail:
      "Faults (SQS_INJECTED_TIMEOUT blocking 2 attempts, SQS_INJECTED_503 blocking 1 attempt) are injected inside the isolated benchmark fault adapter right before SQS SendMessage. They verify application behavior under publisher timeouts and 503s, not a live AWS SQS or DynamoDB regional service outage.",
  },
  {
    id: "disc-2",
    title: "Synthetic DynamoDB Fulfillment Effects",
    category: "Scope",
    detail:
      "The downstream business effect is a synthetic fulfillment record written atomically with OrdersTable status FULFILLED inside DynamoDB. Zero-duplicate claims prove conditional database idempotency in this synthetic model, not end-to-end financial ledger or physical carrier settlement.",
  },
  {
    id: "disc-3",
    title: "Finite 160-Order Benchmark, Not a Universal Zero-Loss Guarantee",
    category: "Scope",
    detail:
      "Observed silent loss dropped from 6.25% (10/160) to 0.00% (0/160) across two deterministic 80-order replicates (seeds 20261126 and 20261127). When a publisher exhausts its bounded retry budget (default 25) while publication uncertainty remains true, the system explicitly stops automatic sends and parks the record as UNKNOWN requiring manual operator attention rather than retrying indefinitely.",
  },
  {
    id: "disc-4",
    title: "Modeled Gross On-Demand Cost Estimates",
    category: "Cost & Telemetry",
    detail:
      "Campaign spend estimates ($0.002112 Before vs $0.002901 Corrected Live After window) are computed from metered CloudWatch invocations, billed Lambda duration, API Gateway HTTP requests, SQS API calls, and DynamoDB consumed RCUs/WCUs using public us-east-1 on-demand pricing prior to AWS Always-Free tier deductions.",
  },
  {
    id: "disc-5",
    title: "Non-Destructive Queue Inspection Boundary (83.33% Source Completeness)",
    category: "Cost & Telemetry",
    detail:
      "AwsEvidenceAcquirer checks SQS main and dead-letter queue visibility non-destructively via GetQueueAttributes without calling ReceiveMessage on active queues. dlq_evidence_complete therefore remains false by design (83.33% source completeness across combined datasets).",
  },
  {
    id: "disc-6",
    title: "Hermetic-Only Verification of Rare Crash & Rejection Sequences",
    category: "Verification",
    detail:
      "Live AWS tests verified normal ingestion, B-02 timeouts, B-03 503s, idempotent replays, duplicate SQS suppression, live pre-send reservations (send_attempts_reserved=1), and live scheduled sweeper confirmation on healthy PENDING order ord_fin26r1__B-02_0009. Cross-invocation lost-ack followed by AccessDenied, interrupted reservation writes, terminal transaction readback failure (TERMINAL_STATE_UNVERIFIED), and intentional missed-stream/expired-lease crash failover are verified strictly in the 109-test hermetic suite.",
  },
  {
    id: "disc-7",
    title: "Warm Live Campaign Timings vs. Cold-Start Smoke Timings",
    category: "Verification",
    detail:
      "During the warm 160-order final campaign, the 10 B-02 timeout-injected orders recovered in 1.667s to 2.800s (avg 2.058s). Immediately after the controlled deployment cutover, first-invocation ARM64 cold starts in the 3-order finpref smoke check produced recovery_time_sec of 15.906s to 19.278s (including initial container initialization and stream polling).",
  },
];

export const DATASET_DOWNLOADS = [
  {
    label: "Final Benchmark Summary (JSON)",
    file: "./data/final_benchmark_summary.json",
    description: "Sanitized Before and Corrected Live After metrics, replicate subtotals, scenario breakdowns, and modeled cost telemetry.",
  },
  {
    label: "Final Benchmark Scenarios (CSV)",
    file: "./data/final_benchmark_results.csv",
    description: "Tabular row-per-scenario and replicate metrics (fin26r1, fin26r2, COMBINED_FINAL_CAMPAIGN).",
  },
  {
    label: "Before vs. After Delta Dataset (JSON)",
    file: "./data/before_after_dataset.json",
    description: "Structured comparison deltas across C/F/P/E/U/I/D invariants, ingestion Lambda durations, HTTP RTT, and spend.",
  },
  {
    label: "B-02 Recovery Traces (10 Orders, JSON)",
    file: "./data/b02_recovery_traces.json",
    description: "All ten timeout-injected order traces joining API commit, outbox reservation/attempt state, relay logs, and fulfillment timestamps.",
  },
  {
    label: "Publication Claims Matrix (JSON)",
    file: "./data/claims_matrix.json",
    description: "Claim-by-claim verification status and required technical qualifications post-live revalidation.",
  },
];

export const BRANDING_ASSETS_NEEDED = [
  "Primary SVG logo mark & dark/light horizontal lockup (currently using a minimal geometric CSS/SVG split-square wordmark placeholder)",
  "Social preview / OpenGraph card image (1200x630 PNG) for GitHub and Builder Center embeds",
  "Favicon pack (favicon.svg, apple-touch-icon.png) matching final logo mark",
];
