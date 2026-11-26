import { Menu, X } from 'lucide-react';
import { useState } from 'react';

const NAV_LINKS = [
  { href: '#architecture', label: 'Architecture' },
  { href: '#benchmark', label: 'Results' },
  { href: '#simulator', label: 'Simulator' },
  { href: '#traces', label: 'Recovery' },
  { href: '#well-architected', label: 'AWS Review' },
  { href: '#evidence', label: 'Evidence' },
];

export function Navbar() {
  const [open, setOpen] = useState(false);

  return (
    <header className="sticky top-0 z-40 h-16 border-b border-[#292E37] bg-[#0B0D11]/92 backdrop-blur-md">
      <div className="mx-auto flex h-full max-w-[1280px] items-center justify-between px-4 sm:px-6 lg:px-8">
        <a
          href="#top"
          className="flex items-center gap-2.5 rounded-sm text-sm font-semibold tracking-tight text-[#F3F4F6] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#E7A34D]"
        >
          <span
            aria-hidden="true"
            className="grid h-6 w-6 grid-cols-2 gap-0.5 rounded-[4px] border border-[#292E37] bg-[#11151B] p-1"
          >
            <span className="rounded-[1px] bg-[#E7A34D]" />
            <span className="rounded-[1px] bg-[#58C9A3]" />
            <span className="rounded-[1px] bg-[#A5ADB9]/40" />
            <span className="rounded-[1px] bg-[#E7A34D]/35" />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">SilentLoss</span>
        </a>

        <nav aria-label="Primary navigation" className="hidden items-center gap-6 lg:flex">
          {NAV_LINKS.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="whitespace-nowrap rounded-sm px-1 py-1 text-[14px] font-medium text-[#A5ADB9] transition-colors duration-150 hover:text-[#F3F4F6] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#E7A34D]"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="hidden lg:flex">
          <a
            href="#simulator"
            className="inline-flex items-center rounded-lg border border-[#292E37] bg-[#11151B] px-3.5 py-1.5 text-[13px] font-medium text-[#F3F4F6] transition-colors duration-150 hover:border-[#373E4A] hover:bg-[#171B22] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#E7A34D]"
          >
            Reliability Lab
          </a>
        </div>

        <button
          type="button"
          aria-expanded={open}
          aria-controls="mobile-nav-menu"
          aria-label={open ? 'Close navigation menu' : 'Open navigation menu'}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex items-center justify-center rounded-lg border border-[#292E37] bg-[#11151B] p-2 text-[#A5ADB9] hover:text-[#F3F4F6] lg:hidden focus-visible:outline-2 focus-visible:outline-[#E7A34D]"
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </div>

      {open && (
        <div
          id="mobile-nav-menu"
          className="border-b border-[#292E37] bg-[#0B0D11] px-4 py-3 lg:hidden"
        >
          <nav aria-label="Mobile navigation" className="flex flex-col gap-1">
            {NAV_LINKS.map((item) => (
              <a
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-2 text-sm font-medium text-[#A5ADB9] hover:bg-[#11151B] hover:text-[#F3F4F6]"
              >
                {item.label}
              </a>
            ))}
          </nav>
        </div>
      )}
    </header>
  );
}
