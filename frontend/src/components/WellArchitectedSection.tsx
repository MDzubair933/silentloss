import { ChevronDown } from 'lucide-react';
import { useState } from 'react';
import { WA_PILLAR_SCORECARD, WA_REVIEW_OVERVIEW } from '../data/wellArchitectedData';

type RiskFilter = 'ALL' | 'F1' | 'HIGH';

export function WellArchitectedSection() {
  const [showScorecard, setShowScorecard] = useState(false);
  const [filter, setFilter] = useState<RiskFilter>('ALL');

  const visibleQuestions = WA_PILLAR_SCORECARD.filter((q) => {
    if (filter === 'F1') return q.f1Remediated;
    if (filter === 'HIGH') return q.risk === 'High';
    return true;
  });

  return (
    <section
      id="well-architected"
      className="border-b border-[#292E37] bg-[#0B0D11] py-14 sm:py-20"
    >
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <div>
          <h2 className="text-3xl font-bold tracking-[-0.02em] text-[#F3F4F6] sm:text-4xl">
            Official AWS Agent Toolkit review
          </h2>
          <p className="mt-3 max-w-[64ch] text-[15px] leading-relaxed text-[#A5ADB9] sm:text-base">
            Pre-remediation architectural review conducted with the AWS Agent Toolkit
            Well-Architected Reliability Workflow across the Reliability Pillar.
          </p>
        </div>

        {/* 3 Primary Review Metrics */}
        <div className="mt-8 grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-[#292E37] bg-[#292E37] sm:grid-cols-3 tabular-nums">
          <div className="bg-[#11151B] px-6 py-5">
            <div className="text-3xl font-semibold tracking-tight text-[#F3F4F6] sm:text-4xl">
              63/63
            </div>
            <div className="mt-1.5 text-[13px] text-[#A5ADB9]">
              Reliability best practices assessed
            </div>
          </div>
          <div className="bg-[#11151B] px-6 py-5">
            <div className="text-3xl font-semibold tracking-tight text-[#F3F4F6] sm:text-4xl">
              13/13
            </div>
            <div className="mt-1.5 text-[13px] text-[#A5ADB9]">
              Reliability pillar questions covered
            </div>
          </div>
          <div className="bg-[#11151B] px-6 py-5">
            <div className="text-3xl font-semibold tracking-tight text-[#E7A34D] sm:text-4xl">
              {WA_REVIEW_OVERVIEW.maturityBeforeScore}/{WA_REVIEW_OVERVIEW.maturityMax}
            </div>
            <div className="mt-1.5 text-[13px] text-[#A5ADB9]">
              Baseline Reliability maturity score
            </div>
          </div>
        </div>

        <p className="mt-3 text-xs text-[#7C8593]">
          Scope disclosure: Scoped solely to the Reliability Pillar (63 of 261 total Well-Architected Framework best practices; 17 evidence shards). Not a six-pillar assessment or official AWS certification, and no post-remediation score is fabricated.
        </p>

        {/* Primary Finding Case Study: F1 */}
        <div className="mt-8 surface-card p-6 sm:p-8">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#292E37] pb-5">
            <div>
              <div className="flex flex-wrap items-center gap-2.5 text-xs">
                <span className="font-mono font-semibold text-[#E1817B]">
                  {WA_REVIEW_OVERVIEW.selectedFinding.id} ({WA_REVIEW_OVERVIEW.selectedFinding.severity} risk)
                </span>
                <span className="text-[#7C8593]">|</span>
                <span className="font-medium text-[#58C9A3]">Selected &amp; remediated</span>
              </div>
              <h3 className="mt-2 text-xl font-semibold text-[#F3F4F6] sm:text-2xl">
                {WA_REVIEW_OVERVIEW.selectedFinding.title}
              </h3>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {WA_REVIEW_OVERVIEW.selectedFinding.bestPractices.map((bp) => (
                <span
                  key={bp}
                  className="rounded-md border border-[#292E37] bg-[#171B22] px-2.5 py-1 font-mono text-xs text-[#A5ADB9]"
                >
                  {bp}
                </span>
              ))}
            </div>
          </div>

          <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-2">
            <div>
              <h4 className="text-sm font-semibold text-[#F3F4F6]">Why this finding mattered</h4>
              <p className="mt-2 text-sm leading-relaxed text-[#A5ADB9]">
                {WA_REVIEW_OVERVIEW.selectedFinding.gapSummary}
              </p>
            </div>
            <div>
              <h4 className="text-sm font-semibold text-[#58C9A3]">
                Architectural recommendation implemented
              </h4>
              <p className="mt-2 text-sm leading-relaxed text-[#A5ADB9]">
                {WA_REVIEW_OVERVIEW.selectedFinding.implementedRecommendation}
              </p>
            </div>
          </div>

          <div className="mt-6 border-t border-[#292E37] pt-4 text-xs text-[#7C8593]">
            <strong className="font-medium text-[#A5ADB9]">Deferred high finding ({WA_REVIEW_OVERVIEW.secondaryHighFinding.id}):</strong>{' '}
            {WA_REVIEW_OVERVIEW.secondaryHighFinding.title}. {WA_REVIEW_OVERVIEW.secondaryHighFinding.statusNote}
          </div>
        </div>

        {/* Secondary Expandable Full-Width 13-Question Scorecard */}
        <div className="mt-6">
          <button
            type="button"
            aria-expanded={showScorecard}
            onClick={() => setShowScorecard((v) => !v)}
            className="inline-flex items-center gap-2 text-sm font-medium text-[#A5ADB9] hover:text-[#F3F4F6] focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
          >
            <ChevronDown
              className={`h-4 w-4 transition-transform duration-200 ${showScorecard ? 'rotate-180' : ''}`}
              aria-hidden="true"
            />
            View complete 13-question Reliability scorecard (REL01-REL13)
          </button>

          {showScorecard && (
            <div className="mt-4 surface-card overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#292E37] px-6 py-4">
                <h3 className="text-sm font-semibold text-[#F3F4F6]">
                  Reliability pillar scorecard ({visibleQuestions.length} questions)
                </h3>
                <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Scorecard filter">
                  {[
                    { id: 'ALL' as const, label: 'All 13' },
                    { id: 'F1' as const, label: 'F1 Addressed (3)' },
                    { id: 'HIGH' as const, label: 'High Risk (5)' },
                  ].map((b) => (
                    <button
                      key={b.id}
                      type="button"
                      aria-pressed={filter === b.id}
                      onClick={() => setFilter(b.id)}
                      className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${
                        filter === b.id
                          ? 'bg-[#171B22] text-[#F3F4F6]'
                          : 'text-[#7C8593] hover:text-[#F3F4F6]'
                      }`}
                    >
                      {b.label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="overflow-x-auto">
                <table className="w-full min-w-[720px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-[#292E37] bg-[#11151B] text-xs font-medium text-[#7C8593]">
                      <th className="px-6 py-3">Question</th>
                      <th className="px-4 py-3 text-right">BPs</th>
                      <th className="px-4 py-3 text-right">Baseline score</th>
                      <th className="px-4 py-3">Risk</th>
                      <th className="px-6 py-3">Assessment evidence</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#292E37]">
                    {visibleQuestions.map((q) => (
                      <tr key={q.id} className="hover:bg-[#171B22]/50">
                        <td className="px-6 py-3.5 align-top">
                          <div className="font-mono text-xs font-medium text-[#F3F4F6]">
                            {q.id}
                          </div>
                          <div className="text-xs text-[#A5ADB9]">{q.category}</div>
                          {q.f1Remediated && (
                            <span className="mt-1 inline-block text-[11px] font-medium text-[#58C9A3]">
                              F1 remediated
                            </span>
                          )}
                        </td>
                        <td className="px-4 py-3.5 text-right align-top text-xs text-[#A5ADB9] tabular-nums">
                          {q.bpsAssessed}
                        </td>
                        <td className="px-4 py-3.5 text-right align-top text-xs text-[#F3F4F6] tabular-nums">
                          {q.scoreOutOf5}/5
                        </td>
                        <td className="px-4 py-3.5 align-top text-xs">
                          <span className={q.risk === 'High' ? 'text-[#E1817B]' : 'text-[#E7A34D]'}>
                            {q.risk}
                          </span>
                        </td>
                        <td className="px-6 py-3.5 align-top text-xs leading-relaxed text-[#A5ADB9]">
                          {q.keyEvidence}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
