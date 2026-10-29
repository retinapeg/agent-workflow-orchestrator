import React, {useId} from 'react';
import {EYE_GRADIENT, GRADIENTS, LION, MANE_COUNT} from './lion';
import {shortSha, type Family} from './types';

const PATHS: Record<string, React.ReactNode> = {
  overview: <path d="M4 11.5 12 4.5l8 7V20h-5.5v-5.5h-5V20H4z" />,
  projects: <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />,
  build: (
    <>
      <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9z" />
      <path d="M4 7.5 12 12l8-4.5M12 12v9" />
    </>
  ),
  agents: (
    <>
      <rect x="4" y="6" width="16" height="13" rx="3" />
      <path d="M9 12h.01M15 12h.01M12 3v3M9 16h6" />
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
  back: <path d="m15 5-7 7 7 7" />,
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
  search: (
    <>
      <circle cx="11" cy="11" r="6.5" />
      <path d="m16 16 4.5 4.5" />
    </>
  ),
  bell: <path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 21h4" />,
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1" />
    </>
  ),
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
  chat: <path d="M4 5h16v11H9l-5 4z" />,
  send: <path d="M4 12 20 4l-4 16-4-7z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  warn: <path d="M12 3 2.5 20h19zM12 10v4M12 17.5h.01" />,
  wave: <path d="M3 12h2M7 8v8M11 5v14M15 8v8M19 12h2" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  archive: (
    <>
      <rect x="3.5" y="4.5" width="17" height="4" rx="1" />
      <path d="M5.5 8.5V19a1.5 1.5 0 0 0 1.5 1.5h10a1.5 1.5 0 0 0 1.5-1.5V8.5M10 12.5h4" />
    </>
  ),
  mac: (
    <>
      <rect x="5" y="5" width="14" height="10" rx="1.5" />
      <path d="M2.5 18.5h19" />
    </>
  ),
  refresh: (
    <>
      <path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" />
      <path d="M19.5 4.5V9H15" />
    </>
  ),
  server: (
    <>
      <rect x="4" y="4" width="16" height="7" rx="2" />
      <rect x="4" y="13" width="16" height="7" rx="2" />
      <path d="M8 7.5h.01M8 16.5h.01" />
    </>
  ),
  flask: <path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3" />,
  undo: <path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3" />,
};

export function Icon({name, size = 20}: {name: string; size?: number}) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false">
      {PATHS[name] || PATHS.build}
    </svg>
  );
}

// Faceted chrome lion emblem (original vector art, see lion.ts). Gradient ids are unique
// per instance because a hidden copy would otherwise own the id.
export function Mark({size = 36, halo = true}: {size?: number; halo?: boolean}) {
  const id = useId().replaceAll(':', '');
  const g = (k: string) => `${k}-${id}`;
  return (
    <svg className="mark" viewBox="0 0 100 100" width={size} height={size} aria-hidden="true" focusable="false">
      <defs>
        {Object.entries(GRADIENTS).map(([k, s]) => (
          <linearGradient key={k} id={g(k)} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor={s[0]} />
            <stop offset=".42" stopColor={s[1]} />
            <stop offset=".5" stopColor={s[2]} />
            <stop offset=".62" stopColor={s[3]} />
            <stop offset="1" stopColor={s[4]} />
          </linearGradient>
        ))}
        <linearGradient id={g('eye')} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor={EYE_GRADIENT[0]} />
          <stop offset=".5" stopColor={EYE_GRADIENT[1]} />
          <stop offset="1" stopColor={EYE_GRADIENT[2]} />
        </linearGradient>
        <linearGradient id={g('eyeHot')} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffffff" />
          <stop offset="1" stopColor="#bfe6ff" />
        </linearGradient>
        <radialGradient id={g('halo')} cx=".5" cy=".5" r=".5">
          <stop offset="0" stopColor="#2b7bff" stopOpacity=".7" />
          <stop offset=".55" stopColor="#2b7bff" stopOpacity=".22" />
          <stop offset="1" stopColor="#2b7bff" stopOpacity="0" />
        </radialGradient>
        <filter id={g('glow')} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.2" />
        </filter>
        <filter id={g('mane')} x="-30%" y="-30%" width="160%" height="160%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>
      {halo && <circle cx="50" cy="54" r="52" fill={`url(#${g('halo')})`} />}
      <g fill="#2b7bff" opacity=".85" filter={`url(#${g('mane')})`}>
        {LION.slice(0, MANE_COUNT).map((f, i) => (
          <polygon key={'m' + i} points={f.points} />
        ))}
      </g>
      <g stroke="#05080f" strokeWidth={size < 56 ? 0.25 : 0.45} strokeLinejoin="round">
        {LION.map((f, i) => (
          <polygon key={i} points={f.points} fill={`url(#${g(f.fill)})`} />
        ))}
      </g>
      {LION.filter(f => f.fill === 'eye').map((f, i) => (
        <polygon key={'e' + i} points={f.points} fill="#4fa3ff" filter={`url(#${g('glow')})`} />
      ))}
    </svg>
  );
}

export const Wordmark = ({className = ''}: {className?: string}) => <span className={'wordmark ' + className}>ARENA</span>;

export type Tone = Family | 'neutral' | 'pass' | 'fail';

export function Chip({tone, children, title}: {tone: Tone; children: React.ReactNode; title?: string}) {
  return (
    <span className={'chip ' + tone} title={title}>
      <i />
      {children}
    </span>
  );
}

// Status glyph: ✓ done (green), ◐ running (blue), ⚠ needs you (amber), ○ idle/off.
export function Glyph({tone, size = 28}: {tone: Tone; size?: number}) {
  const name = tone === 'done' || tone === 'pass' ? 'check' : tone === 'running' ? 'wave' : tone === 'decision' || tone === 'blocked' || tone === 'fail' ? 'warn' : tone === 'waiting' ? 'clock' : 'more';
  return (
    <span className={'status-glyph ' + tone} style={{width: size, height: size}} aria-hidden="true">
      <Icon name={name} size={Math.round(size * 0.55)} />
    </span>
  );
}

// Provider glyph in a small chrome square.
export const Provider = ({glyph, on = false, size = 'md'}: {glyph: string; on?: boolean; size?: 'sm' | 'md' | 'lg'}) => (
  <span className={`glyph ${size}${on ? ' on' : ''}`} aria-hidden="true">
    {glyph}
  </span>
);

export const Eyebrow = ({children, className = ''}: {children: React.ReactNode; className?: string}) => (
  <span className={'eyebrow ' + className}>{children}</span>
);

export function Panel({
  title,
  aside,
  children,
  className = '',
  eyebrow,
}: {
  title: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  eyebrow?: React.ReactNode;
}) {
  return (
    <section className={'panel ' + className}>
      <header className="panel-head">
        <div className="panel-title">
          {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
          <h2>{title}</h2>
        </div>
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

// Slim stage bar: fills by recorded pipeline stage only (0..total), never by time.
export function StageBar({stage, total, tone = 'running', label}: {stage: number; total: number; tone?: Tone; label: string}) {
  const n = Math.max(0, Math.min(total, stage));
  return (
    <span className={'stage-bar ' + tone} role="img" aria-label={label}>
      {Array.from({length: total}, (_, i) => (
        <i key={i} className={i < n ? 'on' : ''} />
      ))}
    </span>
  );
}

// An agent's avatar: a letter glyph, never a brand logo. Claude wears the warm accent,
// Codex the blue one, anything else the neutral chrome. Decorative: the name is beside it.
export function Avatar({provider, size = 'md'}: {provider?: string | null; size?: 'sm' | 'md' | 'lg'}) {
  const family = provider === 'claude' ? 'claude' : provider === 'codex' ? 'codex' : 'other';
  return (
    <span className={`avatar-glyph ${family} ${size}`} aria-hidden="true">
      {family === 'claude' ? 'Cl' : family === 'codex' ? 'Cx' : '··'}
    </span>
  );
}

// Work done out of the work planned, from recorded task states only (never from time): the
// host's stage-weighted fraction when it gives one, else blocks done of blocks planned.
export function Progress({done, total, fraction, tone = 'running', label}: {done: number; total: number; fraction?: number | null; tone?: Tone; label: string}) {
  const share = typeof fraction === 'number' && isFinite(fraction) ? fraction : total > 0 ? done / total : 0;
  const pct = Math.min(100, Math.max(0, Math.round(share * 100)));
  return (
    <span className={'progress ' + tone} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-valuetext={label}>
      <i style={{width: pct + '%'}} />
    </span>
  );
}

// The accent wave along the foot of a running block: a static gradient shape that drifts
// slowly, and stays still when the platform prefers reduced motion (CSS).
export function Wave() {
  return (
    <svg className="wave" viewBox="0 0 480 48" preserveAspectRatio="none" aria-hidden="true" focusable="false">
      <path className="w1" d="M0 30 C 40 12, 80 40, 120 26 S 200 8, 240 26 S 320 44, 360 22 S 440 12, 480 28 V48 H0Z" />
      <path className="w2" d="M0 36 C 50 22, 90 46, 140 32 S 220 18, 270 34 S 350 46, 400 30 S 450 26, 480 34 V48 H0Z" />
    </svg>
  );
}
