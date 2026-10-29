import React from 'react';
import {useArena} from '../App';
import {usePolled} from '../polled';
import {ago, plainError, type Activity, type MacActivity} from '../types';
import {Avatar, Icon} from '../ui';

// What is running on the Mac, from a fresh read-only look at its processes (GET
// mac-activity): what Arena manages, what was discovered independently, and what could not
// be verified, each in a plain sentence. Nothing here can stop or touch a process.

const GROUPS: {key: 'managed' | 'external' | 'unknown'; title: string; empty: string}[] = [
  {key: 'managed', title: 'Managed by Arena', empty: 'Arena is not running anything on the Mac right now.'},
  {key: 'external', title: 'Discovered on your Mac', empty: 'No other coding agents, development servers or test runs were found.'},
  {key: 'unknown', title: "Couldn't verify", empty: 'Nothing that needed verifying.'},
];

const projectOf = (a: Activity): string => (typeof a.project === 'string' ? a.project : a.project?.name || '');
// The repository's folder name only; the full path is evidence, kept in the details.
const folderOf = (repo: string | null | undefined): string => (repo || '').replace(/\/+$/, '').split('/').pop() || '';

// To the nearest minute: the phone's clock is the host's, read a moment earlier.
export function activeAgo(epoch: number | null | undefined, now: number): string {
  if (!epoch) return 'no recent activity recorded';
  const s = Math.max(0, now - epoch);
  if (s < 45) return 'active just now';
  const m = Math.max(1, Math.round(s / 60));
  if (m < 60) return `active ${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `active ${h} h ago` : `active ${Math.round(h / 24)} days ago`;
}

function KindIcon({kind}: {kind: string}) {
  if (kind === 'claude' || kind === 'codex') return <Avatar provider={kind} size="sm" />;
  const name = kind === 'dev_server' ? 'server' : kind === 'tests' ? 'flask' : kind === 'agent' ? 'agents' : 'build';
  return (
    <span className={'kind-icon ' + kind} aria-hidden="true">
      <Icon name={name} size={16} />
    </span>
  );
}

function ActivityRow({a, compact}: {a: Activity; compact?: boolean}) {
  const {now, openGoal} = useArena();
  const where = projectOf(a) || folderOf(a.repo);
  const bits = [where ? `in ${where}` : 'working folder unknown', activeAgo(a.last_activity, now), a.started ? `started ${ago(a.started, now)}` : '', a.ports?.length ? `port ${a.ports.join(', ')}` : '', a.headless ? 'in the background' : ''].filter(Boolean);
  const goal = a.arena?.goal_id;
  return (
    <li className="activity-row">
      <KindIcon kind={a.kind} />
      <div className="activity-main">
        <b>{a.label}</b>
        <span className={'small muted' + (compact ? ' clamp2' : '')}>{bits.join(' · ')}</span>
        {!compact && (a.evidence?.length || a.repo) ? (
          <details className="evidence-details">
            <summary>
              Advanced <span className="muted">· how Arena knows</span>
            </summary>
            <ul className="small muted">
              {a.repo && <li>Folder {a.repo}</li>}
              {(a.evidence || []).map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </details>
        ) : null}
      </div>
      {goal && !compact && (
        <button type="button" className="text-btn" aria-label={`Open the build: ${a.label}`} onClick={() => openGoal(goal)}>
          Open
        </button>
      )}
    </li>
  );
}

// The discovery, full on its own screen or compact inside a chat reply.
export function MacActivityView({data, compact = false}: {data: MacActivity; compact?: boolean}) {
  const {now, setTab} = useArena();
  const groups = data.groups || {};
  const total = GROUPS.reduce((n, g) => n + (groups[g.key]?.length || 0), 0);
  // How complete this look is, before what it found.
  const caveat = data.stale ? 'This is an earlier look: Arena is still checking the Mac.' : data.partial ? 'Part of the Mac did not answer in time, so this look may be incomplete.' : '';
  return (
    <div className={'mac-activity' + (compact ? ' compact' : '')}>
      {caveat && (
        <p className="mac-caveat small" role="note">
          <Icon name="warn" size={16} />
          {caveat}
        </p>
      )}
      {/* In a chat reply the director's own words are the summary; the card shows the rows. */}
      {!compact && (data.summary?.length || 0) > 0 && (
        <section className="card mac-summary" aria-label="In short">
          <h2>In short</h2>
          <ul>
            {data.summary!.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </section>
      )}
      {GROUPS.map(g => {
        const items = groups[g.key] || [];
        if (compact && !items.length) return null;
        return (
          <section key={g.key} className={'activity-group ' + g.key} aria-label={g.title}>
            <h3 className="group-title">
              {g.title} <span className="count-pill num">{items.length}</span>
            </h3>
            {items.length ? (
              <ul className="activity-list">
                {items.map((a, i) => (
                  <ActivityRow key={`${a.kind}-${a.pid ?? i}-${i}`} a={a} compact={compact} />
                ))}
              </ul>
            ) : (
              <p className="small muted group-empty">{g.empty}</p>
            )}
          </section>
        );
      })}
      {compact && !total && <p className="small muted">Arena found no development activity on the Mac.</p>}
      {(data.limitations?.length || 0) > 0 && (
        <div className={compact ? 'mac-limits small muted' : 'note mac-limits'}>
          {!compact && <span className="eyebrow">What Arena could not see</span>}
          <ul>
            {data.limitations!.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </div>
      )}
      {compact ? (
        <button type="button" className="btn sm" aria-label="Open the full view of what is running on the Mac" onClick={() => setTab('mac')}>
          <Icon name="mac" size={16} />
          Full view
        </button>
      ) : (
        data.generated && <p className="small muted">Checked {ago(data.generated, now)}.</p>
      )}
    </div>
  );
}

export function MacScreen() {
  const {back, openChat} = useArena();
  const {data, error, busy, reload} = usePolled<MacActivity>('mac-activity', false, null);
  // The host's own words while another look is still running (503): "Arena is still looking
  // at the Mac. Try again in a moment.", as a heading and a line.
  const busyText = error?.status === 503 ? plainError(error).split(/(?<=\.)\s+/, 2) : [];
  return (
    <div className="screen mac-screen">
      <div className="screen-head">
        <button type="button" className="square-btn" aria-label="Back" onClick={back}>
          <Icon name="back" size={20} />
        </button>
        <div className="screen-head-main">
          <div className="screen-title-row">
            <h1 className="screen-title">{"What's running on my Mac"}</h1>
          </div>
          <p className="screen-sub">A fresh, read-only look. Arena never stops or changes anything it finds here.</p>
        </div>
      </div>
      <div className="btn-row mac-actions">
        <button type="button" className="btn" disabled={busy} aria-busy={busy} onClick={() => reload()}>
          <Icon name="refresh" size={16} />
          {busy ? 'Checking…' : 'Refresh'}
        </button>
        <button type="button" className="btn primary" onClick={() => openChat({text: "What's running on my Mac?"})}>
          <Icon name="chat" size={16} />
          Ask the director
        </button>
      </div>
      {data ? (
        <MacActivityView data={data} />
      ) : error ? (
        <div className="empty">
          <h3>{error.status === 404 ? 'This version of Arena cannot look at the Mac yet' : error.status === 503 ? busyText[0] : !error.status ? "Couldn't reach your Mac" : 'Arena could not look at the Mac right now'}</h3>
          <p>{error.status === 404 ? 'Update Arena on the Mac to see what is running there.' : error.status === 503 ? busyText[1] || 'Tap Retry in a moment.' : !error.status ? 'Check the connection, then tap Retry.' : 'Tap Retry in a moment.'}</p>
          {error.status !== 404 && (
            <button type="button" className="btn" disabled={busy} onClick={() => reload()}>
              <Icon name="refresh" size={16} />
              {busy ? 'Checking…' : 'Retry'}
            </button>
          )}
        </div>
      ) : (
        <p className="muted small loading-line">Looking at the Mac…</p>
      )}
    </div>
  );
}
