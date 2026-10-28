import React, {useEffect, useState} from 'react';
import {useArena} from '../App';
import {liveLineText, useLiveTail} from '../live';
import {
  ACTIVE_TASK,
  HISTORY_TASK,
  QUEUED_TASK,
  STAGES,
  STAGES_SHORT,
  ago,
  duration,
  eventLine,
  goalClass,
  goalElapsed,
  goalStatus,
  goalView,
  liveAttemptOf,
  milestoneTree,
  needsYouRows,
  providerGlyph,
  providersOf,
  shortSha,
  stageIndex,
  stateLabel,
  when,
  type GoalView,
  type NeedRow,
  type RecordData,
  type Snapshot,
} from '../types';
import {Chip, Empty, Glyph, Icon, Panel, Provider, StageBar} from '../ui';

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

export function StageRail({view}: {view: GoalView}) {
  const tasks = view.children.length ? view.children : view.tasks;
  const indexes = tasks.map(stageIndex).filter(i => i >= 0);
  if (view.plan && !view.children.length) indexes.push(stageIndex(view.plan));
  const min = indexes.length ? Math.min(...indexes) : -1;
  return (
    <ol className="rail" aria-label="Pipeline stages">
      {STAGES.map((name, i) => {
        const planDone = i === 0 && (view.children.length > 0 || view.plan?.state === 'completed');
        const done = planDone || (indexes.length > 0 && min > i);
        const active = !done && indexes.includes(i);
        return (
          <li key={name} className={done ? 'done' : active ? 'active' : ''}>
            <i />
            <span className="full">{name}</span>
            <span className="abbr" aria-hidden="true">
              {STAGES_SHORT[i]}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// What one build is doing now, from the snapshot and (while an attempt runs) its live tail:
// status, stage, the attempt and agent on it, the task in front, an agent line (agent,
// model, effort, elapsed; else the current task and its state) and the last line the agent
// produced. Home's Active builds and the chat build card both read it.
export function useBuildProgress(view: GoalView) {
  const {data, now} = useArena();
  const status = goalStatus(view);
  const stage = goalStage(view);
  const liveAttempt = liveAttemptOf(view, data);
  const agent = liveAttempt && data.agents.find(a => a.id === liveAttempt.account_id);
  const tail = useLiveTail(liveAttempt?.id);
  const liveTask = view.tasks.find(t => ACTIVE_TASK.includes(t.state));
  const currentTask = (liveAttempt && view.tasks.find(t => t.id === liveAttempt.task_id)) || liveTask || view.children.find(t => t.state !== 'completed' && t.state !== 'cancelled') || view.plan;
  const lastLine = tail?.lines.length ? liveLineText(tail.lines[tail.lines.length - 1], agent?.name || 'Agent') : '';
  const agentLine = liveAttempt
    ? `${agent?.name || 'Agent'} · ${liveAttempt.model}${liveAttempt.effort ? ' · ' + liveAttempt.effort : ''} · ${duration(now - liveAttempt.created)}`
    : currentTask
      ? `${currentTask.title} · ${stateLabel(currentTask.state)}`
      : status.label;
  return {status, stage, liveAttempt, currentTask, lastLine, agentLine};
}

// One live build: project, title, stage bar, the agent and model on it, elapsed, and the
// last line the agent produced (from the live tail while an attempt runs).
function BuildRow({view}: {view: GoalView}) {
  const {data, now, openGoal} = useArena();
  const {status, stage, liveAttempt, lastLine, agentLine} = useBuildProgress(view);
  const providers = providersOf(view, data);
  const tree = milestoneTree(view, data);
  return (
    <button className="build-row" onClick={() => openGoal(view.goal.id)}>
      <Glyph tone={status.tone} />
      <span className="build-main">
        <span className="build-name">
          <b>{view.project?.name || 'Project'}</b>
          <span className="dash" aria-hidden="true">
            –
          </span>
          <span className="clamp1">{view.title}</span>
        </span>
        <StageBar stage={stage} total={STAGES.length} tone={status.tone} label={`Stage ${Math.min(stage + 1, STAGES.length)} of ${STAGES.length}: ${STAGES[Math.min(stage, STAGES.length - 1)]}`} />
        <span className="build-meta small">
          <Chip tone={status.tone}>{status.label}</Chip>
          <span className="num">{duration(goalElapsed(view, now))}</span>
          {tree.has ? (
            <span className="muted num">
              {tree.done}/{tree.total} milestones
            </span>
          ) : view.children.length ? (
            <span className="muted num">
              {view.children.filter(t => t.state === 'completed').length}/{view.children.length} tasks
            </span>
          ) : null}
        </span>
        <span className="small muted clamp1 build-agent">{agentLine}</span>
        {lastLine && (
          <span className="small build-last clamp1" aria-live="polite">
            {lastLine}
          </span>
        )}
      </span>
      <span className="build-side">
        <span className="providers">
          {providers.map(p => (
            <Provider key={p} glyph={providerGlyph(p)} size="sm" on={!!liveAttempt} />
          ))}
        </span>
        <Icon name="arrow" size={18} />
      </span>
    </button>
  );
}

function RecentRow({view}: {view: GoalView}) {
  const {now, openGoal} = useArena();
  const status = goalStatus(view);
  const last = Math.max(view.goal.created, ...view.tasks.map(t => t.updated || 0));
  return (
    <button className="list-row press recent" onClick={() => openGoal(view.goal.id)}>
      <Glyph tone={status.tone} size={32} />
      <span className="list-main">
        <b className="clamp2">{view.title}</b>
        <span className="small muted">
          <span className={'tone ' + status.tone}>{status.label}</span> · {ago(last, now)}
          {view.project ? ` · ${view.project.name}` : ''}
        </span>
      </span>
      <Icon name="arrow" size={18} />
    </button>
  );
}

// One row of the away summary: a request, what became of it, a tap into its build.
type AwayRow = {key: string; tone: 'done' | 'decision' | 'blocked'; label: string; title: string; sub: string; open: () => void};

// "Since you were away": what finished, what needs the owner and what failed since this
// device was last seen by the host, each a tap into its build. Rendering it marks the
// absence as seen on the host; the panel stays until the owner leaves Home or dismisses it.
export function awayRows(data: Snapshot, since: number, now: number, openGoal: (id: string) => void, openNeed: (r: NeedRow) => void): AwayRow[] {
  const views = (data.goals || []).map(g => goalView(g, data)).filter(v => !v.archived);
  const byGoal = new Map(views.map(v => [v.goal.id, v]));
  const rows: AwayRow[] = [];
  const latest = (tasks: RecordData[]) => Math.max(...tasks.map(t => (t.updated as number) || 0));
  const grouped = (keep: (t: RecordData) => boolean) => {
    const out = new Map<string, RecordData[]>();
    for (const t of data.tasks) {
      if (!t.goal_id || !byGoal.has(t.goal_id) || (t.updated || 0) < since || !keep(t)) continue;
      out.set(t.goal_id, [...(out.get(t.goal_id) || []), t]);
    }
    return out;
  };
  for (const [goalId, tasks] of grouped(t => t.state === 'completed')) {
    const v = byGoal.get(goalId)!;
    const sha = tasks.map(t => t.integrated_commit).find(Boolean) || v.tasks.map(t => t.integrated_commit).find(Boolean);
    rows.push({key: 'done-' + goalId, tone: 'done', label: sha ? `Integrated · ${shortSha(sha, 7)}` : 'Completed', title: v.title, sub: `${ago(latest(tasks), now)}${v.project ? ' · ' + v.project.name : ''}`, open: () => openGoal(goalId)});
  }
  for (const r of needsYouRows(data).filter(r => r.at >= since)) {
    rows.push({key: 'need-' + r.id, tone: r.tone, label: r.label, title: r.title, sub: `${ago(r.at, now)}${r.project ? ' · ' + r.project : ''} · ${r.sub}`, open: () => openNeed(r)});
  }
  for (const [goalId, tasks] of grouped(t => HISTORY_TASK.includes(t.state) && t.state !== 'cancelled')) {
    const v = byGoal.get(goalId)!;
    const last = [...tasks].sort((a, b) => b.updated - a.updated)[0];
    rows.push({key: 'failed-' + goalId, tone: 'blocked', label: stateLabel(last.state), title: v.title, sub: `${ago(last.updated, now)}${v.project ? ' · ' + v.project.name : ''}${last.reason ? ' · ' + last.reason : ''}`, open: () => openGoal(goalId)});
  }
  return rows;
}

function AwayPanel({since, openNeed}: {since: number; openNeed: (r: NeedRow) => void}) {
  const {data, now, openGoal, markSeen} = useArena();
  const [dismissed, setDismissed] = useState(false);
  const rows = awayRows(data, since, now, openGoal, openNeed);
  useEffect(() => {
    markSeen(); // viewed: the host forgets the absence, this panel stays until Home is left
  }, []);
  if (dismissed || !rows.length) return null;
  return (
    <Panel
      title="Since you were away"
      eyebrow={`Last here ${ago(since, now)}`}
      className="away-panel accent-cobalt"
      aside={
        <button type="button" className="text-btn" onClick={() => setDismissed(true)}>
          Got it
        </button>
      }
    >
      {rows.slice(0, 12).map(r => (
        <button className="list-row press" key={r.key} onClick={r.open}>
          <Glyph tone={r.tone} size={32} />
          <span className="list-main">
            <b className="clamp2">{r.title}</b>
            <span className="small muted clamp2">
              <span className={'tone ' + r.tone}>{r.label}</span> · {r.sub}
            </span>
          </span>
          <Icon name="arrow" size={18} />
        </button>
      ))}
    </Panel>
  );
}

// Home: status first. What needs the owner, what is building, one button to ask for more,
// then what finished. Requests on archived projects stay out of the way.
export function Overview() {
  const {data, now, disabled, setModal, openGoal, search, awaySince} = useArena();
  // Captured once per visit: marking the absence seen must not remove the panel mid-read.
  const [away] = useState(awaySince);
  const q = search.trim().toLowerCase();
  const tasks = data.tasks;
  const working = tasks.filter(t => ACTIVE_TASK.includes(t.state) || t.state === 'integrating');
  const queued = tasks.filter(t => QUEUED_TASK.includes(t.state));
  const day = tasks.filter(t => t.state === 'completed' && t.updated >= now - 86400);
  const integrated24 = day.filter(t => t.integrated_commit).length;
  const views = (data.goals || [])
    .map(g => goalView(g, data))
    .filter(v => !v.archived)
    .sort((a, b) => b.goal.created - a.goal.created);
  const matches = (v: GoalView) => !q || `${v.title} ${v.goal.request} ${v.goal.title || ''} ${v.project?.name || ''} ${v.tasks.map(t => t.title).join(' ')}`.toLowerCase().includes(q);
  const active = views.filter(v => ['active', 'needs'].includes(goalClass(v)) && matches(v));
  const recent = views.filter(v => goalClass(v) === 'done' && matches(v)).slice(0, 6);
  const stale = (t: number) => t < now - 86400;
  const needRows = needsYouRows(data);
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
  const summary = [
    working.length ? `${working.length} running` : data.paused ? 'dispatch paused' : 'nothing running',
    queued.length ? `${queued.length} queued` : '',
    day.length ? `${day.length} completed today${integrated24 ? ` (${integrated24} integrated)` : ''}` : '',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="home-main">
      <div className="page-head">
        <div>
          <h1 className="page-title">Build control.</h1>
          <p className="page-sub">{summary}</p>
        </div>
      </div>

      {away !== null && <AwayPanel since={away} openNeed={openNeed} />}

      {needRows.length > 0 && (
        <Panel title="Needs you" aside={<span className="count num">{needRows.length}</span>} className="accent-amber">
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

      <Panel title="Active builds" aside={<span className="count num">{active.length} active</span>}>
        <div className="build-list">
          {active.map(v => (
            <BuildRow key={v.goal.id} view={v} />
          ))}
          {!active.length && (
            <div className="panel-body">
              <Empty title={q ? 'No active builds match' : 'No builds running'}>
                {q ? 'Clear the search to see everything.' : 'Describe what you want changed. Your director inspects the project, plans bounded tasks and coordinates the agents.'}
              </Empty>
            </div>
          )}
        </div>
      </Panel>

      <button className="btn primary full new-request" disabled={disabled || !data.projects.length} onClick={() => setModal({kind: 'goal'})}>
        <Icon name="plus" size={16} />
        New request
      </button>

      <Panel title="Recent requests" aside={<span className="count num">{recent.length}</span>}>
        {recent.map(v => (
          <RecentRow key={v.goal.id} view={v} />
        ))}
        {!recent.length && <p className="panel-body muted small">Verified, accepted and integrated work will be listed here with its commit.</p>}
      </Panel>

      <details className="activity">
        <summary>
          Activity log <span className="count num">{data.events.length}</span>
        </summary>
        <div className="timeline">
          {data.events.slice(0, 8).map((e: RecordData) => (
            <div className="event" key={e.id}>
              <span className="circle" />
              <div>
                <p>{eventLine(e, data)}</p>
                <time className="num">{when(e.time)}</time>
              </div>
            </div>
          ))}
          {!data.events.length && <p className="muted small">No events recorded yet.</p>}
        </div>
      </details>
    </div>
  );
}
