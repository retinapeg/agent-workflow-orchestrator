import React, {useEffect, useId, useRef, useState} from 'react';
import {useArena, type PostError} from '../App';
import {usePolled} from '../polled';
import {
  ACTIVE_TASK,
  QUEUED_TASK,
  RETRYABLE,
  STORY_TONE,
  UNSTOPPABLE,
  agentName,
  ago,
  goalClass,
  goalSignal,
  goalView,
  isLive,
  plainError,
  progressFraction,
  progressLabel,
  quietLine,
  quietMinutes,
  seconds,
  storyProgress,
  stripLegacy,
  taskSignal,
  workProgress,
  type BlockStory,
  type Failure,
  type GoalStory,
  type RecordData,
  type StoryBlock,
  type StoryNeed,
} from '../types';
import {Avatar, Chip, Icon, LiveDot, Progress, Sha, Wave} from '../ui';

// "Claude (sonnet)" for a block's agent; nothing invented when no agent has started.
const agentLabel = (b: StoryBlock | null | undefined) => (b?.agent ? `${agentName(b.agent)}${b.agent.model ? ` (${b.agent.model})` : ''}` : '');
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
// A running block whose agent the host calls stalled: quiet, never shown as live.
const isQuiet = (b: StoryBlock | null | undefined) => !!b && b.stalled && b.state === 'running';
// The plain titles of blocks seen on a story, so a block's screen never shows its raw task
// title while its own story loads.
const plainTitles = new Map<string, string>();
// When a screen's data was last read, on this device's clock.
const hhmm = (ms: number | null) => (ms ? new Date(ms).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '');

// The screen head shared by the build story and the block detail: back, the subject, its
// state chip and a line under it. A long name is clamped to two lines.
function Head({title, full, sub, chip, avatar, children}: {title: React.ReactNode; full?: string; sub?: React.ReactNode; chip?: React.ReactNode; avatar?: React.ReactNode; children?: React.ReactNode}) {
  const {back} = useArena();
  return (
    <div className="screen-head">
      <button type="button" className="square-btn" aria-label="Back" onClick={back}>
        <Icon name="back" size={20} />
      </button>
      {avatar}
      <div className="screen-head-main">
        <div className="screen-title-row">
          <h1 className="screen-title clamp2" title={full}>
            {title}
          </h1>
          {chip}
        </div>
        {sub && <p className="screen-sub">{sub}</p>}
      </div>
      {children}
    </div>
  );
}

// A refresh failed but the last answer is still on screen: say how old it is, offer Retry.
// Nothing on the screen is presented as live meanwhile.
function StaleBar({at, busy, onRetry}: {at: number | null; busy: boolean; onRetry: () => void}) {
  return (
    <div className="stale-bar" role="status">
      <Icon name="warn" size={16} />
      <span className="stale-text">{`Couldn't refresh — as of ${hhmm(at) || 'earlier'}`}</span>
      <button type="button" className="text-btn" disabled={busy} onClick={onRetry}>
        {busy ? 'Retrying…' : 'Retry'}
      </button>
    </div>
  );
}

// Nothing to show yet and the read failed: why, in plain words, and Retry. A screen that polls
// keeps trying on its own; one that does not (a finished build) waits for Retry.
function ReadFailed({what, error, polling, busy, onRetry}: {what: 'build' | 'block'; error: PostError; polling: boolean; busy: boolean; onRetry: () => void}) {
  const missing = error.status === 404;
  const unreachable = !error.status;
  return (
    <div className="empty">
      <h3>{missing ? `This ${what} could not be found` : unreachable ? "Couldn't reach your Mac" : `Arena could not read this ${what} right now`}</h3>
      <p>{missing ? 'It may have been removed from this Mac.' : polling ? 'Arena keeps trying while this screen is open. You can also tap Retry.' : unreachable ? 'Check the connection, then tap Retry.' : 'Tap Retry.'}</p>
      {!missing && (
        <button type="button" className="btn" disabled={busy} onClick={onRetry}>
          <Icon name="refresh" size={16} />
          {busy ? 'Retrying…' : 'Retry'}
        </button>
      )}
    </div>
  );
}

// Tasks of a build that a Stop can still act on: running, queued or waiting on an answer.
function stoppableTasks(tasks: RecordData[]): RecordData[] {
  return tasks.filter(t => !UNSTOPPABLE.includes(t.state) && t.state !== 'cancelled' && (ACTIVE_TASK.includes(t.state) || t.state === 'awaiting_input' || QUEUED_TASK.includes(t.state)));
}

// What went wrong, in plain English, with the one action that helps (if any) and, always,
// a question to the director about it. Built only from the host's recorded failure; the raw
// reason stays under Advanced.
export function FailureCard({failure, tasks, onAnswer, about}: {failure: Failure; tasks: RecordData[]; onAnswer?: () => void; about: {goal_id: string} | {task_id: string}}) {
  const {disabled, post, notify, openChat, restoredNote} = useArena();
  const [retrying, setRetrying] = useState(false);
  // Every stopped block of this build (or the one block) is tried again, each once.
  const retryable = tasks.filter(t => RETRYABLE.includes(t.state));
  const asking = tasks.find(t => t.state === 'awaiting_input');
  const retry = async () => {
    if (retrying) return;
    setRetrying(true);
    try {
      let note = '';
      for (const t of retryable) note = restoredNote(`tasks/${t.id}/retry`, await post(`tasks/${t.id}/retry`, {revision: t.rev})) || note;
      notify('Arena is trying that again.' + note);
    } catch (e) {
      notify(plainError(e));
    } finally {
      setRetrying(false);
    }
  };
  const ask = () => openChat({text: 'goal_id' in about ? 'What went wrong with this build?' : 'What went wrong with this part of the build?', about});
  let button: React.ReactNode = null;
  if ((failure.action === 'retry' || failure.action === 'sign_in' || failure.action === 'permission') && retryable.length)
    button = (
      <button type="button" className="btn primary" disabled={disabled || retrying} onClick={retry}>
        <Icon name="refresh" size={16} />
        {failure.action === 'retry' ? 'Try again' : "I've done it — try again"}
      </button>
    );
  else if (failure.action === 'answer' && asking && onAnswer)
    button = (
      <button type="button" className="btn primary" onClick={onAnswer}>
        <Icon name="chat" size={16} />
        Answer the question
      </button>
    );
  return (
    <section className="failure-card" aria-label="What went wrong">
      <div className="failure-title">
        <Icon name="warn" size={20} />
        <h2>What went wrong</h2>
      </div>
      <p className="failure-what">{failure.what_failed || 'Arena recorded a failure but not what failed.'}</p>
      <dl className="failure-facts">
        {failure.intended && (
          <div>
            <dt>It was meant to</dt>
            <dd>{failure.intended}</dd>
          </div>
        )}
        <div>
          <dt>Can it recover?</dt>
          <dd>{failure.recoverable ? 'Yes' : 'Not on its own'}</dd>
        </div>
        {failure.tried?.length > 0 && (
          <div>
            <dt>Already tried</dt>
            <dd>
              <ul>
                {failure.tried.map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        {failure.next && (
          <div>
            <dt>What happens next</dt>
            <dd>{failure.next}</dd>
          </div>
        )}
        <div>
          <dt>Do you need to act?</dt>
          <dd>{failure.needs_you ? 'Yes' : 'No'}</dd>
        </div>
      </dl>
      <div className="btn-row">
        {button}
        <button type="button" className={'btn' + (button ? '' : ' primary')} onClick={ask}>
          <Icon name="chat" size={16} />
          Ask the director
        </button>
      </div>
    </section>
  );
}

// What the build waits on the owner for: the director's question with an answer box, an
// agent that needs sign-in or permission on the Mac, or a verified change to decide on.
function NeedsCard({needs, tasks}: {needs: StoryNeed[]; tasks: RecordData[]}) {
  const {data, disabled, action, setModal} = useArena();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  if (!needs.length) return null;
  return (
    <section className="card needs-card accent-amber" aria-label="Needs you">
      <span className="eyebrow">Needs you</span>
      {needs.map((n, i) => {
        const task = tasks.find(t => t.id === n.task_id);
        const approval = n.kind === 'approve' ? data.approvals.find(a => a.state === 'pending' && a.task_id === n.task_id) : undefined;
        return (
          <div className="need" key={n.task_id + n.kind + i}>
            <p>{n.text}</p>
            {n.kind === 'answer' && task && (
              <>
                <label htmlFor={'answer-' + n.task_id}>Your answer</label>
                <textarea id={'answer-' + n.task_id} rows={3} value={answers[n.task_id] || ''} onChange={e => setAnswers(a => ({...a, [n.task_id]: e.target.value}))} />
                <button type="button" className="btn primary" disabled={disabled || !(answers[n.task_id] || '').trim()} onClick={() => action(`tasks/${n.task_id}/answer`, {revision: task.rev, text: answers[n.task_id]}, 'Your answer is with the director.')}>
                  Continue with my answer
                </button>
              </>
            )}
            {n.kind === 'approve' && approval && (
              <button type="button" className="btn primary" onClick={() => setModal({kind: 'review', id: approval.id})}>
                <Icon name="review" size={16} />
                Review and decide
              </button>
            )}
            {(n.kind === 'sign_in' || n.kind === 'permission') && task && RETRYABLE.includes(task.state) && (
              <button type="button" className="btn" disabled={disabled} onClick={() => action(`tasks/${n.task_id}/retry`, {revision: task.rev}, 'Arena is trying that again.')}>
                <Icon name="refresh" size={16} />
                {"I've done it — try again"}
              </button>
            )}
          </div>
        );
      })}
    </section>
  );
}

// One story block on the build's timeline. A quiet (stalled) agent says when it last did
// something instead of looking live; a stale screen shows nothing as live.
function BlockCard({b, goalId, stale}: {b: StoryBlock; goalId: string; stale: boolean}) {
  const {data, now, openBlock} = useArena();
  const tone = STORY_TONE[b.state] || 'off';
  const needs = b.connects.filter(c => c.relation === 'needs').map(c => c.title);
  const unblocks = b.connects.filter(c => c.relation === 'unblocks').map(c => c.title);
  const quiet = isQuiet(b);
  const text = quiet ? quietLine(b, data.attempts, now) : b.now || b.purpose;
  const lit = !!b.now && !quiet && !stale;
  const descId = 'block-desc-' + b.id;
  const family = b.agent?.provider === 'claude' ? 'claude' : b.agent?.provider === 'codex' ? 'codex' : 'other';
  const agent = agentLabel(b) || (b.state === 'completed' ? 'Agent not recorded' : 'No agent yet');
  plainTitles.set(b.id, b.title);
  return (
    <li className={`story-block ${tone}`}>
      <span className="timeline-dot" aria-hidden="true" />
      <button
        type="button"
        className={`block-card ${tone} agent-${family}${quiet ? ' quiet' : ''}`}
        aria-label={`${b.title} · ${agent} · ${b.state_label}${quiet ? ` · quiet for ${quietMinutes(b, data.attempts, now)} min` : ''}`}
        aria-describedby={descId}
        onClick={() => openBlock(b.id, goalId)}
      >
        <Avatar provider={b.agent?.provider} />
        <span className="block-main">
          <span className="block-top">
            <span className="block-agent">{agent}</span>
            <Chip tone={tone}>{b.state_label}</Chip>
          </span>
          <b className="block-title">{b.title}</b>
          <span className={'block-text clamp3' + (quiet ? ' block-stalled' : '')} id={descId}>
            {lit && <LiveDot tone="live" />}
            {text || 'No live activity yet'}
          </span>
          {(needs.length > 0 || unblocks.length > 0) && (
            <span className="block-links small muted clamp2">
              {needs.length > 0 && `Waits for ${needs.join(', ')}`}
              {needs.length > 0 && unblocks.length > 0 && ' · '}
              {unblocks.length > 0 && `Unblocks ${unblocks.join(', ')}`}
            </span>
          )}
        </span>
        <Icon name="arrow" size={18} />
        {b.state === 'running' && !quiet && !stale && <Wave />}
      </button>
    </li>
  );
}

// A ⋯ menu (a menu button) of a few actions. Arrow keys move between the items; Escape
// closes it and returns to ⋯. With no action to offer it is not shown at all.
type MoreItem = {label: string; icon: string; run: () => void; danger?: boolean};
function MoreMenu({label, items}: {label: string; items: MoreItem[]}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const buttons = () => [...(list.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') || [])];
  const close = (refocus: boolean) => {
    setOpen(false);
    if (refocus) toggle.current?.focus();
  };
  useEffect(() => {
    if (!open) return;
    buttons()[0]?.focus({preventScroll: true});
    const outside = (e: Event) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      close(true);
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', outside);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);
  const onKey = (e: React.KeyboardEvent) => {
    const all = buttons();
    const at = all.indexOf(document.activeElement as HTMLElement);
    const move = (i: number) => {
      e.preventDefault();
      all[(i + all.length) % all.length]?.focus();
    };
    if (e.key === 'ArrowDown') move(at + 1);
    else if (e.key === 'ArrowUp') move(at - 1);
    else if (e.key === 'Home') move(0);
    else if (e.key === 'End') move(all.length - 1);
    else if (e.key === 'Tab') setOpen(false);
  };
  const pick = (run: () => void) => () => {
    setOpen(false);
    run();
  };
  if (!items.length) return null;
  return (
    <div className="more-wrap" ref={wrap}>
      <button ref={toggle} type="button" className="square-btn" aria-label={label} aria-haspopup="menu" aria-expanded={open} aria-controls={open ? menuId : undefined} onClick={() => setOpen(o => !o)}>
        <Icon name="more" size={22} />
      </button>
      {open && (
        <div className="more-menu" role="menu" id={menuId} aria-label={label} ref={list} onKeyDown={onKey}>
          {items.map(item => (
            <button key={item.label} type="button" role="menuitem" tabIndex={-1} className={item.danger ? 'danger' : undefined} onClick={pick(item.run)}>
              <Icon name={item.icon} size={16} />
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// The build's ⋯ menu: Stop build while it can be stopped, Archive once it has finished,
// Restore when archived, and in Advanced mode the technical build view.
function StoryMenu({goalId, onStop, canStop, canArchive, onArchive, onRestore}: {goalId: string; onStop: () => void; canStop: boolean; canArchive: boolean; onArchive: () => void; onRestore?: () => void}) {
  const {openAdvanced, advanced} = useArena();
  const items: MoreItem[] = [];
  if (canStop) items.push({label: 'Stop build', icon: 'stop', run: onStop, danger: true});
  if (canArchive) items.push({label: 'Archive build', icon: 'archive', run: onArchive});
  if (onRestore) items.push({label: 'Restore build', icon: 'undo', run: onRestore});
  if (advanced) items.push({label: 'Technical build view', icon: 'code', run: () => openAdvanced(goalId)});
  return <MoreMenu label="Build options" items={items} />;
}

// The line under a build's name: when it started and who is on it (an agent gone quiet is not
// counted as working), or how it ended.
function storyLine(s: GoalStory, now: number, attempts: RecordData[]): string {
  const started = s.started ? `Started ${ago(s.started, now)}` : 'Not started yet';
  if (s.status === 'running') {
    const quiet = (s.blocks || []).filter(isQuiet);
    const working = Math.max(0, s.agents_working - quiet.length);
    const quietFor = quiet.length ? `quiet for ${Math.max(...quiet.map(b => quietMinutes(b, attempts, now)))} min` : '';
    if (working) return `${started} · ${plural(working, 'agent')} working${quiet.length ? ` · ${quiet.length} ${quietFor}` : ''}`;
    if (quiet.length) return `${started} · ${plural(quiet.length, 'agent')} ${quietFor}`;
    return `${started} · no agent on it right now`;
  }
  if (s.status === 'queued') return `${started} · waiting for an agent`;
  if (s.status === 'needs_you') return `${started} · waiting for you`;
  const ended = s.updated ? ago(s.updated, now) : '';
  if (s.status === 'completed') return `Finished ${ended}`.trim();
  if (s.status === 'blocked') return `Stopped ${ended}`.trim();
  return `Cancelled ${ended}`.trim();
}

// Screen 2: one build as plain-English story blocks on a timeline, in dependency order.
export function BuildStory({goalId}: {goalId: string}) {
  const {data, now, post, notify, disabled, setTab, advanced} = useArena();
  const goal = (data.goals || []).find(g => g.id === goalId);
  const view = goal ? goalView(goal, data) : null;
  const live = !!view && isLive(view);
  // Re-read on this build's own records changing (and every few seconds while it is live).
  const {data: story, error, loading, busy, at, reload} = usePolled<GoalStory>(`goals/${goalId}/story`, live, goalSignal(view, data));
  const [confirming, setConfirming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const confirmRef = useRef<HTMLDivElement>(null);
  const answerFocus = () => {
    const box = document.querySelector<HTMLTextAreaElement>('.needs-card textarea');
    box?.focus();
    box?.scrollIntoView({block: 'center'});
  };

  const tasks = view?.tasks || [];
  const stoppable = stoppableTasks(tasks);
  const cls = view ? goalClass(view) : null;
  const finished = story ? ['completed', 'blocked', 'cancelled'].includes(story.status) : cls === 'done' || cls === 'history';
  const running = story ? story.status === 'running' : live;
  const stale = !!story && !!error;

  const askStop = () => {
    setConfirming(true);
    window.requestAnimationFrame(() => confirmRef.current?.scrollIntoView({block: 'center'}));
  };
  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    try {
      for (const t of stoppable) await post(`tasks/${t.id}/cancel`, {revision: t.rev});
      notify('Stopping the build. The agents stop now; their work so far is kept.');
      setConfirming(false);
    } catch (e) {
      notify(plainError(e));
    } finally {
      setStopping(false);
    }
  };
  const archive = async () => {
    try {
      const r = await post('goals/archive', {ids: [goalId]});
      if ((r?.archived || []).includes(goalId)) {
        notify('Build archived. It stays under Archived in the menu, with all its history.');
        setTab('overview');
      } else notify(`Not archived: ${r?.skipped?.[0]?.reason || 'your Mac kept it'}.`);
    } catch (e) {
      notify(plainError(e));
    }
  };

  const restore = async () => {
    try {
      const r = await post('goals/unarchive', {ids: [goalId]});
      notify((r?.restored || []).includes(goalId) ? 'Build restored. It is back in Build history.' : 'Your Mac did not restore this build.');
    } catch (e) {
      notify(plainError(e));
    }
  };

  const projectName = story?.project?.name || view?.project?.name || 'Build';
  const title = stripLegacy(story?.title || view?.title || '');
  // The story's own progress once it has answered; until then the snapshot's reckoning of the
  // same formula, so the bar does not jump.
  const progress = storyProgress(story, view ? workProgress(view, data) : {done: 0, total: 0});
  const tone = story ? STORY_TONE[story.status] || 'off' : 'off';

  if (!story && error)
    return (
      <div className="screen story">
        <Head title={projectName} full={projectName}>
          <StoryMenu goalId={goalId} canStop={false} onStop={askStop} canArchive={false} onArchive={archive} />
        </Head>
        {title && <p className="story-title">{title}</p>}
        <ReadFailed what="build" error={error} polling={live} busy={busy} onRetry={() => reload()} />
      </div>
    );

  return (
    <div className="screen story">
      <Head title={projectName} full={projectName} chip={story ? <Chip tone={tone}>{story.status_label}</Chip> : null} sub={story ? storyLine(story, now, data.attempts) : loading ? 'Reading the build…' : ''}>
        <StoryMenu goalId={goalId} canStop={stoppable.length > 0} onStop={askStop} canArchive={finished && !goal?.archived} onArchive={archive} onRestore={goal?.archived ? restore : undefined} />
      </Head>
      {title && <p className="story-title">{title}</p>}
      {stale && <StaleBar at={at} busy={busy} onRetry={() => reload()} />}
      {progress.total > 0 && (
        <div className="story-progress">
          <Progress done={progress.done} total={progress.total} fraction={progressFraction(progress)} tone={tone === 'off' ? 'off' : tone} label={progressLabel(progress)} />
          <span className="small muted num">{progressLabel(progress)}</span>
        </div>
      )}
      {goal?.archived && (
        <p className="note archived-note">
          <Icon name="archive" size={16} /> This build is archived, out of Home and Build history. Restore it from ⋯ or from Archived in the menu.
        </p>
      )}
      {story?.failure && <FailureCard failure={story.failure} tasks={tasks} onAnswer={answerFocus} about={{goal_id: goalId}} />}
      {story && <NeedsCard needs={story.needs_you || []} tasks={tasks} />}
      {story?.summary && <p className="lede story-summary">{story.summary}</p>}

      {story && story.blocks.length > 0 && (
        <ol className="timeline-blocks" aria-label="Story blocks">
          {story.blocks.map(b => (
            <BlockCard key={b.id} b={b} goalId={goalId} stale={stale} />
          ))}
        </ol>
      )}
      {story && !story.blocks.length && (
        <div className="empty">
          <h3>{running || story.status === 'queued' ? 'The director is still planning this build' : 'No work was recorded for this build'}</h3>
          <p>{running || story.status === 'queued' ? 'Blocks appear here as soon as the plan exists.' : advanced ? 'Open the technical build view from the ⋯ menu for the record.' : 'Its technical record is in Advanced mode, at the bottom of the ☰ menu.'}</p>
        </div>
      )}
      {!story && loading && <p className="muted small loading-line">Reading the build…</p>}

      {confirming && stoppable.length > 0 && (
        <div className="card confirm-card" ref={confirmRef} role="group" aria-label="Stop this build?">
          <h2>Stop this build?</h2>
          <p className="small muted">The agents stop now. Work so far is kept, and you can ask for it again later.</p>
          <div className="btn-row">
            <button type="button" className="btn danger" disabled={disabled || stopping} onClick={stop}>
              <Icon name="stop" size={16} />
              {stopping ? 'Stopping…' : 'Yes, stop the build'}
            </button>
            <button type="button" className="btn" onClick={() => setConfirming(false)}>
              Keep building
            </button>
          </div>
        </div>
      )}
      {running && stoppable.length > 0 && !confirming && (
        <button type="button" className="btn stop-build" disabled={disabled} onClick={askStop}>
          <Icon name="stop" size={16} />
          Stop build
        </button>
      )}
    </div>
  );
}

const RAIL_STATE: Record<string, string> = {done: 'done', current: 'in progress', todo: 'not started', failed: 'failed'};

// Screen 3: one block in plain English, its progress rail, recent updates and decisions,
// any failure, the technical record folded under Advanced, and a question box.
export function BlockDetail({taskId, goalId}: {taskId: string; goalId: string | null}) {
  const {data, now, openChat, openGoal, setModal, advanced, openAdvanced} = useArena();
  const task = data.tasks.find(t => t.id === taskId);
  // In Advanced mode the block's ⋯ opens its build's technical view; otherwise it has none.
  const parent = goalId || (task?.goal_id as string | undefined) || null;
  const more = advanced && parent ? <MoreMenu label="Block options" items={[{label: 'Technical build view', icon: 'code', run: () => openAdvanced(parent)}]} /> : null;
  const live = !!task && (ACTIVE_TASK.includes(task.state) || task.state === 'integrating' || QUEUED_TASK.includes(task.state));
  // Re-read on this block's own task, attempts and approvals changing (and while it is live).
  const {data: story, error, loading, busy, at, reload} = usePolled<BlockStory>(`tasks/${taskId}/story`, live, taskSignal(task, data));
  const [question, setQuestion] = useState('');
  const b = story?.block;
  const tone = b ? STORY_TONE[b.state] || 'off' : 'off';
  const goalTasks = task ? data.tasks.filter(t => t.goal_id === task.goal_id) : [];
  const stale = !!story && !!error;
  const quiet = isQuiet(b);
  const known = plainTitles.get(taskId);
  const ask = (e: React.FormEvent) => {
    e.preventDefault();
    const text = question.trim();
    if (!text) return;
    openChat({text, about: {task_id: taskId}});
  };
  // The director's question is answered on the build's story, where the answer box is.
  const answerFocus = () => {
    if (parent) openGoal(parent);
  };

  if (!story && error)
    return (
      <div className="screen block-detail">
        <Head title={known || 'This block'}>{more}</Head>
        <ReadFailed what="block" error={error} polling={live} busy={busy} onRetry={() => reload()} />
      </div>
    );

  // Live only while the host says the block is working now and the screen is current.
  const isLiveNow = !!b && !quiet && !stale && (b.state === 'running' || !!story?.now);
  // The first update: a quiet agent's last activity; else what it is doing now (as of the last
  // good read when a refresh failed).
  const nowRow =
    b && quiet ? (
      <li className="now quiet">
        <i className="dot warn" aria-hidden="true" />
        <span className="when small">Quiet</span>
        <span>{quietLine(b, data.attempts, now)}</span>
      </li>
    ) : story?.now ? (
      <li className="now">
        <i className={'dot ' + (stale ? 'neutral' : 'running')} aria-hidden="true" />
        <span className="when small num">{stale ? hhmm(at) : 'Now'}</span>
        <span>{story.now}</span>
      </li>
    ) : null;
  // Never the raw task title: the block's plain title, the one tapped on the story, or a
  // placeholder until the story answers.
  const heading = b?.title || known || (
    <span className="skeleton-line" role="img" aria-label="Loading the block">
      <span />
    </span>
  );
  return (
    <div className="screen block-detail">
      <Head title={heading} full={b?.title || known} avatar={<Avatar provider={b?.agent?.provider} size="lg" />} chip={b ? <Chip tone={tone}>{b.state_label}</Chip> : null} sub={b ? agentLabel(b) || 'No agent has worked on this yet' : loading ? 'Reading the block…' : ''}>
        {more}
      </Head>
      {stale && <StaleBar at={at} busy={busy} onRetry={() => reload()} />}

      {story && (
        <>
          <section className="card plain-card" aria-labelledby="block-doing">
            <h2 id="block-doing">What this block is doing</h2>
            <p className="lede">{story.what || 'Arena has not recorded what this block builds.'}</p>
            {story.why && <p className="lede">{story.why}</p>}
            {story.connects_text && <p className="lede muted-lede">{story.connects_text}</p>}
          </section>

          <section className="card" aria-labelledby="block-progress">
            <h2 id="block-progress">Progress</h2>
            <ol className="progress-rail" aria-label="Stages">
              {(story.stage_rail || []).map(s => (
                <li key={s.label} className={s.state} aria-label={`${s.label}: ${RAIL_STATE[s.state] || s.state}`}>
                  <i>{s.state === 'done' ? <Icon name="check" size={14} /> : s.state === 'failed' ? <Icon name="x" size={14} /> : null}</i>
                  <span>{s.label}</span>
                </li>
              ))}
            </ol>
          </section>

          {story.failure && <FailureCard failure={story.failure} tasks={task ? [task] : goalTasks} onAnswer={answerFocus} about={{task_id: taskId}} />}

          <section className="card updates-card" aria-labelledby="block-updates">
            <div className="row between">
              <h2 id="block-updates">Recent updates</h2>
              {isLiveNow && <Chip tone="running">Live</Chip>}
            </div>
            {nowRow || story.updates.length ? (
              <ul className="updates">
                {nowRow}
                {story.updates.map((u, i) => (
                  <li key={i}>
                    <i className={'dot ' + (u.tone || 'neutral')} aria-hidden="true" />
                    <span className="when small num">{u.t ? ago(u.t, now) : ''}</span>
                    <span>{u.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="small muted updates-empty">Nothing has happened on this block yet.</p>
            )}
          </section>

          {story.decisions?.length > 0 && (
            <section className="card" aria-labelledby="block-decisions">
              <h2 id="block-decisions">Decisions</h2>
              <ul className="decisions">
                {story.decisions.map((d, i) => (
                  <li key={i}>{d}</li>
                ))}
              </ul>
            </section>
          )}

          <details className="disclosure advanced">
            <summary>Advanced</summary>
            <div className="disclosure-body advanced-body">
              <h3 className="section-title">Attempts</h3>
              {story.advanced.attempts.length ? (
                <ul className="adv-list">
                  {story.advanced.attempts.map(a => (
                    <li key={a.id}>
                      <span>
                        {a.phase} · {agentName(a)} {a.model}
                      </span>
                      <span className="small muted num">
                        {a.state}
                        {a.seconds != null ? ` · ${seconds(a.seconds)}` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="small muted">No attempts recorded.</p>
              )}
              <h3 className="section-title">Checks</h3>
              {story.advanced.checks.length ? (
                <div className="checks">
                  {story.advanced.checks.map(c => (
                    <Chip key={c.name} tone={c.status === 'PASS' ? 'pass' : 'fail'}>
                      {c.name} · {c.status}
                    </Chip>
                  ))}
                </div>
              ) : (
                <p className="small muted">No checks recorded.</p>
              )}
              {(story.advanced.candidate || story.advanced.integrated_commit) && (
                <>
                  <h3 className="section-title">Commits</h3>
                  <p className="path">
                    {story.advanced.candidate && (
                      <>
                        Candidate <Sha value={story.advanced.candidate} />{' '}
                      </>
                    )}
                    {story.advanced.integrated_commit && (
                      <>
                        On main <Sha value={story.advanced.integrated_commit} />
                      </>
                    )}
                  </p>
                </>
              )}
              {story.advanced.scope.length > 0 && (
                <>
                  <h3 className="section-title">Scope</h3>
                  <p className="path">{story.advanced.scope.join(' · ')}</p>
                </>
              )}
              <button type="button" className="btn" onClick={() => setModal({kind: 'task', id: taskId})}>
                <Icon name="doc" size={16} />
                Transcripts and evidence
              </button>
            </div>
          </details>
        </>
      )}
      {!story && loading && <p className="muted small loading-line">Reading the block…</p>}

      <form className="ask-bar" onSubmit={ask}>
        <Icon name="chat" size={20} />
        <label htmlFor="block-question" className="sr-only">
          Ask a question about this block
        </label>
        <input id="block-question" type="text" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask a question about this block…" enterKeyHint="send" autoComplete="off" />
        <button type="submit" className="send-btn" aria-label="Ask the director" disabled={!question.trim()}>
          <Icon name="send" size={18} />
        </button>
      </form>
    </div>
  );
}
