import React, {useCallback, useEffect, useRef, useState} from 'react';
import {useArena} from '../App';
import {useSpeech} from '../speech';
import {
  ACTIVE_TASK,
  STAGES,
  agentsWorking,
  ago,
  duration,
  finishedAt,
  goalArchived,
  goalClass,
  goalSignal,
  goalStatus,
  goalView,
  isLive,
  isMerging,
  lastChange,
  liveAttemptOf,
  needsYouRows,
  outcomeLine,
  pickableProjects,
  plainError,
  progressFraction,
  progressLabel,
  quietLine,
  quietMinutes,
  stageIndex,
  stateLabel,
  stoppedAt,
  storyProgress,
  stripLegacy,
  workProgress,
  type GoalStory,
  type GoalView,
  type NeedRow,
  type RecordData,
  type Snapshot,
} from '../types';
import {Glyph, Icon, LiveDot, Panel, Progress} from '../ui';

// Per-build stepper. Every "done" comes from a recorded state or attempt result, never
// from elapsed time: plan task completed → Plan; build attempt passed → Code; its frozen
// checks all PASS → Test; independent review attempt passed → Review; integrated commit
// recorded → Integrate. The current step reuses the task stage derivation (stageIndex).
export const STEPS = ['Plan', 'Code', 'Test', 'Review', 'Integrate'];
const STAGE_TO_STEP = [0, 1, 2, 3, 3, 4, 4];

export function buildSteps(view: GoalView, attempts: RecordData[]): {done: boolean[]; active: number; detail: string[]} {
  const live = view.children.filter(t => t.state !== 'cancelled');
  const byId = (id?: string) => (id ? attempts.find(a => a.id === id) : undefined);
  const coded = live.filter(t => byId(t.build_attempt)?.state === 'passed');
  const tested = coded.filter(t => {
    const checks = byId(t.build_attempt)?.result?.checks;
    return Array.isArray(checks) && checks.length > 0 && checks.every((c: RecordData) => c.status === 'PASS');
  });
  const reviewed = live.filter(t => byId(t.review_attempt)?.state === 'passed');
  const integrated = live.filter(t => t.integrated_commit);
  const planDone = view.plan?.state === 'completed' || view.children.length > 0;
  const all = (n: number) => live.length > 0 && n === live.length;
  const done = [planDone, all(coded.length), all(tested.length), all(reviewed.length), all(integrated.length)];
  const count = (n: number, what: string) => (live.length ? `${n}/${live.length} tasks ${what}` : 'no tasks yet');
  const detail = [
    planDone ? 'Specification recorded' : view.plan ? `Director: ${stateLabel(view.plan.state)}` : 'No plan task',
    count(coded.length, 'built'),
    count(tested.length, 'passed frozen checks'),
    count(reviewed.length, 'passed independent review'),
    count(integrated.length, 'integrated into main'),
  ];
  let active = -1;
  if (view.active) {
    const indexes = (live.length ? live : view.plan ? [view.plan] : []).map(stageIndex).filter(i => i >= 0);
    const current = indexes.length ? STAGE_TO_STEP[Math.min(...indexes)] : 0;
    active = done[current] ? done.findIndex(d => !d) : current;
  }
  return {done, active, detail};
}

// Connected circles: check (done) · glowing ring (current) · hollow (pending).
export function BuildSteps({view, size = 'md'}: {view: GoalView; size?: 'sm' | 'md'}) {
  const {data} = useArena();
  const {done, active, detail} = buildSteps(view, data.attempts);
  return (
    <ol className={'stepper ' + size} aria-label="Build steps">
      {STEPS.map((name, i) => {
        const state = done[i] ? 'done' : i === active ? 'active' : 'pending';
        return (
          <li key={name} className={state} title={`${name} · ${detail[i]}`} aria-label={`${name}: ${state === 'done' ? 'done' : state === 'active' ? 'in progress' : 'not started'} · ${detail[i]}`}>
            <i>{done[i] ? <Icon name="check" size={size === 'sm' ? 10 : 14} /> : null}</i>
            <span>{name}</span>
          </li>
        );
      })}
    </ol>
  );
}

// Overall pipeline stage of a request: the least advanced live task, out of the six
// recorded stages. Used for slim stage bars; never a percentage.
export function goalStage(view: GoalView): number {
  const tasks = view.children.length ? view.children : view.tasks;
  const indexes = tasks.map(stageIndex).filter(i => i >= 0);
  if (view.plan && !view.children.length) indexes.push(stageIndex(view.plan));
  if (!indexes.length) return 0;
  const cls = goalClass(view);
  if (cls === 'done') return STAGES.length;
  return Math.min(...indexes);
}

// What one build is doing now, from the snapshot and its story: status, stage, the attempt
// and agent on it, the task in front, an agent line (agent, model, effort, elapsed; else the
// current block and its state) and what the agent is doing, in the host's plain words (the
// story's `now`, read from its newest transcript line), never a raw command. A quiet agent
// says when it last did something instead. The chat build card reads it.
export function useBuildProgress(view: GoalView, story: GoalStory | null) {
  const {data, now} = useArena();
  const status = goalStatus(view);
  const stage = goalStage(view);
  const liveAttempt = liveAttemptOf(view, data);
  const agent = liveAttempt && data.agents.find(a => a.id === liveAttempt.account_id);
  const liveTask = view.tasks.find(t => ACTIVE_TASK.includes(t.state));
  const currentTask = (liveAttempt && view.tasks.find(t => t.id === liveAttempt.task_id)) || liveTask || view.children.find(t => t.state !== 'completed' && t.state !== 'cancelled') || view.plan;
  const block = story?.blocks?.find(b => b.id === currentTask?.id);
  const liveBlock = (liveAttempt && story?.blocks?.find(b => b.id === liveAttempt.task_id)) || undefined;
  const lastLine = !liveBlock ? '' : liveBlock.stalled ? quietLine(liveBlock, data.attempts, now) : liveBlock.now || '';
  const agentLine = liveAttempt
    ? `${agent?.name || 'Agent'} · ${liveAttempt.model}${liveAttempt.effort ? ' · ' + liveAttempt.effort : ''} · ${duration(now - liveAttempt.created)}`
    : currentTask
      ? `${block?.title || stripLegacy(currentTask.title)} · ${block?.state_label || stateLabel(currentTask.state)}`
      : status.label;
  return {status, stage, liveAttempt, currentTask, lastLine, quiet: !!liveBlock?.stalled, agentLine};
}

// Home ------------------------------------------------------------------------------
// Only what matters: one box to talk to the director, what needs the owner, one row per
// project that is building, the projects ready for work, and finished builds folded away
// (where they can be ticked and archived). Archived builds and projects never appear here.

// One row of the away summary: a build, what became of it, a tap into it.
type AwayGroup = 'finished' | 'needs' | 'stopped';
type AwayRow = {key: string; group: AwayGroup; tone: 'done' | 'decision' | 'blocked'; label: string; title: string; sub: string; at: number; open: () => void};

// "Since you were away": what finished, what needs the owner and what stopped since this
// device was last seen by the host. Each build appears once, in one group: one that needs the
// owner is listed for that alone; a finished one only when its blocks finished (a completed
// plan is not a finished build) after the absence began; a stopped one when a block stopped
// then. Builds still building are not listed. Plain English: no commits, no raw reasons (the
// build's story explains a failure).
export function awayRows(data: Snapshot, since: number, now: number, openGoal: (id: string) => void, openNeed: (r: NeedRow) => void): AwayRow[] {
  const views = (data.goals || []).filter(g => !goalArchived(g, data)).map(g => goalView(g, data));
  const needs = new Map<string, NeedRow>(); // the newest need of each build
  const loose: NeedRow[] = [];
  for (const r of needsYouRows(data)) {
    if (r.at < since) continue;
    if (!r.goalId) loose.push(r);
    else if ((needs.get(r.goalId)?.at ?? -1) < r.at) needs.set(r.goalId, r);
  }
  const where = (v: GoalView) => (v.project ? ' · ' + v.project.name : '');
  const rows: AwayRow[] = [...needs.values(), ...loose].map(r => ({key: 'need-' + r.id, group: 'needs', tone: r.tone, label: r.label, title: r.title, sub: `${ago(r.at, now)}${r.project ? ' · ' + r.project : ''} · ${r.sub}`, at: r.at, open: () => openNeed(r)}));
  for (const v of views) {
    if (needs.has(v.goal.id)) continue;
    const cls = goalClass(v);
    if (cls === 'done') {
      const at = finishedAt(v);
      if (at && at >= since) rows.push({key: 'done-' + v.goal.id, group: 'finished', tone: 'done', label: v.tasks.some(t => t.integrated_commit) ? 'Merged into main' : 'Finished', title: v.title, sub: `${ago(at, now)}${where(v)}`, at, open: () => openGoal(v.goal.id)});
    } else if (cls === 'history') {
      const at = stoppedAt(v);
      if (at && at >= since) rows.push({key: 'stopped-' + v.goal.id, group: 'stopped', tone: 'blocked', label: 'Stopped', title: v.title, sub: `${ago(at, now)}${where(v)}`, at, open: () => openGoal(v.goal.id)});
    }
  }
  const order: AwayGroup[] = ['finished', 'needs', 'stopped'];
  return rows.sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group) || b.at - a.at);
}

// The compact banner (a heading and one line of counts, which wraps rather than being cut
// on a narrow phone), expandable into the rows above. Showing it marks the absence as seen
// on the host; it stays until the owner leaves Home or says "Got it".
function AwayBanner({since, openNeed}: {since: number; openNeed: (r: NeedRow) => void}) {
  const {data, now, openGoal, markSeen} = useArena();
  const [open, setOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const rows = awayRows(data, since, now, openGoal, openNeed);
  useEffect(() => {
    markSeen(); // viewed: the host forgets the absence, the banner stays until Home is left
  }, []);
  if (dismissed || !rows.length) return null;
  const count = (group: AwayGroup) => rows.filter(r => r.group === group).length;
  const needs = count('needs');
  const parts = [count('finished') && `${count('finished')} finished`, needs && `${needs} need${needs === 1 ? 's' : ''} you`, count('stopped') && `${count('stopped')} stopped`].filter(Boolean);
  return (
    <section className={'away-panel away-banner' + (open ? ' open' : '')} aria-label="Since you were away">
      <div className="away-line">
        <button type="button" className="away-toggle" aria-expanded={open} onClick={() => setOpen(o => !o)}>
          <LiveDot tone="ok" />
          <span className="away-text">
            <b>Since you were away</b>
            <span className="away-counts muted">{parts.join(' · ')}</span>
          </span>
          <Icon name="arrow" size={16} />
        </button>
        <button type="button" className="text-btn" onClick={() => setDismissed(true)}>
          Got it
        </button>
      </div>
      {open && (
        <div className="away-rows">
          <p className="small muted away-since">Last here {ago(since, now)}</p>
          {rows.slice(0, 12).map(r => (
            <button className="list-row press" key={r.key} onClick={r.open}>
              <Glyph tone={r.tone} size={30} />
              <span className="list-main">
                <b className="clamp2">{r.title}</b>
                <span className="small muted clamp2">
                  <span className={'tone ' + r.tone}>{r.label}</span> · {r.sub}
                </span>
              </span>
              <Icon name="arrow" size={18} />
            </button>
          ))}
        </div>
      )}
    </section>
  );
}

// The conversational input: typing or dictating here talks to the director. Sending opens
// Chat with the turn under way (in the current conversation, else a new one on the
// project the host picks).
function AskCard() {
  const {openChat, online} = useArena();
  const [text, setText] = useState('');
  const box = useRef<HTMLTextAreaElement>(null);
  const speech = useSpeech(t => setText(d => (d.trim() ? d.replace(/\s+$/, '') + ' ' : '') + t));
  // The box fits its text (or its placeholder) up to four lines, also after the screen
  // changes width (a phone turned round).
  useEffect(() => {
    const fit = () => {
      const el = box.current;
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 4 * 24 + 16) + 'px';
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [text]);
  const submit = () => {
    const t = text.trim();
    if (!t || !online) return;
    setText('');
    openChat({text: t});
  };
  return (
    <form
      className={'ask-card' + (speech.supported ? '' : ' no-mic')}
      onSubmit={e => {
        e.preventDefault();
        submit();
      }}
    >
      {speech.supported && (
        <button type="button" className={'ask-mic' + (speech.listening ? ' on' : '')} aria-pressed={speech.listening} aria-label={speech.listening ? 'Stop dictating' : 'Dictate'} title={speech.listening ? 'Stop dictating' : 'Dictate'} onClick={speech.toggle}>
          <Icon name={speech.listening ? 'stop' : 'mic'} size={22} />
        </button>
      )}
      <label htmlFor="home-ask" className="sr-only">
        Tell Arena what to build
      </label>
      <textarea
        id="home-ask"
        ref={box}
        rows={1}
        value={text}
        onChange={e => setText(e.target.value)}
        onKeyDown={e => {
          if (e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
          e.preventDefault();
          submit();
        }}
        placeholder="Tell Arena what to build…"
        enterKeyHint="send"
        autoComplete="off"
      />
      <button type="submit" className="send-btn" aria-label="Send to Arena" disabled={!text.trim() || !online}>
        <Icon name="send" size={18} />
      </button>
      {(speech.listening || speech.error) && (
        <p className={'ask-note ' + (speech.error ? 'warn' : '')} aria-live="polite">
          {speech.error || (speech.interim ? speech.interim : 'Listening…')}
        </p>
      )}
    </form>
  );
}

// The stories of the builds Home shows as building: their progress, and which agents have
// gone quiet. Read when one of those builds' own records change (at most once every 4 s), every
// 30 s while Home is open, and never while the page is hidden (once on return instead).
// Without an answer the snapshot's reckoning stands in; a failed read keeps the last answer.
function useStories(ids: string[], signal: string): Record<string, GoalStory> {
  const {get} = useArena();
  const getRef = useRef(get);
  getRef.current = get;
  const idsRef = useRef(ids);
  idsRef.current = ids;
  const [stories, setStories] = useState<Record<string, GoalStory>>({});
  const last = useRef(0);
  const owed = useRef(false);
  const timer = useRef(0);
  const inflight = useRef(false);
  const again = useRef(false);
  const run = useCallback(async (): Promise<void> => {
    window.clearTimeout(timer.current);
    timer.current = 0;
    const list = idsRef.current;
    if (!list.length) return;
    if (document.visibilityState === 'hidden') {
      owed.current = true;
      return;
    }
    // One round at a time; a change during a slow round is read right after it.
    if (inflight.current) {
      again.current = true;
      return;
    }
    inflight.current = true;
    owed.current = false;
    last.current = Date.now();
    const out: Record<string, GoalStory> = {};
    await Promise.all(
      list.map(async id => {
        try {
          const s = (await getRef.current(`goals/${id}/story`)) as GoalStory;
          if (s && s.progress) out[id] = s;
        } catch {
          /* an older host or a transient error: the last answer or the snapshot stands in */
        }
      }),
    );
    setStories(prev => {
      const next: Record<string, GoalStory> = {};
      for (const id of idsRef.current) if (out[id] || prev[id]) next[id] = out[id] || prev[id];
      return next;
    });
    inflight.current = false;
    if (again.current) {
      again.current = false;
      timer.current = window.setTimeout(run, Math.max(0, 4000 - (Date.now() - last.current)));
    }
  }, []);
  const key = ids.join(',');
  useEffect(() => {
    if (!ids.length || timer.current) return;
    timer.current = window.setTimeout(run, Math.max(0, 4000 - (Date.now() - last.current)));
  }, [key, signal]);
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible' && owed.current) run();
    };
    document.addEventListener('visibilitychange', onVisible);
    const poll = window.setInterval(() => Date.now() - last.current >= 25000 && run(), 30000);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.clearInterval(poll);
      window.clearTimeout(timer.current);
      timer.current = 0;
    };
  }, [run]);
  return stories;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

// A section heading in the reference's style: spaced capitals and a count pill. A
// collapsible one is a disclosure button inside the heading that says whether it is open.
function SectionHead({title, count, open, onToggle, children}: {title: string; count: number; open?: boolean; onToggle?: () => void; children?: React.ReactNode}) {
  const inner = (
    <>
      <span className="section-name">{title}</span>
      <span className="count-pill num">{count}</span>
      {onToggle && <Icon name="arrow" size={16} />}
    </>
  );
  return (
    <div className="section-head">
      <h2 className="section-label">
        {onToggle ? (
          <button type="button" className={'section-toggle' + (open ? ' open' : '')} aria-expanded={open} onClick={onToggle}>
            {inner}
          </button>
        ) : (
          inner
        )}
      </h2>
      {children}
    </div>
  );
}

type ProjectBuild = {project: RecordData; views: GoalView[]};

export function Overview() {
  const {data, now, openGoal, openChat, setModal, search, awaySince, post, notify, disabled} = useArena();
  // Captured per absence: marking it seen must not remove the banner mid-read, and coming
  // back to the app while Home is open shows the new absence.
  const [away, setAway] = useState(awaySince);
  useEffect(() => {
    if (awaySince !== null) setAway(awaySince);
  }, [awaySince]);
  const [readyOpen, setReadyOpen] = useState(true);
  const [finishedOpen, setFinishedOpen] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [archiving, setArchiving] = useState(false);
  const q = search.trim().toLowerCase();
  const matches = (text: string) => !q || text.toLowerCase().includes(q);

  const views = (data.goals || [])
    .filter(g => !goalArchived(g, data))
    .map(g => goalView(g, data))
    .sort((a, b) => b.goal.created - a.goal.created);
  const projects = pickableProjects(data);

  // One row per project with live work: its newest live build is the one a tap opens.
  const building: ProjectBuild[] = [];
  for (const v of views.filter(isLive)) {
    const row = building.find(b => b.project.id === v.goal.project_id);
    if (row) row.views.push(v);
    else if (v.project) building.push({project: v.project, views: [v]});
  }
  const shownBuilding = building.filter(b => matches(`${b.project.name} ${b.views.map(v => v.title).join(' ')}`));
  const buildingIds = new Set(building.map(b => b.project.id));
  const ready = projects.filter(p => !buildingIds.has(p.id) && matches(p.name));
  const finished = views.filter(v => !isLive(v) && ['done', 'history'].includes(goalClass(v)) && matches(`${v.title} ${v.project?.name || ''}`)).sort((a, b) => lastChange(b) - lastChange(a));
  const stories = useStories(
    building.map(b => b.views[0].goal.id),
    building.map(b => goalSignal(b.views[0], data)).join('/'),
  );

  // What a Select can act on now: finished builds and Ready projects on screen. A ticked row
  // that starts building, or leaves the screen, drops out of the selection.
  const selectable = [...finished.map(v => 'g:' + v.goal.id), ...ready.map(p => 'p:' + p.id)];
  const selectableKey = selectable.join(',');
  useEffect(() => {
    const keep = new Set(selectable);
    setPicked(s => ([...s].every(k => keep.has(k)) ? s : new Set([...s].filter(k => keep.has(k)))));
  }, [selectableKey]);
  const chosen = [...picked].filter(k => selectable.includes(k));

  // The shared list (archived builds and projects never ask for anything).
  const needRows = needsYouRows(data);
  const stale = (t: number) => t < now - 86400;
  const fresh = needRows.filter(r => !stale(r.at));
  const earlier = needRows.filter(r => stale(r.at));
  const openNeed = (r: NeedRow) => {
    if (r.kind === 'review') setModal({kind: 'review', id: r.id});
    else if (r.goalId) openGoal(r.goalId);
    else setModal({kind: 'task', id: r.id});
  };
  const needRow = (r: NeedRow) => (
    <button className="list-row press" key={r.id} onClick={() => openNeed(r)}>
      <Glyph tone={r.tone} size={32} />
      <span className="list-main">
        <b className="clamp2">{r.title}</b>
        <span className="small muted clamp2">
          <span className={'tone ' + r.tone}>{r.label}</span> · {ago(r.at, now)}
          {r.project ? ` · ${r.project}` : ''} · {r.sub}
        </span>
      </span>
      <Icon name="arrow" size={18} />
    </button>
  );

  const toggle = (key: string) =>
    setPicked(s => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const startSelecting = () => {
    setSelecting(true);
    setFinishedOpen(true);
    setReadyOpen(true);
  };
  const stopSelecting = () => {
    setSelecting(false);
    setPicked(new Set());
  };
  const selectAll = () => setPicked(s => new Set([...s, ...finished.map(v => 'g:' + v.goal.id)]));
  const archiveSelected = async () => {
    if (archiving || !chosen.length) return;
    setArchiving(true);
    const goalIds = chosen.filter(k => k.startsWith('g:')).map(k => k.slice(2));
    const projectIds = chosen.filter(k => k.startsWith('p:')).map(k => k.slice(2));
    let builds = 0;
    let projectsDone = 0;
    const left: string[] = [];
    try {
      if (goalIds.length) {
        const r = await post('goals/archive', {ids: goalIds});
        builds = (r?.archived || []).length;
        const reasons = new Map<string, number>();
        for (const s of r?.skipped || []) reasons.set(s.reason || 'skipped', (reasons.get(s.reason || 'skipped') || 0) + 1);
        for (const [reason, n] of reasons) left.push(`${plural(n, 'build')} ${reason}`);
      }
      for (const id of projectIds) {
        const p = data.projects.find(x => x.id === id);
        if (!p) continue;
        try {
          await post(`projects/${id}/archive`, {revision: p.rev});
          projectsDone += 1;
        } catch (e) {
          const why = plainError(e).replace(/\.$/, '');
          left.push(`${p.name} (${why[0].toLowerCase()}${why.slice(1)})`);
        }
      }
      const done = [builds && plural(builds, 'build'), projectsDone && plural(projectsDone, 'project')].filter(Boolean).join(' and ');
      notify(`${done ? `Archived ${done}. Restore from Archived in the menu.` : 'Nothing was archived.'}${left.length ? ` Left alone: ${left.join('; ')}.` : ''}`);
      stopSelecting();
    } catch (e) {
      notify(plainError(e));
    } finally {
      setArchiving(false);
    }
  };

  // The newest build of a project (on Home), its plain outcome, and where a tap lands.
  const latestOf = (projectId: string) => views.find(v => v.goal.project_id === projectId);
  const readySub = (p: RecordData) => {
    const v = latestOf(p.id);
    const paused = p.enabled === false ? 'Paused · ' : '';
    if (!v) return paused + 'No builds yet';
    return paused + outcomeLine(v, now).label;
  };

  return (
    <div className="home-screen">
      <AskCard />

      {away !== null && <AwayBanner key={away} since={away} openNeed={openNeed} />}

      {needRows.length > 0 && (
        <Panel title="Needs you" aside={<span className="count num">{needRows.length}</span>} className="accent-amber home-needs">
          {fresh.map(needRow)}
          {earlier.length > 0 && (
            <details className="earlier" open={!fresh.length || undefined}>
              <summary>
                Earlier <span className="count num">{earlier.length}</span>
              </summary>
              {earlier.map(needRow)}
            </details>
          )}
        </Panel>
      )}

      <section className="home-section" aria-label="Active builds">
        <SectionHead title="Active builds" count={shownBuilding.length} />
        {shownBuilding.map(({project, views: live}) => {
          const v = live[0];
          const story = stories[v.goal.id];
          const progress = storyProgress(story, workProgress(v, data));
          // Agents at work, counted as the story counts them (not one still starting).
          const agents = agentsWorking(new Set(live.flatMap(x => x.tasks.map(t => t.id as string))), data);
          // An agent the host calls stalled is quiet, not working (its story says how long).
          const quiet = (story?.blocks || []).filter(b => b.stalled && b.state === 'running');
          const working = Math.max(0, agents - quiet.length);
          const quietFor = quiet.length ? `quiet for ${Math.max(...quiet.map(b => quietMinutes(b, data.attempts, now)))} min` : '';
          const doing = working
            ? `${plural(working, 'agent')} working${quiet.length ? ` · ${quiet.length} ${quietFor}` : ''}`
            : quiet.length
              ? `${plural(quiet.length, 'agent')} ${quietFor}`
              : live.some(isMerging)
                ? 'Merging into main'
                : 'Waiting for an agent';
          const started = `Started ${ago(v.goal.created, now)}`;
          return (
            <div className="home-row-wrap" key={project.id}>
              {selecting && (
                <span className="check-slot">
                  <input type="checkbox" disabled aria-label={`Building ${project.name} is running and cannot be archived`} />
                  <span className="small muted">running</span>
                </span>
              )}
              <button type="button" className="home-row active-row" onClick={() => openGoal(v.goal.id)}>
                <span className="tile-icon" aria-hidden="true">
                  <Icon name="build" size={22} />
                </span>
                <span className="home-row-main">
                  <b className="clamp2">Building {project.name}</b>
                  <span className="small row-meta">
                    <LiveDot tone={working ? 'ok' : quiet.length ? 'warn' : 'off'} />
                    <span className="clamp2">
                      {doing} · {started}
                      {live.length > 1 ? ` · ${live.length} builds` : ''}
                    </span>
                  </span>
                  {progress.total > 0 ? (
                    <>
                      <span aria-hidden="true">
                        <Progress done={progress.done} total={progress.total} fraction={progressFraction(progress)} label="" />
                      </span>
                      <span className="sr-only">{progressLabel(progress)}</span>
                    </>
                  ) : (
                    <span className="small muted">Planning the work</span>
                  )}
                </span>
                <Icon name="arrow" size={20} />
              </button>
            </div>
          );
        })}
        {!shownBuilding.length && <p className="home-empty small muted">{q ? 'No active build matches the search.' : 'Nothing is building right now. Tell Arena what to build above.'}</p>}
      </section>

      <section className="home-section" aria-label="Ready">
        <SectionHead title="Ready" count={ready.length} open={readyOpen} onToggle={() => setReadyOpen(o => !o)} />
        {readyOpen && (
          <div className="home-list">
            {ready.map(p => {
              const latest = latestOf(p.id);
              const key = 'p:' + p.id;
              return (
                <div className="home-row-wrap" key={p.id}>
                  {selecting ? (
                    <label className="home-row ready-row selectable">
                      <input type="checkbox" checked={picked.has(key)} onChange={() => toggle(key)} aria-label={`Select project ${p.name}`} />
                      <span className="tile-icon sm" aria-hidden="true">
                        <Icon name="build" size={18} />
                      </span>
                      <span className="home-row-main">
                        <b className="clamp1">{p.name}</b>
                        <span className="small muted clamp1">{readySub(p)}</span>
                      </span>
                    </label>
                  ) : (
                    <>
                      <button type="button" className="home-row ready-row" onClick={() => (latest ? openGoal(latest.goal.id) : openChat({projectId: p.id}))}>
                        <span className="tile-icon sm" aria-hidden="true">
                          <Icon name="build" size={18} />
                        </span>
                        <span className="home-row-main">
                          <b className="clamp1">{p.name}</b>
                          <span className="small muted clamp1">{readySub(p)}</span>
                        </span>
                      </button>
                      <button type="button" className="play-btn" aria-label={`Chat about ${p.name}`} onClick={() => openChat({projectId: p.id})}>
                        <Icon name="play" size={18} />
                      </button>
                    </>
                  )}
                </div>
              );
            })}
            {!ready.length && <p className="home-empty small muted">{q ? 'No project matches the search.' : projects.length ? 'Every project is building.' : 'No projects are enrolled on the Mac yet.'}</p>}
          </div>
        )}
      </section>

      <section className="home-section" aria-label="Finished">
        <SectionHead title="Finished" count={finished.length} open={finishedOpen} onToggle={() => setFinishedOpen(o => !o)}>
          {(finished.length > 0 || ready.length > 0) && (
            <button type="button" className="text-btn" onClick={selecting ? stopSelecting : startSelecting}>
              {selecting ? 'Cancel' : 'Select'}
            </button>
          )}
        </SectionHead>
        {finishedOpen && (
          <div className="home-list">
            {finished.slice(0, 40).map(v => {
              const out = outcomeLine(v, now);
              const key = 'g:' + v.goal.id;
              const body = (
                <>
                  <Glyph tone={out.tone} size={30} />
                  <span className="home-row-main">
                    <b className="clamp2">{v.title}</b>
                    <span className="small muted clamp2">
                      {v.project ? `${v.project.name} · ` : ''}
                      <span className={'tone ' + out.tone}>{out.label}</span>
                    </span>
                  </span>
                </>
              );
              return (
                <div className="home-row-wrap" key={v.goal.id}>
                  {selecting ? (
                    <label className="home-row finished-row selectable">
                      <input type="checkbox" checked={picked.has(key)} onChange={() => toggle(key)} aria-label={`Select ${v.title}`} />
                      {body}
                    </label>
                  ) : (
                    <button type="button" className="home-row finished-row" onClick={() => openGoal(v.goal.id)}>
                      {body}
                      <Icon name="arrow" size={18} />
                    </button>
                  )}
                </div>
              );
            })}
            {!finished.length && <p className="home-empty small muted">Finished and stopped builds collect here.</p>}
          </div>
        )}
      </section>

      {selecting && (
        <div className="select-bar" role="group" aria-label="Archive selection">
          <button type="button" className="btn" onClick={selectAll} disabled={!finished.length}>
            Select all
          </button>
          <button type="button" className="btn primary" disabled={disabled || archiving || !chosen.length} onClick={archiveSelected}>
            <Icon name="archive" size={16} />
            Archive selected ({chosen.length})
          </button>
        </div>
      )}
    </div>
  );
}
