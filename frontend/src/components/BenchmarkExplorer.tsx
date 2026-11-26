import { useMemo, useState } from 'react';
import { finalBenchmarkSummary, type ScenarioRecord } from '../data/benchmarkData';

type ReplicateView = 'combined' | 'r1' | 'r2';
type ScenarioFilter = 'ALL' | 'B-01' | 'B-02' | 'B-03' | 'B-04';

const SCENARIO_IDS: Array<'B-01' | 'B-02' | 'B-03' | 'B-04'> = ['B-01', 'B-02', 'B-03', 'B-04'];

const SCENARIO_LABELS: Record<'B-01' | 'B-02' | 'B-03' | 'B-04', string> = {
  'B-01': 'Healthy traffic and controls',
  'B-02': 'SQS timeout fault (25%)',
  'B-03': 'Transient 503 fault (20%)',
  'B-04': 'API replay and SQS duplicates',
};

function mergeScenarios(a: ScenarioRecord, b: ScenarioRecord) {
  return {
    C: a.reconciliation.C + b.reconciliation.C,
    F: a.reconciliation.F + b.reconciliation.F,
    P: a.reconciliation.P + b.reconciliation.P,
    E: a.reconciliation.E + b.reconciliation.E,
    U: a.reconciliation.U + b.reconciliation.U,
    I: a.reconciliation.I + b.reconciliation.I,
    D: a.reconciliation.D + b.reconciliation.D,
    maxRecSec: Math.max(a.reconciliation.recovery_time_sec, b.reconciliation.recovery_time_sec),
    avgLatencyMs: Number(
      ((a.standard_api_latency_ms.avg_ms + b.standard_api_latency_ms.avg_ms) / 2).toFixed(2)
    ),
    p95LatencyMs: Number(
      Math.max(a.standard_api_latency_ms.p95_ms, b.standard_api_latency_ms.p95_ms).toFixed(2)
    ),
  };
}

export function BenchmarkExplorer() {
  const [repView, setRepView] = useState<ReplicateView>('combined');
  const [scenFilter, setScenFilter] = useState<ScenarioFilter>('ALL');

  const { beforeSummary, afterSummary, scenarioRows } = useMemo(() => {
    const bef = finalBenchmarkSummary.before_campaign;
    const aft = finalBenchmarkSummary.corrected_after_campaign;

    const bTotals =
      repView === 'combined'
        ? {
            C: bef.combined.total_committed_orders_C,
            F: bef.combined.fulfilled_orders_F,
            P: bef.combined.durably_pending_P,
            E: bef.combined.explicitly_failed_E,
            U: bef.combined.confirmed_silent_loss_U,
            I: bef.combined.indeterminate_I,
            D: bef.combined.duplicate_completed_effects_D,
            lossRate: bef.combined.silent_loss_rate_percent,
            completionRate: bef.combined.fulfillment_completion_rate_percent,
            avgMs: bef.combined.standard_api_latency_ms.avg_ms,
            p95Ms: bef.combined.standard_api_latency_ms.p95_ms,
          }
        : (() => {
            const rep = repView === 'r1' ? bef.replicates.bef26r1 : bef.replicates.bef26r2;
            const t = rep.replicate_totals;
            return {
              C: t.C,
              F: t.F,
              P: t.P,
              E: t.E,
              U: t.U,
              I: t.I,
              D: t.D,
              lossRate: t.silent_loss_rate_percent,
              completionRate: t.fulfillment_completion_rate_percent,
              avgMs: t.standard_api_latency_ms.avg_ms,
              p95Ms: t.standard_api_latency_ms.p95_ms,
            };
          })();

    const aTotals =
      repView === 'combined'
        ? {
            C: aft.combined.total_committed_orders_C,
            F: aft.combined.fulfilled_orders_F,
            P: aft.combined.durably_pending_P,
            E: aft.combined.explicitly_failed_E,
            U: aft.combined.confirmed_silent_loss_U,
            I: aft.combined.indeterminate_I,
            D: aft.combined.duplicate_completed_effects_D,
            lossRate: aft.combined.silent_loss_rate_percent,
            completionRate: aft.combined.fulfillment_completion_rate_percent,
            avgMs: aft.combined.standard_api_latency_ms.avg_ms,
            p95Ms: aft.combined.standard_api_latency_ms.p95_ms,
          }
        : (() => {
            const rep = repView === 'r1' ? aft.replicates.fin26r1 : aft.replicates.fin26r2;
            const t = rep.replicate_totals;
            return {
              C: t.C,
              F: t.F,
              P: t.P,
              E: t.E,
              U: t.U,
              I: t.I,
              D: t.D,
              lossRate: t.silent_loss_rate_percent,
              completionRate: t.fulfillment_completion_rate_percent,
              avgMs: t.standard_api_latency_ms.avg_ms,
              p95Ms: t.standard_api_latency_ms.p95_ms,
            };
          })();

    const rows = SCENARIO_IDS.map((sid) => {
      const bRec =
        repView === 'combined'
          ? mergeScenarios(bef.replicates.bef26r1.scenarios[sid], bef.replicates.bef26r2.scenarios[sid])
          : (() => {
              const s = (repView === 'r1' ? bef.replicates.bef26r1 : bef.replicates.bef26r2).scenarios[sid];
              return {
                ...s.reconciliation,
                maxRecSec: s.reconciliation.recovery_time_sec,
                avgLatencyMs: s.standard_api_latency_ms.avg_ms,
                p95LatencyMs: s.standard_api_latency_ms.p95_ms,
              };
            })();

      const aRec =
        repView === 'combined'
          ? mergeScenarios(aft.replicates.fin26r1.scenarios[sid], aft.replicates.fin26r2.scenarios[sid])
          : (() => {
              const s = (repView === 'r1' ? aft.replicates.fin26r1 : aft.replicates.fin26r2).scenarios[sid];
              return {
                ...s.reconciliation,
                maxRecSec: s.reconciliation.recovery_time_sec,
                avgLatencyMs: s.standard_api_latency_ms.avg_ms,
                p95LatencyMs: s.standard_api_latency_ms.p95_ms,
              };
            })();

      return { sid, label: SCENARIO_LABELS[sid], before: bRec, after: aRec };
    });

    return { beforeSummary: bTotals, afterSummary: aTotals, scenarioRows: rows };
  }, [repView]);

  const visibleRows = useMemo(
    () => (scenFilter === 'ALL' ? scenarioRows : scenarioRows.filter((r) => r.sid === scenFilter)),
    [scenarioRows, scenFilter]
  );

  const b02Row = scenarioRows.find((r) => r.sid === 'B-02')!;
  const b02LossBefore = b02Row.before.C > 0 ? (b02Row.before.U / b02Row.before.C) * 100 : 0;
  const b02LossAfter = b02Row.after.C > 0 ? (b02Row.after.U / b02Row.after.C) * 100 : 0;
  const timeoutCount = repView === 'combined' ? 10 : 5;

  const befCost =
    finalBenchmarkSummary.before_campaign.cost_assessment.estimated_on_demand_usd_gross_before_free_tier;
  const aftCost =
    finalBenchmarkSummary.corrected_after_campaign.cost_assessment.estimated_on_demand_usd_gross_before_free_tier;

  return (
    <section
      id="benchmark"
      className="border-b border-[#292E37] bg-[#0B0D11] py-14 sm:py-20"
    >
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="text-3xl font-bold tracking-[-0.02em] text-[#F3F4F6] sm:text-4xl">
              Live AWS benchmark results
            </h2>
            <p className="mt-3 max-w-[62ch] text-[15px] leading-relaxed text-[#A5ADB9] sm:text-base">
              Phase 9 live measurements comparing the baseline deployment against the corrected
              transactional outbox across identical seeded traffic.
            </p>
          </div>

          <div
            role="group"
            aria-label="Replicate scope filter"
            className="inline-flex w-fit flex-wrap gap-1 rounded-lg border border-[#292E37] bg-[#11151B] p-1"
          >
            {[
              { id: 'combined' as const, label: 'Combined (160)' },
              { id: 'r1' as const, label: 'fin26r1 (80)' },
              { id: 'r2' as const, label: 'fin26r2 (80)' },
            ].map((tab) => (
              <button
                key={tab.id}
                type="button"
                aria-pressed={repView === tab.id}
                onClick={() => setRepView(tab.id)}
                className={`rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors whitespace-nowrap focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                  repView === tab.id
                    ? 'bg-[#E7A34D] font-semibold text-[#0B0D11]'
                    : 'text-[#A5ADB9] hover:text-[#F3F4F6]'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>

        {/* Primary Reliability Outcome Comparison */}
        <div className="mt-8 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-[#292E37] bg-[#292E37] sm:grid-cols-2 lg:grid-cols-5">
          {[
            {
              title: 'Silent loss rate',
              before: `${beforeSummary.lossRate.toFixed(2)}%`,
              after: `${afterSummary.lossRate.toFixed(2)}%`,
              barPct: 0,
              accent: 'text-[#58C9A3]',
            },
            {
              title: 'Fulfillment completion',
              before: `${beforeSummary.completionRate.toFixed(2)}%`,
              after: `${afterSummary.completionRate.toFixed(2)}%`,
              barPct: afterSummary.completionRate,
              accent: 'text-[#58C9A3]',
            },
            {
              title: 'B-02 timeout loss',
              before: `${b02LossBefore.toFixed(0)}%`,
              after: `${b02LossAfter.toFixed(0)}%`,
              barPct: 0,
              accent: 'text-[#58C9A3]',
            },
            {
              title: 'Timeout recovery',
              before: `0/${timeoutCount}`,
              after: `${timeoutCount}/${timeoutCount}`,
              barPct: 100,
              accent: 'text-[#58C9A3]',
            },
            {
              title: 'Duplicate effects (D)',
              before: `${beforeSummary.D}`,
              after: `${afterSummary.D}`,
              barPct: 0,
              accent: 'text-[#F3F4F6]',
            },
          ].map((metric) => (
            <div key={metric.title} className="bg-[#11151B] p-5 sm:p-6 tabular-nums">
              <div className="text-[13px] font-medium text-[#A5ADB9]">{metric.title}</div>
              <div className="mt-2.5 flex items-baseline gap-2">
                <span className="text-sm text-[#7C8593]">{metric.before}</span>
                <span className="text-xs text-[#7C8593]" aria-hidden="true">→</span>
                <span className={`text-3xl font-semibold tracking-tight ${metric.accent}`}>
                  {metric.after}
                </span>
              </div>
              <div className="mt-3 h-1 w-full rounded-full bg-[#171B22]">
                <div
                  className="h-1 rounded-full bg-[#58C9A3] transition-all duration-300"
                  style={{ width: `${metric.barPct}%` }}
                />
              </div>
            </div>
          ))}
        </div>

        {/* Chart + Compact Invariant Validation */}
        <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="surface-card p-6 lg:col-span-7">
            <div className="flex flex-wrap items-center justify-between gap-2 pb-4">
              <h3 className="text-base font-semibold text-[#F3F4F6]">
                Fulfilled vs. unaccounted orders by scenario
              </h3>
              <div className="flex items-center gap-4 text-xs text-[#A5ADB9]">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-xs bg-[#64748B]" /> Before fulfilled
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-xs bg-[#E1817B]" /> Before silent loss
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-xs bg-[#58C9A3]" /> After fulfilled
                </span>
              </div>
            </div>

            <div className="overflow-x-auto">
              <svg
                viewBox="0 0 600 220"
                className="w-full min-w-[460px]"
                role="img"
                aria-label="Bar chart comparing fulfilled and unaccounted orders across scenarios B-01 through B-04"
              >
                {[0, 0.25, 0.5, 0.75, 1].map((t) => {
                  const y = 170 - t * 136;
                  const tick = Math.round((repView === 'combined' ? 40 : 20) * t);
                  return (
                    <g key={t}>
                      <line x1="42" y1={y} x2="584" y2={y} stroke="#292E37" strokeWidth="1" />
                      <text x="34" y={y + 4} textAnchor="end" fill="#7C8593" fontSize="11">
                        {tick}
                      </text>
                    </g>
                  );
                })}

                {scenarioRows.map((row, idx) => {
                  const maxVal = repView === 'combined' ? 40 : 20;
                  const groupX = 76 + idx * 128;
                  const befFH = (row.before.F / maxVal) * 136;
                  const befUH = (row.before.U / maxVal) * 136;
                  const aftFH = (row.after.F / maxVal) * 136;

                  return (
                    <g key={row.sid}>
                      <rect x={groupX} y={170 - befFH} width="32" height={befFH} rx="3" fill="#64748B" />
                      {row.before.U > 0 && (
                        <rect
                          x={groupX}
                          y={170 - befFH - befUH}
                          width="32"
                          height={befUH}
                          rx="3"
                          fill="#E1817B"
                        />
                      )}
                      <text
                        x={groupX + 16}
                        y={170 - befFH - befUH - 6}
                        textAnchor="middle"
                        fill={row.before.U > 0 ? '#E1817B' : '#A5ADB9'}
                        fontSize="11"
                        fontWeight="500"
                      >
                        {row.before.U > 0 ? `${row.before.F}+${row.before.U}U` : `${row.before.F}`}
                      </text>

                      <rect x={groupX + 42} y={170 - aftFH} width="32" height={aftFH} rx="3" fill="#58C9A3" />
                      <text
                        x={groupX + 58}
                        y={170 - aftFH - 6}
                        textAnchor="middle"
                        fill="#58C9A3"
                        fontSize="11"
                        fontWeight="600"
                      >
                        {row.after.F}
                      </text>

                      <text x={groupX + 37} y="192" textAnchor="middle" fill="#F3F4F6" fontSize="12" fontWeight="600">
                        {row.sid}
                      </text>
                      <text x={groupX + 37} y="208" textAnchor="middle" fill="#7C8593" fontSize="11">
                        {row.label.split(' ')[0]}
                      </text>
                    </g>
                  );
                })}
              </svg>
            </div>
          </div>

          {/* Compact Reconciliation Invariant Validation */}
          <div className="surface-card flex flex-col justify-between p-6 lg:col-span-5 tabular-nums">
            <div>
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-[#F3F4F6]">Reconciliation invariant</h3>
                <span className="font-mono text-xs text-[#A5ADB9]">C = F + P + E + U + I</span>
              </div>
              <p className="mt-1.5 text-sm text-[#7C8593]">
                Every unique committed order resolves into one mutually exclusive state.
              </p>

              <div className="mt-5 space-y-3">
                <div className="rounded-lg border border-[#E1817B]/30 bg-[#171B22] p-4">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-[#A5ADB9]">Before baseline</span>
                    <span className="font-semibold text-[#E1817B]">{beforeSummary.U} unaccounted</span>
                  </div>
                  <div className="mt-1.5 font-mono text-sm text-[#F3F4F6]">
                    {beforeSummary.C} = {beforeSummary.F}F + {beforeSummary.P}P + {beforeSummary.E}E +{' '}
                    <span className="text-[#E1817B]">{beforeSummary.U}U</span> + {beforeSummary.I}I
                  </div>
                </div>

                <div className="rounded-lg border border-[#58C9A3]/30 bg-[#171B22] p-4">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-[#A5ADB9]">After transactional outbox</span>
                    <span className="font-semibold text-[#58C9A3]">0 unaccounted</span>
                  </div>
                  <div className="mt-1.5 font-mono text-sm text-[#F3F4F6]">
                    {afterSummary.C} = <span className="text-[#58C9A3]">{afterSummary.F}F</span> +{' '}
                    {afterSummary.P}P + {afterSummary.E}E + {afterSummary.U}U + {afterSummary.I}I
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-4 flex items-center justify-between border-t border-[#292E37] pt-3.5 text-xs text-[#7C8593]">
              <span>Non-destructive SQS visibility mode</span>
              <span>83.33% source completeness</span>
            </div>
          </div>
        </div>

        {/* Scenario Table */}
        <div className="mt-6 surface-card overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#292E37] px-6 py-4">
            <h3 className="text-base font-semibold text-[#F3F4F6]">Scenario breakdown</h3>
            <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Scenario filter">
              {(['ALL', 'B-01', 'B-02', 'B-03', 'B-04'] as ScenarioFilter[]).map((sid) => (
                <button
                  key={sid}
                  type="button"
                  aria-pressed={scenFilter === sid}
                  onClick={() => setScenFilter(sid)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                    scenFilter === sid
                      ? 'bg-[#171B22] text-[#F3F4F6]'
                      : 'text-[#7C8593] hover:text-[#F3F4F6]'
                  }`}
                >
                  {sid}
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm tabular-nums">
              <thead>
                <tr className="border-b border-[#292E37] bg-[#11151B] text-xs font-medium text-[#7C8593]">
                  <th className="px-6 py-3">Scenario</th>
                  <th className="px-4 py-3 text-right">Committed (C)</th>
                  <th className="px-4 py-3 text-right">Before F / U</th>
                  <th className="px-4 py-3 text-right">After F / U</th>
                  <th className="px-4 py-3 text-right">Duplicates (D)</th>
                  <th className="px-4 py-3 text-right">Max recovery</th>
                  <th className="px-6 py-3 text-right">API avg / p95</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#292E37]">
                {visibleRows.map((row) => (
                  <tr key={row.sid} className="hover:bg-[#171B22]/50">
                    <td className="px-6 py-3.5">
                      <span className="font-mono text-xs font-medium text-[#E7A34D]">{row.sid}</span>{' '}
                      <span className="ml-1 font-medium text-[#F3F4F6]">{row.label}</span>
                    </td>
                    <td className="px-4 py-3.5 text-right text-[#F3F4F6]">{row.after.C}</td>
                    <td className="px-4 py-3.5 text-right">
                      <span className="text-[#A5ADB9]">{row.before.F}</span>
                      {' / '}
                      <span className={row.before.U > 0 ? 'font-semibold text-[#E1817B]' : 'text-[#A5ADB9]'}>
                        {row.before.U}
                      </span>
                    </td>
                    <td className="px-4 py-3.5 text-right">
                      <span className="font-semibold text-[#58C9A3]">{row.after.F}</span>
                      <span className="text-[#7C8593]"> / {row.after.U}</span>
                    </td>
                    <td className="px-4 py-3.5 text-right text-[#F3F4F6]">{row.after.D}</td>
                    <td className="px-4 py-3.5 text-right text-[#A5ADB9]">
                      {row.after.maxRecSec > 0 ? `${row.after.maxRecSec.toFixed(3)}s` : '-'}
                    </td>
                    <td className="px-6 py-3.5 text-right text-[#A5ADB9]">
                      {row.after.avgLatencyMs.toFixed(1)} / {row.after.p95LatencyMs.toFixed(1)} ms
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Distinct Secondary Performance & Cost Trade-Offs */}
        <div className="mt-8">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-base font-semibold text-[#F3F4F6]">
              Secondary performance and cost trade-offs
            </h3>
            <span className="text-xs text-[#7C8593]">Separate from reliability totals</span>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-3 tabular-nums">
            <div className="surface-card p-5">
              <div className="text-xs font-medium text-[#A5ADB9]">
                Average ingestion Lambda duration
              </div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-sm text-[#7C8593]">71.28 ms</span>
                <span className="text-xs text-[#7C8593]" aria-hidden="true">→</span>
                <span className="text-2xl font-semibold text-[#58C9A3]">37.88 ms</span>
              </div>
              <p className="mt-1.5 text-xs text-[#7C8593]">
                p95 dropped from 98.85 ms to 43.91 ms after removing inline SQS I/O.
              </p>
            </div>

            <div className="surface-card p-5">
              <div className="text-xs font-medium text-[#A5ADB9]">
                Average client HTTP RTT
              </div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-sm text-[#7C8593]">{beforeSummary.avgMs.toFixed(2)} ms</span>
                <span className="text-xs text-[#7C8593]" aria-hidden="true">→</span>
                <span className="text-2xl font-semibold text-[#F3F4F6]">
                  {afterSummary.avgMs.toFixed(2)} ms
                </span>
              </div>
              <p className="mt-1.5 text-xs text-[#7C8593]">
                End-to-end RTT remained essentially flat; outbox durability is not a latency speedup.
              </p>
            </div>

            <div className="surface-card p-5">
              <div className="text-xs font-medium text-[#A5ADB9]">
                Estimated gross campaign spend
              </div>
              <div className="mt-2 flex items-baseline gap-2">
                <span className="text-sm text-[#7C8593]">${befCost.total_estimated_usd.toFixed(6)}</span>
                <span className="text-xs text-[#7C8593]" aria-hidden="true">→</span>
                <span className="text-2xl font-semibold text-[#E7A34D]">
                  ${aftCost.total_estimated_usd.toFixed(6)}
                </span>
              </div>
              <p className="mt-1.5 text-xs text-[#7C8593]">
                USD gross before Free Tier. Additional Outbox writes and scheduled polling increase cost.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
