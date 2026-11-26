import { Download } from 'lucide-react';
import { useState } from 'react';
import { claimsMatrix } from '../data/benchmarkData';
import { DATASET_DOWNLOADS, ENGINEERING_DISCLOSURES } from '../data/disclosuresData';

type ClaimFilter = 'ALL' | 'VERIFIED' | 'QUALIFIED' | 'HERMETIC' | 'NOT SUPPORTED';

function getRestrainedStatusStyle(rawStatus: string): { label: string; badgeClass: string } {
  const s = rawStatus.toUpperCase();
  if (s.includes('NOT SUPPORTED')) {
    return {
      label: 'Scope limitation',
      badgeClass: 'border-[#E1817B]/35 bg-[#E1817B]/10 text-[#E1817B]',
    };
  }
  if (s.includes('HERMETIC')) {
    return {
      label: 'Locally verified',
      badgeClass: 'border-[#373E4A] bg-[#171B22] text-[#A5ADB9]',
    };
  }
  if (s.includes('QUALIFIED') || s.includes('HISTORICAL')) {
    return {
      label: rawStatus.includes('HISTORICAL') ? 'Historical verified' : 'Qualified',
      badgeClass: 'border-[#E7A34D]/35 bg-[#E7A34D]/10 text-[#E7A34D]',
    };
  }
  return {
    label: 'Live verified',
    badgeClass: 'border-[#58C9A3]/35 bg-[#58C9A3]/10 text-[#58C9A3]',
  };
}

export function EvidenceDownloadsSection() {
  const [claimFilter, setClaimFilter] = useState<ClaimFilter>('ALL');

  const filteredClaims = claimsMatrix.filter((row) => {
    if (claimFilter === 'ALL') return true;
    const st = row.status.toUpperCase();
    if (claimFilter === 'VERIFIED') {
      return st.startsWith('VERIFIED') && !st.includes('HERMETIC') && !st.includes('QUALIFIED');
    }
    if (claimFilter === 'QUALIFIED') return st.includes('QUALIFIED') || st.includes('HISTORICAL');
    if (claimFilter === 'HERMETIC') return st.includes('HERMETIC');
    if (claimFilter === 'NOT SUPPORTED') return st.includes('NOT SUPPORTED');
    return true;
  });

  return (
    <section
      id="evidence"
      className="bg-[#0B0D11] py-14 sm:py-20"
    >
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <div>
          <h2 className="text-3xl font-bold tracking-[-0.02em] text-[#F3F4F6] sm:text-4xl">
            Evidence, trade-offs, and publication claims
          </h2>
          <p className="mt-3 max-w-[64ch] text-[15px] leading-relaxed text-[#A5ADB9] sm:text-base">
            Material methodology bounds, downloadable static benchmark artifacts, and the complete
            claim-by-claim verification ledger.
          </p>
        </div>

        {/* Subsection A: Engineering Transparency */}
        <div className="mt-10">
          <h3 className="text-xl font-semibold tracking-tight text-[#F3F4F6]">
            A. Engineering transparency
          </h3>
          <p className="mt-1.5 text-sm text-[#7C8593]">
            Explicit experimental boundaries and caveats preserved alongside live AWS results.
          </p>

          <div className="mt-4 divide-y divide-[#292E37] rounded-xl border border-[#292E37] bg-[#11151B]">
            {ENGINEERING_DISCLOSURES.map((d) => (
              <div
                key={d.id}
                className="grid grid-cols-1 gap-2 px-5 py-4 sm:px-6 lg:grid-cols-12 lg:gap-6"
              >
                <div className="lg:col-span-4">
                  <div className="text-sm font-semibold text-[#F3F4F6]">{d.title}</div>
                  <div className="mt-0.5 text-xs text-[#7C8593]">{d.category}</div>
                </div>
                <p className="text-sm leading-relaxed text-[#A5ADB9] lg:col-span-8">{d.detail}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Subsection B: Evidence Downloads */}
        <div className="mt-12">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h3 className="text-xl font-semibold tracking-tight text-[#F3F4F6]">
                B. Evidence downloads
              </h3>
              <p className="mt-1.5 text-sm text-[#7C8593]">
                Sanitized static JSON and CSV datasets exported from Phase 9 verification.
              </p>
            </div>
            <p className="text-xs text-[#7C8593]">
              Read-only static host. Zero AWS account IDs or credentials shipped.
            </p>
          </div>

          <div className="mt-4 overflow-hidden rounded-xl border border-[#292E37] bg-[#11151B]">
            <div className="divide-y divide-[#292E37]">
              {DATASET_DOWNLOADS.map((item) => {
                const fileExt = item.file.toLowerCase().endsWith('.csv') ? 'CSV' : 'JSON';
                const cleanTitle = item.label.replace(/\s*\([^)]*\)$/, '');
                return (
                  <div
                    key={item.file}
                    className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2.5">
                        <span className="text-sm font-semibold text-[#F3F4F6]">{cleanTitle}</span>
                        <span className="rounded border border-[#292E37] bg-[#171B22] px-2 py-0.5 font-mono text-[11px] text-[#A5ADB9]">
                          {fileExt}
                        </span>
                      </div>
                      <p className="mt-1 text-xs leading-relaxed text-[#A5ADB9]">{item.description}</p>
                    </div>
                    <a
                      href={item.file}
                      download
                      className="inline-flex w-fit shrink-0 items-center gap-1.5 rounded-lg border border-[#292E37] bg-[#171B22] px-3.5 py-2 text-xs font-medium text-[#F3F4F6] transition-colors hover:border-[#E7A34D]/60 hover:text-[#E7A34D] focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
                    >
                      <Download className="h-3.5 w-3.5" aria-hidden="true" />
                      Download
                    </a>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Subsection C: Publication Claims Matrix (ZERO internal scrollbar, full-width natural flow) */}
        <div className="mt-12">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h3 className="text-xl font-semibold tracking-tight text-[#F3F4F6]">
                C. Publication claims verification matrix
              </h3>
              <p className="mt-1.5 text-sm text-[#7C8593]">
                Showing {filteredClaims.length} of {claimsMatrix.length} verified claims and
                qualifications in natural document flow.
              </p>
            </div>

            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Claims matrix filter">
              {[
                { id: 'ALL' as const, label: 'All' },
                { id: 'VERIFIED' as const, label: 'Live verified' },
                { id: 'QUALIFIED' as const, label: 'Qualified' },
                { id: 'HERMETIC' as const, label: 'Locally verified' },
                { id: 'NOT SUPPORTED' as const, label: 'Scope limitation' },
              ].map((cf) => (
                <button
                  key={cf.id}
                  type="button"
                  aria-pressed={claimFilter === cf.id}
                  onClick={() => setClaimFilter(cf.id)}
                  className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[#E7A34D] ${claimFilter === cf.id
                    ? 'border-[#373E4A] bg-[#171B22] text-[#F3F4F6]'
                    : 'border-[#292E37] bg-[#11151B] text-[#7C8593] hover:text-[#F3F4F6]'
                    }`}
                >
                  {cf.label}
                </button>
              ))}
            </div>
          </div>

          {/* Desktop / Tablet Full-Width Table (NO max-height, NO overflow-y-auto) */}
          <div className="mt-5 hidden rounded-xl border border-[#292E37] bg-[#11151B] md:block">
            <table className="w-full table-fixed text-left text-sm">
              <thead>
                <tr className="border-b border-[#292E37] text-xs font-medium text-[#7C8593]">
                  <th className="w-[27%] px-5 py-3.5">Public claim</th>
                  <th className="w-[17%] px-4 py-3.5">Verification status</th>
                  <th className="w-[24%] px-4 py-3.5">Evidence reference</th>
                  <th className="w-[32%] px-5 py-3.5">Qualification or limitation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#292E37]">
                {filteredClaims.map((row, idx) => {
                  const statusMeta = getRestrainedStatusStyle(row.status);
                  return (
                    <tr key={`${row.claim}-${idx}`} className="align-top hover:bg-[#171B22]/40">
                      <td className="px-5 py-4 font-medium leading-relaxed text-[#F3F4F6]">
                        {row.claim}
                      </td>
                      <td className="px-4 py-4">
                        <span
                          className={`inline-block rounded-md border px-2.5 py-1 text-xs font-medium ${statusMeta.badgeClass}`}
                        >
                          {statusMeta.label}
                        </span>
                        <div className="mt-1.5 text-[11px] leading-snug text-[#7C8593]">
                          {row.status}
                        </div>
                      </td>
                      <td className="px-4 py-4 text-xs leading-relaxed text-[#A5ADB9] break-words">
                        {row.evidence.replace(/`/g, '')}
                      </td>
                      <td className="px-5 py-4 text-xs leading-relaxed text-[#A5ADB9] break-words">
                        {row.qualification.replace(/`/g, '')}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* Mobile Responsive Stacked Rows (Zero Horizontal Overflow) */}
          <div className="mt-5 space-y-3 md:hidden">
            {filteredClaims.map((row, idx) => {
              const statusMeta = getRestrainedStatusStyle(row.status);
              return (
                <div
                  key={`${row.claim}-${idx}`}
                  className="rounded-xl border border-[#292E37] bg-[#11151B] p-4 space-y-2.5"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span
                      className={`inline-block rounded-md border px-2.5 py-0.5 text-xs font-medium ${statusMeta.badgeClass}`}
                    >
                      {statusMeta.label}
                    </span>
                    <span className="text-[11px] text-[#7C8593]">{row.status}</span>
                  </div>
                  <div className="text-sm font-semibold leading-snug text-[#F3F4F6]">{row.claim}</div>
                  <div className="text-xs leading-relaxed text-[#A5ADB9]">
                    <span className="font-medium text-[#7C8593]">Evidence: </span>
                    {row.evidence.replace(/`/g, '')}
                  </div>
                  <div className="text-xs leading-relaxed text-[#A5ADB9]">
                    <span className="font-medium text-[#7C8593]">Qualification: </span>
                    {row.qualification.replace(/`/g, '')}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
