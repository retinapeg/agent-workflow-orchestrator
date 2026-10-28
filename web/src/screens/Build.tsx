import React, {useState} from 'react';
import {useArena} from '../App';
import {
  ACTIVE_ATTEMPT,
  cap,
  duration,
  family,
  goalView,
  lanes,
  seconds,
  shortSha,
  stateLabel,
  when,
  type GoalView,
  type RecordData,
} from '../types';
import {Chip, Empty, Eyebrow, Facts, Icon, LiveDot, Sha} from '../ui';
import {StageRail} from './Overview';

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
        <span className="small muted num">
          {task.complexity ? `${task.complexity} · ${task.risk} risk` : ''}
        </span>
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
          <span className="small muted">
            {live.state === 'verifying' ? 'verifying · waiting for frozen checks' : live.state === 'leased' ? 'starting' : `${live.phase} in progress`}
          </span>
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
          <button
            className="btn primary"
            disabled={disabled || !answer.trim()}
            onClick={() => action(`tasks/${asking.id}/answer`, {revision: asking.rev, text: answer}, 'Your answer is with the director.')}
          >
            Continue with my answer
          </button>
        </div>
      )}
    </article>
  );
}

export function Build() {
  const {data, goal, setGoal, setModal, disabled} = useArena();
  const views = (data.goals || []).map(g => goalView(g, data)).sort((a, b) => b.goal.created - a.goal.created);
  const selected = views.find(v => v.goal.id === goal) || views.find(v => v.active) || views[0];
  if (!selected)
    return (
      <>
        <div className="heading">
          <div>
            <h1>Build.</h1>
            <p>Objective, director's interpretation, dependency lanes and evidence for one request.</p>
          </div>
        </div>
        <Empty title="No requests yet">
          Submit a request from the Overview and your director will produce a specification, bounded tasks and their dependencies here.{' '}
          <button className="text-btn" disabled={disabled || !data.projects.length} onClick={() => setModal({kind: 'goal'})}>
            Describe a change
          </button>
        </Empty>
      </>
    );
  const laneList = lanes(selected.children.length ? selected.children : selected.tasks);
  const completed = selected.children.filter(t => t.state === 'completed').length;
  return (
    <>
      <div className="heading">
        <div>
          <h1>Build.</h1>
          <p>Objective, director's interpretation, dependency lanes and evidence for one request.</p>
        </div>
        <div className="field inline">
          <label htmlFor="goal-pick">Request</label>
          <select id="goal-pick" value={selected.goal.id} onChange={e => setGoal(e.target.value)}>
            {views.map(v => (
              <option key={v.goal.id} value={v.goal.id}>
                {v.status} · {v.project?.name || ''} · {v.goal.request.slice(0, 48)}
                {v.goal.request.length > 48 ? '…' : ''}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="build-grid">
        <div className="stack">
          <article className="card objective">
            <div className="row between wrap">
              <Eyebrow>
                Objective · {selected.project?.name || 'Project'} · {when(selected.goal.created)}
              </Eyebrow>
              <Chip tone={selected.tone}>{selected.status}</Chip>
            </div>
            <blockquote>{selected.goal.request}</blockquote>
            <StageRail view={selected} />
            <Facts
              rows={[
                ['Tasks', `${completed}/${selected.children.length} completed`],
                ['Scope authority', selected.project?.scope.join(', ') || ''],
                selected.project?.integrated_head ? ['Main', <Sha value={selected.project.integrated_head} />] : null,
              ]}
            />
          </article>
          <Director view={selected} />
        </div>

        <div className="stack">
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
      </div>
    </>
  );
}
