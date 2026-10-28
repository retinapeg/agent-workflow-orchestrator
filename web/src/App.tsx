import React, {createContext, useContext, useEffect, useRef, useState} from 'react';
import {ACTIVE_ATTEMPT, ACTIVE_TASK, TABS, needsYou, shortSha, type RecordData, type Snapshot, type Tab} from './types';
import {Icon, LiveDot, Mark, Wordmark} from './ui';
import {Overview} from './screens/Overview';
import {Projects} from './screens/Projects';
import {Build} from './screens/Build';
import {Agents} from './screens/Agents';
import {Review} from './screens/Review';
import {DialogBody} from './screens/Dialogs';

export type Modal = {kind: 'goal' | 'task' | 'review' | 'device' | 'account' | 'enrol'; id?: string} | null;
export type Evidence = {label: string; text: string; kind: 'diff' | 'text'; warn?: string} | null;
export type Link = 'live' | 'polling' | 'reconnecting';

export type Ctx = {
  data: Snapshot;
  now: number;
  online: boolean;
  link: Link;
  lastSeen: string;
  busy: boolean;
  disabled: boolean;
  tab: Tab;
  setTab: (t: Tab) => void;
  goal: string | null;
  setGoal: (id: string) => void;
  openGoal: (id: string) => void;
  modal: Modal;
  setModal: (m: Modal) => void;
  action: (path: string, body: RecordData, success: string, close?: boolean) => Promise<void>;
  post: (path: string, body: RecordData) => Promise<any>;
  notify: (s: string) => void;
  evidence: Evidence;
  setEvidence: (e: Evidence) => void;
  showEvidence: (attempt: string, name?: string) => Promise<void>;
  showApprovalDiff: (id: string) => Promise<void>;
  unpair: () => Promise<void>;
  handoff: string;
  clearHandoff: () => void;
  search: string;
  setSearch: (s: string) => void;
};

const ArenaContext = createContext<Ctx>(null!);
export const useArena = () => useContext(ArenaContext);

// Visual labels follow the phone mockup; the accessible names stay the contract names.
const VISUAL: Record<Tab, string> = {overview: 'Home', projects: 'Projects', build: 'Builds', agents: 'Agents', review: 'Review'};

const RESTARTING = 'Arena is restarting — reconnecting…';

// A spec shared from another app (Web Share Target: GET /share?title&text&url) or a plain
// ?text= link lands here. The address is rewritten to / at once so a reload cannot resubmit.
function readHandoff(): string {
  try {
    const url = new URL(window.location.href);
    const shared = url.pathname === '/share' || url.searchParams.has('text') || url.searchParams.has('title');
    if (!shared) return '';
    const title = (url.searchParams.get('title') || '').trim();
    const text = (url.searchParams.get('text') || '').trim();
    const link = (url.searchParams.get('url') || '').trim();
    window.history.replaceState(null, '', '/');
    return [title, text, link && !text.includes(link) ? link : ''].filter(Boolean).join('\n\n').trim();
  } catch {
    return '';
  }
}

// Thrown by post() so callers can read the host's structured answer (e.g. project candidates).
export type PostError = Error & {status: number; body: any};

function Splash({children}: {children: React.ReactNode}) {
  return (
    <div className="pairing">
      <div className="pair-card">
        <div className="splash-brand">
          <Mark size={120} />
          <Wordmark className="xl" />
          <span className="tagline">Ideas → Agents → Real software</span>
        </div>
        {children}
      </div>
    </div>
  );
}

export function App() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [paired, setPaired] = useState<boolean | null>(null);
  const [csrf, setCsrf] = useState('');
  const [tab, setTabState] = useState<Tab>('overview');
  const [online, setOnline] = useState(false);
  const [sse, setSse] = useState(false);
  const [lastSeen, setLastSeen] = useState('');
  const [toast, setToast] = useState('');
  const [modal, setModal] = useState<Modal>(null);
  const [evidence, setEvidence] = useState<Evidence>(null);
  const [busy, setBusy] = useState(false);
  const [goal, setGoal] = useState<string | null>(null);
  const [handoff, setHandoff] = useState(readHandoff);
  const [search, setSearch] = useState('');
  const [, setTick] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const csrfRef = useRef('');
  csrfRef.current = csrf;
  const received = useRef({server: 0, at: 0});
  const toastTimer = useRef(0);

  const notify = (s: string) => {
    setToast(s);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(''), 6500);
  };

  async function get(path: string) {
    const r = await fetch('/api/v1/' + path, {cache: 'no-store'});
    if (r.status === 401) {
      setPaired(false);
      throw Error('Device pairing required');
    }
    const j = await r.json();
    if (!r.ok) throw Error(j.detail || 'Request failed');
    return j;
  }

  async function refresh() {
    try {
      const d = await get('overview');
      received.current = {server: d.server_time, at: Date.now()};
      setData(d);
      setOnline(true);
      setLastSeen(new Date().toLocaleTimeString());
    } catch {
      setOnline(false);
    }
  }

  const [bootError, setBootError] = useState('');
  useEffect(() => {
    let stopped = false;
    let timer = 0;
    const bootstrap = async () => {
      try {
        const r = await fetch('/api/v1/session', {cache: 'no-store'});
        if (r.status === 401) {
          setPaired(false); // The only answer that means this device is not paired.
          return;
        }
        if (r.status === 403 || r.status === 404) {
          // Permanent for this address (wrong origin/host, old URL): say why, do not spin.
          const j = await r.json().catch(() => ({}));
          setBootError(`${j.detail || 'Host refused this address'} (HTTP ${r.status}). Open the private HTTPS address from the Device session screen.`);
          return;
        }
        // 502/503 is Tailscale Serve answering while the controller restarts.
        if (r.status === 502 || r.status === 503) throw Error(RESTARTING);
        if (!r.ok) throw Error(`Host answered ${r.status}`);
        const s = await r.json();
        setCsrf(s.csrf);
        setBootError('');
        setPaired(true);
      } catch (e) {
        // Tailscale not up yet, cached shell offline, or a transient host error:
        // keep the paired state and retry instead of demanding a new pairing code.
        if (stopped) return;
        const reason = e instanceof Error ? e.message : String(e);
        setBootError(reason === RESTARTING ? RESTARTING : `Reaching your build host · retrying (${reason}). Check the Tailscale app is connected.`);
        timer = window.setTimeout(bootstrap, 3000);
      }
    };
    bootstrap();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!paired) return;
    refresh();
    const ev = new EventSource('/api/v1/events');
    ev.addEventListener('update', refresh);
    ev.addEventListener('snapshot', refresh);
    ev.onerror = () => {
      setSse(false);
      setOnline(false);
    };
    ev.onopen = () => {
      setSse(true);
      refresh();
    };
    const timer = setInterval(refresh, 5000);
    return () => {
      ev.close();
      clearInterval(timer);
    };
  }, [paired]);

  useEffect(() => {
    if (modal && !dialog.current?.open) dialog.current?.showModal();
    if (!modal) dialog.current?.close();
  }, [modal]);

  // A one-second tick only while something is genuinely running, so elapsed readouts stay honest.
  const live =
    !!data &&
    (data.attempts.some(a => ACTIVE_ATTEMPT.includes(a.state)) ||
      data.tasks.some(t => ACTIVE_TASK.includes(t.state) || t.state === 'integrating'));
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => {
      if (document.visibilityState !== 'hidden') setTick(n => n + 1);
    }, 1000);
    return () => clearInterval(id);
  }, [live]);
  const now = received.current.server + (Date.now() - received.current.at) / 1000;

  async function post(path: string, body: RecordData) {
    if (!online) throw Error('Reconnect and refresh before changing the queue');
    const key = crypto.randomUUID();
    const r = await fetch('/api/v1/' + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfRef.current, 'Idempotency-Key': key},
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) {
      const error = Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) as PostError;
      error.status = r.status;
      error.body = j;
      throw error;
    }
    await refresh();
    return j;
  }

  async function action(path: string, body: RecordData, success: string, close = false) {
    if (busy) return;
    setBusy(true);
    try {
      await post(path, body);
      notify(success);
      if (close) setModal(null);
    } catch (e) {
      notify(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function showApprovalDiff(id: string) {
    if (data?.version === '0.4.0-local') {
      const a = data.approvals.find(a => a.id === id);
      const t = data.tasks.find(t => t.id === a?.task_id);
      if (!t?.build_attempt) {
        setEvidence({label: 'Combined staging diff', kind: 'text', text: 'Build evidence is not available yet.'});
        return;
      }
      await showEvidence(t.build_attempt);
      notify('Running server needs a restart for combined diffs. This is the individual task diff.');
      return;
    }
    setEvidence({label: 'Combined staging diff', kind: 'diff', text: 'Loading combined staging diff…'});
    try {
      const r = await get(`approvals/${id}/diff`);
      setEvidence({
        label: `Combined diff · ${shortSha(r.base)} → ${shortSha(r.candidate)}`,
        kind: 'diff',
        text: r.text,
        warn: r.truncated ? 'Diff truncated at 200,000 characters. Inspect the full commit on the Mac before accepting.' : undefined,
      });
    } catch (e) {
      setEvidence({label: 'Combined staging diff', kind: 'text', text: String(e)});
    }
  }

  async function showEvidence(attempt: string, name = 'diff') {
    const label = name === 'diff' ? 'Attempt diff' : name === 'transcript.jsonl' ? 'Transcript' : name;
    const kind = name === 'diff' ? 'diff' : 'text';
    setEvidence({label, kind, text: 'Loading preserved evidence…'});
    try {
      const r = await get(`attempts/${attempt}/${name === 'diff' ? 'diff' : 'artifacts/' + name}`);
      setEvidence({label, kind, text: r.text});
    } catch (e) {
      setEvidence({label, kind: 'text', text: String(e)});
    }
  }

  async function unpair() {
    await post('logout', {});
    setPaired(false);
    setModal(null);
  }

  const setTab = (t: Tab) => {
    setTabState(t);
    window.scrollTo(0, 0);
  };
  const openGoal = (id: string) => {
    setGoal(id);
    setTab('build');
  };

  if (paired === null)
    return (
      <Splash>
        <h1 className="splash-title">Connecting…</h1>
        <p className="muted" aria-live="polite">
          {bootError || 'Reaching your build host.'}
        </p>
      </Splash>
    );

  if (!paired)
    return (
      <div className="pairing">
        <form
          className="pair-card"
          onSubmit={async e => {
            e.preventDefault();
            setBusy(true);
            try {
              const f = new FormData(e.currentTarget);
              const r = await fetch('/api/v1/pair', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({code: f.get('code')}),
              });
              const j = await r.json();
              if (!r.ok) throw Error(j.detail);
              setCsrf(j.csrf);
              setPaired(true);
            } catch (e) {
              notify(String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="splash-brand">
            <Mark size={120} />
            <Wordmark className="xl" />
            <span className="tagline">Ideas → Agents → Real software</span>
          </div>
          <span className="eyebrow">Device pairing</span>
          <h1 className="splash-title">
            Your builds.
            <br />
            Within reach.
          </h1>
          <p className="muted">Pair this device with your Mac. Account credentials never leave the host.</p>
          <div className="field">
            <label htmlFor="pair-code">One-time pairing code</label>
            <input id="pair-code" name="code" autoComplete="off" autoCapitalize="off" spellCheck={false} required minLength={8} className="mono" />
          </div>
          <button className="btn primary full" disabled={busy}>
            Pair device
          </button>
          <p className="field-help">
            Generate a ten-minute code on the host with <code>python -m agent_arena.control pair</code>.
          </p>
          {toast && (
            <p role="alert" className="alert">
              {toast}
            </p>
          )}
        </form>
      </div>
    );

  if (!data)
    return (
      <Splash>
        <h1 className="splash-title">Connecting…</h1>
        <p className="muted">Waiting for a fresh host snapshot.</p>
        <button className="btn" onClick={refresh}>
          Reconnect
        </button>
      </Splash>
    );

  const link: Link = !online ? 'reconnecting' : sse ? 'live' : 'polling';
  const disabled = busy || !online;
  const running = data.tasks.filter(t => ACTIVE_TASK.includes(t.state) || t.state === 'integrating').length;
  const attention = needsYou(data).count;
  const counts: Partial<Record<Tab, number>> = {build: running, review: attention};
  const heartbeatAge = data.heartbeat ? data.server_time - data.heartbeat : null;
  const scheduler =
    heartbeatAge === null ? {tone: 'off' as const, text: 'Scheduler off'} : heartbeatAge > 30 ? {tone: 'warn' as const, text: `Stale · ${Math.round(heartbeatAge)}s`} : {tone: 'ok' as const, text: 'Online'};
  const linkTone = link === 'live' ? 'live' : link === 'polling' ? 'ok' : 'warn';
  const ready = data.agents.filter(a => a.enabled && a.health === 'ready').length;
  const busyAgents = data.agents.filter(a => data.attempts.some(x => x.account_id === a.id && ACTIVE_ATTEMPT.includes(x.state))).length;

  const ctx: Ctx = {
    data,
    now,
    online,
    link,
    lastSeen,
    busy,
    disabled,
    tab,
    setTab,
    goal,
    setGoal,
    openGoal,
    modal,
    setModal,
    action,
    post,
    notify,
    evidence,
    setEvidence,
    showEvidence,
    showApprovalDiff,
    unpair,
    handoff,
    clearHandoff: () => setHandoff(''),
    search,
    setSearch,
  };

  return (
    <ArenaContext.Provider value={ctx}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand side-brand">
            <Mark size={44} />
            <div>
              <Wordmark />
              <span className="brand-sub">Build control</span>
            </div>
          </div>
          <nav className="nav" aria-label="Primary">
            {TABS.map(t => (
              <button
                key={t.key}
                className={'nav-btn' + (tab === t.key ? ' active' : '')}
                aria-label={t.label}
                aria-current={tab === t.key ? 'page' : undefined}
                onClick={() => setTab(t.key)}
              >
                <span className="nav-icon">
                  <Icon name={t.key} />
                </span>
                <span className="nav-label" aria-hidden="true">
                  {VISUAL[t.key]}
                </span>
                {counts[t.key] ? (
                  <b className="nav-count num" aria-hidden="true">
                    {counts[t.key]}
                  </b>
                ) : null}
              </button>
            ))}
          </nav>
          <button className="side-status" onClick={() => setModal({kind: 'device'})} aria-label="System status">
            <span className="k">System</span>
            <span className="v">
              <LiveDot tone={link === 'reconnecting' ? 'warn' : scheduler.tone === 'ok' ? 'ok' : scheduler.tone} />
              {link === 'reconnecting' ? 'Reconnecting' : scheduler.text}
            </span>
            <span className="v">
              <LiveDot tone={busyAgents ? 'live' : ready ? 'ok' : 'off'} />
              <span className="num">
                {busyAgents ? `${busyAgents} busy · ` : ''}
                {ready}/{data.agents.length || 0} agents
              </span>
            </span>
            <span className="v small">
              <LiveDot tone={linkTone} />
              {link === 'live' ? 'Live link' : link === 'polling' ? 'Polling' : 'Read-only'}
              {data.paused ? ' · Dispatch paused' : ''}
            </span>
          </button>
        </aside>

        <main>
          <header className="topbar">
            <div className="brand mobile-brand">
              <Mark size={40} />
              <Wordmark />
            </div>
            <label className="search">
              <Icon name="search" size={18} />
              <input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search builds, projects or agents…" aria-label="Search builds, projects or agents" />
            </label>
            <div className="topbar-actions">
              <button
                className={'btn ghost icon-btn' + (data.paused ? ' paused' : '')}
                disabled={disabled}
                aria-label={data.paused ? 'Resume queue' : 'Pause queue'}
                title={data.paused ? 'Resume queue' : 'Pause queue'}
                onClick={() =>
                  action(
                    'control/' + (data.paused ? 'resume-dispatch' : 'pause-dispatch'),
                    {revision: data.revision},
                    data.paused ? 'Dispatch resumed' : 'New dispatch paused; current work continues',
                  )
                }
              >
                <Icon name={data.paused ? 'play' : 'pause'} size={18} />
                <span className="btn-text">{data.paused ? 'Resume queue' : 'Pause queue'}</span>
              </button>
              <button className="avatar" aria-label="Device session" onClick={() => setModal({kind: 'device'})}>
                <Icon name="user" size={18} />
              </button>
            </div>
          </header>

          {link === 'reconnecting' && (
            <div className="instrument reconnecting" role="status">
              <LiveDot tone="warn" />
              Reconnecting to the build host · read-only until the link returns
            </div>
          )}
          {data.paused && link !== 'reconnecting' && (
            <div className="instrument paused">
              <LiveDot tone="warn" />
              Dispatch is paused · running work continues, nothing new starts
            </div>
          )}

          {tab === 'overview' && <Overview />}
          {tab === 'projects' && <Projects />}
          {tab === 'build' && <Build />}
          {tab === 'agents' && <Agents />}
          {tab === 'review' && <Review />}
        </main>

        <dialog ref={dialog} onCancel={() => setModal(null)} onClose={() => modal && setModal(null)}>
          {modal && <DialogBody key={modal.kind + (modal.id || '')} />}
        </dialog>
        {toast && (
          <div className="toast" role="status">
            {toast}
          </div>
        )}
      </div>
    </ArenaContext.Provider>
  );
}
