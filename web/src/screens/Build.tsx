import React, {useEffect, useRef, useState} from 'react';
import {useArena, type PostError} from '../App';
import {bytes, liveLineText, stamp, useLiveTail} from '../live';
import {
  usageTotals,
  ACTIVE_ATTEMPT,
  ACTIVE_TASK,
  QUEUED_TASK,
  STAGES,
  UNSTOPPABLE,
  ago,
  cap,
  defaultGoal,
  duration,
  eventLine,
  family,
  goalClass,
  goalStatus,
  goalView,
  liveAttemptOf,
  milestoneTree,
  providerGlyph,
  seconds,
  shortSha,
  stageIndex,
  stateLabel,
  when,
  type GoalDetail,
  type GoalView,
  type MilestoneNode,
  type RecordData,
} from '../types';
import {Chip, Empty, Eyebrow, Facts, Glyph, Icon, LiveDot, Provider, Sha, StageBar} from '../ui';
import {BuildSteps} from './Overview';

function last<T>(items: T[]): T | undefined {
  return items[items.length - 1];
}

function TaskCard({task, all}: {task: RecordData; all: RecordData[]}) {
  const {data, now, setModal} = useArena();
  const attempts = data.attempts.filter(a => a.task_id === task.id).sort((a, b) => a.created - b.created);
  const live = attempts.find(a => ACTIVE_ATTEMPT.includes(a.state));
  const agent = live && data.agents.find(a => a.id === live.account_id);
  const build = last(attempts.filter(a => a.phase === 'build' && a.result?.checks?.length));
  const review = last(attempts.filter(a => a.phase === 'review' && a.result?.review));
  const director = last(attempts.filter(a => a.phase === 'direct' && a.result?.review));
  const failed = last(attempts.filter(a => ['failed', 'model_unavailable', 'interrupted', 'rate_limited', 'transient', 'needs_auth', 'needs_permission'].includes(a.state)));
  const deps = (task.dependencies || []).map((id: string) => all.find(t => t.id === id) || data.tasks.find(t => t.id === id));
  const approval = task.approval_id ? data.approvals.find(a => a.id === task.approval_id) : undefined;
  const integration = [
    {name: 'Staged', on: !!task.staged || !!task.candidate},
    {name: 'Approval', on: !!approval || task.state === 'ready_for_approval'},
    {name: 'Accepted', on: approval?.state === 'accept' || !!task.integrated_commit},
    {name: 'Integrated', on: !!task.integrated_commit || approval?.publication?.state === 'integrated'},
  ];
  return (
    <article className={'task-card ' + family(task.state)}>
      <div className="row between wrap">
        <div className="row wrap">
          <Chip tone={family(task.state)}>{stateLabel(task.state)}</Chip>
          <span className="tag">{cap(task.phase)}</span>
          {task.cancel_requested && <Chip tone="blocked">Stop requested</Chip>}
        </div>
        <span className="small muted num">{task.complexity ? `${task.complexity} · ${task.risk} risk` : ''}</span>
      </div>
      <h3>{task.title}</h3>
      {deps.length > 0 && (
        <div className="deps">
          <span className="deps-label">
            <Icon name="git" size={14} />
            Waits on
          </span>
          {deps.map((d: RecordData, i: number) => (
            <span className="dep" key={d?.id || i}>
              <b>{d?.title || 'task'}</b>
              {d && <Chip tone={family(d.state)}>{stateLabel(d.state)}</Chip>}
            </span>
          ))}
        </div>
      )}
      <p className="path">{task.scope.join(' · ')}</p>
      <p className="reason">{task.reason}</p>

      {live && (
        <div className="worker-line">
          <LiveDot tone="live" />
          <span>
            <b>{agent?.name || 'Worker'}</b> · {live.model} · {live.effort || 'provider default'} · <span className="num">{duration(now - live.created)}</span>
          </span>
          <span className="small muted">{live.state === 'verifying' ? 'verifying · waiting for frozen checks' : live.state === 'leased' ? 'starting' : `${live.phase} in progress`}</span>
        </div>
      )}

      {build && (
        <div className="checks">
          <span className="checks-head">
            <Eyebrow>Checks</Eyebrow>
            <Sha value={build.result.candidate} n={7} />
          </span>
          {build.result.checks.map((c: RecordData) => (
            <Chip key={c.name} tone={c.status === 'PASS' ? 'pass' : 'fail'}>
              {c.name} · {c.status} · {seconds(c.seconds)}
            </Chip>
          ))}
        </div>
      )}

      {(review || director) && (
        <div className="reviews">
          {review && (
            <div className="review-line">
              <Chip tone={review.result.review.approved ? 'pass' : 'fail'}>{review.result.review.approved ? 'Review approved' : 'Review rejected'}</Chip>
              <span className="small muted">
                {review.model} · {review.result.review.findings?.length || 0} finding{review.result.review.findings?.length === 1 ? '' : 's'}
              </span>
            </div>
          )}
          {director && (
            <div className="review-line">
              <Chip tone={director.result.review.approved ? 'pass' : 'fail'}>{director.result.review.approved ? 'Director approved' : 'Director rejected'}</Chip>
              <span className="small muted clamp2">{director.result.review.summary || director.result.reason}</span>
            </div>
          )}
        </div>
      )}

      {(failed || task.repairs > 0) && (
        <div className="failure">
          <Eyebrow>Failures</Eyebrow>
          <span className="small">
            {failed ? `${cap(failed.phase)} ${failed.state.replaceAll('_', ' ')}: ${failed.result?.reason || failed.reason}` : 'Earlier approach repaired'} · repairs{' '}
            <span className="num">
              {task.repairs}/{task.max_repairs}
            </span>
          </span>
        </div>
      )}

      {integration.some(s => s.on) && (
        <ol className="rail compact">
          {integration.map((s, i) => (
            <li key={s.name} className={s.on ? 'done' : integration[i - 1]?.on ? 'active' : ''}>
              <i />
              <span>
                {s.name}
                {s.name === 'Integrated' && task.integrated_commit ? ` · ${shortSha(task.integrated_commit, 7)}` : ''}
              </span>
            </li>
          ))}
        </ol>
      )}

      <div className="row between task-foot">
        <span className="small muted num">
          {attempts.length} attempt{attempts.length === 1 ? '' : 's'} · updated {when(task.updated)}
        </span>
        <button className="btn sm" onClick={() => setModal({kind: 'task', id: task.id})}>
          Details & controls
        </button>
      </div>
    </article>
  );
}

// The director's specification is no longer in the snapshot (it is large); it arrives with
// GET goals/{id} once Details is opened. Until then the card says so instead of "none".
function Director({view, detail, loading}: {view: GoalView; detail: GoalDetail | null; loading: boolean}) {
  const {data, now} = useArena();
  const spec = detail?.goal?.specification ?? view.goal.specification;
  const plan = view.plan;
  const planAttempt = plan && data.attempts.find(a => a.task_id === plan.id && ACTIVE_ATTEMPT.includes(a.state));
  return (
    <article className="card director">
      <div className="row between wrap">
        <Eyebrow>Director's interpretation</Eyebrow>
        {spec ? <Chip tone="neutral">{cap(spec.strategy)} strategy</Chip> : plan ? <Chip tone={family(plan.state)}>{stateLabel(plan.state)}</Chip> : null}
      </div>
      {loading && !spec ? (
        <p className="lede">Loading the director's plan…</p>
      ) : spec ? (
        <>
          <h3>{spec.outcome}</h3>
          <p className="lede">{spec.summary}</p>
          <div className="spec-grid">
            {[
              ['Observations', spec.observations],
              ['Acceptance criteria', spec.acceptance],
              ['Constraints', spec.constraints],
            ].map(([title, items]) => (
              <div key={title as string}>
                <Eyebrow>{title as string}</Eyebrow>
                <ul>
                  {((items as string[]) || []).map((x, i) => (
                    <li key={i}>{x}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </>
      ) : plan ? (
        <>
          <h3>{plan.state === 'awaiting_input' ? 'Your director needs one answer' : 'Your director is assessing the request'}</h3>
          <p className="lede">{plan.reason}</p>
          {planAttempt && (
            <div className="worker-line">
              <LiveDot tone="live" />
              <span>
                {planAttempt.model} · {planAttempt.effort || 'provider default'} · <span className="num">{duration(now - planAttempt.created)}</span>
              </span>
            </div>
          )}
        </>
      ) : (
        <p className="lede">No director specification was recorded for this request.</p>
      )}
    </article>
  );
}

// What this build is waiting on the owner for, at the top of the screen: the director's
// question with an answer box, a worker that needs sign-in or permission on the Mac with a
// retry, or a verified candidate waiting for a decision.
function NeedsCard({view}: {view: GoalView}) {
  const {data, now, disabled, action, setModal} = useArena();
  const [answer, setAnswer] = useState('');
  const asking = view.tasks.find(t => t.state === 'awaiting_input');
  const access = view.tasks.filter(t => t.state === 'needs_auth' || t.state === 'needs_permission');
  const approvals = data.approvals.filter(a => a.state === 'pending' && view.tasks.some(t => t.id === a.task_id && t.state === 'ready_for_approval'));
  if (!asking && !access.length && !approvals.length) return null;
  return (
    <section className="card needs-card accent-amber" aria-label="Needs you">
      <div className="row between wrap">
        <Eyebrow>Needs you</Eyebrow>
        {asking && <Chip tone="decision">{stateLabel(asking.state)}</Chip>}
      </div>
      {asking && (
        <div className="question">
          <p>
            <b>{asking.title}</b>
          </p>
          <p>{asking.question || asking.reason}</p>
          <label htmlFor="build-answer">Your answer</label>
          <textarea id="build-answer" rows={3} value={answer} onChange={e => setAnswer(e.target.value)} />
          <button className="btn primary" disabled={disabled || !answer.trim()} onClick={() => action(`tasks/${asking.id}/answer`, {revision: asking.rev, text: answer}, 'Your answer is with the director.')}>
            Continue with my answer
          </button>
        </div>
      )}
      {access.map(t => (
        <div className="row between wrap" key={t.id}>
          <span className="list-main">
            <b>{t.title}</b>
            <span className="small muted">
              <span className="tone blocked">{stateLabel(t.state)}</span> · {ago(t.updated, now)} · {t.reason}
            </span>
          </span>
          <button className="btn" disabled={disabled} onClick={() => action(`tasks/${t.id}/retry`, {revision: t.rev}, 'Bounded retry queued')}>
            Retry
          </button>
        </div>
      ))}
      {approvals.map(a => {
        const t = view.tasks.find(x => x.id === a.task_id);
        return (
          <div className="row between wrap" key={a.id}>
            <span className="list-main">
              <b>{t?.title || 'Verified change'}</b>
              <span className="small muted">
                <span className="tone decision">Ready for approval</span> · candidate <Sha value={a.candidate} n={10} />
              </span>
            </span>
            <button className="btn primary" disabled={disabled} onClick={() => setModal({kind: 'review', id: a.id})}>
              Review and decide
            </button>
          </div>
        );
      })}
    </section>
  );
}

// Events of one request from GET goals/{id}/events (last 40, oldest first), refreshed with
// `after=<cursor>` whenever the snapshot reports a new event. An older host answers 404,
// and the screen then shows whatever the snapshot's global event window still holds.
function useGoalEvents(goalId: string, latestEventId: number | undefined, fallback: RecordData[]): {events: RecordData[]; recorded: boolean} {
  const {get} = useArena();
  const [state, setState] = useState<{id: string; events: RecordData[]; cursor: unknown; supported: boolean}>({id: '', events: [], cursor: null, supported: true});
  const getRef = useRef(get);
  getRef.current = get;
  const stateRef = useRef(state);
  stateRef.current = state;
  useEffect(() => {
    if (!goalId) return;
    let stopped = false;
    const fresh = stateRef.current.id !== goalId;
    if (!fresh && !stateRef.current.supported) return;
    const cursor = fresh ? null : stateRef.current.cursor;
    const query = cursor !== null && cursor !== undefined ? `?after=${encodeURIComponent(String(cursor))}` : '';
    (async () => {
      try {
        const r = await getRef.current(`goals/${goalId}/events${query}`);
        if (stopped || !r || !Array.isArray(r.events)) return;
        setState(s => {
          const base = s.id === goalId && query ? s.events : [];
          const seen = new Set(base.map(e => e.id));
          const merged = [...base, ...r.events.filter((e: RecordData) => !seen.has(e.id))].sort((a, b) => a.time - b.time || a.id - b.id);
          return {id: goalId, events: merged.slice(-40), cursor: r.cursor ?? merged[merged.length - 1]?.id ?? cursor, supported: true};
        });
      } catch (e) {
        if (stopped) return;
        const status = (e as PostError)?.status;
        if (status === 404 || status === 405) setState({id: goalId, events: [], cursor: null, supported: false});
      }
    })();
    return () => {
      stopped = true;
    };
  }, [goalId, latestEventId]);
  if (state.id !== goalId || !state.supported) return {events: fallback, recorded: false};
  return {events: state.events, recorded: true};
}

// Full records of one request, fetched only when Details is opened.
function useGoalDetail(goalId: string, wanted: boolean): {detail: GoalDetail | null; loading: boolean} {
  const {get} = useArena();
  const [state, setState] = useState<{id: string; detail: GoalDetail | null; loading: boolean}>({id: '', detail: null, loading: false});
  const getRef = useRef(get);
  getRef.current = get;
  useEffect(() => {
    if (!wanted || (state.id === goalId && (state.detail || state.loading))) return;
    let stopped = false;
    setState({id: goalId, detail: null, loading: true});
    (async () => {
      try {
        const r = (await getRef.current(`goals/${goalId}`)) as GoalDetail;
        if (!stopped) setState({id: goalId, detail: r && r.goal ? r : null, loading: false});
      } catch {
        if (!stopped) setState({id: goalId, detail: null, loading: false});
      }
    })();
    return () => {
      stopped = true;
    };
  }, [goalId, wanted]);
  return state.id === goalId ? {detail: state.detail, loading: state.loading} : {detail: null, loading: false};
}

// Milestone tree. Each node is a native <details> (keyboard operable: Tab to the summary,
// Enter or Space toggles). The summary shows the recorded state, the accounts that ran the
// latest attempts (glyph lit only while an attempt is active now), what the milestone waits
// on, and the recorded evidence. The body nests child milestones and then the task cards.
function Milestone({node, all, depth = 0}: {node: MilestoneNode; all: RecordData[]; depth?: number}) {
  const live = node.tone === 'running' || node.tone === 'blocked' || node.tone === 'decision';
  const ev = node.evidence;
  const active = node.owners.filter(o => o.active);
  const names = node.owners.map(o => o.name).join(', ');
  const summary = [`${node.code} ${node.title}`, node.status, names ? `owner ${names}` : '', node.waitsOn.length ? `waits on ${node.waitsOn.join(', ')}` : ''].filter(Boolean).join(' · ');
  return (
    <details className={`milestone ${node.kind} ${node.tone}` + (depth ? ' nested' : '')} open={live || undefined}>
      <summary aria-label={summary}>
        <Glyph tone={node.tone} size={30} />
        <span className="ms-main">
          <span className="ms-title">
            <b className="ms-code num">{node.code}</b>
            <span className="ms-name">{node.title}</span>
            <Chip tone={node.tone}>{node.status}</Chip>
          </span>
          {(node.owners.length > 0 || node.waitsOn.length > 0) && (
            <span className="ms-meta small muted">
              {node.owners.length > 0 && (
                <span className="ms-owner">
                  {active.map(o => (
                    <Provider key={o.id} glyph={providerGlyph(o.provider)} size="sm" on />
                  ))}
                  <span>{names}</span>
                </span>
              )}
              {node.waitsOn.map(code => (
                <span className="dep" key={code}>
                  <Icon name="git" size={12} />
                  waits on <b className="num">{code}</b>
                </span>
              ))}
            </span>
          )}
          {(ev.pass + ev.fail > 0 || ev.approved + ev.rejected > 0 || ev.integrated.length > 0) && (
            <span className="ms-evidence small">
              {ev.pass + ev.fail > 0 && (
                <Chip tone={ev.fail ? 'fail' : 'pass'}>
                  checks {ev.pass} PASS{ev.fail ? ` · ${ev.fail} FAIL` : ''}
                </Chip>
              )}
              {ev.approved + ev.rejected > 0 && (
                <Chip tone={ev.rejected ? 'fail' : 'pass'}>
                  review {ev.approved} approved{ev.rejected ? ` · ${ev.rejected} rejected` : ''} · {ev.findings} finding{ev.findings === 1 ? '' : 's'}
                </Chip>
              )}
              {ev.integrated.map(sha => (
                <Sha key={sha} value={sha} n={7} />
              ))}
            </span>
          )}
        </span>
        <span className="ms-count num small muted">{node.kind === 'milestone' ? `${node.all.filter(t => t.state === 'completed').length}/${node.all.length}` : ''}</span>
      </summary>
      <div className="ms-body">
        {node.children.map(c => (
          <Milestone key={c.code} node={c} all={all} depth={depth + 1} />
        ))}
        {node.tasks.map(t => (
          <TaskCard key={t.id} task={t} all={all} />
        ))}
        {node.kind !== 'milestone' && !node.tasks.length && (
          <p className="small muted ms-note">{node.kind === 'review' ? 'Independent review attempts are recorded on each task; open a task for its findings.' : 'Integration is recorded per task once a verified candidate is accepted.'}</p>
        )}
        {node.kind === 'milestone' && !node.all.length && <p className="small muted ms-note">No tasks recorded under this milestone.</p>}
      </div>
    </details>
  );
}

export function MilestoneTree({view}: {view: GoalView}) {
  const {data} = useArena();
  const tree = milestoneTree(view, data);
  if (!view.children.length && !view.plan) return <Empty title="No tasks yet">The director has not produced tasks for this request.</Empty>;
  return (
    <section className="milestones" aria-label="Milestones">
      <div className="row between wrap">
        <Eyebrow>Milestones</Eyebrow>
        <span className="count num">{tree.has ? `${tree.done}/${tree.total} done` : `${view.children.filter(t => t.state === 'completed').length}/${view.children.length} tasks`}</span>
      </div>
      {tree.nodes.map(n => (
        <Milestone key={n.code} node={n} all={view.tasks} />
      ))}
    </section>
  );
}

const TITLES: Record<string, string> = {active: 'Build in progress', needs: 'Build needs you', done: 'Build complete', history: 'Build history'};

export function Build() {
  const {data, now, goal, setGoal, setModal, disabled, action} = useArena();
  const [message, setMessage] = useState('');
  const [composing, setComposing] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const views = (data.goals || []).map(g => goalView(g, data)).sort((a, b) => b.goal.created - a.goal.created);
  const selected = views.find(v => v.goal.id === goal) || defaultGoal(views, data);
  const selectedId = selected?.goal.id || '';
  const archivedCount = views.filter(v => v.archived).length;
  const listed = views.filter(v => showArchived || !v.archived || v.goal.id === selectedId);
  const liveAttempt = selected ? liveAttemptOf(selected, data) : undefined;
  const ids = new Set(selected ? selected.tasks.map(t => t.id) : []);
  const mine = data.attempts.filter(a => ids.has(a.task_id));
  const attemptIds = new Set(mine.map(a => a.id));
  const snapshotEvents = selected
    ? data.events
        .filter((e: RecordData) => {
          const d = e.data || {};
          return (d.task_id && ids.has(d.task_id)) || (d.attempt_id && attemptIds.has(d.attempt_id)) || d.goal_id === selectedId;
        })
        .slice(0, 40)
        .reverse()
    : [];
  // Hooks run on every render, so they come before the empty-state return below.
  const {events: output, recorded} = useGoalEvents(selectedId, data.events[0]?.id, snapshotEvents);
  const tail = useLiveTail(liveAttempt?.id);
  const {detail, loading} = useGoalDetail(selectedId, detailsOpen && !!selected);
  useEffect(() => {
    setComposing(false);
    setDetailsOpen(false);
  }, [selectedId]);
  // The terminal keeps its newest line in view, like a real console.
  const terminal = useRef<HTMLPreElement>(null);
  const lineCount = output.length + (tail?.lines.length || 0);
  useEffect(() => {
    const el = terminal.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [selectedId, lineCount]);
  if (!selected)
    return (
      <>
        <div className="page-head">
          <div>
            <h2 className="page-title">Builds</h2>
            <p className="page-sub">Each request, its stepper, the agents on it and their live output.</p>
          </div>
        </div>
        <Empty title="No requests yet">
          Describe a change on Home and your director will produce a specification, bounded tasks and their dependencies here.{' '}
          <button className="text-btn" disabled={disabled || !data.projects.length} onClick={() => setModal({kind: 'goal'})}>
            Describe a change
          </button>
        </Empty>
      </>
    );

  const cls = goalClass(selected);
  const status = goalStatus(selected);
  const groups: [string, GoalView[]][] = [
    ['Active', listed.filter(v => goalClass(v) === 'active')],
    ['Needs you', listed.filter(v => goalClass(v) === 'needs')],
    ['Completed', listed.filter(v => goalClass(v) === 'done')],
    ['History', listed.filter(v => goalClass(v) === 'history')],
  ];
  const current =
    (liveAttempt && selected.tasks.find(t => t.id === liveAttempt.task_id)) || selected.tasks.find(t => ACTIVE_TASK.includes(t.state)) || selected.children.find(t => !['completed', 'cancelled'].includes(t.state)) || selected.plan;
  const currentAgent = liveAttempt && data.agents.find(a => a.id === liveAttempt.account_id);
  const agentName = currentAgent?.name || (tail?.provider ? cap(tail.provider) : 'Agent');
  const currentProvider =
    currentAgent?.provider ||
    (current &&
      data.attempts
        .filter(a => a.task_id === current.id)
        .map(a => data.agents.find(x => x.id === a.account_id)?.provider)
        .filter(Boolean)
        .pop()) ||
    '';
  // Stop and Message agents act on work that can still be steered: a task that is running,
  // queued or waiting on an answer. Finished builds get neither button.
  const stoppable = selected.tasks.filter(t => !UNSTOPPABLE.includes(t.state) && t.state !== 'cancelled' && (ACTIVE_TASK.includes(t.state) || t.state === 'awaiting_input' || QUEUED_TASK.includes(t.state)));
  const stopTarget = (current && stoppable.find(t => t.id === current.id)) || stoppable[0];
  const messageTarget = stopTarget;
  const completed = selected.children.filter(t => t.state === 'completed').length;
  const usage = usageTotals(mine);
  const liveElapsed = tail?.elapsed ?? (liveAttempt ? now - liveAttempt.created : 0);

  // Agent stack rows: one per task that has work recorded, with the account that last ran it.
  const stack = (selected.children.length ? selected.children : selected.tasks)
    .filter(t => t.state !== 'cancelled')
    .map(t => {
      const attempts = data.attempts.filter(a => a.task_id === t.id).sort((a, b) => a.created - b.created);
      const live = attempts.find(a => ACTIVE_ATTEMPT.includes(a.state));
      const latest = live || last(attempts);
      const agent = latest && data.agents.find(a => a.id === latest.account_id);
      return {task: t, live, latest, agent};
    });

  return (
    <>
      <div className="page-head build-head">
        <div>
          <div className="row wrap">
            <h2 className="page-title">{TITLES[cls]}</h2>
            <Chip tone={status.tone}>{status.label}</Chip>
            {selected.archived && <Chip tone="off">Archived project</Chip>}
          </div>
          <p className="page-sub clamp2">
            {selected.title}
            {selected.project ? ` · ${selected.project.name}` : ''}
          </p>
        </div>
        <div className="field inline">
          <label htmlFor="goal-pick">Request</label>
          <select id="goal-pick" value={selected.goal.id} onChange={e => setGoal(e.target.value)}>
            {groups
              .filter(([, list]) => list.length)
              .map(([name, list]) => (
                <optgroup key={name} label={name}>
                  {list.map(v => (
                    <option key={v.goal.id} value={v.goal.id}>
                      {goalStatus(v).label} · {v.title} · {v.project?.name || ''}
                      {v.archived ? ' (archived)' : ''}
                    </option>
                  ))}
                </optgroup>
              ))}
          </select>
          {archivedCount > 0 && (
            <label className="check small muted">
              <input type="checkbox" checked={showArchived} onChange={e => setShowArchived(e.target.checked)} />
              Show archived ({archivedCount})
            </label>
          )}
        </div>
      </div>

      <div className="build-grid">
        <div className="stack">
          <NeedsCard view={selected} />

          <section className="card stepper-card">
            <BuildSteps view={selected} />
          </section>

          <section className="panel">
            <header className="panel-head">
              <h2>Current task</h2>
              {current && <span className="count num">{ago(current.updated, now)}</span>}
            </header>
            {current ? (
              <button className="current-task" onClick={() => setModal({kind: 'task', id: current.id})}>
                <Provider glyph={providerGlyph(currentProvider)} size="lg" on={!!liveAttempt} />
                <span className="list-main">
                  <b>{current.title}</b>
                  <span className="small muted clamp2">{liveAttempt ? `${agentName} · ${liveAttempt.model}${liveAttempt.effort ? ' · ' + liveAttempt.effort : ''} · ${cap(tail?.phase || liveAttempt.phase)}` : current.reason}</span>
                  <StageBar stage={Math.max(0, stageIndex(current))} total={STAGES.length} tone={family(current.state)} label={`Stage ${Math.max(0, stageIndex(current)) + 1} of ${STAGES.length}`} />
                </span>
                <span className="current-side">
                  <Chip tone={family(current.state)}>{stateLabel(current.state)}</Chip>
                  <span className="num small muted">{liveAttempt ? duration(liveElapsed) : ''}</span>
                </span>
              </button>
            ) : (
              <p className="panel-body muted small">No task is recorded for this request.</p>
            )}
          </section>

          <section className="panel">
            <header className="panel-head">
              <h2>Agent stack</h2>
              <span className="count num">
                {completed}/{selected.children.length || selected.tasks.length} done
              </span>
            </header>
            {stack.map(({task, live, latest, agent}) => (
              <button className="agent-row" key={task.id} onClick={() => setModal({kind: 'task', id: task.id})}>
                <Provider glyph={providerGlyph(agent?.provider || '')} on={!!live} />
                <span className="list-main">
                  <b>
                    {agent?.name || (latest ? 'Worker' : 'Unassigned')}
                    {latest ? <span className="muted small num"> · {latest.model}</span> : null}
                  </b>
                  <span className="small muted clamp1">{task.title}</span>
                  <StageBar stage={Math.max(0, stageIndex(task))} total={STAGES.length} tone={family(task.state)} label={`${task.title}: stage ${Math.max(0, stageIndex(task)) + 1} of ${STAGES.length}`} />
                </span>
                <span className="current-side">
                  <LiveDot tone={live ? 'live' : task.state === 'completed' ? 'ok' : family(task.state) === 'blocked' || family(task.state) === 'decision' ? 'warn' : 'off'} />
                  <span className="small muted num">{live ? duration(now - live.created) : stateLabel(task.state)}</span>
                </span>
              </button>
            ))}
            {!stack.length && <p className="panel-body muted small">The director has not produced tasks for this request.</p>}
          </section>

          <section className="panel">
            <header className="panel-head">
              <h2>Live output</h2>
              {liveAttempt && tail ? (
                <span className="count num live-meta">
                  <LiveDot tone="live" /> {duration(liveElapsed)} · {bytes(tail.bytes || 0)}
                </span>
              ) : (
                <Eyebrow>{liveAttempt ? 'Live' : recorded ? 'Recorded events' : 'Recent events'}</Eyebrow>
              )}
            </header>
            <pre className="terminal" aria-live="polite" ref={terminal}>
              {output.map((e: RecordData) => (
                <span key={e.id} className={'t-' + (e.kind.split('.')[0] || 'event')}>
                  <span className="t-time">[{stamp(e.time)}]</span> {eventLine(e, data)}
                  {'\n'}
                </span>
              ))}
              {liveAttempt && tail && tail.lines.length > 0 && (
                <span className="t-tail">
                  {output.length > 0 && '\n'}
                  {tail.lines.map((l, i) => (
                    <span key={i} className={'t-' + l.kind}>
                      <span className="t-time">[{stamp(l.t ?? liveAttempt.created + (tail.elapsed || 0))}]</span> {liveLineText(l, agentName)}
                      {'\n'}
                    </span>
                  ))}
                </span>
              )}
              {liveAttempt && (
                <span className="t-live">
                  <span className="t-time">[{stamp(now)}]</span> {agentName} · {cap(tail?.phase || liveAttempt.phase)} · {tail?.done ? 'finishing' : liveAttempt.state} · {duration(liveElapsed)}
                  {'\n'}
                </span>
              )}
              {!output.length && !liveAttempt && (selected.tasks.length ? 'Events for this request are older than the current window; open Details for its recorded attempts and evidence.\n' : 'Nothing has happened on this request yet.\n')}
            </pre>
          </section>

          {stopTarget && (
            <div className="build-actions">
              <button className="btn" disabled={disabled || !messageTarget} onClick={() => setComposing(c => !c)} aria-expanded={composing}>
                <Icon name="chat" size={16} />
                Message agents
              </button>
              <button className="btn danger" disabled={disabled} onClick={() => action(`tasks/${stopTarget.id}/cancel`, {revision: stopTarget.rev}, 'Cancellation requested; owned worker will stop')}>
                <Icon name="stop" size={16} />
                Stop
              </button>
            </div>
          )}
          {composing && messageTarget && (
            <div className="card compose">
              <label htmlFor="agent-message">Instruction for “{messageTarget.title}” · applied at the next safe boundary</label>
              <textarea id="agent-message" rows={3} value={message} onChange={e => setMessage(e.target.value)} placeholder="Prefer sentence case for labels." />
              <div className="dialog-actions">
                <button className="text-btn" onClick={() => setComposing(false)}>
                  Cancel
                </button>
                <button
                  className="btn primary"
                  disabled={disabled || !message.trim()}
                  onClick={async () => {
                    await action(`tasks/${messageTarget.id}/instructions`, {revision: messageTarget.rev, text: message.trim()}, 'Instruction saved for the next safe boundary');
                    setMessage('');
                    setComposing(false);
                  }}
                >
                  <Icon name="send" size={14} />
                  Queue instruction
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="stack">
          <details className="disclosure" open={detailsOpen} onToggle={e => setDetailsOpen((e.currentTarget as HTMLDetailsElement).open)}>
            <summary>
              Details <span className="muted small">director's plan, tasks, evidence and usage</span>
            </summary>
            <div className="stack disclosure-body">
              <article className="card objective">
                <div className="row between wrap">
                  <Eyebrow>
                    Objective · {selected.project?.name || 'Project'} · {when(selected.goal.created)}
                  </Eyebrow>
                </div>
                <blockquote>{detail?.goal?.request || selected.goal.request}</blockquote>
                <Facts
                  rows={[
                    ['Tasks', `${completed}/${selected.children.length} completed`],
                    ['Scope authority', selected.project?.scope?.join(', ') || ''],
                    selected.project?.integrated_head ? ['Main', <Sha value={selected.project.integrated_head} />] : null,
                    ['Tokens this build (subscription, no API charges)', usage ? `${usage.calls} calls · ${usage.fields.map(([k, v]) => `${k} ${v.toLocaleString()}`).join(' · ')}` : 'Not reported yet'],
                  ]}
                />
              </article>
              <Director view={selected} detail={detail} loading={loading} />
              <MilestoneTree view={selected} />
            </div>
          </details>
        </div>
      </div>
    </>
  );
}
