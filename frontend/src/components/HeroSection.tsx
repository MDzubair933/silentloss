import { ArrowRight } from 'lucide-react';
import { motion, useReducedMotion } from 'framer-motion';

const SUPPORTING_METRICS = [
  {
    value: '160/160',
    label: 'Orders fulfilled in the final After campaign',
    tone: 'text-[#58C9A3]',
  },
  {
    value: '10/10',
    label: 'Timeout-injected orders recovered',
    tone: 'text-[#F3F4F6]',
  },
  {
    value: '2.058s',
    label: 'Mean measured benchmark recovery',
    tone: 'text-[#F3F4F6]',
  },
  {
    value: '0',
    label: 'Duplicate completed business effects',
    tone: 'text-[#58C9A3]',
  },
];

export function HeroSection() {
  const reduce = useReducedMotion();

  return (
    <section
      id="top"
      className="border-b border-[#292E37] bg-[#0B0D11] pt-12 pb-14 sm:pt-16 sm:pb-18 lg:pt-20 lg:pb-24"
    >
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <motion.div
          initial={reduce ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.38, ease: [0.22, 1, 0.36, 1] }}
          className="grid grid-cols-1 gap-10 lg:grid-cols-12 lg:items-end lg:gap-12"
        >
          <div className="lg:col-span-7">
            <p className="text-xs font-semibold tracking-[0.12em] uppercase text-[#E7A34D]">
              An AWS reliability experiment
            </p>
            <h1 className="mt-4 text-4xl font-bold tracking-[-0.03em] text-[#F3F4F6] sm:text-5xl lg:text-[64px] lg:leading-[1.04]">
              Committed doesn&apos;t mean delivered.
            </h1>
            <p className="mt-5 max-w-[46ch] text-base leading-relaxed text-[#A5ADB9] sm:text-[18px]">
              One architectural flaw. One targeted fix. Zero observed silent losses.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3.5">
              <a
                href="#architecture"
                className="inline-flex items-center gap-2 rounded-lg bg-[#E7A34D] px-5 py-2.5 text-sm font-semibold text-[#0B0D11] transition-colors duration-150 hover:bg-[#efae5c] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E7A34D]"
              >
                Explore the experiment
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </a>
              <a
                href="#benchmark"
                className="inline-flex items-center gap-2 rounded-lg border border-[#292E37] bg-[#11151B] px-5 py-2.5 text-sm font-medium text-[#F3F4F6] transition-colors duration-150 hover:border-[#373E4A] hover:bg-[#171B22] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E7A34D]"
              >
                View benchmark evidence
              </a>
            </div>
          </div>

          {/* Dominant Outcome Comparison */}
          <div className="surface-card p-6 sm:p-8 lg:col-span-5">
            <div className="text-sm font-medium text-[#A5ADB9]">
              Observed silent order loss
            </div>
            <div className="mt-3 flex flex-wrap items-baseline gap-3 tabular-nums">
              <span className="text-4xl font-semibold tracking-tight text-[#E1817B] line-through decoration-[#E1817B]/60 sm:text-5xl">
                6.25%
              </span>
              <span className="text-2xl font-normal text-[#7C8593]" aria-hidden="true">
                →
              </span>
              <span className="text-5xl font-bold tracking-[-0.03em] text-[#58C9A3] sm:text-6xl">
                0.00%
              </span>
            </div>
            <p className="mt-4 text-sm text-[#A5ADB9]">
              Ten timeout-stranded orders eliminated across 160 committed requests after replacing
              dual-write publication with a transactional outbox.
            </p>
          </div>
        </motion.div>

        {/* Supporting Metric Strip */}
        <div className="mt-10 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-[#292E37] bg-[#292E37] sm:grid-cols-2 lg:grid-cols-4">
          {SUPPORTING_METRICS.map((item) => (
            <div key={item.label} className="bg-[#11151B] px-6 py-5 sm:py-6">
              <div className={`text-3xl font-semibold tracking-tight tabular-nums sm:text-[34px] ${item.tone}`}>
                {item.value}
              </div>
              <div className="mt-1.5 text-[13px] leading-snug text-[#A5ADB9]">
                {item.label}
              </div>
            </div>
          ))}
        </div>

        {/* Concise Verification & Scope Qualification */}
        <div className="mt-5 flex flex-col gap-2 text-[13px] text-[#7C8593] sm:flex-row sm:items-center sm:justify-between">
          <p>
            Measured across a controlled live AWS benchmark in <span className="font-mono text-[#A5ADB9]">us-east-1</span> (two deterministic 80-order replicates).
          </p>
          <p className="tabular-nums text-[#A5ADB9]">
            Verified alongside 109 passing hermetic local tests
          </p>
        </div>
      </div>
    </section>
  );
}
