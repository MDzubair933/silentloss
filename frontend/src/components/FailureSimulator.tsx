import { ChevronDown, Pause, Play, RotateCcw, SkipForward } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import {
  buildSimulationSteps,
  getInitialSnapshot,
  type SimArchitecture,
  type SimScenario,
} from '../data/simulatorEngine';

const SCENARIOS: Array<{ id: SimScenario; label: string }> = [
  { id: 'healthy', label: 'Healthy traffic' },
  { id: 'timeout', label: 'Publication timeout' },
  { id: 'transient503', label: 'Transient 503' },
  { id: 'duplicate', label: 'Duplicate delivery' },
];

export function FailureSimulator() {
  const reduce = useReducedMotion();
  const [arch, setArch] = useState<SimArchitecture>('after');
  const [scenario, setScenario] = useState<SimScenario>('timeout');
  const [stepIdx, setStepIdx] = useState<number>(-1);
  const [playing, setPlaying] = useState<boolean>(false);
  const [showTech, setShowTech] = useState<boolean>(false);

  const steps = useMemo(() => buildSimulationSteps(arch, scenario), [arch, scenario]);
  const activeStep = stepIdx >= 0 && stepIdx < steps.length ? steps[stepIdx] : null;
  const snapshot = activeStep ? activeStep.snapshot : getInitialSnapshot();
  const isComplete = stepIdx >= steps.length - 1;

  useEffect(() => {
    if (!playing) return;
    if (stepIdx >= steps.length - 1) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(() => {
      setStepIdx((prev) => Math.min(prev + 1, steps.length - 1));
    }, 1050);
    return () => window.clearTimeout(timer);
  }, [playing, stepIdx, steps.length]);

  const handleSelectArch = (next: SimArchitecture) => {
    setPlaying(false);
    setArch(next);
    setStepIdx(-1);
  };

  const handleSelectScenario = (next: SimScenario) => {
    setPlaying(false);
    setScenario(next);
    setStepIdx(-1);
  };

  const handleRunOrToggle = () => {
    if (isComplete || stepIdx === -1) {
      setStepIdx(0);
      setPlaying(true);
      return;
    }
    setPlaying((v) => !v);
  };

  const handleStep = () => {
    setPlaying(false);
    setStepIdx((prev) => (prev < steps.length - 1 ? prev + 1 : prev));
  };

  const handleReset = () => {
    setPlaying(false);
    setStepIdx(-1);
  };

  const lifecycleStages = useMemo(() => {
    const activeIds = activeStep?.activeNodeIds ?? [];
    if (arch === 'before') {
      return [
        {
          id: 'received',
          label: 'Order received',
          active: activeIds.includes('client') || activeIds.includes('apigw') || activeIds.includes('orderFn'),
          tone: activeStep?.tone ?? 'amber',
        },
        {
          id: 'committed',
          label: 'Database committed',
          active: activeIds.includes('ddbCore') || snapshot.ordersStatus.includes('COMMITTED'),
          tone: activeStep?.tone === 'red' ? 'amber' : activeStep?.tone ?? 'amber',
        },
        {
          id: 'publication',
          label: 'Event publication',
          active: activeIds.includes('boundary') || activeIds.includes('sqs'),
          tone: activeStep?.tone ?? 'amber',
        },
        {
          id: 'fulfilled',
          label: 'Fulfillment',
          active: activeIds.includes('worker') || activeIds.includes('ddbFinal'),
          tone: activeStep?.tone ?? 'emerald',
        },
      ];
    }

    return [
      {
        id: 'received',
        label: 'Order received',
        active: activeIds.includes('client') || activeIds.includes('apigw') || activeIds.includes('orderFn'),
        tone: activeStep?.tone ?? 'amber',
      },
      {
        id: 'committed',
        label: 'Database + durable outbox',
        active: activeIds.includes('ddbTx'),
        tone: 'emerald' as const,
      },
      {
        id: 'recovery',
        label: 'Streams / Sweeper recovery',
        active: activeIds.includes('streamRelay') || activeIds.includes('sweeper'),
        tone: activeStep?.tone ?? 'amber',
      },
      {
        id: 'publication',
        label: 'Event publication',
        active: activeIds.includes('sqs'),
        tone: activeStep?.tone ?? 'emerald',
      },
      {
        id: 'fulfilled',
        label: 'Fulfillment',
        active: activeIds.includes('worker') || activeIds.includes('ddbFinal'),
        tone: activeStep?.tone ?? 'emerald',
      },
    ];
  }, [arch, activeStep, snapshot.ordersStatus]);

  return (
    <section
      id="simulator"
      className="border-b border-[#292E37] bg-[#0B0D11] py-14 sm:py-20"
    >
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-3xl font-bold tracking-[-0.02em] text-[#F3F4F6] sm:text-4xl">
              Interactive reliability lab
            </h2>
            <p className="mt-3 max-w-[60ch] text-[15px] leading-relaxed text-[#A5ADB9] sm:text-base">
              Walk through how post-commit publication faults strand dual-write orders and how the
              outbox recovers delivery cleanly.
            </p>
          </div>
          <span className="inline-flex w-fit items-center rounded-md border border-[#292E37] bg-[#11151B] px-3 py-1 text-xs font-medium text-[#A5ADB9]">
            Deterministic client-side simulation (non-AWS)
          </span>
        </div>

        <div className="mt-8 surface-card p-5 sm:p-8">
          {/* Clean Unified Toolbar */}
          <div className="flex flex-col gap-4 border-b border-[#292E37] pb-5 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex flex-wrap items-center gap-2">
              {(
                [
                  { id: 'before', label: 'Before - Dual Write' },
                  { id: 'after', label: 'After - Outbox' },
                ] as const
              ).map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => handleSelectArch(m.id)}
                  aria-pressed={arch === m.id}
                  className={`rounded-lg border px-3.5 py-1.5 text-[13px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                    arch === m.id
                      ? m.id === 'after'
                        ? 'border-[#58C9A3]/50 bg-[#58C9A3]/15 text-[#58C9A3]'
                        : 'border-[#E1817B]/50 bg-[#E1817B]/15 text-[#E1817B]'
                      : 'border-[#292E37] bg-[#0B0D11] text-[#A5ADB9] hover:text-[#F3F4F6]'
                  }`}
                >
                  {m.label}
                </button>
              ))}

              <span className="mx-1 hidden h-4 w-px bg-[#292E37] sm:inline-block" aria-hidden="true" />

              {SCENARIOS.map((sc) => (
                <button
                  key={sc.id}
                  type="button"
                  onClick={() => handleSelectScenario(sc.id)}
                  aria-pressed={scenario === sc.id}
                  className={`rounded-lg border px-3 py-1.5 text-[13px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                    scenario === sc.id
                      ? 'border-[#E7A34D]/50 bg-[#E7A34D]/15 text-[#E7A34D]'
                      : 'border-[#292E37] bg-[#0B0D11] text-[#A5ADB9] hover:text-[#F3F4F6]'
                  }`}
                >
                  {sc.label}
                </button>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={handleRunOrToggle}
                className="inline-flex items-center gap-1.5 rounded-lg bg-[#E7A34D] px-4 py-1.5 text-[13px] font-semibold text-[#0B0D11] transition-colors hover:bg-[#efae5c] focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
              >
                {playing ? (
                  <>
                    <Pause className="h-3.5 w-3.5" aria-hidden="true" /> Pause
                  </>
                ) : (
                  <>
                    <Play className="h-3.5 w-3.5" aria-hidden="true" />
                    {isComplete ? 'Replay' : stepIdx >= 0 ? 'Resume' : 'Run'}
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={handleStep}
                disabled={isComplete}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[#292E37] bg-[#171B22] px-3 py-1.5 text-[13px] font-medium text-[#F3F4F6] hover:border-[#373E4A] disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
              >
                <SkipForward className="h-3.5 w-3.5" aria-hidden="true" /> Step
              </button>
              <button
                type="button"
                onClick={handleReset}
                className="inline-flex items-center gap-1.5 rounded-lg border border-[#292E37] bg-[#0B0D11] px-3 py-1.5 text-[13px] font-medium text-[#A5ADB9] hover:text-[#F3F4F6] focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" /> Reset
              </button>
            </div>
          </div>

          {/* Dominant Visual Order Lifecycle */}
          <div className="mt-6">
            <div className="mb-3 flex items-center justify-between text-xs text-[#7C8593] tabular-nums">
              <span>Order lifecycle progression</span>
              <span>
                {activeStep
                  ? `Step ${activeStep.stepNumber} of ${steps.length} (${activeStep.elapsedLabel})`
                  : `Ready (${steps.length} stages)`}
              </span>
            </div>

            <div
              className={`grid grid-cols-1 gap-3 sm:grid-cols-2 ${
                lifecycleStages.length === 4 ? 'lg:grid-cols-4' : 'lg:grid-cols-5'
              }`}
            >
              {lifecycleStages.map((stage, i) => {
                const stateStyle = !stage.active
                  ? 'border-[#292E37] bg-[#0B0D11] text-[#7C8593]'
                  : stage.tone === 'red'
                    ? 'border-[#E1817B]/60 bg-[#E1817B]/10 text-[#E1817B]'
                    : stage.tone === 'emerald'
                      ? 'border-[#58C9A3]/60 bg-[#58C9A3]/10 text-[#58C9A3]'
                      : 'border-[#E7A34D]/60 bg-[#E7A34D]/10 text-[#E7A34D]';

                return (
                  <motion.div
                    key={stage.id}
                    animate={reduce ? undefined : { y: stage.active ? -2 : 0 }}
                    transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                    className={`rounded-xl border px-4 py-4 transition-colors ${stateStyle}`}
                  >
                    <div className="flex items-center justify-between text-xs opacity-80">
                      <span className="tabular-nums">0{i + 1}</span>
                      <span>
                        {stage.active
                          ? stage.tone === 'red'
                            ? 'Blocked'
                            : stage.tone === 'emerald'
                              ? 'Confirmed'
                              : 'Executing'
                          : 'Waiting'}
                      </span>
                    </div>
                    <div className="mt-2 text-sm font-semibold text-[#F3F4F6]">{stage.label}</div>
                  </motion.div>
                );
              })}
            </div>
          </div>

          {/* Concise Outcome Summary */}
          <div className="mt-6 grid grid-cols-2 gap-4 rounded-xl border border-[#292E37] bg-[#0B0D11] p-4 sm:grid-cols-4">
            <div>
              <div className="text-xs text-[#7C8593]">Order committed</div>
              <div className="mt-1 text-sm font-semibold text-[#F3F4F6]">{snapshot.ordersStatus}</div>
            </div>
            <div>
              <div className="text-xs text-[#7C8593]">Event publication</div>
              <div className="mt-1 text-sm font-semibold text-[#F3F4F6]">{snapshot.httpResponse}</div>
            </div>
            <div>
              <div className="text-xs text-[#7C8593]">Recovery status</div>
              <div className="mt-1 text-sm font-semibold text-[#F3F4F6]">{snapshot.dispatchState}</div>
            </div>
            <div>
              <div className="text-xs text-[#7C8593]">Fulfillment outcome</div>
              <div
                className={`mt-1 text-sm font-semibold ${
                  snapshot.reconciliationClass.startsWith('F')
                    ? 'text-[#58C9A3]'
                    : snapshot.reconciliationClass.startsWith('U')
                      ? 'text-[#E1817B]'
                      : 'text-[#E7A34D]'
                }`}
              >
                {snapshot.reconciliationClass}
              </div>
            </div>
          </div>

          {/* Short Readable Event Timeline */}
          <div className="mt-6 space-y-2">
            {steps.map((s, idx) => {
              const current = idx === stepIdx;
              const visited = idx <= stepIdx;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => {
                    setPlaying(false);
                    setStepIdx(idx);
                  }}
                  className={`flex w-full flex-col gap-1 rounded-lg border px-4 py-3 text-left transition-colors sm:flex-row sm:items-center sm:justify-between focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                    current
                      ? 'border-[#373E4A] bg-[#171B22]'
                      : visited
                        ? 'border-[#292E37] bg-[#11151B]'
                        : 'border-[#292E37]/60 bg-[#0B0D11] opacity-60 hover:opacity-90'
                  }`}
                >
                  <div>
                    <div className="text-sm font-medium text-[#F3F4F6]">
                      {s.stepNumber}. {s.title}
                    </div>
                    <div className="mt-0.5 text-xs text-[#A5ADB9]">{s.description}</div>
                  </div>
                  <span className="shrink-0 text-xs text-[#7C8593] tabular-nums">{s.elapsedLabel}</span>
                </button>
              );
            })}
          </div>

          {/* Expandable Secondary Technical Details */}
          <div className="mt-5 border-t border-[#292E37] pt-4">
            <button
              type="button"
              aria-expanded={showTech}
              onClick={() => setShowTech((v) => !v)}
              className="inline-flex items-center gap-2 text-xs font-medium text-[#A5ADB9] hover:text-[#F3F4F6] focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
            >
              <ChevronDown
                className={`h-3.5 w-3.5 transition-transform duration-150 ${showTech ? 'rotate-180' : ''}`}
                aria-hidden="true"
              />
              Technical details (simulated DynamoDB &amp; reservation state)
            </button>

            {showTech && (
              <dl className="mt-3 grid grid-cols-2 gap-3 rounded-lg border border-[#292E37] bg-[#0B0D11] p-4 font-mono text-xs sm:grid-cols-3 lg:grid-cols-6">
                <div>
                  <dt className="text-[#7C8593]">publication_uncertain</dt>
                  <dd className="mt-1 text-[#F3F4F6]">{snapshot.publicationUncertain}</dd>
                </div>
                <div>
                  <dt className="text-[#7C8593]">send_attempts_reserved</dt>
                  <dd className="mt-1 text-[#F3F4F6]">{snapshot.sendAttemptsReserved}</dd>
                </div>
                <div>
                  <dt className="text-[#7C8593]">blocked_attempts</dt>
                  <dd className="mt-1 text-[#F3F4F6]">{snapshot.blockedAttempts}</dd>
                </div>
                <div>
                  <dt className="text-[#7C8593]">sqs_messages</dt>
                  <dd className="mt-1 text-[#F3F4F6]">{snapshot.sqsQueueCount}</dd>
                </div>
                <div>
                  <dt className="text-[#7C8593]">fulfillment_count</dt>
                  <dd className="mt-1 text-[#F3F4F6]">{snapshot.fulfillmentCount}</dd>
                </div>
                <div>
                  <dt className="text-[#7C8593]">class</dt>
                  <dd className="mt-1 text-[#F3F4F6]">{snapshot.reconciliationClass}</dd>
                </div>
              </dl>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
