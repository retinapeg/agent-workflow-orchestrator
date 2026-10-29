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
  liveAttemptOf,
  needsYouRows,
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
  type MacActivity,
  type NeedRow,
  type RecordData,
} from '../types';
import {Glyph, Icon, LiveDot, Progress} from '../ui';

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
// Only what matters, the same at every width: the box to talk to the director, one line about
// what is running on the Mac, what needs the owner (only when something does), one row per
// project with live work (or one calm line when nothing is building), and one line about work
// that finished recently. Everything else (history, archive, settings) is in the ☰ menu.

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
const DAY = 86400;

// The conversational input: typing or dictating here talks to the director. Sending opens
// Chat with the turn under way (in the current conversation, else a new one on the
// project the host picks). Asking a question never starts a build: only the director does
// that, on an explicit request to build.
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
        Talk to Arena
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
        placeholder="Talk to Arena…"
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

// "2 Arena agents · 1 Claude Code session": what a look at the Mac found, counted from its
// groups. Arena's own work first (an agent Arena records as running but whose process was not
// found is counted apart, never as working), then what was discovered on the Mac, verified or
// not. Nothing found: "nothing running".
const NO_PROCESS = /no agent process was found/i;
export function macCounts(look: MacActivity): string {
  const groups = look.groups || {};
  const managed = groups.managed || [];
  const missing = managed.filter(a => NO_PROCESS.test(a.label || '')).length;
  const found = [...(groups.external || []), ...(groups.unknown || [])];
  const count = (kinds: string[]) => found.filter(a => kinds.includes(a.kind)).length;
  const known = ['claude', 'codex', 'tests', 'dev_server'];
  const parts = [
    [managed.length - missing, 'Arena agent'],
    [count(['claude']), 'Claude Code session'],
    [count(['codex']), 'Codex session'],
    [found.filter(a => !known.includes(a.kind)).length, 'other coding agent'],
    [count(['tests']), 'test run'],
    [count(['dev_server']), 'dev server'],
  ] as [number, string][];
  const out = parts.filter(([n]) => n > 0).map(([n, word]) => plural(n, word));
  if (missing) out.push(`${plural(missing, 'Arena agent')} not found`);
  return out.join(' · ') || 'nothing running';
}

// One small line about the Mac, from a fresh read-only look when Home opens and when the app
// comes back to the foreground (never polled). A tap opens the full view; a failed look says
// so and a tap tries again.
function MacLine() {
  const {get, setTab} = useArena();
  const getRef = useRef(get);
  getRef.current = get;
  const [look, setLook] = useState<{data: MacActivity | null; failed: boolean; busy: boolean}>({data: null, failed: false, busy: true});
  const inflight = useRef(false);
  const load = useCallback(async () => {
    if (inflight.current) return;
    inflight.current = true;
    setLook(l => ({...l, busy: true}));
    try {
      const data = (await getRef.current('mac-activity')) as MacActivity;
      setLook({data, failed: false, busy: false});
    } catch {
      setLook({data: null, failed: true, busy: false});
    } finally {
      inflight.current = false;
    }
  }, []);
  useEffect(() => {
    if (document.visibilityState !== 'hidden') load();
    const onVisible = () => {
      if (document.visibilityState === 'visible') load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [load]);
  const retry = look.failed && !look.busy;
  const text = look.data ? macCounts(look.data) : retry ? "couldn't check — tap to retry" : 'checking…';
  return (
    <button type="button" className={'mac-line' + (retry ? ' failed' : '')} aria-busy={look.busy} onClick={() => (retry ? load() : setTab('mac'))}>
      <Icon name={retry ? 'refresh' : 'mac'} size={16} />
      <span className="mac-line-text">On your Mac: {text}</span>
      {!retry && <Icon name="arrow" size={14} />}
    </button>
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

// Work that ended in the last 24 hours, in one line: builds whose blocks all finished, and
// builds that stopped (a part blocked or failed to merge). Archived builds are not counted;
// cancelled ones were the owner's own decision and are not news.
export function recentLine(views: GoalView[], now: number): string {
  const since = now - DAY;
  const ended = views.filter(v => !isLive(v));
  const finished = ended.filter(v => goalClass(v) === 'done' && finishedAt(v) >= since).length;
  const stopped = ended.filter(v => goalClass(v) === 'history' && stoppedAt(v) >= since).length;
  return `Finished recently: ${finished ? plural(finished, 'build') : 'none'}${stopped ? ` · ${stopped} stopped` : ''}`;
}

type ProjectBuild = {project: RecordData; views: GoalView[]};

export function Overview() {
  const {data, now, openGoal, setModal, setTab} = useArena();
  const views = (data.goals || [])
    .filter(g => !goalArchived(g, data))
    .map(g => goalView(g, data))
    .sort((a, b) => b.goal.created - a.goal.created);

  // One row per project with live work: its newest live build is the one a tap opens.
  // Finished, stopped and older work never appears here.
  const building: ProjectBuild[] = [];
  for (const v of views.filter(isLive)) {
    const row = building.find(b => b.project.id === v.goal.project_id);
    if (row) row.views.push(v);
    else if (v.project) building.push({project: v.project, views: [v]});
  }
  const stories = useStories(
    building.map(b => b.views[0].goal.id),
    building.map(b => goalSignal(b.views[0], data)).join('/'),
  );

  // What waits on the owner (archived builds and projects never ask for anything), newest
  // first: one tap opens the decision, the question or the build that needs a sign-in.
  const needRows = needsYouRows(data);
  const openNeed = (r: NeedRow) => {
    if (r.kind === 'review') setModal({kind: 'review', id: r.id});
    else if (r.goalId) openGoal(r.goalId);
    else setModal({kind: 'task', id: r.id});
  };

  return (
    <div className="home-screen">
      <AskCard />
      <MacLine />

      {needRows.length > 0 && (
        <section className="home-needs" aria-label="Needs you">
          {needRows.map(r => (
            <button type="button" className="need-row" key={r.id} onClick={() => openNeed(r)}>
              <Glyph tone={r.tone} size={30} />
              <span className="need-main">
                <b className="clamp2">{r.title}</b>
                <span className="small muted clamp2">
                  <span className={'tone ' + r.tone}>{r.label}</span> · {ago(r.at, now)}
                  {r.project ? ` · ${r.project}` : ''} · {r.sub}
                </span>
              </span>
              <Icon name="arrow" size={18} />
            </button>
          ))}
        </section>
      )}

      {building.length > 0 ? (
        <section className="home-active" aria-labelledby="home-active-label">
          <h2 className="tiny-label" id="home-active-label">
            Active
          </h2>
          <div className="home-list">
            {building.map(({project, views: live}) => {
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
              return (
                <button type="button" className="home-row active-row" key={project.id} onClick={() => openGoal(v.goal.id)}>
                  <span className="tile-icon" aria-hidden="true">
                    <Icon name="build" size={22} />
                  </span>
                  <span className="home-row-main">
                    <b className="clamp2">Building {project.name}</b>
                    <span className="small row-meta">
                      <LiveDot tone={working ? 'ok' : quiet.length ? 'warn' : 'off'} />
                      <span className="clamp2">
                        {doing} · <span className="nowrap">Started {ago(v.goal.created, now)}</span>
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
              );
            })}
          </div>
        </section>
      ) : (
        <div className="home-idle">
          <p className="idle-line">Nothing is building right now.</p>
          <p className="idle-hint">Tell Arena what to build.</p>
        </div>
      )}

      <button type="button" className="finished-line" onClick={() => setTab('builds')}>
        <span>{recentLine(views, now)}</span>
        <Icon name="arrow" size={14} />
      </button>
    </div>
  );
}
