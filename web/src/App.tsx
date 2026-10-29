import React, {createContext, useContext, useEffect, useRef, useState} from 'react';
import {ACTIVE_ATTEMPT, ACTIVE_TASK, needsYouRows, plainError, shortSha, type NeedRow, type RecordData, type Snapshot, type Tab} from './types';
import {Icon, LiveDot, Mark, Wordmark} from './ui';
import {Chat} from './screens/Chat';
import {Overview} from './screens/Overview';
import {Projects} from './screens/Projects';
import {Build} from './screens/Build';
import {Agents} from './screens/Agents';
import {Review} from './screens/Review';
import {BlockDetail, BuildStory} from './screens/Story';
import {MacScreen} from './screens/Mac';
import {Archived, Builds} from './screens/History';
import {DialogBody} from './screens/Dialogs';

export type Modal = {kind: 'goal' | 'task' | 'review' | 'device' | 'account' | 'enrol'; id?: string} | null;
export type Evidence = {label: string; text: string; kind: 'diff' | 'text'; warn?: string} | null;
export type Link = 'live' | 'polling' | 'reconnecting';
// An SSE `update` whose kind is conversation.* names the thread it touched, so the Chat
// screen can refetch that thread's messages without waiting for the next snapshot.
export type ChatPing = {id: string; n: number};
// What a screen hands to Chat when it opens it: a message to send to the director (into the
// current conversation, or a new one on the host's default project), optionally about one
// block or build, or a project to talk about. `n` makes each hand-off distinct.
export type ChatIntent = {n: number; text?: string; about?: {task_id: string} | {goal_id: string}; projectId?: string} | null;

// The screen on show. `goal` is the build of the story, block and technical views; `task`
// the block. Each change is a browser history entry, so Android's back gesture walks back
// through the screens and a reload lands where the owner was.
export type Screen = {tab: Tab; goal: string | null; task: string | null; depth: number};
const HOME: Screen = {tab: 'overview', goal: null, task: null, depth: 0};
const TABS: Tab[] = ['overview', 'chat', 'story', 'block', 'mac', 'builds', 'build', 'projects', 'agents', 'review', 'archived'];
function restored(): Screen {
  try {
    const s = window.history.state?.arena;
    if (s && TABS.includes(s.tab)) return {tab: s.tab, goal: s.goal ?? null, task: s.task ?? null, depth: Number(s.depth) || 0};
  } catch {
    /* no history state: start at Home */
  }
  return HOME;
}

export type Ctx = {
  data: Snapshot;
  now: number;
  online: boolean;
  link: Link;
  lastSeen: string;
  busy: boolean;
  disabled: boolean;
  tab: Tab;
  screen: Screen;
  setTab: (t: Tab) => void;
  // The technical build view's selected request (null: its default).
  goal: string | null;
  setGoal: (id: string) => void;
  // Open a build's plain-English story, one of its blocks, or its technical view.
  openGoal: (id: string) => void;
  openBlock: (taskId: string, goalId: string) => void;
  openAdvanced: (id: string) => void;
  back: () => void;
  openChat: (intent?: Omit<NonNullable<ChatIntent>, 'n'>) => void;
  chatIntent: ChatIntent;
  clearChatIntent: () => void;
  modal: Modal;
  setModal: (m: Modal) => void;
  action: (path: string, body: RecordData, success: string, close?: boolean) => Promise<void>;
  restoredNote: (path: string, response: any) => string;
  post: (path: string, body: RecordData) => Promise<any>;
  get: (path: string) => Promise<any>;
  chatPing: ChatPing;
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
  // When this device was last seen by the host before the current visit (null: not away).
  awaySince: number | null;
  markSeen: () => Promise<void>;
};

const ArenaContext = createContext<Ctx>(null!);
export const useArena = () => useContext(ArenaContext);

// The one menu: the ☰ sheet on phones and the sidebar on wide screens list the same items.
// Accessible names are the visible labels; counts are announced as a description.
const MENU: {tab: Tab; label: string; icon: string; hint?: string}[] = [
  {tab: 'overview', label: 'Home', icon: 'overview'},
  {tab: 'chat', label: 'Chats', icon: 'chat'},
  {tab: 'mac', label: "What's running on my Mac", icon: 'mac'},
  {tab: 'builds', label: 'Builds', icon: 'build', hint: 'history'},
  {tab: 'projects', label: 'Projects', icon: 'projects'},
  {tab: 'agents', label: 'Agents', icon: 'agents'},
  {tab: 'review', label: 'Review', icon: 'review'},
  {tab: 'archived', label: 'Archived', icon: 'archive'},
];
// Screens reached from a list highlight that list's menu item.
const SECTION: Partial<Record<Tab, Tab>> = {story: 'builds', block: 'builds', build: 'builds'};

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

// The menu's items, shared by the phone sheet and the desktop sidebar. `onPick` runs after
// the item's own action (the sheet closes itself there).
function MenuItems({where, current, counts, onPick}: {where: 'side' | 'sheet'; current: Tab; counts: Partial<Record<Tab, number>>; onPick: () => void}) {
  const {data, setTab, setModal, action, disabled} = useArena();
  const section = SECTION[current] || current;
  return (
    <>
      {MENU.map(item => {
        const count = counts[item.tab];
        const countId = `${where}-count-${item.tab}`;
        return (
          <button
            key={item.tab}
            type="button"
            className={'nav-btn' + (section === item.tab ? ' active' : '')}
            aria-label={item.label}
            aria-current={section === item.tab ? 'page' : undefined}
            aria-describedby={count ? countId : undefined}
            onClick={() => {
              onPick();
              setTab(item.tab);
            }}
          >
            <span className="nav-icon">
              <Icon name={item.icon} />
            </span>
            <span className="nav-label" aria-hidden="true">
              {item.label}
              {item.hint && <span className="nav-hint"> {item.hint}</span>}
            </span>
            {count ? (
              <b className="nav-count num" id={countId}>
                {count}
                <span className="sr-only"> waiting</span>
              </b>
            ) : null}
          </button>
        );
      })}
      <span className="nav-rule" aria-hidden="true" />
      <button
        type="button"
        className={'nav-btn' + (data.paused ? ' paused' : '')}
        disabled={disabled}
        aria-label={data.paused ? 'Resume queue' : 'Pause queue'}
        onClick={() => {
          onPick();
          action('control/' + (data.paused ? 'resume-dispatch' : 'pause-dispatch'), {revision: data.revision}, data.paused ? 'Dispatch resumed' : 'New dispatch paused; current work continues');
        }}
      >
        <span className="nav-icon">
          <Icon name={data.paused ? 'play' : 'pause'} />
        </span>
        <span className="nav-label" aria-hidden="true">
          {data.paused ? 'Resume queue' : 'Pause queue'}
        </span>
      </button>
      <button
        type="button"
        className="nav-btn"
        aria-label="Device session"
        onClick={() => {
          onPick();
          setModal({kind: 'device'});
        }}
      >
        <span className="nav-icon">
          <Icon name="user" />
        </span>
        <span className="nav-label" aria-hidden="true">
          Device session
        </span>
      </button>
    </>
  );
}

export function App() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [paired, setPaired] = useState<boolean | null>(null);
  const [csrf, setCsrf] = useState('');
  const [screen, setScreen] = useState<Screen>(restored);
  const [online, setOnline] = useState(false);
  const [sse, setSse] = useState(false);
  const [lastSeen, setLastSeen] = useState('');
  const [toast, setToast] = useState('');
  const [modal, setModal] = useState<Modal>(null);
  const [menu, setMenu] = useState(false);
  const [evidence, setEvidence] = useState<Evidence>(null);
  const [busy, setBusy] = useState(false);
  const [handoff, setHandoff] = useState(readHandoff);
  const [search, setSearch] = useState('');
  const [chatPing, setChatPing] = useState<ChatPing>({id: '', n: 0});
  const [chatIntent, setChatIntent] = useState<ChatIntent>(null);
  const [awaySince, setAwaySince] = useState<number | null>(null);
  const [, setTick] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const menuSheet = useRef<HTMLDialogElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const csrfRef = useRef('');
  csrfRef.current = csrf;
  const screenRef = useRef(screen);
  screenRef.current = screen;
  const received = useRef({server: 0, at: 0});
  const refreshes = useRef({last: 0, timer: 0});
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
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const error = Error(typeof j.detail === 'string' ? j.detail : `Request failed (HTTP ${r.status})`) as PostError;
      error.status = r.status;
      error.body = j;
      throw error;
    }
    return j;
  }

  async function refresh() {
    refreshes.current.last = Date.now();
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

  // Snapshot refreshes are coalesced: a burst of SSE events (one build emits several per
  // second) produces at most one fetch per 750 ms, trailing, so the last event always wins.
  function scheduleRefresh() {
    const s = refreshes.current;
    if (s.timer) return;
    const wait = Math.max(0, 750 - (Date.now() - s.last));
    s.timer = window.setTimeout(() => {
      s.timer = 0;
      refresh();
    }, wait);
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
        setAwaySince(typeof s.away_since === 'number' ? s.away_since : null);
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
    const onUpdate = (e: MessageEvent) => {
      scheduleRefresh();
      try {
        const row = JSON.parse(e.data);
        if (typeof row?.kind === 'string' && row.kind.startsWith('conversation.')) {
          const d = typeof row.data === 'string' ? JSON.parse(row.data) : row.data;
          setChatPing({id: String(d?.conversation_id || ''), n: Date.now()});
        }
      } catch {
        /* a malformed event still refreshed the snapshot above */
      }
    };
    ev.addEventListener('update', onUpdate);
    ev.addEventListener('snapshot', scheduleRefresh);
    ev.onerror = () => {
      setSse(false);
      setOnline(false);
    };
    ev.onopen = () => {
      setSse(true);
      scheduleRefresh();
    };
    const timer = setInterval(() => document.visibilityState !== 'hidden' && scheduleRefresh(), 5000);
    return () => {
      ev.close();
      clearInterval(timer);
      window.clearTimeout(refreshes.current.timer);
      refreshes.current.timer = 0;
    };
  }, [paired]);

  // Coming back to a backgrounded app: ask the host whether this device was away long
  // enough for Home to summarise what happened meanwhile, and refresh at once.
  useEffect(() => {
    if (!paired) return;
    const onVisible = async () => {
      if (document.visibilityState !== 'visible') return;
      scheduleRefresh();
      try {
        const r = await fetch('/api/v1/session', {cache: 'no-store'});
        if (!r.ok) return;
        const s = await r.json();
        if (typeof s.away_since === 'number') setAwaySince(s.away_since);
      } catch {
        /* the next snapshot poll reports the link state */
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [paired]);

  // History: the current screen is the current entry's state, so back/forward (Android's
  // back gesture included) and a reload restore it. Overlays close on any history move.
  useEffect(() => {
    try {
      window.history.replaceState({...(window.history.state || {}), arena: screenRef.current}, '');
    } catch {
      /* a sandboxed frame without history: navigation still works in memory */
    }
    const onPop = (e: PopStateEvent) => {
      const s = e.state?.arena;
      setScreen(s && TABS.includes(s.tab) ? {tab: s.tab, goal: s.goal ?? null, task: s.task ?? null, depth: Number(s.depth) || 0} : HOME);
      setMenu(false);
      setModal(null);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  // A spec shared into Arena opens the request form with the text already in place.
  useEffect(() => {
    if (data && handoff) setModal({kind: 'goal'});
  }, [!!data, handoff]);

  // The menu closes before a dialog it opened (Device session) takes the focus.
  useEffect(() => {
    if (menu && !menuSheet.current?.open) menuSheet.current?.showModal();
    if (!menu && menuSheet.current?.open) {
      menuSheet.current.close();
      menuButton.current?.focus({preventScroll: true});
    }
  }, [menu]);

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
    if (!online) throw Error("Couldn't reach your Mac — check the connection and try again.");
    const key = crypto.randomUUID();
    const r = await fetch('/api/v1/' + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrfRef.current, 'Idempotency-Key': key},
      body: JSON.stringify(body),
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) {
      const error = Error(typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? `HTTP ${r.status}`)) as PostError;
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
      const r = await post(path, body);
      notify(success + restoredNote(path, r));
      if (close) setModal(null);
    } catch (e) {
      notify(plainError(e));
    } finally {
      setBusy(false);
    }
  }

  // Answering, retrying or instructing work in an archived project brings the project back
  // (the host says so with `restored_project`); the toast says it too.
  function restoredNote(path: string, r: any): string {
    if (!r?.restored_project) return '';
    const id = /^tasks\/([^/]+)\//.exec(path)?.[1];
    const project = data?.projects.find(p => p.id === data?.tasks.find(t => t.id === id)?.project_id);
    return ` Arena also brought ${project?.name || 'its project'} back from the archive.`;
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

  async function markSeen() {
    setAwaySince(null);
    try {
      await post('session/seen', {revision: 0});
    } catch {
      /* bookkeeping only; the summary is shown regardless */
    }
  }

  // Navigate: a new history entry for a new screen; `replace` rewrites the current one (the
  // technical view's request picker). The page lands on its head, not where the last was.
  const go = (next: Omit<Screen, 'depth'>, replace = false) => {
    const cur = screenRef.current;
    const same = cur.tab === next.tab && cur.goal === next.goal && cur.task === next.task;
    const s: Screen = {...next, depth: replace || same ? cur.depth : cur.depth + 1};
    setScreen(s);
    screenRef.current = s;
    try {
      if (replace || same) window.history.replaceState({...(window.history.state || {}), arena: s}, '');
      else window.history.pushState({arena: s}, '');
    } catch {
      /* in-memory navigation only */
    }
    if (!replace) window.requestAnimationFrame(() => window.scrollTo({top: 0, behavior: 'auto'}));
  };
  const setTab = (t: Tab) => go({tab: t, goal: null, task: null});
  const openGoal = (id: string) => go({tab: 'story', goal: id, task: null});
  const openBlock = (taskId: string, goalId: string) => go({tab: 'block', goal: goalId, task: taskId});
  const openAdvanced = (id: string) => go({tab: 'build', goal: id, task: null});
  const setGoal = (id: string) => go({tab: 'build', goal: id, task: null}, true);
  // Back: the previous screen in this visit, else the screen's parent (a block's build,
  // otherwise Home) in place of the current entry.
  const back = () => {
    const cur = screenRef.current;
    if (cur.depth > 0) {
      window.history.back();
      return;
    }
    if (cur.tab === 'block' && cur.goal) go({tab: 'story', goal: cur.goal, task: null}, true);
    else go({tab: 'overview', goal: null, task: null}, true);
  };
  const openChat = (intent?: Omit<NonNullable<ChatIntent>, 'n'>) => {
    setChatIntent(intent ? {...intent, n: Date.now()} : null);
    go({tab: 'chat', goal: null, task: null});
  };
  // The global "Needs you" line: one item opens itself, several open the Review list.
  const openNeed = (rows: NeedRow[]) => {
    const r = rows[0];
    if (!r) return;
    if (rows.length > 1) {
      setTab('review');
      return;
    }
    if (r.kind === 'review') setModal({kind: 'review', id: r.id});
    else if (r.goalId) openGoal(r.goalId);
    else setModal({kind: 'task', id: r.id});
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
  const tab = screen.tab;
  const needRows = needsYouRows(data);
  const attention = needRows.length;
  const counts: Partial<Record<Tab, number>> = {review: attention};
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
    screen,
    setTab,
    goal: tab === 'build' ? screen.goal : null,
    setGoal,
    openGoal,
    openBlock,
    openAdvanced,
    back,
    openChat,
    chatIntent,
    clearChatIntent: () => setChatIntent(null),
    modal,
    setModal,
    action,
    restoredNote,
    post,
    get,
    chatPing,
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
    awaySince,
    markSeen,
  };

  return (
    <ArenaContext.Provider value={ctx}>
      <div className={'shell tab-' + tab}>
        <aside className="sidebar">
          <div className="brand side-brand">
            <Mark size={44} />
            <div>
              <Wordmark />
              <span className="brand-sub">Your director</span>
            </div>
          </div>
          <nav className="nav" aria-label="Primary">
            <MenuItems where="side" current={tab} counts={counts} onPick={() => {}} />
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
            <button type="button" className="brand mobile-brand" aria-label="Arena home" onClick={() => setTab('overview')}>
              <Mark size={44} />
              <Wordmark />
            </button>
            <label className="search">
              <Icon name="search" size={18} />
              <input type="search" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search builds and projects…" aria-label="Search builds and projects" />
            </label>
            <button
              ref={menuButton}
              type="button"
              className={'menu-btn' + (attention ? ' attention' : '')}
              aria-label={attention ? `Menu, ${attention} item${attention === 1 ? '' : 's'} need${attention === 1 ? 's' : ''} you` : 'Menu'}
              aria-haspopup="dialog"
              aria-expanded={menu}
              onClick={() => setMenu(true)}
            >
              <Icon name="menu" size={22} />
            </button>
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
          {attention > 0 && tab !== 'overview' && tab !== 'review' && !(tab === 'story' && needRows.every(r => r.goalId === screen.goal)) && (
            <button type="button" className="instrument needs-band" onClick={() => openNeed(needRows)} aria-label={`Needs you: ${attention} item${attention === 1 ? '' : 's'}. ${needRows[0].label}: ${needRows[0].title}`}>
              <LiveDot tone="warn" />
              <span className="needs-band-text">
                <b>Needs you</b>
                <span className="clamp1">
                  {needRows[0].label} · {needRows[0].title}
                  {needRows[0].project ? ` · ${needRows[0].project}` : ''}
                </span>
              </span>
              {attention > 1 && <b className="count num">+{attention - 1}</b>}
              <Icon name="arrow" size={16} />
            </button>
          )}

          {tab === 'overview' && <Overview />}
          {tab === 'chat' && <Chat />}
          {tab === 'story' && screen.goal && <BuildStory key={screen.goal} goalId={screen.goal} />}
          {tab === 'block' && screen.task && <BlockDetail key={screen.task} taskId={screen.task} goalId={screen.goal} />}
          {tab === 'mac' && <MacScreen />}
          {tab === 'builds' && <Builds />}
          {tab === 'build' && <Build />}
          {tab === 'projects' && <Projects />}
          {tab === 'agents' && <Agents />}
          {tab === 'review' && <Review />}
          {tab === 'archived' && <Archived />}
        </main>

        {/* A tap on the backdrop lands on the dialog itself (the content fills it). */}
        <dialog ref={menuSheet} className="menu-sheet" aria-label="Menu" onCancel={() => setMenu(false)} onClose={() => menu && setMenu(false)} onClick={e => e.target === e.currentTarget && setMenu(false)}>
          <div className="menu-inner">
            <div className="menu-head">
              <span className="eyebrow">Menu</span>
              <button type="button" className="close" aria-label="Close menu" onClick={() => setMenu(false)}>
                <Icon name="x" size={18} />
              </button>
            </div>
            <div className="menu-items">{menu && <MenuItems where="sheet" current={tab} counts={counts} onPick={() => setMenu(false)} />}</div>
          </div>
        </dialog>
        <dialog ref={dialog} aria-labelledby="dialog-title" onCancel={() => setModal(null)} onClose={() => modal && setModal(null)}>
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
