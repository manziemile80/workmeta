export function BrandMark({ className = "h-11 w-11" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 64 64"
      className={className}
      role="img"
      aria-label="WorkMeta AI logo"
    >
      <defs>
        <linearGradient
          id="wm-mark-bg"
          x1="0"
          y1="0"
          x2="64"
          y2="64"
          gradientUnits="userSpaceOnUse"
        >
          <stop offset="0" stopColor="#0b1220" />
          <stop offset="1" stopColor="#111c30" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="16" fill="url(#wm-mark-bg)" />
      <path
        d="M15 21L23.5 43L32 29L40.5 43L49 21"
        fill="none"
        stroke="#2dd4bf"
        strokeWidth="4.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="32" cy="29" r="3.2" fill="#fbbf24" />
    </svg>
  );
}
