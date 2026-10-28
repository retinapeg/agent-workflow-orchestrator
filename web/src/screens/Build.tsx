import React, {useState} from 'react';
import {useArena} from '../App';
import {
  usageTotals,
  ACTIVE_ATTEMPT,
  ACTIVE_TASK,
  STAGES,
  UNSTOPPABLE,
  ago,
  cap,
  duration,
  eventLine,
  family,
  goalClass,
  goalStatus,
  goalView,
  lanes,
  providerGlyph,
  seconds,
  shortSha,
  stageIndex,
  stateLabel,
  when,
  type GoalView,
  type RecordData,
} from '../types';
import {Chip, Empty, Eyebrow, Facts, Icon, LiveDot, Provider, Sha, StageBar} from '../ui';
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

function Director({view}: {view: GoalView}) {
  const {data, now, disabled, action} = useArena();
  const spec = view.goal.specification;
  const plan = view.plan;
  const planAttempt = plan && data.attempts.find(a => a.task_id === plan.id && ACTIVE_ATTEMPT.includes(a.state));
  const asking = view.tasks.find(t => t.state === 'awaiting_input' && t.question);
  const [answer, setAnswer] = useState('');
  return (
    <article className="card director">
      <div className="row between wrap">
        <Eyebrow>Director's interpretation</Eyebrow>
        {spec ? <Chip tone="neutral">{cap(spec.strategy)} strategy</Chip> : plan ? <Chip tone={family(plan.state)}>{stateLabel(plan.state)}</Chip> : null}
      </div>
      {spec ? (
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
      {asking && (
        <div className="question">
          <Eyebrow>Your preference</Eyebrow>
          <p>{asking.question}</p>
          <label htmlFor="build-answer">Your answer</label>
          <textarea id="build-answer" rows={3} value={answer} onChange={e => setAnswer(e.target.value)} />
          <button className="btn primary" disabled={disabled || !answer.trim()} onClick={() => action(`tasks/${asking.id}/answer`, {revision: asking.rev, text: answer}, 'Your answer is with the director.')}>
            Continue with my answer
          </button>
        </div>
      )}
    </article>
  );
}

const TITLES: Record<string, string> = {active: 'Build in progress', needs: 'Build needs you', done: 'Build complete', history: 'Build history'};

export function Build() {
  const {data, now, goal, setGoal, setModal, disabled, action} = useArena();
  const [message, setMessage] = useState('');
  const [composing, setComposing] = useState(false);
  const [wide] = useState(() => typeof window !== 'undefined' && window.matchMedia('(min-width: 1181px)').matches);
  const views = (data.goals || []).map(g => goalView(g, data)).sort((a, b) => b.goal.created - a.goal.created);
  const selected = views.find(v => v.goal.id === goal) || views.find(v => goalClass(v) === 'active') || views.find(v => goalClass(v) === 'needs') || views.find(v => goalClass(v) === 'done') || views[0];
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
    ['Active', views.filter(v => goalClass(v) === 'active')],
    ['Needs you', views.filter(v => goalClass(v) === 'needs')],
    ['Completed', views.filter(v => goalClass(v) === 'done')],
    ['History', views.filter(v => goalClass(v) === 'history')],
  ];
  const ids = new Set(selected.tasks.map(t => t.id));
  const liveAttempt = data.attempts.filter(a => ids.has(a.task_id) && ACTIVE_ATTEMPT.includes(a.state)).sort((a, b) => b.created - a.created)[0];
  const current =
    (liveAttempt && selected.tasks.find(t => t.id === liveAttempt.task_id)) ||
    selected.tasks.find(t => ACTIVE_TASK.includes(t.state)) ||
    selected.children.find(t => !['completed', 'cancelled'].includes(t.state)) ||
    selected.plan;
  const currentAgent = liveAttempt && data.agents.find(a => a.id === liveAttempt.account_id);
  const currentProvider = currentAgent?.provider || (current && data.attempts.filter(a => a.task_id === current.id).map(a => data.agents.find(x => x.id === a.account_id)?.provider).filter(Boolean).pop()) || '';
  const stoppable = selected.tasks.filter(t => !UNSTOPPABLE.includes(t.state) && t.state !== 'cancelled' && (ACTIVE_TASK.includes(t.state) || t.state === 'awaiting_input' || ['ready', 'waiting_capacity', 'waiting_dependency', 'cooldown', 'repair_queued'].includes(t.state)));
  const stopTarget = (current && stoppable.find(t => t.id === current.id)) || stoppable[0];
  const messageTarget = stopTarget || current;
  const laneList = lanes(selected.children.length ? selected.children : selected.tasks);
  const completed = selected.children.filter(t => t.state === 'completed').length;
  const mine = data.attempts.filter(a => ids.has(a.task_id));
  const usage = usageTotals(mine);
  const attemptIds = new Set(mine.map(a => a.id));
  const output = data.events
    .filter((e: RecordData) => {
      const d = e.data || {};
      return (d.task_id && ids.has(d.task_id)) || (d.attempt_id && attemptIds.has(d.attempt_id)) || d.goal_id === selected.goal.id;
    })
    .slice(0, 14)
    .reverse();
  const stamp = (t: number) => new Date(t * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false});

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
          </div>
          <p className="page-sub clamp2">{selected.goal.request}</p>
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
                      {goalStatus(v).label} · {v.project?.name || ''} · {v.goal.request.slice(0, 48)}
                      {v.goal.request.length > 48 ? '…' : ''}
                    </option>
                  ))}
                </optgroup>
              ))}
          </select>
        </div>
      </div>

      <div className="build-grid">
        <div className="stack">
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
                  <span className="small muted clamp2">{liveAttempt ? `${currentAgent?.name || 'Worker'} · ${liveAttempt.model}${liveAttempt.effort ? ' · ' + liveAttempt.effort : ''} · ${cap(liveAttempt.phase)}` : current.reason}</span>
                  <StageBar stage={Math.max(0, stageIndex(current))} total={STAGES.length} tone={family(current.state)} label={`Stage ${Math.max(0, stageIndex(current)) + 1} of ${STAGES.length}`} />
                </span>
                <span className="current-side">
                  <Chip tone={family(current.state)}>{stateLabel(current.state)}</Chip>
                  <span className="num small muted">{liveAttempt ? duration(now - liveAttempt.created) : ''}</span>
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
              <Eyebrow>Recorded events</Eyebrow>
            </header>
            <pre className="terminal" aria-live="polite">
              {output.map((e: RecordData) => (
                <span key={e.id} className={'t-' + (e.kind.split('.')[0] || 'event')}>
                  <span className="t-time">[{stamp(e.time)}]</span> {eventLine(e, data)}
                  {'\n'}
                </span>
              ))}
              {liveAttempt && (
                <span className="t-live">
                  <span className="t-time">[{stamp(now)}]</span> {currentAgent?.name || 'Worker'} · {cap(liveAttempt.phase)} · {liveAttempt.state} · {duration(now - liveAttempt.created)}
                  {'\n'}
                </span>
              )}
              {!output.length && !liveAttempt && 'No events recorded for this request yet.\n'}
            </pre>
          </section>

          <div className="build-actions">
            <button className="btn" disabled={disabled || !messageTarget || UNSTOPPABLE.includes(messageTarget.state)} onClick={() => setComposing(c => !c)} aria-expanded={composing}>
              <Icon name="chat" size={16} />
              Message agents
            </button>
            <button
              className="btn danger"
              disabled={disabled || !stopTarget}
              onClick={() => stopTarget && action(`tasks/${stopTarget.id}/cancel`, {revision: stopTarget.rev}, 'Cancellation requested; owned worker will stop')}
            >
              <Icon name="stop" size={16} />
              Stop
            </button>
          </div>
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
          <details className="disclosure" open={wide || cls !== 'active'}>
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
                <blockquote>{selected.goal.request}</blockquote>
                <Facts
                  rows={[
                    ['Tasks', `${completed}/${selected.children.length} completed`],
                    ['Scope authority', selected.project?.scope.join(', ') || ''],
                    selected.project?.integrated_head ? ['Main', <Sha value={selected.project.integrated_head} />] : null,
                    ['Tokens this build (subscription, no API charges)', usage ? `${usage.calls} calls · ${usage.fields.map(([k, v]) => `${k} ${v.toLocaleString()}`).join(' · ')}` : 'Not reported yet'],
                  ]}
                />
              </article>
              <Director view={selected} />
              <div className="lanes">
                {laneList.map((lane, i) => (
                  <section className="lane" key={i}>
                    <div className="lane-head">
                      <Eyebrow>Stage {i + 1}</Eyebrow>
                      <span className="small muted">{i === 0 ? (laneList.length > 1 ? 'No prerequisites · can run in parallel' : 'Independent tasks') : `Waits on stage ${i}`}</span>
                    </div>
                    {lane.map(t => (
                      <TaskCard key={t.id} task={t} all={selected.tasks} />
                    ))}
                  </section>
                ))}
                {!laneList.length && <Empty title="No tasks yet">The director has not produced tasks for this request.</Empty>}
              </div>
            </div>
          </details>
        </div>
      </div>
    </>
  );
}
