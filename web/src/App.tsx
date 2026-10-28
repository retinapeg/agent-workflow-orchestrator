import React, {createContext, useContext, useEffect, useRef, useState} from 'react';
import {ACTIVE_ATTEMPT, ACTIVE_TASK, BLOCKED_TASK, TABS, shortSha, type RecordData, type Snapshot, type Tab} from './types';
import {Icon, LiveDot, Mark} from './ui';
import {Overview} from './screens/Overview';
import {Projects} from './screens/Projects';
import {Build} from './screens/Build';
import {Agents} from './screens/Agents';
import {Review} from './screens/Review';
import {DialogBody} from './screens/Dialogs';

export type Modal = {kind: 'goal' | 'task' | 'review' | 'device' | 'account'; id?: string} | null;
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
};

const ArenaContext = createContext<Ctx>(null!);
export const useArena = () => useContext(ArenaContext);

const LABELS: Record<Tab, string> = Object.fromEntries(TABS.map(t => [t.key, t.label])) as Record<Tab, string>;

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
        if (!r.ok) throw Error(`Host answered ${r.status}`);
        const s = await r.json();
        setCsrf(s.csrf);
        setBootError('');
        setPaired(true);
      } catch (e) {
        // Tailscale not up yet, cached shell offline, or a transient host error:
        // keep the paired state and retry instead of demanding a new pairing code.
        if (stopped) return;
        setBootError(String(e));
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
    if (!r.ok) throw Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail));
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
      <div className="pairing">
        <div className="pair-card">
          <Mark size={44} />
          <h1>Connecting…</h1>
          <p className="muted">{bootError ? `Reaching your build host · retrying (${bootError}). Check the Tailscale app is connected.` : 'Reaching your build host.'}</p>
        </div>
      </div>
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
          <div className="brand">
            <Mark size={40} />
            <span className="wordmark">ARENA</span>
          </div>
          <span className="eyebrow">Device pairing</span>
          <h1>
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
      <div className="pairing">
        <div className="pair-card">
          <Mark size={44} />
          <h1>Connecting…</h1>
          <p className="muted">Waiting for a fresh host snapshot.</p>
          <button className="btn" onClick={refresh}>
            Reconnect
          </button>
        </div>
      </div>
    );

  const link: Link = !online ? 'reconnecting' : sse ? 'live' : 'polling';
  const disabled = busy || !online;
  const running = data.tasks.filter(t => ACTIVE_TASK.includes(t.state) || t.state === 'integrating').length;
  const attention =
    data.approvals.filter(a => a.state === 'pending' || (a.state === 'accept' && a.publication?.state !== 'integrated')).length +
    data.tasks.filter(t => t.state === 'awaiting_input' || BLOCKED_TASK.includes(t.state)).length;
  const counts: Partial<Record<Tab, number>> = {build: running, review: attention};
  const heartbeatAge = data.heartbeat ? data.server_time - data.heartbeat : null;
  const scheduler =
    heartbeatAge === null ? {tone: 'off' as const, text: 'Not running'} : heartbeatAge > 30 ? {tone: 'warn' as const, text: `Stale · ${Math.round(heartbeatAge)}s`} : {tone: 'ok' as const, text: `Beat ${Math.round(heartbeatAge)}s ago`};
  const linkTone = link === 'live' ? 'live' : link === 'polling' ? 'ok' : 'warn';
  const linkText = link === 'live' ? `Live · ${lastSeen}` : link === 'polling' ? `Polling · ${lastSeen}` : `Reconnecting · read-only`;

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
  };

  return (
    <ArenaContext.Provider value={ctx}>
      <div className="shell">
        <aside className="sidebar">
          <div className="brand side-brand">
            <Mark size={38} />
            <div>
              <span className="wordmark">ARENA</span>
              <span className="brand-sub">Build control</span>
            </div>
          </div>
          <nav className="nav" aria-label="Primary">
            {TABS.map(t => (
              <button
                key={t.key}
                className={'nav-btn' + (tab === t.key ? ' active' : '')}
                aria-current={tab === t.key ? 'page' : undefined}
                onClick={() => setTab(t.key)}
              >
                <Icon name={t.key} />
                <span>{t.label}</span>
                {counts[t.key] ? (
                  <b className="nav-count num" aria-hidden="true">
                    {counts[t.key]}
                  </b>
                ) : null}
              </button>
            ))}
          </nav>
          <div className="side-status">
            <div className="side-row">
              <span className="k">Link</span>
              <span className="v">
                <LiveDot tone={linkTone} />
                {link === 'live' ? 'Live' : link === 'polling' ? 'Polling' : 'Reconnecting'}
              </span>
            </div>
            <div className="side-row">
              <span className="k">Scheduler</span>
              <span className="v">
                <LiveDot tone={scheduler.tone} />
                {scheduler.text}
              </span>
            </div>
            <div className="side-row">
              <span className="k">Host</span>
              <span className="v num">{data.version}</span>
            </div>
          </div>
        </aside>

        <main>
          <header className="topbar">
            <div className="brand mobile-brand">
              <Mark size={32} />
              <span className="wordmark">ARENA</span>
            </div>
            <div className="crumb">
              Workspace <span>/</span>
              <b>{LABELS[tab]}</b>
            </div>
            <div className="topbar-actions">
              <button
                className={'btn ghost' + (data.paused ? ' paused' : '')}
                disabled={disabled}
                onClick={() =>
                  action(
                    'control/' + (data.paused ? 'resume-dispatch' : 'pause-dispatch'),
                    {revision: data.revision},
                    data.paused ? 'Dispatch resumed' : 'New dispatch paused; current work continues',
                  )
                }
              >
                <Icon name={data.paused ? 'play' : 'pause'} size={16} />
                {data.paused ? 'Resume queue' : 'Pause queue'}
              </button>
              <button className="avatar" aria-label="Device session" onClick={() => setModal({kind: 'device'})}>
                <Icon name="user" size={16} />
              </button>
            </div>
          </header>

          <div className={'instrument ' + link}>
            <div>
              <span className="k">Link</span>
              <span className="v">
                <LiveDot tone={linkTone} />
                {linkText}
              </span>
            </div>
            <div>
              <span className="k">Scheduler</span>
              <span className="v">
                <LiveDot tone={scheduler.tone} />
                {scheduler.text}
              </span>
            </div>
            <div>
              <span className="k">Workers</span>
              <span className="v num">
                <LiveDot tone={running ? 'live' : 'off'} />
                {running} / {data.limit} slots
              </span>
            </div>
            <div>
              <span className="k">Dispatch</span>
              <span className="v">
                <LiveDot tone={data.paused ? 'warn' : 'ok'} />
                {data.paused ? 'Paused' : 'Open'}
              </span>
            </div>
          </div>

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
