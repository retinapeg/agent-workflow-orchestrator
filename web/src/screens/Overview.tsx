import React, {useEffect, useRef, useState} from 'react';
import {useArena} from '../App';
import {useSpeech} from '../speech';
import {AUTO, ProjectAsk, askedFrom, type Asked} from './Dialogs';
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
import {Chip, Empty, Eyebrow, Icon, LiveDot, Panel, Sha} from '../ui';

// Per-build stepper. Every "done" comes from a recorded state or attempt result, never
// from elapsed time: plan task completed → Plan; build attempt passed → Code; its frozen
// checks all PASS → Test; independent review attempt passed → Review; integrated commit
// recorded → Integrate. The current step reuses the task stage derivation (stageIndex).
export const STEPS = ['Plan', 'Code', 'Test', 'Review', 'Integrate'];
const STEPS_SHORT = ['Plan', 'Code', 'Test', 'Review', 'Merge'];
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

export function BuildSteps({view}: {view: GoalView}) {
  const {data} = useArena();
  const {done, active, detail} = buildSteps(view, data.attempts);
  return (
    <ol className="rail compact" aria-label="Build steps">
      {STEPS.map((name, i) => (
        <li key={name} className={done[i] ? 'done' : i === active ? 'active' : ''} title={`${name} · ${detail[i]}`} aria-label={`${name}: ${done[i] ? 'done' : i === active ? 'in progress' : 'not started'} · ${detail[i]}`}>
          <i />
          <span className="full">{name}</span>
          <span className="abbr" aria-hidden="true">
            {STEPS_SHORT[i]}
          </span>
        </li>
      ))}
    </ol>
  );
}

// "Tell me what to build": the everyday entry. Quick intents prefill the text, the
// project defaults to "Let Arena decide", and a shared spec (ChatGPT → Share → Arena)
// lands here through the handoff read on load.
const INTENTS = [
  {label: 'Build a new feature', prefix: 'Build a new feature: ', self: false},
  {label: 'Fix a bug', prefix: 'Fix a bug: ', self: false},
  {label: 'Improve Arena', prefix: 'Improve Arena: ', self: true},
  {label: 'Add to an existing project', prefix: 'Add to an existing project: ', self: false},
];
const PREFIX = /^(Build a new feature|Fix a bug|Improve Arena|Add to an existing project): ?/;

function TellArena() {
  const {data, post, notify, disabled, handoff, clearHandoff} = useArena();
  const [draft, setDraft] = useState(handoff);
  const [project, setProject] = useState(AUTO);
  const [asked, setAsked] = useState<Asked | null>(null);
  const [sending, setSending] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLSelectElement>(null);
  const speech = useSpeech(text => setDraft(d => (d.trim() ? d.replace(/\s+$/, '') + ' ' : '') + text));
  const self = data.projects.find(p => p.self);
  const enabled = data.projects.filter(p => p.enabled !== false);

  useEffect(() => {
    if (!handoff) return;
    setDraft(handoff);
    setProject(AUTO);
    setAsked(null);
    clearHandoff();
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    box.current?.scrollIntoView({block: 'center', behavior: reduce ? 'auto' : 'smooth'});
    box.current?.focus({preventScroll: true});
  }, [handoff]);

  const intent = (i: (typeof INTENTS)[number]) => {
    setDraft(d => i.prefix + d.replace(PREFIX, ''));
    setAsked(null);
    if (i.self && self) setProject(self.id);
    if (i.label.startsWith('Add to')) {
      setProject(AUTO);
      picker.current?.focus();
    } else box.current?.focus();
  };

  const submit = async (target: string) => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const r = await post('requests', target === AUTO ? {instructions: text} : {instructions: text, project_id: target});
      const name = data.projects.find(p => p.id === r.project_id)?.name || 'your project';
      const why = typeof r.why === 'string' ? r.why.replace(`${name}: `, '') : '';
      setDraft('');
      setAsked(null);
      setProject(AUTO);
      notify(`Sent to ${name}${r.resolved && why ? ` (${why})` : ''}. Your director is inspecting the project and preparing the work.`);
    } catch (e) {
      const which = askedFrom(e);
      if (which) setAsked(which);
      else notify(String(e));
    } finally {
      setSending(false);
    }
  };

  const busy = disabled || sending;
  return (
    <Panel title="Tell me what to build" aside={<Eyebrow>{speech.supported ? 'Voice or text' : 'Text'}</Eyebrow>}>
      <div className="panel-body stack" style={{gap: 12}}>
        <div className="btn-row" style={{marginTop: 0}} role="group" aria-label="Quick intents">
          {INTENTS.map(i => (
            <button key={i.label} type="button" className="btn" disabled={i.self && !self} title={i.self && !self ? 'Arena is not enrolled as a project on this host' : undefined} onClick={() => intent(i)}>
              {i.label}
            </button>
          ))}
        </div>
        <div className="field" style={{marginBottom: 0}}>
          <div className="field-row">
            <label htmlFor="tell-text">What should Arena build?</label>
            {speech.supported && (
              <button type="button" className={'mic' + (speech.listening ? ' on' : '')} aria-pressed={speech.listening} onClick={speech.toggle}>
                <Icon name={speech.listening ? 'stop' : 'mic'} size={15} />
                {speech.listening ? 'Stop' : 'Dictate'}
              </button>
            )}
          </div>
          <textarea
            id="tell-text"
            ref={box}
            rows={5}
            style={{fontSize: 17, minHeight: 132}}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') submit(project);
            }}
            placeholder="Describe the feature, the bug, or paste the spec you finished in ChatGPT."
          />
          {speech.listening && (
            <p className="listening" aria-live="polite">
              <LiveDot tone="live" />
              Listening{speech.interim ? ` · ${speech.interim}` : ' · speak now; the transcript is appended when you pause.'}
            </p>
          )}
          {speech.error && <p className="field-help warn">{speech.error}</p>}
        </div>
        <div className="row wrap" style={{alignItems: 'flex-end'}}>
          <div className="field inline" style={{flex: '1 1 220px'}}>
            <label htmlFor="tell-project">Build for</label>
            <select
              id="tell-project"
              ref={picker}
              value={project}
              onChange={e => {
                setProject(e.target.value);
                setAsked(null);
              }}
            >
              <option value={AUTO}>Let Arena decide</option>
              {enabled.map(p => (
                <option key={p.id} value={p.id}>
                  {p.name}
                  {p.self ? ' (this app)' : ''}
                </option>
              ))}
            </select>
          </div>
          <button type="button" className="btn primary" style={{flex: '1 1 160px'}} disabled={busy || !draft.trim() || !enabled.length} onClick={() => submit(project)}>
            <Icon name="bolt" size={16} />
            Start building
          </button>
        </div>
        <p className="field-help" style={{margin: 0}}>
          From ChatGPT: Share → Arena, or paste your spec here.{!enabled.length ? ' Enrol a project on the Mac first.' : ''}
        </p>
        {asked && <ProjectAsk asked={asked} busy={busy} onPick={submit} onCancel={() => setAsked(null)} />}
      </div>
    </Panel>
  );
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
      <BuildSteps view={view} />
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

      <div className="stack" style={{marginBottom: 22}}>
        <TellArena />
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
