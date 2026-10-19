/** CollectFlow AI mark: a follow-up loop around a check. Same artwork as app/icon.svg. */
export function Logo({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#4f46e5" />
      <g fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
        <path d="M23.5 11.2A9 9 0 1 0 25 16" />
        <path d="M25.6 7.6v4.6H21" />
        <path d="M11.8 16.3l3 3 5.4-6" />
      </g>
    </svg>
  );
}
