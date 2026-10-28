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

// Restrained waveform: five bars that breathe only while `live`; static otherwise and
// whenever the platform prefers reduced motion (CSS).
export function Waveform({live = false, bars = 21}: {live?: boolean; bars?: number}) {
  const heights = [4, 7, 12, 8, 16, 10, 22, 14, 28, 18, 34, 18, 28, 14, 22, 10, 16, 8, 12, 7, 4];
  return (
    <span className={'waveform' + (live ? ' live' : '')} aria-hidden="true">
      {Array.from({length: bars}, (_, i) => (
        <i key={i} style={{height: heights[i % heights.length], animationDelay: `${(i % 7) * 0.09}s`}} />
      ))}
    </span>
  );
}
