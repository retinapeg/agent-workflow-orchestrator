import React, {useId} from 'react';
import {shortSha, type Family} from './types';

const PATHS: Record<string, React.ReactNode> = {
  overview: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </>
  ),
  projects: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  build: (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="6" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M7.5 8.2 10.8 15.5M16.5 8.2 13.2 15.5" />
    </>
  ),
  agents: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="3" />
      <rect x="9" y="9" width="6" height="6" rx="1" />
      <path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3" />
    </>
  ),
  review: (
    <>
      <path d="M12 3l8 3.5v5.5c0 4.5-3.5 7.8-8 9-4.5-1.2-8-4.5-8-9V6.5z" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  pause: <path d="M8 5v14M16 5v14" />,
  play: <path d="M7 4.5v15l12-7.5z" />,
  arrow: <path d="m9 5 7 7-7 7" />,
  mic: (
    <>
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6" />
    </>
  ),
  stop: <rect x="6" y="6" width="12" height="12" rx="2" />,
  check: <path d="m5 12 4.5 4.5L19 7" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  bolt: <path d="M13 2 4 14h7l-1 8 9-12h-7z" />,
  code: <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 14" />,
  doc: (
    <>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4M9 12h6M9 16h6" />
    </>
  ),
  git: (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="6" cy="18" r="2.5" />
      <circle cx="18" cy="9" r="2.5" />
      <path d="M6 8.5v7M18 11.5a6 6 0 0 1-6 4.5H6" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21a8 8 0 0 1 16 0" />
    </>
  ),
};

export function Icon({name, size = 20}: {name: string; size?: number}) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false">
      {PATHS[name] || PATHS.build}
    </svg>
  );
}

export function Mark({size = 36}: {size?: number}) {
  // Gradient ids must be unique per instance: a hidden copy would otherwise own the id.
  const id = useId().replaceAll(':', '');
  const face = `mk-face-${id}`;
  const glow = `mk-glow-${id}`;
  return (
    <svg className="mark" viewBox="0 0 48 48" width={size} height={size} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={face} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#24406f" />
          <stop offset="1" stopColor="#0a1327" />
        </linearGradient>
        <linearGradient id={glow} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#a6dcff" />
          <stop offset="1" stopColor="#2a7bea" />
        </linearGradient>
      </defs>
      <path d="M24 3 42 13.5v21L24 45 6 34.5v-21z" fill={`url(#${face})`} stroke="#5486cc" strokeWidth="1.2" />
      <path d="M24 7 38.5 15.5v17L24 41 9.5 32.5v-17z" fill="none" stroke="rgba(255,255,255,.09)" />
      <path d="m15 31 9-15 9 15" fill="none" stroke={`url(#${glow})`} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M19.5 27h9" stroke={`url(#${glow})`} strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export type Tone = Family | 'neutral' | 'pass' | 'fail';

export function Chip({tone, children, title}: {tone: Tone; children: React.ReactNode; title?: string}) {
  return (
    <span className={'chip ' + tone} title={title}>
      <i />
      {children}
    </span>
  );
}

export const Eyebrow = ({children, className = ''}: {children: React.ReactNode; className?: string}) => (
  <span className={'eyebrow ' + className}>{children}</span>
);

export function Panel({
  title,
  aside,
  children,
  className = '',
}: {
  title: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={'panel ' + className}>
      <header className="panel-head">
        <h2>{title}</h2>
        {aside}
      </header>
      {children}
    </section>
  );
}

export function Facts({rows}: {rows: ([string, React.ReactNode] | null | false | undefined)[]}) {
  return (
    <dl className="facts">
      {rows.filter(Boolean).map(r => {
        const [k, v] = r as [string, React.ReactNode];
        return (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        );
      })}
    </dl>
  );
}

export function Empty({title, children}: {title: string; children?: React.ReactNode}) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export const Sha = ({value, n = 10}: {value?: string | null; n?: number}) =>
  value ? (
    <code className="sha" title={value}>
      {shortSha(value, n)}
    </code>
  ) : null;

export const LiveDot = ({tone = 'live'}: {tone?: 'live' | 'warn' | 'off' | 'ok'}) => <i className={'live-dot ' + tone} aria-hidden="true" />;
