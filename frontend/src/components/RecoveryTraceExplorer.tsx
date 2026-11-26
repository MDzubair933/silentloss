import { CheckCircle2, ChevronDown } from 'lucide-react';
import { useMemo, useState } from 'react';
import { finalBenchmarkSummary, recoveryTraces } from '../data/benchmarkData';

type TraceFilter = 'ALL' | 'fin26r1' | 'fin26r2';

export function RecoveryTraceExplorer() {
  const [filter, setFilter] = useState<TraceFilter>('ALL');
  const [selectedOrderId, setSelectedOrderId] = useState<string>(recoveryTraces[0]?.order_id ?? '');
  const [showRawIds, setShowRawIds] = useState(false);

  const visibleTraces = useMemo(
    () => (filter === 'ALL' ? recoveryTraces : recoveryTraces.filter((t) => t.run_id === filter)),
    [filter]
  );

  const selected = visibleTraces.find((t) => t.order_id === selectedOrderId) || visibleTraces[0];

  const stats = finalBenchmarkSummary.corrected_after_campaign.combined.b02_timeout_recovery_time_sec;
  const sweeperOrder = finalBenchmarkSummary.corrected_after_campaign.sweeper_live_pickup_order;

  const firstBlockedLog = selected?.cloudwatch_relay_logs[0];

  return (
    <section
      id="traces"
      className="border-b border-[#292E37] bg-[#0B0D11] py-14 sm:py-20"
    >
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-3xl font-bold tracking-[-0.02em] text-[#F3F4F6] sm:text-4xl">
              Recovery trace explorer
            </h2>
            <p className="mt-3 max-w-[62ch] text-[15px] leading-relaxed text-[#A5ADB9] sm:text-base">
              All ten timeout-injected B-02 orders from the live deployment, linking initial commit,
              blocked send retries, outbox publication, and confirmed fulfillment.
            </p>
          </div>

          <div
            role="group"
            aria-label="Filter traces by replicate"
            className="inline-flex w-fit gap-1 rounded-lg border border-[#292E37] bg-[#11151B] p-1"
          >
            {[
              { id: 'ALL' as const, label: 'All 10 orders' },
              { id: 'fin26r1' as const, label: 'fin26r1 (5)' },
              { id: 'fin26r2' as const, label: 'fin26r2 (5)' },
            ].map((b) => (
              <button
                key={b.id}
                type="button"
                aria-pressed={filter === b.id}
                onClick={() => setFilter(b.id)}
                className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors whitespace-nowrap focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                  filter === b.id
                    ? 'bg-[#E7A34D] font-semibold text-[#0B0D11]'
                    : 'text-[#A5ADB9] hover:text-[#F3F4F6]'
                }`}
              >
                {b.label}
              </button>
            ))}
          </div>
        </div>

        {/* 3 Primary Metrics */}
        <div className="mt-8 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-[#292E37] bg-[#292E37] sm:grid-cols-3 tabular-nums">
          <div className="bg-[#11151B] px-6 py-5">
            <div className="text-3xl font-semibold tracking-tight text-[#58C9A3] sm:text-4xl">
              {stats.count}/10
            </div>
            <div className="mt-1.5 text-[13px] text-[#A5ADB9]">Orders recovered</div>
          </div>
          <div className="bg-[#11151B] px-6 py-5">
            <div className="text-3xl font-semibold tracking-tight text-[#F3F4F6] sm:text-4xl">
              {stats.avg.toFixed(3)}s
            </div>
            <div className="mt-1.5 text-[13px] text-[#A5ADB9]">Average benchmark recovery</div>
          </div>
          <div className="bg-[#11151B] px-6 py-5">
            <div className="text-3xl font-semibold tracking-tight text-[#F3F4F6] sm:text-4xl">
              {stats.max.toFixed(3)}s
            </div>
            <div className="mt-1.5 text-[13px] text-[#A5ADB9]">Maximum benchmark recovery</div>
          </div>
        </div>

        <p className="mt-3 text-xs text-[#7C8593] tabular-nums">
          Observed range across B-02 fault traces: {stats.min.toFixed(3)}s min to {stats.max.toFixed(3)}s max via DynamoDB Streams. Separate live EventBridge sweeper confirmation also recorded on{' '}
          <span className="font-mono text-[#A5ADB9]">{sweeperOrder.order_id}</span> ({sweeperOrder.recovery_time_sec}s).
        </p>

        {/* Naturally Sized Order Grid (Zero nested vertical scrollbar) */}
        <div className="mt-6 grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-5">
          {visibleTraces.map((trace) => {
            const active = selected?.order_id === trace.order_id;
            return (
              <button
                key={trace.order_id}
                type="button"
                aria-pressed={active}
                onClick={() => setSelectedOrderId(trace.order_id)}
                className={`flex items-center justify-between gap-2 rounded-lg border px-3.5 py-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                  active
                    ? 'border-[#E7A34D]/60 bg-[#171B22]'
                    : 'border-[#292E37] bg-[#11151B] hover:border-[#373E4A]'
                }`}
              >
                <div className="min-w-0">
                  <div className="truncate font-mono text-xs font-medium text-[#F3F4F6]">
                    {trace.order_id}
                  </div>
                  <div className="mt-1 text-xs text-[#7C8593]">
                    {trace.outbox_durable_state.blocked_attempts_executed} blocked
                  </div>
                </div>
                <span className="shrink-0 text-sm font-semibold text-[#58C9A3] tabular-nums">
                  {trace.outbox_durable_state.recovery_time_sec.toFixed(3)}s
                </span>
              </button>
            );
          })}
        </div>

        {/* Selected Trace 5-Stage Vertical Timeline */}
        {selected && (
          <div className="mt-6 surface-card p-6 sm:p-8">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#292E37] pb-5">
              <div>
                <div className="text-xs font-medium text-[#7C8593]">Selected live order trace</div>
                <h3 className="mt-1 font-mono text-base font-medium text-[#F3F4F6] sm:text-lg">
                  {selected.order_id}
                </h3>
              </div>
              <span className="inline-flex items-center gap-1.5 rounded-md border border-[#58C9A3]/35 bg-[#58C9A3]/10 px-3 py-1 text-xs font-medium text-[#58C9A3]">
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                {selected.final_business_effect.orders_status}
              </span>
            </div>

            <ol className="relative mt-6 space-y-6 border-l border-[#292E37] pl-6">
              {/* 1. Order committed */}
              <li className="relative">
                <span
                  aria-hidden="true"
                  className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full bg-[#A5ADB9]"
                />
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="text-sm font-semibold text-[#F3F4F6]">1. Order committed</h4>
                  <span className="font-mono text-xs text-[#7C8593] tabular-nums">
                    {selected.api_ingestion.committed_at} ({selected.api_ingestion.duration_ms} ms)
                  </span>
                </div>
                <p className="mt-1 text-sm text-[#A5ADB9]">
                  API returned HTTP {selected.api_ingestion.http_status} with database outcome{' '}
                  <span className="font-mono text-xs text-[#F3F4F6]">{selected.api_ingestion.db_outcome}</span> and queue status{' '}
                  <span className="font-mono text-xs text-[#F3F4F6]">{selected.api_ingestion.queue_outcome_at_commit}</span>.
                </p>
              </li>

              {/* 2. Publication attempts blocked */}
              <li className="relative">
                <span
                  aria-hidden="true"
                  className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full bg-[#E1817B]"
                />
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="text-sm font-semibold text-[#F3F4F6]">
                    2. Publication attempts blocked
                  </h4>
                  {firstBlockedLog && (
                    <span className="font-mono text-xs text-[#7C8593] tabular-nums">
                      {firstBlockedLog.timestamp}
                    </span>
                  )}
                </div>
                <p className="mt-1 text-sm text-[#A5ADB9]">
                  {selected.outbox_durable_state.blocked_attempts_executed} pre-send SQS attempts blocked under injected fault{' '}
                  <span className="font-mono text-xs text-[#E7A34D]">{selected.api_ingestion.fault_scheduled}</span>.
                </p>
              </li>

              {/* 3. Durable retry state */}
              <li className="relative">
                <span
                  aria-hidden="true"
                  className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full bg-[#E7A34D]"
                />
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="text-sm font-semibold text-[#F3F4F6]">3. Durable retry state</h4>
                  <span className="font-mono text-xs text-[#7C8593] tabular-nums">
                    {selected.outbox_durable_state.attempt_count} total attempts
                  </span>
                </div>
                <p className="mt-1 text-sm text-[#A5ADB9]">
                  Outbox preserved retry scheduling with {selected.outbox_durable_state.send_attempts_reserved} pre-send reservation via{' '}
                  <span className="font-mono text-xs text-[#F3F4F6]">{selected.outbox_durable_state.recovery_source}</span>.
                </p>
              </li>

              {/* 4. Successful publication */}
              <li className="relative">
                <span
                  aria-hidden="true"
                  className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full bg-[#58C9A3]"
                />
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="text-sm font-semibold text-[#F3F4F6]">4. Successful publication</h4>
                  <span className="font-mono text-xs text-[#58C9A3] tabular-nums">
                    {selected.outbox_durable_state.published_at} ({selected.outbox_durable_state.recovery_time_sec.toFixed(3)}s elapsed)
                  </span>
                </div>
                <p className="mt-1 text-sm text-[#A5ADB9]">
                  Outbox transitioned to <span className="font-mono text-xs text-[#58C9A3]">{selected.outbox_durable_state.dispatch_state}</span> and cleared pre-send uncertainty (<span className="font-mono text-xs text-[#F3F4F6]">publication_uncertain={String(selected.outbox_durable_state.publication_uncertain)}</span>).
                </p>
              </li>

              {/* 5. Confirmed fulfillment */}
              <li className="relative">
                <span
                  aria-hidden="true"
                  className="absolute -left-[29px] top-1.5 h-2.5 w-2.5 rounded-full bg-[#58C9A3]"
                />
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <h4 className="text-sm font-semibold text-[#F3F4F6]">5. Confirmed fulfillment</h4>
                  <span className="font-mono text-xs text-[#7C8593] tabular-nums">
                    {selected.final_business_effect.fulfilled_at}
                  </span>
                </div>
                <p className="mt-1 text-sm text-[#A5ADB9]">
                  Downstream worker committed single fulfillment record with status{' '}
                  <span className="font-mono text-xs text-[#58C9A3]">{selected.final_business_effect.fulfillment_status}</span>.
                </p>
              </li>
            </ol>

            {/* Expandable Identifiers & Raw Log Disclosure */}
            <div className="mt-6 border-t border-[#292E37] pt-4">
              <button
                type="button"
                aria-expanded={showRawIds}
                onClick={() => setShowRawIds((v) => !v)}
                className="inline-flex items-center gap-2 text-xs font-medium text-[#A5ADB9] hover:text-[#F3F4F6] focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
              >
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform duration-150 ${showRawIds ? 'rotate-180' : ''}`}
                  aria-hidden="true"
                />
                Technical identifiers and CloudWatch relay telemetry
              </button>

              {showRawIds && (
                <div className="mt-3 space-y-3 rounded-lg border border-[#292E37] bg-[#0B0D11] p-4 font-mono text-xs text-[#A5ADB9]">
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <div>
                      <span className="text-[#7C8593]">outbox_id: </span>
                      <span className="text-[#F3F4F6] break-all">{selected.outbox_durable_state.outbox_id}</span>
                    </div>
                    <div>
                      <span className="text-[#7C8593]">event_id: </span>
                      <span className="text-[#F3F4F6] break-all">{selected.outbox_durable_state.event_id}</span>
                    </div>
                    <div>
                      <span className="text-[#7C8593]">sqs_message_id: </span>
                      <span className="text-[#F3F4F6] break-all">{selected.outbox_durable_state.sqs_message_id}</span>
                    </div>
                    <div>
                      <span className="text-[#7C8593]">fulfillment_id: </span>
                      <span className="text-[#F3F4F6] break-all">{selected.final_business_effect.fulfillment_id}</span>
                    </div>
                  </div>
                  <div className="border-t border-[#292E37] pt-2.5 space-y-1.5">
                    {selected.cloudwatch_relay_logs.map((log, i) => (
                      <div key={i} className="break-all">
                        <span className="text-[#7C8593]">[{log.timestamp}]</span> {log.message}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
