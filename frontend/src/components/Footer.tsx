import { ArrowUpRight } from 'lucide-react';

export function Footer() {
  return (
    <footer className="border-t border-[#292E37] bg-[#0B0D11] py-12">
      <div className="mx-auto max-w-[1280px] px-4 sm:px-6 lg:px-8">
        <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2.5">
              <span
                aria-hidden="true"
                className="grid h-5 w-5 grid-cols-2 gap-0.5 rounded-[4px] border border-[#292E37] bg-[#11151B] p-0.5"
              >
                <span className="rounded-[1px] bg-[#E7A34D]" />
                <span className="rounded-[1px] bg-[#58C9A3]" />
                <span className="rounded-[1px] bg-[#A5ADB9]/40" />
                <span className="rounded-[1px] bg-[#E7A34D]/35" />
              </span>
              <span className="text-sm font-semibold tracking-tight text-[#F3F4F6]">
                SilentLoss
              </span>
            </div>
            <p className="mt-2 text-sm text-[#7C8593]">
              AWS reliability experiment and transactional outbox case study. Static publication-safe edition.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-6 text-sm text-[#A5ADB9]">
            <span className="text-[#7C8593]">GitHub repository (pending publication)</span>
            <span className="text-[#7C8593]">AWS Builder Center article (drafting)</span>
            <a
              href="#top"
              className="inline-flex items-center gap-1 font-medium text-[#F3F4F6] transition-colors hover:text-[#E7A34D] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E7A34D]"
            >
              Back to top
              <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
