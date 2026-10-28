import React from 'react';
import {useArena} from '../App';
import {
  ACTIVE_ATTEMPT,
  ACTIVE_TASK,
  BLOCKED_TASK,
  QUEUED_TASK,
  STAGES,
  STAGES_SHORT,
  cap,
  clock,
  duration,
  eventLine,
  family,
  goalView,
  providerGlyph,
  providerName,
  stageIndex,
  stateLabel,
  when,
  type GoalView,
  type RecordData,
} from '../types';
import {Chip, Empty, Eyebrow, Icon, Panel, Sha} from '../ui';

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

function Stack({view}: {view: GoalView}) {
  const {data, now, openGoal} = useArena();
  const director = view.goal.specification?.summary || (view.plan && !view.children.length ? view.plan.reason : '');
  return (
    <article className="stack-card">
      <div className="row between wrap">
        <Eyebrow>
          {view.project?.name || 'Project'} · {when(view.goal.created)}
        </Eyebrow>
        <Chip tone={view.tone}>{view.status}</Chip>
      </div>
      <h3 className="stack-request">{view.goal.request}</h3>
      {director && (
        <p className="stack-director">
          <span className="eyebrow">Director</span>
          {director}
        </p>
      )}
      {view.children.length > 0 && (
        <ol className="stack-tasks">
          {view.children.map(t => {
            const attempt = data.attempts.find(a => a.task_id === t.id && ACTIVE_ATTEMPT.includes(a.state));
            return (
              <li key={t.id}>
                <Chip tone={family(t.state)}>{stateLabel(t.state)}</Chip>
                <span className="stack-task-title">{t.title}</span>
                <span className="num muted small">{attempt ? duration(now - attempt.created) : t.integrated_commit ? t.integrated_commit.slice(0, 7) : cap(t.phase)}</span>
              </li>
            );
          })}
        </ol>
      )}
      <StageRail view={view} />
      <div className="row between stack-foot">
        <span className="small muted">
          {view.children.length ? `${view.children.filter(t => t.state === 'completed').length}/${view.children.length} tasks completed` : 'Director is preparing the specification'}
        </span>
        <button className="text-btn" onClick={() => openGoal(view.goal.id)}>
          Open build <Icon name="arrow" size={14} />
        </button>
      </div>
    </article>
  );
}

export function Overview() {
  const {data, now, disabled, setModal, setTab} = useArena();
  const tasks = data.tasks;
  const working = tasks.filter(t => ACTIVE_TASK.includes(t.state) || t.state === 'integrating');
  const queued = tasks.filter(t => QUEUED_TASK.includes(t.state));
  const completed = tasks.filter(t => t.state === 'completed').sort((a, b) => b.updated - a.updated);
  const blocked = tasks.filter(t => BLOCKED_TASK.includes(t.state));
  const answers = tasks.filter(t => t.state === 'awaiting_input');
  const pending = data.approvals.filter(a => a.state === 'pending');
  const accepted = data.approvals.filter(a => a.state === 'accept' && a.publication?.state !== 'integrated');
  const attention = pending.length + accepted.length + answers.length + blocked.length;
  const views = (data.goals || []).map(g => goalView(g, data)).sort((a, b) => b.goal.created - a.goal.created);
  const activeViews = views.filter(v => v.active);
  const shown = activeViews.length ? activeViews : views.slice(0, 2);
  const integrated = completed.filter(t => t.integrated_commit).length;
  const taskTitle = (id: string) => tasks.find(t => t.id === id)?.title || id;

  return (
    <>
      <div className="heading">
        <div>
          <h1>Build control.</h1>
          <p>Describe the outcome. Your director plans, the agents build, you decide.</p>
        </div>
        <button className="btn primary" disabled={disabled || !data.projects.length} onClick={() => setModal({kind: 'goal'})}>
          <Icon name="plus" size={16} />
          New request
        </button>
      </div>

      <div className="tiles">
        <div className={'tile' + (working.length ? ' hot' : '')}>
          <span className="k">Running</span>
          <div className="v">
            {working.length}
            <small>/ {data.limit} slots</small>
          </div>
          <span className="s">{data.paused ? 'dispatch paused' : working.length ? 'live workers' : 'idle · no model calls'}</span>
        </div>
        <div className="tile">
          <span className="k">Waiting</span>
          <div className="v">{queued.length}</div>
          <span className="s">queued tasks</span>
        </div>
        <div className={'tile' + (attention ? ' warn' : '')}>
          <span className="k">Needs you</span>
          <div className="v">{attention}</div>
          <span className="s">{attention ? 'decisions waiting' : 'nothing waiting'}</span>
        </div>
        <div className="tile">
          <span className="k">Completed</span>
          <div className="v">{completed.length}</div>
          <span className="s">{integrated} integrated into main</span>
        </div>
      </div>

      <div className="grid">
        <div className="stack">
          <Panel title="Build stacks" aside={<span className="count num">{activeViews.length} active</span>}>
            <div className="panel-body stack">
              {shown.map(v => (
                <Stack key={v.goal.id} view={v} />
              ))}
              {!shown.length && (
                <Empty title="No builds yet">Describe what you want changed. Your director inspects the project, plans bounded tasks and coordinates the agents.</Empty>
              )}
            </div>
          </Panel>

          <Panel title="Completed work" aside={<span className="count num">{completed.length}</span>}>
            {completed.slice(0, 6).map(t => (
              <div className="list-row" key={t.id}>
                <Chip tone="done">Done</Chip>
                <div className="list-main">
                  <b>{t.title}</b>
                  <span className="small muted">
                    {t.integrated_commit ? (
                      <>
                        Integrated <Sha value={t.integrated_commit} /> ·{' '}
                      </>
                    ) : null}
                    {t.reason}
                  </span>
                </div>
                <span className="num small muted">{clock(t.updated)}</span>
              </div>
            ))}
            {!completed.length && <p className="panel-body muted small">Verified, accepted and integrated work will be listed here with its commit.</p>}
          </Panel>
        </div>

        <div className="stack">
          <Panel title="Needs your decision" aside={<span className="count num">{attention}</span>} className={attention ? 'accent-amber' : ''}>
            {pending.map(a => (
              <button className="list-row press" key={a.id} onClick={() => setModal({kind: 'review', id: a.id})}>
                <Chip tone="decision">Approve</Chip>
                <div className="list-main">
                  <b>{taskTitle(a.task_id)}</b>
                  <span className="small muted">Verified candidate {a.candidate?.slice(0, 10)} awaits your decision</span>
                </div>
                <Icon name="arrow" size={16} />
              </button>
            ))}
            {accepted.map(a => (
              <button className="list-row press" key={a.id} onClick={() => setModal({kind: 'review', id: a.id})}>
                <Chip tone={a.publication?.state === 'failed' ? 'blocked' : 'decision'}>{a.publication ? cap(a.publication.state) : 'Integrate'}</Chip>
                <div className="list-main">
                  <b>{taskTitle(a.task_id)}</b>
                  <span className="small muted">{a.publication?.reason || 'Accepted · authorise integration into main'}</span>
                </div>
                <Icon name="arrow" size={16} />
              </button>
            ))}
            {answers.map(t => (
              <button className="list-row press" key={t.id} onClick={() => setModal({kind: 'task', id: t.id})}>
                <Chip tone="decision">Answer</Chip>
                <div className="list-main">
                  <b>{t.title}</b>
                  <span className="small muted">{t.question || t.reason}</span>
                </div>
                <Icon name="arrow" size={16} />
              </button>
            ))}
            {blocked.map(t => (
              <button className="list-row press" key={t.id} onClick={() => setModal({kind: 'task', id: t.id})}>
                <Chip tone="blocked">{stateLabel(t.state)}</Chip>
                <div className="list-main">
                  <b>{t.title}</b>
                  <span className="small muted">{t.reason}</span>
                </div>
                <Icon name="arrow" size={16} />
              </button>
            ))}
            {!attention && <p className="panel-body muted small">Nothing is waiting on you. Checks passing is never permission to publish; decisions appear here.</p>}
          </Panel>

          <Panel
            title="Workers"
            aside={
              <button className="text-btn" onClick={() => setTab('agents')}>
                Manage
              </button>
            }
          >
            {data.agents.map(agent => {
              const job = data.attempts.find(a => a.account_id === agent.id && ACTIVE_ATTEMPT.includes(a.state));
              const task = job && tasks.find(t => t.id === job.task_id);
              const cooling = (agent.cooldown_until || 0) > now;
              const tone = job ? 'running' : !agent.enabled ? 'off' : agent.health !== 'ready' ? 'blocked' : cooling ? 'waiting' : 'neutral';
              const label = job ? cap(job.phase) : !agent.enabled ? 'Disabled' : agent.health !== 'ready' ? stateLabel(agent.health) : cooling ? `Cooling · ${clock(agent.cooldown_until)}` : 'Available';
              return (
                <div className="worker-row" key={agent.id}>
                  <span className={'glyph ' + (job ? 'on' : '')}>{providerGlyph(agent.provider)}</span>
                  <div className="list-main">
                    <b>
                      {agent.name} <span className="muted small num">{job ? `${job.model} · ${job.effort || 'default'}` : agent.model}</span>
                    </b>
                    <span className="small muted">{job ? `${task?.title || ''} · ${duration(now - job.created)}` : agent.health !== 'ready' ? agent.reason : providerName(agent.provider)}</span>
                  </div>
                  <Chip tone={tone as any}>{label}</Chip>
                </div>
              );
            })}
            {!data.agents.length && <p className="panel-body muted small">No enrolled accounts. Set up a subscription account on the host.</p>}
          </Panel>

          <Panel title="Activity" aside={<Eyebrow>Persisted events</Eyebrow>}>
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
          </Panel>
        </div>
      </div>
    </>
  );
}
