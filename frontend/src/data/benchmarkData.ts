import beforeAfterRaw from "./raw/before_after_dataset.json";
import tracesRaw from "./raw/b02_recovery_traces.json";
import summaryRaw from "./raw/final_benchmark_summary.json";
import claimsRaw from "./raw/claims_matrix.json";

export interface LatencyStats {
  count: number;
  min_ms: number;
  avg_ms: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
}

export interface ScenarioReconciliation {
  C: number;
  F: number;
  P: number;
  E: number;
  U: number;
  I: number;
  D: number;
  duplicate_delivery_attempts: number;
  recovery_time_sec: number;
  silent_loss_rate_percent: number;
  fulfillment_completion_rate_percent: number;
  invariant_holds: boolean;
  clean_success_verified: boolean;
  source_completeness_percent: number;
}

export interface ScenarioRecord {
  scenario_id: "B-01" | "B-02" | "B-03" | "B-04";
  scenario_name: string;
  total_steps: number;
  standard_orders: number;
  negative_controls_rejected_400: number;
  replay_controls_200: number;
  duplicate_events_sent: number;
  scheduled_faults: number;
  confirmed_executed_faults: number;
  throttled_429_count: number;
  http_status_counts: Record<string, number>;
  inline_convergence_observed: boolean;
  reconciliation: ScenarioReconciliation;
  standard_api_latency_ms: LatencyStats;
}

export interface ReplicateRecord {
  replicate: number;
  run_id: string;
  seed: number;
  start_utc: string;
  end_utc: string;
  wall_clock_duration_seconds: number;
  replicate_totals: {
    total_steps: number;
    C: number;
    F: number;
    P: number;
    E: number;
    U: number;
    I: number;
    D: number;
    duplicate_delivery_attempts: number;
    recovery_time_max_sec?: number;
    silent_loss_rate_percent: number;
    fulfillment_completion_rate_percent: number;
    scheduled_faults: number;
    confirmed_executed_faults: number;
    throttled_429_count: number;
    invariant_holds: boolean;
    clean_success_verified?: boolean;
    standard_api_latency_ms: LatencyStats;
  };
  scenarios: Record<"B-01" | "B-02" | "B-03" | "B-04", ScenarioRecord>;
}

export interface RecoveryTraceRecord {
  run_id: string;
  order_id: string;
  correlation_id: string;
  api_ingestion: {
    http_status: number;
    duration_ms: number;
    db_outcome: string;
    queue_outcome_at_commit: string;
    fault_scheduled: string;
    committed_at: string;
  };
  outbox_durable_state: {
    outbox_id: string;
    event_id: string;
    dispatch_state: string;
    publication_uncertain: boolean;
    requires_attention: boolean;
    send_attempts_reserved: number;
    blocked_attempts_executed: number;
    fault_remaining_blocks: number;
    attempt_count: number;
    sqs_api_calls: number;
    last_error: string;
    recovery_source: string;
    recovery_time_sec: number;
    sqs_message_id: string;
    published_at: string;
  };
  cloudwatch_relay_logs: Array<{
    timestamp: string;
    service: string;
    level: string;
    message: string;
    trigger_source: string;
    error?: string;
    blocked_attempts: number;
    sqs_api_calls: number;
    next_attempt_at?: number;
    sqs_message_id?: string;
    recovery_time_sec?: number;
  }>;
  final_business_effect: {
    orders_status: string;
    orders_dispatch_status: string;
    fulfillment_id: string;
    fulfillment_status: string;
    fulfilled_at: string;
  };
}

export interface ClaimsMatrixRow {
  claim: string;
  evidence: string;
  status: string;
  qualification: string;
}

export const beforeAfterDataset = beforeAfterRaw;
export const recoveryTraces = tracesRaw as RecoveryTraceRecord[];
export const finalBenchmarkSummary = summaryRaw as {
  project: string;
  generated_utc: string;
  region: string;
  invariant: string;
  smoke_verification_finpref: {
    run_id: string;
    verified_at_utc: string;
    unauthenticated_denials: Record<string, number>;
    unsupported_fault_response: { status_code: number; body: { error: string } };
    ordinary_orders_header_isolation: {
      status_code: number;
      db_outcome: string;
      queue_outcome: string;
      fault_execution_evidence: null;
    };
    idempotent_replays: {
      ordinary_order_200: boolean;
      experiment_order_200: boolean;
    };
    duplicate_sqs_control_fulfillment_count: number;
    cold_start_recovery_times_sec: Record<string, number>;
  };
  before_campaign: {
    combined: {
      total_steps: number;
      total_committed_orders_C: number;
      fulfilled_orders_F: number;
      durably_pending_P: number;
      explicitly_failed_E: number;
      confirmed_silent_loss_U: number;
      indeterminate_I: number;
      duplicate_completed_effects_D: number;
      duplicate_delivery_attempts: number;
      silent_loss_rate_percent: number;
      fulfillment_completion_rate_percent: number;
      b02_fault_scenario_loss_rate_percent: number;
      throttled_429_count: number;
      invariant_holds: boolean;
      source_completeness_percent: number;
      standard_api_latency_ms: LatencyStats;
    };
    replicates: {
      bef26r1: ReplicateRecord;
      bef26r2: ReplicateRecord;
    };
    cost_assessment: {
      metered_usage: {
        lambda_invocations: number;
        lambda_billed_duration_ms: number;
        dynamodb_consumed_rcu: number;
        dynamodb_consumed_wcu: number;
        sqs_api_requests: number;
      };
      estimated_on_demand_usd_gross_before_free_tier: {
        lambda_usd: number;
        apigw_usd: number;
        sqs_usd: number;
        dynamodb_usd: number;
        total_estimated_usd: number;
      };
    };
  };
  corrected_after_campaign: {
    combined: {
      total_steps: number;
      total_committed_orders_C: number;
      fulfilled_orders_F: number;
      durably_pending_P: number;
      explicitly_failed_E: number;
      confirmed_silent_loss_U: number;
      indeterminate_I: number;
      duplicate_completed_effects_D: number;
      duplicate_delivery_attempts: number;
      silent_loss_rate_percent: number;
      fulfillment_completion_rate_percent: number;
      b02_fault_orders_recovered: number;
      b02_timeout_recovery_time_sec: { count: number; min: number; avg: number; max: number };
      b03_503_recovery_time_sec: { count: number; min: number; avg: number; max: number };
      throttled_429_count: number;
      invariant_holds: boolean;
      clean_success_verified: boolean;
      source_completeness_percent: number;
      standard_api_latency_ms: LatencyStats;
    };
    replicates: {
      fin26r1: ReplicateRecord;
      fin26r2: ReplicateRecord;
    };
    sweeper_live_pickup_order: {
      order_id: string;
      trigger_source: string;
      recovery_time_sec: number;
      confirmed: number;
      errors: number;
    };
    cost_assessment: {
      metered_usage: {
        lambda_campaign_invocations_cw_logs: number;
        lambda_window_invocations_cw_metrics: number;
        lambda_campaign_billed_duration_ms: number;
        apigw_window_http_requests: number;
        sqs_window_api_requests: number;
        dynamodb_window_consumed_rcu: number;
        dynamodb_window_consumed_wcu: number;
      };
      estimated_on_demand_usd_gross_before_free_tier: {
        lambda_usd: number;
        apigw_usd: number;
        sqs_usd: number;
        dynamodb_usd: number;
        total_estimated_usd: number;
      };
    };
  };
};
export const claimsMatrix = claimsRaw as ClaimsMatrixRow[];
