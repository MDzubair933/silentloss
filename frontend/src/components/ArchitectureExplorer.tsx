import { ChevronDown, GitCompareArrows, Zap } from 'lucide-react';
import { useState } from 'react';

type ArchMode = 'before' | 'after' | 'compare';
type PathFocus = 'fast' | 'sweeper';

const IMPLEMENTATION_DETAILS = [
  {
    component: 'OrderService Lambda',
    before: 'Commits 2 DynamoDB tables, then synchronously calls sqs:SendMessage inline. Zero durable recovery intent remains on HTTP 504 timeout.',
    after: 'Commits 3 DynamoDB tables (Orders, Idempotency, Outbox) atomically, holds zero sqs:* IAM permissions, and returns HTTP 201 PENDING.',
  },
  {
    component: 'OutboxRelay (Fast Path)',
    before: 'Not present in baseline.',
    after: 'Triggered by DynamoDB Streams INSERT filter (BatchSize=5, MaxRetries=2). Acquires conditional lease before calling SQS; recovers warm B-02 faults in 1.667s to 2.800s.',
  },
  {
    component: 'OutboxRecoverySweeper (Safety Net)',
    before: 'Not present in baseline.',
    after: 'EventBridge rate(1 minute) scanner over DispatchStateIndex. Recovers missed stream events, due RETRY_WAIT items, or expired 10s leases.',
  },
  {
    component: 'FulfillmentWorker',
    before: 'Consumes SQS messages with conditional attribute_not_exists(order_id) check (D = 0).',
    after: 'Unchanged idempotent consumer (D = 0 across 8 SQS duplicate deliveries and 16 API replays).',
  },
];

function BeforeSvg({ compact = false }: { compact?: boolean }) {
  return (
    <svg
      viewBox="0 0 920 330"
      className={compact ? 'w-full' : 'w-full min-w-[680px]'}
      role="img"
      aria-label="Before dual-write architecture showing synchronous database commit followed by an unlinked SQS publication failure boundary"
    >
      <defs>
        <marker id="bef-slate" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 10 5 L 0 9 z" fill="#7C8593" />
        </marker>
        <marker id="bef-green" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 10 5 L 0 9 z" fill="#58C9A3" />
        </marker>
        <marker id="bef-red" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 10 5 L 0 9 z" fill="#E1817B" />
        </marker>
      </defs>

      {/* Hazard Highlight Zone */}
      <rect
        x="432"
        y="34"
        width="206"
        height="168"
        rx="10"
        fill="rgba(225, 129, 123, 0.06)"
        stroke="rgba(225, 129, 123, 0.45)"
        strokeDasharray="5 4"
      />
      <text x="535" y="56" textAnchor="middle" fill="#E1817B" fontSize="11" fontWeight="600">
        Publication failure boundary
      </text>

      {/* Arrows */}
      <line x1="168" y1="115" x2="218" y2="115" stroke="#7C8593" strokeWidth="1.8" markerEnd="url(#bef-slate)" />
      <line x1="305" y1="156" x2="305" y2="218" stroke="#58C9A3" strokeWidth="2.2" markerEnd="url(#bef-green)" />
      <line x1="392" y1="115" x2="452" y2="115" stroke="#E1817B" strokeWidth="2.2" strokeDasharray="5 4" markerEnd="url(#bef-red)" />
      <line x1="616" y1="115" x2="676" y2="115" stroke="#7C8593" strokeWidth="1.8" strokeDasharray="4 4" markerEnd="url(#bef-slate)" />
      <line x1="762" y1="156" x2="762" y2="218" stroke="#7C8593" strokeWidth="1.8" markerEnd="url(#bef-slate)" />

      {/* Edge labels */}
      <text x="322" y="193" fill="#58C9A3" fontSize="11.5" fontWeight="500">1. Committed</text>
      <text x="535" y="186" textAnchor="middle" fill="#E1817B" fontSize="11.5" fontWeight="500">
        No outbox intent written
      </text>

      {/* Uniform Nodes (164 x 80) */}
      {[
        { x: 16, y: 75, w: 152, h: 80, title: 'API Gateway', sub: 'SigV4 HTTP API', stroke: '#373E4A' },
        { x: 222, y: 75, w: 168, h: 80, title: 'OrderService', sub: 'Dual-write handler', stroke: '#E7A34D' },
        { x: 222, y: 222, w: 168, h: 80, title: 'DynamoDB Core', sub: 'Orders + Idempotency', stroke: '#58C9A3' },
        { x: 456, y: 75, w: 160, h: 80, title: 'Sync SendMessage', sub: '504 ReadTimeout -> U=10', stroke: '#E1817B' },
        { x: 680, y: 75, w: 164, h: 80, title: 'Fulfillment SQS', sub: 'Unreached on fault', stroke: '#373E4A' },
        { x: 680, y: 222, w: 164, h: 80, title: 'FulfillmentWorker', sub: 'Idempotent update', stroke: '#373E4A' },
      ].map((n) => (
        <g key={n.title}>
          <rect x={n.x} y={n.y} width={n.w} height={n.h} rx="10" fill="#171B22" stroke={n.stroke} strokeWidth="1.5" />
          <text x={n.x + n.w / 2} y={n.y + 34} textAnchor="middle" fill="#F3F4F6" fontSize="13.5" fontWeight="600">
            {n.title}
          </text>
          <text
            x={n.x + n.w / 2}
            y={n.y + 56}
            textAnchor="middle"
            fill="#A5ADB9"
            fontSize="11"
            fontFamily="JetBrains Mono, monospace"
          >
            {n.sub}
          </text>
        </g>
      ))}
    </svg>
  );
}

function AfterSvg({ pathFocus, compact = false }: { pathFocus: PathFocus; compact?: boolean }) {
  const fastStroke = pathFocus === 'fast' ? '#58C9A3' : '#373E4A';
  const sweeperStroke = pathFocus === 'sweeper' ? '#E7A34D' : '#4B5563';

  return (
    <svg
      viewBox="0 0 980 390"
      className={compact ? 'w-full' : 'w-full min-w-[700px]'}
      role="img"
      aria-label="After transactional outbox architecture showing atomic 3-table commit, fast DynamoDB Streams relay, scheduled EventBridge sweeper, lease-protected publisher, and idempotent worker"
    >
      <defs>
        <marker id="aft-slate" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 10 5 L 0 9 z" fill="#7C8593" />
        </marker>
        <marker id="aft-green" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 10 5 L 0 9 z" fill="#58C9A3" />
        </marker>
        <marker id="aft-amber" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
          <path d="M 0 1 L 10 5 L 0 9 z" fill="#E7A34D" />
        </marker>
      </defs>

      {/* Atomic TX Boundary */}
      <rect
        x="196"
        y="34"
        width="206"
        height="274"
        rx="12"
        fill="rgba(88, 201, 163, 0.04)"
        stroke="rgba(88, 201, 163, 0.3)"
        strokeDasharray="4 4"
      />
      <text x="299" y="54" textAnchor="middle" fill="#58C9A3" fontSize="11" fontWeight="600">
        Atomic ingestion boundary
      </text>

      {/* Connectors */}
      <line x1="158" y1="108" x2="210" y2="108" stroke="#7C8593" strokeWidth="1.8" markerEnd="url(#aft-slate)" />
      <line x1="299" y1="148" x2="299" y2="206" stroke="#58C9A3" strokeWidth="2.2" markerEnd="url(#aft-green)" />

      {/* Fast Streams Path */}
      <path
        d="M 388 242 L 434 242 L 434 114 L 466 114"
        fill="none"
        stroke={fastStroke}
        strokeWidth={pathFocus === 'fast' ? 2.4 : 1.5}
        markerEnd={pathFocus === 'fast' ? 'url(#aft-green)' : 'url(#aft-slate)'}
      />
      {/* Scheduled Sweeper Path */}
      <path
        d="M 388 262 L 434 262 L 434 316 L 466 316"
        fill="none"
        stroke={sweeperStroke}
        strokeWidth={pathFocus === 'sweeper' ? 2.4 : 1.5}
        strokeDasharray="5 4"
        markerEnd={pathFocus === 'sweeper' ? 'url(#aft-amber)' : 'url(#aft-slate)'}
      />

      {/* Converge on Lease Publisher -> Worker */}
      <path
        d="M 634 114 L 664 114 L 664 195 L 686 195"
        fill="none"
        stroke={fastStroke}
        strokeWidth={pathFocus === 'fast' ? 2.2 : 1.5}
        markerEnd={pathFocus === 'fast' ? 'url(#aft-green)' : 'url(#aft-slate)'}
      />
      <path
        d="M 634 316 L 664 316 L 664 235 L 686 235"
        fill="none"
        stroke={sweeperStroke}
        strokeWidth={pathFocus === 'sweeper' ? 2.2 : 1.5}
        strokeDasharray="5 4"
        markerEnd={pathFocus === 'sweeper' ? 'url(#aft-amber)' : 'url(#aft-slate)'}
      />
      <line x1="846" y1="215" x2="866" y2="215" stroke="#58C9A3" strokeWidth="2.2" markerEnd="url(#aft-green)" />

      {/* Clear inline path labels */}
      <text x="550" y="60" textAnchor="middle" fill={pathFocus === 'fast' ? '#58C9A3' : '#7C8593'} fontSize="11.5" fontWeight="600">
        Fast path: DynamoDB Streams
      </text>
      <text x="550" y="374" textAnchor="middle" fill={pathFocus === 'sweeper' ? '#E7A34D' : '#7C8593'} fontSize="11.5" fontWeight="600">
        Safety net: EventBridge 1-min sweeper
      </text>

      {/* Nodes */}
      {[
        { x: 16, y: 68, w: 142, h: 80, title: 'API Gateway', sub: 'SigV4 HTTP API', stroke: '#373E4A' },
        { x: 214, y: 68, w: 170, h: 80, title: 'OrderService', sub: '0 SQS grants (201)', stroke: '#58C9A3' },
        { x: 210, y: 210, w: 178, h: 84, title: '3-Table Atomic TX', sub: 'Orders+Idemp+Outbox', stroke: '#58C9A3' },
        { x: 470, y: 74, w: 164, h: 80, title: 'OutboxRelay', sub: 'Stream INSERT ESM', stroke: pathFocus === 'fast' ? '#58C9A3' : '#373E4A' },
        { x: 470, y: 276, w: 164, h: 80, title: 'RecoverySweeper', sub: 'DispatchStateIndex', stroke: pathFocus === 'sweeper' ? '#E7A34D' : '#373E4A' },
        { x: 690, y: 174, w: 156, h: 82, title: 'Lease Publisher', sub: 'Pre-send lock + SQS', stroke: '#E7A34D' },
        { x: 870, y: 174, w: 96, h: 82, title: 'Worker', sub: 'Idempotent F', stroke: '#58C9A3' },
      ].map((n) => (
        <g key={n.title}>
          <rect x={n.x} y={n.y} width={n.w} height={n.h} rx="10" fill="#171B22" stroke={n.stroke} strokeWidth="1.5" />
          <text x={n.x + n.w / 2} y={n.y + 34} textAnchor="middle" fill="#F3F4F6" fontSize="13.5" fontWeight="600">
            {n.title}
          </text>
          <text
            x={n.x + n.w / 2}
            y={n.y + 56}
            textAnchor="middle"
            fill="#A5ADB9"
            fontSize="11"
            fontFamily="JetBrains Mono, monospace"
          >
            {n.sub}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function ArchitectureExplorer() {
  const [arch, setArch] = useState<ArchMode>('after');
  const [pathFocus, setPathFocus] = useState<PathFocus>('fast');
  const [showDetails, setShowDetails] = useState(false);

  return (
    <section
      id="architecture"
      className="border-b border-[#292E37] bg-[#0B0D11] py-14 sm:py-20"
    >
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="text-3xl font-bold tracking-[-0.02em] text-[#F3F4F6] sm:text-4xl">
              Architecture and failure boundary
            </h2>
            <p className="mt-3 max-w-[62ch] text-[15px] leading-relaxed text-[#A5ADB9] sm:text-base">
              Decoupling database commit from SQS publication closes the dual-write gap while
              separating fast stream relay delivery from scheduled EventBridge recovery.
            </p>
          </div>

          <div
            role="tablist"
            aria-label="Architecture view selector"
            className="inline-flex w-fit rounded-lg border border-[#292E37] bg-[#11151B] p-1"
          >
            <button
              type="button"
              role="tab"
              aria-selected={arch === 'before'}
              onClick={() => setArch('before')}
              className={`rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors whitespace-nowrap focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                arch === 'before'
                  ? 'bg-[#E1817B]/15 text-[#E1817B]'
                  : 'text-[#A5ADB9] hover:text-[#F3F4F6]'
              }`}
            >
              Before - Dual Write
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={arch === 'after'}
              onClick={() => setArch('after')}
              className={`rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors whitespace-nowrap focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                arch === 'after'
                  ? 'bg-[#58C9A3]/15 text-[#58C9A3]'
                  : 'text-[#A5ADB9] hover:text-[#F3F4F6]'
              }`}
            >
              After - Transactional Outbox
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={arch === 'compare'}
              onClick={() => setArch('compare')}
              className={`hidden rounded-md px-3.5 py-1.5 text-[13px] font-medium transition-colors whitespace-nowrap lg:inline-block focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                arch === 'compare'
                  ? 'bg-[#E7A34D]/15 text-[#E7A34D]'
                  : 'text-[#A5ADB9] hover:text-[#F3F4F6]'
              }`}
            >
              Side by side
            </button>
          </div>
        </div>

        {(arch === 'after' || arch === 'compare') && (
          <div className="mt-5 flex flex-wrap items-center gap-2">
            <span className="text-[13px] text-[#7C8593]">Highlight delivery path:</span>
            <button
              type="button"
              onClick={() => setPathFocus('fast')}
              aria-pressed={pathFocus === 'fast'}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1 text-[13px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                pathFocus === 'fast'
                  ? 'border-[#58C9A3]/45 bg-[#58C9A3]/10 text-[#58C9A3]'
                  : 'border-[#292E37] bg-[#11151B] text-[#A5ADB9] hover:text-[#F3F4F6]'
              }`}
            >
              <Zap className="h-3.5 w-3.5" aria-hidden="true" />
              DynamoDB Streams fast path
            </button>
            <button
              type="button"
              onClick={() => setPathFocus('sweeper')}
              aria-pressed={pathFocus === 'sweeper'}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1 text-[13px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                pathFocus === 'sweeper'
                  ? 'border-[#E7A34D]/45 bg-[#E7A34D]/10 text-[#E7A34D]'
                  : 'border-[#292E37] bg-[#11151B] text-[#A5ADB9] hover:text-[#F3F4F6]'
              }`}
            >
              <GitCompareArrows className="h-3.5 w-3.5" aria-hidden="true" />
              EventBridge scheduled sweeper
            </button>
          </div>
        )}

        {/* Dominant SVG Diagrams */}
        {arch === 'compare' ? (
          <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <div className="surface-card p-5">
              <div className="mb-3 flex items-center justify-between text-xs font-medium">
                <span className="text-[#E1817B]">Before - Dual Write (U = 10)</span>
                <span className="text-[#7C8593]">Synchronous DB then SQS</span>
              </div>
              <BeforeSvg compact />
            </div>
            <div className="surface-card p-5">
              <div className="mb-3 flex items-center justify-between text-xs font-medium">
                <span className="text-[#58C9A3]">After - Transactional Outbox (U = 0)</span>
                <span className="text-[#7C8593]">Atomic 3-table TX + async recovery</span>
              </div>
              <AfterSvg pathFocus={pathFocus} compact />
            </div>
          </div>
        ) : (
          <div className="mt-6 surface-card overflow-x-auto p-5 sm:p-8">
            {arch === 'before' ? <BeforeSvg /> : <AfterSvg pathFocus={pathFocus} />}
          </div>
        )}

        {/* Concise Architectural Synthesis (Max 3 Primary Points) */}
        <div className="mt-8 grid grid-cols-1 gap-6 border-t border-[#292E37] pt-6 md:grid-cols-3">
          <div>
            <h3 className="text-sm font-semibold text-[#F3F4F6]">1. Atomic outbox commitment</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-[#A5ADB9]">
              Orders, idempotency locks, and outbox obligations commit in one DynamoDB transaction.
              Ingestion has zero SQS permissions.
            </p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-[#F3F4F6]">2. Lease-protected publication</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-[#A5ADB9]">
              OutboxRelay persists a pre-send reservation before calling SQS so interrupted sends stay
              safely marked uncertain until reconciled.
            </p>
          </div>
          <div>
            <h3 className="text-sm font-semibold text-[#F3F4F6]">3. Dual async recovery</h3>
            <p className="mt-1.5 text-sm leading-relaxed text-[#A5ADB9]">
              DynamoDB Streams handle sub-3-second retries; EventBridge sweeping catches stalled
              items and expired leases every minute.
            </p>
          </div>
        </div>

        {/* Accessible Technical Disclosure */}
        <div className="mt-6">
          <button
            type="button"
            aria-expanded={showDetails}
            onClick={() => setShowDetails((v) => !v)}
            className="inline-flex items-center gap-2 text-sm font-medium text-[#A5ADB9] transition-colors hover:text-[#F3F4F6] focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform duration-200 ${showDetails ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
            Implementation contract and IAM boundaries
          </button>

          {showDetails && (
            <div className="mt-4 overflow-x-auto rounded-xl border border-[#292E37] bg-[#11151B]">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-[#292E37] text-xs text-[#7C8593]">
                    <th className="px-5 py-3 font-medium">Component</th>
                    <th className="px-5 py-3 font-medium">Before (Dual Write)</th>
                    <th className="px-5 py-3 font-medium">After (Transactional Outbox)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#292E37] text-[#A5ADB9]">
                  {IMPLEMENTATION_DETAILS.map((row) => (
                    <tr key={row.component}>
                      <td className="px-5 py-3.5 align-top font-mono text-xs font-medium text-[#F3F4F6]">
                        {row.component}
                      </td>
                      <td className="px-5 py-3.5 align-top text-xs leading-relaxed">{row.before}</td>
                      <td className="px-5 py-3.5 align-top text-xs leading-relaxed">{row.after}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
