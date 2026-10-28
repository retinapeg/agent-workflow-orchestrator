import React, {useEffect, useRef, useState} from 'react';
import {useArena} from '../App';
import {useSpeech} from '../speech';
import {AUTO, ProjectAsk, askedFrom, type Asked} from './Dialogs';
import {ProposalCard, ProvisionCard, type Proposal} from './Provision';
import {
  ACTIVE_TASK,
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
  milestoneTree,
  needsYou,
  providerGlyph,
  providersOf,
  stageIndex,
  stateLabel,
  when,
  type GoalView,
  type RecordData,
} from '../types';
import {Chip, Empty, Eyebrow, Glyph, Icon, LiveDot, Mark, Panel, Provider, StageBar, Waveform} from '../ui';

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

// "Tell me what to build": the everyday entry. Quick intents prefill the text, the
// project defaults to "Let Arena decide", and a shared spec (ChatGPT → Share → Arena)
// lands here through the handoff read on load.
const INTENTS = [
  {label: 'Build a new feature', prefix: 'Build a new feature: ', self: false, mode: 'build'},
  {label: 'Fix a bug', prefix: 'Fix a bug: ', self: false, mode: 'fix'},
  {label: 'Improve Arena', prefix: 'Improve Arena: ', self: true, mode: 'improve'},
  {label: 'Add to an existing project', prefix: 'Add to an existing project: ', self: false, mode: 'build'},
];
const MODES = [
  {key: 'build', label: 'Build'},
  {key: 'fix', label: 'Fix'},
  {key: 'improve', label: 'Improve'},
];
const PREFIX = /^(Build a new feature|Fix a bug|Improve Arena|Add to an existing project): ?/;

type Kind = 'existing' | 'new';

function TellArena() {
  const {data, post, notify, disabled, handoff, clearHandoff} = useArena();
  const [draft, setDraft] = useState(handoff);
  const [project, setProject] = useState(AUTO);
  const [asked, setAsked] = useState<Asked | null>(null);
  const [sending, setSending] = useState(false);
  const [kind, setKind] = useState<Kind>('existing');
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [started, setStarted] = useState<RecordData | null>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLSelectElement>(null);
  const speech = useSpeech(text => setDraft(d => (d.trim() ? d.replace(/\s+$/, '') + ' ' : '') + text));
  const self = data.projects.find(p => p.self);
  const enabled = data.projects.filter(p => p.enabled !== false);
  const mode = draft.startsWith('Fix a bug:') ? 'fix' : draft.startsWith('Improve Arena:') ? 'improve' : draft.startsWith('Build a new feature:') ? 'build' : '';

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

  // New project: names first (nothing is created), then the owner's explicit approval.
  const propose = async (description: string) => {
    const text = description.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const r = (await post('projects/proposals', {description: text})) as Proposal;
      setProposal(r);
      setStarted(null);
    } catch (e) {
      notify(String(e));
    } finally {
      setSending(false);
    }
  };
  const approve = async () => {
    if (!proposal || sending) return;
    setSending(true);
    try {
      const r = await post('projects/provision', {proposal_id: proposal.id, revision: 0});
      setStarted(r);
      setProposal(null);
      setDraft('');
      notify(`Creating ${proposal.name} on the Mac and on GitHub.`);
    } catch (e) {
      notify(String(e));
    } finally {
      setSending(false);
    }
  };
  const retry = (p: RecordData) => {
    setStarted(null);
    setProposal(null);
    setDraft(p.description || '');
    propose(p.description || '');
  };
  const provision = started ? (data.provisions || []).find(p => p.id === started.id) || started : null;

  const tapMic = () => {
    if (speech.supported) speech.toggle();
    else {
      box.current?.focus();
      notify('Dictation is not available in this browser. Type below, or use your keyboard microphone.');
    }
  };

  const busy = disabled || sending;
  return (
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-top">
        <h1 id="hero-title" className="hero-eyebrow">
          Build control.
        </h1>
        <Eyebrow className="only-desk">{speech.supported ? 'Voice or text' : 'Text'}</Eyebrow>
      </div>
      <div className="segmented kind" role="group" aria-label="Project">
        {(
          [
            ['existing', 'Existing project'],
            ['new', 'New project'],
          ] as [Kind, string][]
        ).map(([k, label]) => (
          <button
            key={k}
            type="button"
            className={kind === k ? 'on' : ''}
            aria-pressed={kind === k}
            onClick={() => {
              setKind(k);
              setAsked(null);
              if (k === 'existing') {
                setProposal(null);
                setStarted(null);
              }
            }}
          >
            {label}
          </button>
        ))}
      </div>
      {kind === 'existing' && (
      <div className="segmented" role="group" aria-label="Request type">
        {MODES.map(m => {
          const i = INTENTS.find(x => x.mode === m.key && !x.label.startsWith('Add to'))!;
          return (
            <button key={m.key} type="button" className={mode === m.key ? 'on' : ''} aria-pressed={mode === m.key} disabled={i.self && !self} onClick={() => intent(i)}>
              {m.label}
            </button>
          );
        })}
      </div>
      )}
      <div className="mic-stage">
        <Waveform live={speech.listening} />
        <button type="button" className={'mic-big' + (speech.listening ? ' on' : '')} aria-pressed={speech.supported ? speech.listening : undefined} aria-label={speech.listening ? 'Stop listening' : 'Tap to speak'} onClick={tapMic}>
          <span className="mic-ring" />
          <span className="mic-face">
            <Icon name={speech.listening ? 'stop' : 'mic'} size={40} />
          </span>
        </button>
        <Waveform live={speech.listening} />
      </div>
      <div className="hero-copy">
        <p className="hero-title">
          <span className="only-phone">Tell me what to build.</span>
          <span className="only-desk">Voice to Build</span>
        </p>
        <p className="hero-sub">{speech.listening ? 'Listening · speak now, the transcript lands below.' : speech.supported ? 'Tap to speak · or type' : 'Type below · or use your keyboard microphone'}</p>
      </div>
      {speech.listening && (
        <p className="listening" aria-live="polite">
          <LiveDot tone="live" />
          {speech.interim ? speech.interim : 'Listening…'}
        </p>
      )}
      {speech.error && <p className="field-help warn">{speech.error}</p>}
      <div className="field hero-field">
        <label htmlFor="tell-text">What should Arena build?</label>
        <textarea
          id="tell-text"
          ref={box}
          rows={3}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') (kind === 'new' ? propose(draft) : submit(project));
          }}
          placeholder={kind === 'new' ? 'Describe the product you want…' : 'Describe the feature, the bug, or paste the spec you finished in ChatGPT.'}
        />
      </div>
      {kind === 'new' ? (
        <>
          <div className="hero-row single">
            <button type="button" className="btn primary" disabled={busy || !draft.trim()} onClick={() => propose(draft)}>
              <Icon name="plus" size={16} />
              Propose project
            </button>
          </div>
          {proposal && <ProposalCard proposal={proposal} busy={busy} onApprove={approve} onChange={() => setProposal(null)} />}
          {provision && <ProvisionCard provision={provision} onRetry={retry} />}
          <p className="field-help hero-help">Arena proposes a name, a folder on the Mac and a private GitHub repository. Nothing is created until you approve.</p>
        </>
      ) : (
        <>
      <div className="hero-row">
        <div className="field inline">
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
        <button type="button" className="btn primary" disabled={busy || !draft.trim() || !enabled.length} onClick={() => submit(project)}>
          <Icon name="bolt" size={16} />
          Start building
        </button>
      </div>
      {asked && <ProjectAsk asked={asked} busy={busy} onPick={submit} onCancel={() => setAsked(null)} />}
      <div className="chips" role="group" aria-label="Quick intents">
        {INTENTS.map(i => (
          <button key={i.label} type="button" className="chip-btn" disabled={i.self && !self} title={i.self && !self ? 'Arena is not enrolled as a project on this host' : undefined} onClick={() => intent(i)}>
            <span>{i.label}</span>
            <Icon name="arrow" size={14} />
          </button>
        ))}
      </div>
      <p className="field-help hero-help">
        From ChatGPT: Share → Arena, or paste your spec here.{!enabled.length ? ' Enrol a project on the Mac first.' : ''}
      </p>
        </>
      )}
    </section>
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

function BuildRow({view}: {view: GoalView}) {
  const {data, now, openGoal} = useArena();
  const status = goalStatus(view);
  const stage = goalStage(view);
  const providers = providersOf(view, data);
  const live = view.tasks.find(t => ACTIVE_TASK.includes(t.state));
  const current = live?.title || view.children.find(t => t.state !== 'completed' && t.state !== 'cancelled')?.title || view.goal.request;
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
          <span className="clamp1">{current}</span>
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
      </span>
      <span className="build-side">
        <span className="providers">
          {providers.map(p => (
            <Provider key={p} glyph={providerGlyph(p)} size="sm" on={!!live} />
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
        <b className="clamp1">{view.goal.request}</b>
        <span className="small muted">
          <span className={'tone ' + status.tone}>{status.label}</span> · {ago(last, now)}
          {view.project ? ` · ${view.project.name}` : ''}
        </span>
      </span>
      <Icon name="arrow" size={18} />
    </button>
  );
}

export function Overview() {
  const {data, now, disabled, setModal, search} = useArena();
  const q = search.trim().toLowerCase();
  const tasks = data.tasks;
  const working = tasks.filter(t => ACTIVE_TASK.includes(t.state) || t.state === 'integrating');
  const queued = tasks.filter(t => QUEUED_TASK.includes(t.state));
  const day = tasks.filter(t => t.state === 'completed' && t.updated >= now - 86400);
  const integrated24 = day.filter(t => t.integrated_commit).length;
  const needs = needsYou(data);
  const views = (data.goals || []).map(g => goalView(g, data)).sort((a, b) => b.goal.created - a.goal.created);
  const matches = (v: GoalView) => !q || `${v.goal.request} ${v.goal.title || ''} ${v.project?.name || ''} ${v.tasks.map(t => t.title).join(' ')}`.toLowerCase().includes(q);
  const active = views.filter(v => ['active', 'needs'].includes(goalClass(v)) && matches(v));
  const recent = views.filter(v => goalClass(v) === 'done' && matches(v)).slice(0, 6);
  const taskTitle = (id: string) => tasks.find(t => t.id === id)?.title || id;
  const stale = (t: number) => t < now - 86400;
  const needRows = [
    ...needs.approvals.map(a => ({id: a.id, at: a.created as number, tone: 'decision' as const, label: 'Approve', title: taskTitle(a.task_id), sub: `Verified candidate ${a.candidate?.slice(0, 10)} awaits your decision`, open: () => setModal({kind: 'review', id: a.id})})),
    ...needs.answers.map(t => ({id: t.id, at: t.updated as number, tone: 'decision' as const, label: 'Answer', title: t.title, sub: t.question || t.reason, open: () => setModal({kind: 'task', id: t.id})})),
    ...needs.access.map(t => ({id: t.id, at: t.updated as number, tone: 'blocked' as const, label: stateLabel(t.state), title: t.title, sub: t.reason, open: () => setModal({kind: 'task', id: t.id})})),
  ].sort((a, b) => b.at - a.at);
  const fresh = needRows.filter(r => !stale(r.at));
  const earlier = needRows.filter(r => stale(r.at));
  const needRow = (r: (typeof needRows)[number]) => (
    <button className="list-row press" key={r.id} onClick={r.open}>
      <Glyph tone={r.tone} size={32} />
      <span className="list-main">
        <b className="clamp2">{r.title}</b>
        <span className="small muted clamp2">
          <span className={'tone ' + r.tone}>{r.label}</span> · {ago(r.at, now)} · {r.sub}
        </span>
      </span>
      <Icon name="arrow" size={18} />
    </button>
  );

  return (
    <div className="home">
      <aside className="home-side">
        <TellArena />
        <div className="lion-panel" aria-hidden="true">
          <Mark size={200} />
          <span className="tagline">
            Ideas
            <br />
            Agents
            <br />
            Real software
          </span>
        </div>
      </aside>
      <div className="home-main">
        <div className="page-head">
          <div>
            <h2 className="page-title">Overview</h2>
            <p className="page-sub">Your director plans, the agents build, you decide.</p>
          </div>
          <button className="btn primary" disabled={disabled || !data.projects.length} onClick={() => setModal({kind: 'goal'})}>
            <Icon name="plus" size={16} />
            New request
          </button>
        </div>

        <div className="tiles">
          <div className={'tile' + (working.length ? ' hot' : '')}>
            <Glyph tone={working.length ? 'running' : 'off'} />
            <div>
              <div className="v">{working.length}</div>
              <span className="k">Running</span>
              <span className="s">{data.paused ? 'dispatch paused' : `${working.length} / ${data.limit} slots`}</span>
            </div>
          </div>
          <div className="tile">
            <Glyph tone={queued.length ? 'waiting' : 'off'} />
            <div>
              <div className="v">{queued.length}</div>
              <span className="k">Waiting</span>
              <span className="s">queued tasks</span>
            </div>
          </div>
          <div className="tile">
            <Glyph tone={day.length ? 'done' : 'off'} />
            <div>
              <div className="v">{day.length}</div>
              <span className="k">Completed (24h)</span>
              <span className="s">{integrated24} integrated into main</span>
            </div>
          </div>
          <div className={'tile' + (needs.count ? ' warn' : '')}>
            <Glyph tone={needs.count ? 'decision' : 'off'} />
            <div>
              <div className="v">{needs.count}</div>
              <span className="k">Needs you</span>
              <span className="s">{needs.count ? 'decisions waiting' : 'nothing waiting'}</span>
            </div>
          </div>
        </div>

        <Panel title="Active builds" aside={<span className="count num">{active.length} active</span>}>
          <div className="build-list">
            {active.map(v => (
              <BuildRow key={v.goal.id} view={v} />
            ))}
            {!active.length && (
              <div className="panel-body">
                <Empty title={q ? 'No active builds match' : 'No builds running'}>{q ? 'Clear the search to see everything.' : 'Describe what you want changed. Your director inspects the project, plans bounded tasks and coordinates the agents.'}</Empty>
              </div>
            )}
          </div>
        </Panel>

        {needs.count > 0 && (
          <Panel title="Needs you" aside={<span className="count num">{needs.count}</span>} className="accent-amber">
            {fresh.map(needRow)}
            {earlier.length > 0 && (
              <details className="earlier">
                <summary>
                  Earlier <span className="count num">{earlier.length}</span>
                </summary>
                {earlier.map(needRow)}
              </details>
            )}
          </Panel>
        )}

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
    </div>
  );
}
