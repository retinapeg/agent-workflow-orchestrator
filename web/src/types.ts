// Shared types and pure helpers for the Arena build-control UI.
// Everything here derives from the real /api/v1/overview snapshot; nothing is simulated.

export type RecordData = {[key: string]: any};

export type Snapshot = {
  projects: RecordData[];
  tasks: RecordData[];
  attempts: RecordData[];
  agents: RecordData[];
  approvals: RecordData[];
  events: RecordData[];
  goals: RecordData[];
  provisions?: RecordData[];
  conversations?: Conversation[];
  paused: boolean;
  limit: number;
  revision: number;
  heartbeat: number | null;
  server_time: number;
  version: string;
};

// Snapshot rows carry whatever the host records; these name the fields the UI relies on
// from the usability contract. Every one is optional so an older host still renders.
export type Goal = RecordData & {id: string; project_id: string; request: string; title?: string; created: number; display_title?: string};
export type Project = RecordData & {id: string; name: string; archived?: boolean; enabled?: boolean};

// GET /api/v1/goals/{id}: the full records behind one request, fetched on demand.
export type GoalDetail = {goal: Goal; tasks: RecordData[]; attempts: RecordData[]; events: RecordData[]};

// GET /api/v1/attempts/{id}/live: the tail of the agent's transcript while (and after) it runs.
export type LiveKind = 'assistant' | 'tool' | 'command' | 'file' | 'test' | 'error' | 'system';
export type LiveLine = {t: number | null; kind: LiveKind; text: string};
export type LiveTail = {
  state: string;
  phase: string;
  model: string;
  provider: string;
  started: number | null;
  elapsed: number | null;
  bytes: number;
  done: boolean;
  lines: LiveLine[];
};

// Screens. 'overview' is Home; 'story' and 'block' are the plain-English build and block
// views; 'build' is the technical build view (reached as "Advanced"); 'builds' is the
// history list; 'mac' is What's running on my Mac.
export type Tab = 'overview' | 'chat' | 'story' | 'block' | 'mac' | 'builds' | 'build' | 'projects' | 'agents' | 'review' | 'archived';

// A request's short name. The host supplies `display_title`; an older host does not, so the
// same rule runs here: drop a leading OUTCOME:/Outcome:/Request: label and markdown, keep the
// first sentence, cap at 72 characters with an ellipsis.
export function deriveTitle(text: string | null | undefined, max = 72): string {
  let s = String(text || '')
    .replace(/\r\n/g, '\n')
    .trim()
    .replace(/^(?:\*\*)?(?:outcome|request)(?:\*\*)?\s*:\s*/i, '')
    .replace(/^#+\s*/, '')
    .replace(/[*_`]/g, '')
    .trim();
  const nl = s.indexOf('\n');
  if (nl > 0) s = s.slice(0, nl).trim();
  const m = /^(.+?[.!?])(?:\s|$)/.exec(s);
  if (m && m[1].length >= 12) s = m[1];
  s = s.replace(/[.\s]+$/, '').trim();
  if (s.length > max) s = s.slice(0, max - 1).replace(/\s+\S*$/, '').trim() + '…';
  return s;
}
// Requests once carried the composer's chip as a prefix ("Add to an existing project: …");
// plain views never show it, whichever host wrote the title.
// The same chips the host strips (titles.strip_legacy), stacked ones too.
const LEGACY_PREFIX = /^\s*(?:add to an existing project|build a new feature|fix a bug|improve arena|build|fix|improve)\s*:\s*/i;
export function stripLegacy(title: string): string {
  const original = String(title || '').trim();
  let value = original;
  for (let previous = ''; value !== previous; ) {
    previous = value;
    value = value.replace(LEGACY_PREFIX, '');
  }
  return value && value !== original ? value[0].toUpperCase() + value.slice(1) : original;
}
export const displayTitle = (goal: RecordData | null | undefined): string => stripLegacy((goal?.display_title as string) || deriveTitle(goal?.request || goal?.title));

export const isArchived = (project: RecordData | null | undefined): boolean => project?.archived ?? false;
// Projects a request can be sent to: enrolled, not archived. Pickers never list archived ones.
export const pickableProjects = (snap: Snapshot): RecordData[] => snap.projects.filter(p => !isArchived(p));

// Conversations with the director (GET /api/v1/conversations, snapshot.conversations).
export type PlanTask = {title: string; instructions?: string; scope?: string[]; dependencies?: number[]; acceptance?: string[]; complexity?: string; risk?: string};
export type PlanMilestone = {code: string; title: string; tasks?: PlanTask[]};
export type Spec = {outcome?: string; acceptance?: string[]; constraints?: string[]; notes?: string; plan?: {milestones?: PlanMilestone[]} | null} | null;
// The owner's own words start a build; the exact phrase depends on whether a plan exists.
export const planTasks = (spec: Spec): PlanTask[] => (spec?.plan?.milestones || []).flatMap(m => m.tasks || []);
export const buildPhrase = (spec: Spec) => (planTasks(spec).length ? 'Execute this plan' : 'Build it');
export type Conversation = {
  id: string;
  rev: number;
  title: string;
  created: number;
  updated: number;
  project_id: string | null;
  spec: Spec;
  status: 'idle' | 'thinking';
  builds?: {goal_id: string; task_id: string; time: number}[];
  // What the director is doing while status is 'thinking' (host clock).
  step?: string | null;
  step_detail?: string;
  step_since?: number | null;
  turn_started?: number | null;
  turn_repo?: string | null;
  commands?: number;
};
export type ChatMessage = {
  id: number;
  time: number;
  role: 'user' | 'assistant';
  text: string;
  model?: string;
  effort?: string | null;
  provider?: string;
  seconds?: number;
  inspected?: string | null;
  project_selected?: string;
  offer_build?: boolean;
  clarify?: boolean;
  build_started?: boolean;
  goal_id?: string;
  task_id?: string;
  project?: string;
  launch_error?: string;
  error?: boolean;
  error_kind?: string;
  detail?: string | null;
  retry_text?: string | null;
  // A reply about what is running on the Mac carries the discovery it answered from.
  mac_activity?: MacActivity | null;
};
export type ConversationDetail = Conversation & {project: {id: string; name: string} | null; messages: ChatMessage[]};

export const ACTIVE_ATTEMPT = ['leased', 'running', 'verifying'];
export const ACTIVE_TASK = ['leased', 'running', 'verifying', 'reviewing', 'recovering'];
export const QUEUED_TASK = ['ready', 'waiting_capacity', 'waiting_dependency', 'cooldown', 'repair_queued'];
export const DECISION_TASK = ['ready_for_approval', 'awaiting_input'];
export const BLOCKED_TASK = ['blocked', 'interrupted', 'needs_auth', 'needs_permission'];
export const UNSTOPPABLE = ['completed', 'ready_for_approval', 'integrating', 'cancelled'];
export const RETRYABLE = ['interrupted', 'blocked', 'needs_auth', 'needs_permission'];

export type Family = 'running' | 'waiting' | 'decision' | 'blocked' | 'done' | 'off';

export function family(state: string): Family {
  if (ACTIVE_TASK.includes(state) || state === 'integrating') return 'running';
  if (QUEUED_TASK.includes(state)) return 'waiting';
  if (DECISION_TASK.includes(state)) return 'decision';
  if (BLOCKED_TASK.includes(state)) return 'blocked';
  if (state === 'completed') return 'done';
  return 'off';
}

export function attemptTone(state: string): Family | 'pass' | 'fail' {
  if (ACTIVE_ATTEMPT.includes(state)) return 'running';
  if (state === 'passed') return 'pass';
  if (['failed', 'needs_auth', 'needs_permission', 'model_unavailable'].includes(state)) return 'fail';
  if (['rate_limited', 'transient', 'interrupted'].includes(state)) return 'decision';
  return 'off';
}

const LABELS: Record<string, string> = {
  ready: 'Ready',
  waiting_capacity: 'Waiting for a slot',
  waiting_dependency: 'Waiting on dependency',
  cooldown: 'Cooling down',
  repair_queued: 'Repair queued',
  leased: 'Starting',
  running: 'Running',
  verifying: 'Verifying',
  reviewing: 'In review',
  recovering: 'Recovering',
  integrating: 'Integrating',
  ready_for_approval: 'Ready for approval',
  awaiting_input: 'Needs your answer',
  completed: 'Completed',
  blocked: 'Blocked',
  interrupted: 'Interrupted',
  needs_auth: 'Needs sign-in',
  needs_permission: 'Needs permission',
  cancelled: 'Cancelled',
};

export const human = (s: string) => (s || '').replaceAll('_', ' ');
export const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
export const stateLabel = (state: string) => LABELS[state] || cap(human(state));

export function duration(secs: number): string {
  const s = Math.max(0, Math.floor(secs));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
}

export const seconds = (n?: number | null) =>
  typeof n === 'number' ? (n < 10 ? n.toFixed(1) : String(Math.round(n))) + 's' : '';

export const shortSha = (s?: string | null, n = 10) => (s ? String(s).slice(0, n) : '');

export const clock = (epoch?: number | null) =>
  epoch ? new Date(epoch * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : '';

export const when = (epoch?: number | null) =>
  epoch
    ? new Date(epoch * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})
    : '';

export const num = (n: number) => n.toLocaleString();

export const providerName = (provider: string) =>
  provider === 'codex' ? 'OpenAI Codex CLI' : provider === 'claude' ? 'Anthropic Claude CLI' : cap(provider || '');

export const providerGlyph = (provider: string) => (provider === 'codex' ? 'Cx' : provider === 'claude' ? 'Cl' : '··');

// Pipeline stage index of a task, derived only from its recorded state and phase.
// 0 plan · 1 build · 2 verify · 3 review · 4 approve · 5 integrate · 6 done
export function stageIndex(task: RecordData): number {
  const s = task.state as string;
  if (s === 'completed') return 6;
  if (s === 'cancelled') return -1;
  if (task.phase === 'plan' || task.phase === 'replan') return 0;
  if (s === 'integrating') return 5;
  if (s === 'ready_for_approval') return 4;
  if (s === 'reviewing' || s === 'recovering' || task.phase === 'review' || task.phase === 'direct') return 3;
  if (s === 'verifying') return 2;
  return 1;
}

export const STAGES = ['Plan', 'Build', 'Verify', 'Review', 'Approve', 'Integrate'];

// The director's (or planner's) own task: the root that plans the work. Build tasks carry the
// planner attempt that created them. The same rule as the host's story (is_plan_task), so a
// build's blocks, outcome and progress are reckoned over the same tasks on both sides.
export const isPlanTask = (t: RecordData): boolean => !t.planner_attempt && (t.phase === 'plan' || t.phase === 'replan' || 'planned_tasks' in t);

// The newest approval that covers a task (one approval may cover several tasks).
export function approvalOf(task: RecordData, approvals: RecordData[] | undefined): RecordData | undefined {
  let best: RecordData | undefined;
  for (const a of approvals || []) {
    if (a.task_id !== task.id && !(a.covered_tasks || []).includes(task.id)) continue;
    if (!best || (a.created || 0) > (best.created || 0)) best = a;
  }
  return best;
}
// A verified change the host tried to merge into main and could not: the task still reads
// "ready for approval", but nothing is on its way to main (the host's block is "blocked").
export const mergeFailed = (task: RecordData, approvals: RecordData[] | undefined): boolean =>
  task.state === 'ready_for_approval' && approvalOf(task, approvals)?.publication?.state === 'failed';
// Tasks whose verified change is being merged into main now (a publication queued or
// running, whatever the policy, also once the task reads completed: story.MERGING).
function mergingTasks(tasks: RecordData[], approvals: RecordData[] | undefined): Set<string> {
  const ids = new Set(tasks.map(t => t.id as string));
  const out = new Set<string>();
  for (const a of approvals || []) {
    if (!['queued', 'running'].includes(a.publication?.state)) continue;
    for (const id of [a.task_id, ...(a.covered_tasks || [])]) if (ids.has(id)) out.add(id);
  }
  return out;
}
// Work waiting on a part that stopped (blocked, interrupted, cancelled, or waiting for a
// sign-in or permission), directly or through another such part: nothing starts it until the
// owner restarts what it waits for (story.stuck_waits). Such a build is stopped, not building.
const HALTED = ['blocked', 'interrupted', 'cancelled', 'needs_auth', 'needs_permission'];
export function stuckWaits(tasks: RecordData[]): Set<string> {
  const states = new Map(tasks.map(t => [t.id as string, t.state as string]));
  const stuck = new Set<string>();
  for (let grew = true; grew; ) {
    grew = false;
    for (const t of tasks) {
      if (t.state !== 'waiting_dependency' || stuck.has(t.id)) continue;
      if ((t.dependencies || []).some((d: string) => HALTED.includes(states.get(d) || '') || stuck.has(d))) {
        stuck.add(t.id);
        grew = true;
      }
    }
  }
  return stuck;
}

export type GoalView = {
  goal: RecordData;
  project?: RecordData;
  plan?: RecordData;
  children: RecordData[];
  tasks: RecordData[];
  // The blocks the host's story shows (story.Records.shown): the work tasks, plus the plan
  // while planning is unfinished or when it is the build's only task. What became of a build
  // is read from these, never from a finished plan.
  shown: RecordData[];
  // Tasks whose verified change failed to merge into main (see mergeFailed), tasks whose change
  // is being merged now, and waiting tasks stuck behind a stopped part (see stuckWaits).
  stuck: Set<string>;
  merging: Set<string>;
  halted: Set<string>;
  tone: Family;
  status: string;
  active: boolean;
  title: string;
  archived: boolean;
};

export function goalView(goal: RecordData, snap: Snapshot): GoalView {
  const tasks = snap.tasks.filter(t => t.goal_id === goal.id).sort((a, b) => a.created - b.created);
  const plan = tasks.find(isPlanTask);
  const children = tasks.filter(t => t !== plan);
  const shown = plan && (!children.length || plan.state !== 'completed') ? [plan, ...children] : children;
  const project = snap.projects.find(p => p.id === goal.project_id);
  const stuck = new Set(tasks.filter(t => mergeFailed(t, snap.approvals)).map(t => t.id as string));
  const merging = mergingTasks(tasks, snap.approvals);
  const halted = stuckWaits(tasks);
  const states = tasks.map(t => t.state as string);
  const live = shown.filter(t => t.state !== 'cancelled');
  let tone: Family = 'waiting';
  let status = 'Queued';
  if (!tasks.length) {
    tone = 'off';
    status = 'No tasks';
  } else if (states.some(s => BLOCKED_TASK.includes(s))) {
    tone = 'blocked';
    status = 'Needs attention';
  } else if (stuck.size) {
    tone = 'blocked';
    status = 'Merge failed';
  } else if (states.includes('awaiting_input')) {
    tone = 'decision';
    status = 'Needs your answer';
  } else if (states.some(s => ACTIVE_TASK.includes(s) || s === 'integrating')) {
    tone = 'running';
    status = states.includes('integrating') ? 'Integrating' : states.includes('verifying') ? 'Verifying' : states.includes('reviewing') ? 'In review' : 'Building';
  } else if (states.includes('ready_for_approval')) {
    tone = 'decision';
    status = 'Ready for approval';
  } else if (live.length && live.every(t => t.state === 'completed')) {
    tone = 'done';
    status = 'Completed';
  } else if (!live.length) {
    tone = 'off';
    status = 'Cancelled';
  } else if (!tasks.some(t => QUEUED_TASK.includes(t.state) && !halted.has(t.id))) {
    // What is left waits on a part that stopped: nothing will start it on its own.
    tone = 'off';
    status = 'Stopped';
  }
  // Still going (its clock runs): working, queued or waiting on the owner. A stopped build's
  // time ends at its last recorded change.
  const active = tone === 'running' || tone === 'waiting' || tone === 'decision';
  // Archived: the request itself, or its project (both leave Home and the pickers).
  return {goal, project, plan, children, tasks, shown, stuck, merging, halted, tone, status, active, title: displayTitle(goal), archived: isArchived(project) || !!goal.archived};
}

// What a screen re-reads a build's story on: this build's own tasks, attempts and approvals
// as the snapshot records them (never another build's events).
export function goalSignal(view: GoalView | null | undefined, snap: Snapshot): string {
  if (!view) return '';
  const ids = new Set(view.tasks.map(t => t.id as string));
  return [
    view.goal.archived ? 'a' : '',
    view.tasks.map(t => `${t.id}:${t.state}:${t.rev ?? ''}:${t.updated ?? ''}`).join(),
    snap.attempts.filter(a => ids.has(a.task_id)).map(a => `${a.id}:${a.state}`).join(),
    snap.approvals.filter(a => ids.has(a.task_id) || (a.covered_tasks || []).some((id: string) => ids.has(id))).map(a => `${a.id}:${a.state}:${a.publication?.state ?? ''}`).join(),
  ].join('|');
}
// The same for one block: its task, its attempts and the approvals that cover it.
export function taskSignal(task: RecordData | null | undefined, snap: Snapshot): string {
  if (!task) return '';
  return [
    `${task.state}:${task.rev ?? ''}:${task.updated ?? ''}`,
    snap.attempts.filter(a => a.task_id === task.id).map(a => `${a.id}:${a.state}`).join(),
    snap.approvals.filter(a => a.task_id === task.id || (a.covered_tasks || []).includes(task.id)).map(a => `${a.id}:${a.state}:${a.publication?.state ?? ''}`).join(),
  ].join('|');
}

// The live attempt of a request (newest first), if any task of it is being worked on now.
export function liveAttemptOf(view: GoalView, snap: Snapshot): RecordData | undefined {
  const ids = new Set(view.tasks.map(t => t.id));
  return snap.attempts.filter(a => ids.has(a.task_id) && ACTIVE_ATTEMPT.includes(a.state)).sort((a, b) => b.created - a.created)[0];
}

// Task states in which the owner has to act for the request to move on.
export const NEEDS_TASK = ['awaiting_input', 'needs_auth', 'needs_permission', 'ready_for_approval'];

// The verified candidates a request waits on: pending approvals of its tasks that are ready
// for approval (the Builds tab lists them; the chat build card opens the first).
export function pendingApprovals(view: GoalView, snap: Snapshot): RecordData[] {
  return snap.approvals.filter(a => a.state === 'pending' && view.tasks.some(t => t.id === a.task_id && t.state === 'ready_for_approval'));
}

// The commit a finished request left on main: the most recently integrated of its tasks.
export function integratedCommit(view: GoalView): string {
  const integrated = view.tasks.filter(t => t.integrated_commit).sort((a, b) => (b.updated || 0) - (a.updated || 0));
  return (integrated[0]?.integrated_commit as string) || '';
}

// Which request the Builds tab should open on when none is chosen: the live one (an
// attempt is running now), else one waiting on the owner, else the newest. A merely queued
// request does not outrank one that needs an answer. Archived projects' requests only when
// live.
export function defaultGoal(views: GoalView[], snap: Snapshot): GoalView | undefined {
  const byTime = [...views].sort((a, b) => b.goal.created - a.goal.created);
  return (
    byTime.find(v => !!liveAttemptOf(v, snap)) ||
    byTime.find(v => !v.archived && v.tasks.some(t => NEEDS_TASK.includes(t.state))) ||
    byTime.find(v => !v.archived) ||
    byTime[0]
  );
}

export type Usage = {calls: number; fields: [string, number][]};

// Sum only what the CLI stream actually recorded. Index-like fields are not totals.
export function usageTotals(attempts: RecordData[]): Usage | null {
  const totals: Record<string, number> = {};
  let calls = 0;
  for (const a of attempts) {
    const usage = a.result?.usage;
    if (!Array.isArray(usage)) continue;
    for (const rec of usage) {
      if (!rec || typeof rec !== 'object') continue;
      calls += 1;
      for (const [k, v] of Object.entries(rec)) {
        if (typeof v === 'number' && k !== 'call') totals[k] = (totals[k] || 0) + v;
      }
    }
  }
  if (!calls) return null;
  const order = ['input', 'output', 'reasoning', 'cache_read', 'cache_write'];
  const fields = Object.entries(totals).sort(
    ([a], [b]) => (order.includes(a) ? order.indexOf(a) : 99) - (order.includes(b) ? order.indexOf(b) : 99) || a.localeCompare(b),
  );
  return {calls, fields};
}

export function eventLine(e: RecordData, snap: Snapshot): string {
  const d = e.data || {};
  const attempt = d.attempt_id ? snap.attempts.find(a => a.id === d.attempt_id) : null;
  const task = snap.tasks.find(t => t.id === (d.task_id || attempt?.task_id));
  const title = task?.title || '';
  const project = d.project_id ? snap.projects.find(p => p.id === d.project_id) : null;
  switch (e.kind) {
    case 'goal.created':
      return `Request received · ${d.title || title}`;
    case 'attempt.claimed':
      return `Worker claimed · ${title}`;
    case 'routing.selected':
      return `${cap(d.provider || '')} ${d.model || ''}${d.effort ? ' · ' + d.effort : ''} selected${d.reason ? ' · ' + d.reason : ''}`;
    case 'attempt.finished':
      return `${title || 'Attempt'} · ${human(d.status || 'finished')}${attempt?.result?.reason ? ' · ' + attempt.result.reason : ''}`;
    case 'task.changed':
      return `${title ? title + ' · ' : ''}${stateLabel(d.state || '')}${d.reason ? ' · ' + d.reason : ''}`;
    case 'director.question':
      return `Director asks · ${d.reason || ''}`;
    case 'director.replanned':
      return `Director replanned · ${d.reason || ''}`;
    case 'integration.authorized':
      return `Integration authorised · ${shortSha(d.candidate)} → ${d.branch || 'main'}`;
    case 'integration.integrated':
      return `Integrated ${shortSha(d.candidate)} into ${d.branch || 'main'}`;
    case 'integration.failed':
      return `Integration failed · ${d.reason || ''}`;
    case 'scheduler.error':
      return `Scheduler error · ${d.reason || ''}`;
    case 'device.paired':
      return 'Device paired';
    case 'sessions.revoked':
      return 'Device sessions revoked';
    case 'control.changed':
      return `Control · ${human(d.action || 'changed')}`;
    case 'project.enrolled':
      return `Project enrolled · ${d.name || project?.name || ''}`;
    case 'project.changed':
      return `Project policy changed${project ? ' · ' + project.name : ''}`;
    case 'account.changed':
      return 'Worker assignment policy changed';
    case 'account.health':
      return `Account health · ${human(d.health || '')}`;
    case 'account.enrolled':
      return `Account enrolled · ${d.provider || ''}`;
    case 'remote.configured':
      return 'Remote access configured';
    default:
      return d.reason || d.title || cap(human(e.kind.replaceAll('.', ' ')));
  }
}

// Relative time for readouts ("12m ago"). `now` is the host-corrected clock from App.
export function ago(epoch: number | null | undefined, now: number): string {
  if (!epoch) return '';
  const s = Math.max(0, Math.floor(now - epoch));
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// Tasks whose record is kept for inspection but which never ask the owner for anything.
export const HISTORY_TASK = ['blocked', 'interrupted', 'cancelled'];
export const isHistoryTask = (t: RecordData) => HISTORY_TASK.includes(t.state);

// "Needs you" is strictly what the owner can act on now: an unanswered director question,
// a worker that needs sign-in or permission on the Mac, and a verified candidate waiting
// for a decision on a project that integrates manually. Nothing historical counts.
// Archived builds and archived projects never ask for anything: one list, shared by Home, the
// ☰ attention dot, Review (and its count) and the amber band, so they always agree.
export type NeedsYou = {answers: RecordData[]; access: RecordData[]; approvals: RecordData[]; count: number};
export function needsYou(snap: Snapshot): NeedsYou {
  const shelved = new Set(snap.projects.filter(isArchived).map(p => p.id));
  const archivedGoals = new Set((snap.goals || []).filter(g => g.archived || shelved.has(g.project_id)).map(g => g.id));
  const open = (t: RecordData | undefined) => !!t && !shelved.has(t.project_id) && !archivedGoals.has(t.goal_id);
  const answers = snap.tasks.filter(t => t.state === 'awaiting_input' && open(t));
  const access = snap.tasks.filter(t => (t.state === 'needs_auth' || t.state === 'needs_permission') && open(t));
  const manual = new Set(snap.projects.filter(p => p.integration_policy !== 'auto_local_main').map(p => p.id));
  const approvals = snap.approvals.filter(a => {
    const t = snap.tasks.find(x => x.id === a.task_id);
    return a.state === 'pending' && open(t) && manual.has(t?.project_id);
  });
  return {answers, access, approvals, count: answers.length + access.length + approvals.length};
}

// The same items as rows the owner can tap, newest first. `kind` says where a tap lands:
// an approval opens the review dialog, a task opens its build (which shows the question or
// the access problem at the top).
export type NeedRow = {id: string; at: number; tone: 'decision' | 'blocked'; label: string; title: string; sub: string; kind: 'review' | 'task'; taskId?: string; goalId?: string; project: string};
export function needsYouRows(snap: Snapshot): NeedRow[] {
  const needs = needsYou(snap);
  const task = (id: string) => snap.tasks.find(t => t.id === id);
  const projectName = (id?: string) => snap.projects.find(p => p.id === id)?.name || '';
  // Plain English only: the commit and the worker's raw reason stay in the review dialog
  // and the build's Advanced section.
  const rows: NeedRow[] = [
    ...needs.approvals.map(a => {
      const t = task(a.task_id);
      return {id: a.id, at: a.created as number, tone: 'decision' as const, label: 'Approve', title: stripLegacy(t?.title || 'Verified change'), sub: 'A verified change is waiting for your decision', kind: 'review' as const, taskId: t?.id, goalId: t?.goal_id, project: projectName(t?.project_id)};
    }),
    ...needs.answers.map(t => ({id: t.id, at: t.updated as number, tone: 'decision' as const, label: 'Answer', title: stripLegacy(t.title as string), sub: (t.question || 'The director has a question for you') as string, kind: 'task' as const, taskId: t.id as string, goalId: t.goal_id as string, project: projectName(t.project_id)})),
    ...needs.access.map(t => ({
      id: t.id,
      at: t.updated as number,
      tone: 'blocked' as const,
      label: stateLabel(t.state),
      title: stripLegacy(t.title as string),
      sub: t.state === 'needs_auth' ? 'An agent needs you to sign in on the Mac' : 'An agent needs your permission on the Mac',
      kind: 'task' as const,
      taskId: t.id as string,
      goalId: t.goal_id as string,
      project: projectName(t.project_id),
    })),
  ];
  return rows.sort((a, b) => b.at - a.at);
}

// Front-page class of a request. "active" has live or queued work; "needs" is waiting on the
// owner; "done" finished cleanly; "history" is blocked, interrupted or cancelled work that is
// kept for inspection but never presented as something to act on.
// Done, stopped or cancelled is read from the build's blocks (GoalView.shown), as the host's
// story does: a completed plan never makes a stopped or cancelled build look finished, and a
// verified change that failed to merge is stopped, not waiting.
export type GoalClass = 'active' | 'needs' | 'done' | 'history';
export function goalClass(view: GoalView): GoalClass {
  if (view.tasks.some(t => ACTIVE_TASK.includes(t.state) || t.state === 'integrating' || (QUEUED_TASK.includes(t.state) && !view.halted.has(t.id)))) return 'active';
  if (view.tasks.some(t => NEEDS_TASK.includes(t.state) && !view.stuck.has(t.id))) return 'needs';
  const live = view.shown.filter(t => t.state !== 'cancelled');
  if (live.length && live.every(t => t.state === 'completed')) return 'done';
  return 'history';
}
// A stopped block: blocked, interrupted, or a verified change that could not be merged.
export const isStopped = (view: GoalView, t: RecordData): boolean => t.state === 'blocked' || t.state === 'interrupted' || view.stuck.has(t.id);

// Status line for a request on the front page and in history, from recorded states only.
export function goalStatus(view: GoalView): {label: string; tone: Family} {
  const cls = goalClass(view);
  if (cls === 'history') {
    const last = [...view.shown].sort((a, b) => b.updated - a.updated).find(t => HISTORY_TASK.includes(t.state) || view.stuck.has(t.id));
    return {label: last ? (view.stuck.has(last.id) ? 'Merge failed' : stateLabel(last.state)) : 'Cancelled', tone: 'off'};
  }
  if (cls === 'done') return {label: view.tasks.some(t => t.integrated_commit) ? 'Merged into main' : 'Completed', tone: 'done'};
  if (cls === 'needs') {
    const t = view.tasks.find(t => NEEDS_TASK.includes(t.state) && !view.stuck.has(t.id));
    return {label: t ? stateLabel(t.state) : view.status, tone: 'decision'};
  }
  return {label: view.status, tone: view.tone};
}

// Which provider glyphs to show for a build: the accounts that actually recorded attempts.
export function providersOf(view: GoalView, snap: Snapshot): string[] {
  const ids = new Set(view.tasks.map(t => t.id));
  const seen: string[] = [];
  for (const a of snap.attempts) {
    if (!ids.has(a.task_id)) continue;
    const agent = snap.agents.find(x => x.id === a.account_id);
    const p = agent?.provider || '';
    if (p && !seen.includes(p)) seen.push(p);
  }
  return seen;
}

// Elapsed time of a build: from the request until its last recorded update (or now while live).
export function goalElapsed(view: GoalView, now: number): number {
  const start = view.goal.created as number;
  if (view.active) return now - start;
  const last = Math.max(start, ...view.tasks.map(t => (t.updated as number) || 0));
  return last - start;
}

// Milestone tree ----------------------------------------------------------------
// The director labels each child task with a milestone such as "M2.1 — Frontend". The tree
// groups tasks by that code, nests "M2.1" under "M2", and wraps the director's milestones
// with the recorded pipeline around them: M0 (the plan task), Review (independent review of
// every task) and Final (integration). Every state, owner and count below is read from the
// recorded tasks, attempts and agents; nothing is estimated.

export type MilestoneKind = 'plan' | 'milestone' | 'review' | 'final';
export type Owner = {id: string; name: string; provider: string; active: boolean};
export type MilestoneEvidence = {pass: number; fail: number; approved: number; rejected: number; findings: number; integrated: string[]};
export type MilestoneNode = {
  code: string;
  title: string;
  kind: MilestoneKind;
  tasks: RecordData[];
  children: MilestoneNode[];
  all: RecordData[];
  tone: Family;
  status: string;
  owners: Owner[];
  waitsOn: string[];
  evidence: MilestoneEvidence;
  synthesised: boolean;
};
export type MilestoneTree = {nodes: MilestoneNode[]; milestones: MilestoneNode[]; done: number; total: number; has: boolean};

const MILESTONE = /^\s*(M\d+(?:\.\d+)*)\s*[—–-]+\s*(.*?)\s*$/;

export function parseMilestone(label: string | undefined | null): {code: string; title: string} | null {
  const m = MILESTONE.exec(label || '');
  return m ? {code: m[1], title: m[2]} : null;
}

const codeParts = (code: string) => code.replace(/^M/, '').split('.').map(Number);
function compareCodes(a: string, b: string): number {
  const x = codeParts(a);
  const y = codeParts(b);
  for (let i = 0; i < Math.max(x.length, y.length); i++) {
    const d = (x[i] ?? -1) - (y[i] ?? -1);
    if (d) return d;
  }
  return 0;
}

function taskTone(tasks: RecordData[]): {tone: Family; status: string} {
  const states = tasks.map(t => t.state as string);
  const live = tasks.filter(t => t.state !== 'cancelled');
  if (!tasks.length) return {tone: 'off', status: 'No tasks'};
  if (states.some(s => BLOCKED_TASK.includes(s))) return {tone: 'blocked', status: 'Needs attention'};
  if (states.includes('awaiting_input')) return {tone: 'decision', status: 'Needs your answer'};
  if (states.some(s => ACTIVE_TASK.includes(s) || s === 'integrating')) {
    return {tone: 'running', status: states.includes('integrating') ? 'Integrating' : states.includes('verifying') ? 'Verifying' : states.includes('reviewing') ? 'In review' : 'Building'};
  }
  if (states.includes('ready_for_approval')) return {tone: 'decision', status: 'Ready for approval'};
  if (live.length && live.every(t => t.state === 'completed')) return {tone: 'done', status: live.some(t => t.integrated_commit) ? 'Integrated' : 'Completed'};
  if (states.some(s => QUEUED_TASK.includes(s))) {
    const waiting = states.filter(s => QUEUED_TASK.includes(s));
    return {tone: 'waiting', status: waiting.every(s => s === 'waiting_dependency') ? 'Waiting on dependency' : 'Queued'};
  }
  if (!live.length) return {tone: 'off', status: 'Cancelled'};
  return {tone: 'off', status: 'Not started'};
}

// Latest attempt per task → account; the glyph lights only for attempts that are ACTIVE now.
function ownersOf(tasks: RecordData[], snap: Snapshot): Owner[] {
  const owners: Owner[] = [];
  for (const t of tasks) {
    const attempts = snap.attempts.filter(a => a.task_id === t.id).sort((a, b) => a.created - b.created);
    const active = attempts.filter(a => ACTIVE_ATTEMPT.includes(a.state));
    const latest = attempts[attempts.length - 1];
    for (const a of active.length ? active : latest ? [latest] : []) {
      const agent = snap.agents.find(x => x.id === a.account_id);
      const id = a.account_id as string;
      const on = ACTIVE_ATTEMPT.includes(a.state);
      const seen = owners.find(o => o.id === id);
      if (seen) seen.active = seen.active || on;
      else owners.push({id, name: agent?.name || 'Worker', provider: agent?.provider || '', active: on});
    }
  }
  return owners;
}

function evidenceOf(tasks: RecordData[], snap: Snapshot): MilestoneEvidence {
  const ev: MilestoneEvidence = {pass: 0, fail: 0, approved: 0, rejected: 0, findings: 0, integrated: []};
  for (const t of tasks) {
    const attempts = snap.attempts.filter(a => a.task_id === t.id).sort((a, b) => a.created - b.created);
    const build = [...attempts].reverse().find(a => a.phase === 'build' && Array.isArray(a.result?.checks) && a.result.checks.length);
    for (const c of build?.result?.checks || []) {
      if (c.status === 'PASS') ev.pass += 1;
      else ev.fail += 1;
    }
    const review = [...attempts].reverse().find(a => a.phase === 'review' && a.result?.review);
    if (review) {
      if (review.result.review.approved) ev.approved += 1;
      else ev.rejected += 1;
      ev.findings += Array.isArray(review.result.review.findings) ? review.result.review.findings.length : 0;
    }
    if (t.integrated_commit) ev.integrated.push(shortSha(t.integrated_commit, 7));
  }
  return ev;
}

function reviewed(t: RecordData, snap: Snapshot): boolean {
  if (t.review_attempt) return snap.attempts.find(a => a.id === t.review_attempt)?.state === 'passed';
  return snap.attempts.some(a => a.task_id === t.id && a.phase === 'review' && a.state === 'passed');
}

export function milestoneTree(view: GoalView, snap: Snapshot): MilestoneTree {
  const live = view.children.filter(t => t.state !== 'cancelled');
  const byCode = new Map<string, MilestoneNode>();
  const unlabelled: RecordData[] = [];
  const blank = (code: string, title: string, kind: MilestoneKind, synthesised = false): MilestoneNode => ({
    code,
    title,
    kind,
    tasks: [],
    children: [],
    all: [],
    tone: 'off',
    status: '',
    owners: [],
    waitsOn: [],
    evidence: {pass: 0, fail: 0, approved: 0, rejected: 0, findings: 0, integrated: []},
    synthesised,
  });
  for (const t of view.children) {
    const m = parseMilestone(t.milestone);
    if (!m) {
      unlabelled.push(t);
      continue;
    }
    const node = byCode.get(m.code) || blank(m.code, m.title, 'milestone');
    if (!node.title && m.title) node.title = m.title;
    node.tasks.push(t);
    byCode.set(m.code, node);
  }
  // Missing parents ("M2" for "M2.1") are created so the nesting stays complete.
  for (const code of [...byCode.keys()]) {
    const parts = code.split('.');
    for (let i = parts.length - 1; i >= 1; i--) {
      const parent = parts.slice(0, i).join('.');
      if (!byCode.has(parent)) byCode.set(parent, blank(parent, `Milestone ${parent}`, 'milestone', true));
    }
  }
  const taskNode = new Map<string, string>();
  for (const node of byCode.values()) for (const t of node.tasks) taskNode.set(t.id, node.code);
  const roots: MilestoneNode[] = [];
  for (const node of byCode.values()) {
    const parts = node.code.split('.');
    const parent = parts.length > 1 ? byCode.get(parts.slice(0, -1).join('.')) : undefined;
    (parent ? parent.children : roots).push(node);
  }
  const finish = (node: MilestoneNode): MilestoneNode => {
    node.children.sort((a, b) => compareCodes(a.code, b.code));
    node.children.forEach(finish);
    node.tasks.sort((a, b) => a.created - b.created);
    node.all = [...node.tasks, ...node.children.flatMap(c => c.all)];
    const inside = new Set(node.all.map(t => t.id));
    const codes = new Set<string>();
    for (const t of node.all) for (const id of t.dependencies || []) {
      if (inside.has(id)) continue;
      const code = taskNode.get(id);
      if (code && code !== node.code && !code.startsWith(node.code + '.') && !node.code.startsWith(code + '.')) codes.add(code);
    }
    node.waitsOn = [...codes].sort(compareCodes);
    const {tone, status} = taskTone(node.all);
    node.tone = tone;
    node.status = status;
    node.owners = ownersOf(node.all, snap);
    node.evidence = evidenceOf(node.all, snap);
    return node;
  };
  roots.sort((a, b) => compareCodes(a.code, b.code));
  roots.forEach(finish);
  if (unlabelled.length) {
    const node = blank('Tasks', 'Tasks without a recorded milestone', 'milestone', true);
    node.tasks = unlabelled;
    roots.push(finish(node));
  }

  // M0: the director's plan task, with that task's own state.
  const plan = blank('M0', 'Inspect and define scope', 'plan', true);
  if (view.plan) {
    plan.tasks = [view.plan];
    plan.all = [view.plan];
    const planDone = view.plan.state === 'completed' || view.children.length > 0;
    plan.tone = planDone ? 'done' : family(view.plan.state);
    plan.status = planDone ? 'Specification recorded' : stateLabel(view.plan.state);
    plan.owners = ownersOf([view.plan], snap);
  } else {
    plan.status = 'No plan task';
  }

  // Review: independent review across every task. Waiting until a task reaches review.
  const review = blank('Review', 'Independent review', 'review', true);
  const reviewedTasks = live.filter(t => reviewed(t, snap));
  const inReview = live.filter(t => t.state === 'reviewing' || snap.attempts.some(a => a.task_id === t.id && ACTIVE_ATTEMPT.includes(a.state) && (a.phase === 'review' || a.phase === 'direct')));
  review.all = live;
  if (live.length && reviewedTasks.length === live.length) {
    review.tone = 'done';
    review.status = `${reviewedTasks.length}/${live.length} reviewed`;
  } else if (inReview.length) {
    review.tone = 'running';
    review.status = 'In review';
  } else {
    review.tone = 'waiting';
    review.status = reviewedTasks.length ? `${reviewedTasks.length}/${live.length} reviewed` : 'Waiting for builds';
  }
  review.owners = ownersOf(inReview, snap).filter(o => o.active);
  review.evidence = evidenceOf(live, snap);
  review.evidence.pass = 0;
  review.evidence.fail = 0;
  review.evidence.integrated = [];

  // Final: integration and delivery, from integrated commits and integrating/approval states.
  const final = blank('Final', 'Integration and delivery', 'final', true);
  const integrated = live.filter(t => t.integrated_commit);
  final.all = live;
  if (live.length && integrated.length === live.length) {
    final.tone = 'done';
    final.status = 'Integrated';
  } else if (live.some(t => t.state === 'integrating')) {
    final.tone = 'running';
    final.status = 'Integrating';
  } else if (live.some(t => t.state === 'ready_for_approval')) {
    final.tone = 'decision';
    final.status = 'Ready for approval';
  } else {
    final.tone = 'waiting';
    final.status = integrated.length ? `${integrated.length}/${live.length} integrated` : 'Waiting for review';
  }
  final.evidence = {pass: 0, fail: 0, approved: 0, rejected: 0, findings: 0, integrated: integrated.map(t => shortSha(t.integrated_commit, 7))};

  const flat = (nodes: MilestoneNode[]): MilestoneNode[] => nodes.flatMap(n => [n, ...flat(n.children)]);
  const milestones = flat(roots).filter(n => n.tasks.length > 0);
  const has = byCode.size > 0;
  return {
    nodes: [plan, ...roots, review, final],
    milestones,
    done: milestones.filter(n => n.tone === 'done').length,
    total: milestones.length,
    has,
  };
}

// Round 3 ---------------------------------------------------------------------------
// Plain-English build stories (GET goals/{id}/story and tasks/{id}/story) and read-only Mac
// activity discovery (GET mac-activity). Shapes follow docs/ROUND3_CONTRACT.md section A;
// every field may be null when the host has not recorded it, and the UI then says so.

export type StoryState = 'running' | 'queued' | 'waiting' | 'needs_you' | 'completed' | 'blocked' | 'cancelled';
// Blocks done of blocks planned; `fraction` (0..1) also credits the stages unfinished blocks
// have reached, and `in_progress` counts the blocks being worked on or merged now. `stopped`
// (counted here from the blocks) is how many stopped; the rest are waiting to start.
export type StoryProgress = {done: number; total: number; fraction?: number | null; in_progress?: number | null; stopped?: number | null};
export type StoryAgent = {provider: string; model: string; name: string};
export type StoryBlock = {
  id: string;
  title: string;
  purpose: string;
  connects: {id: string; title: string; relation: 'needs' | 'unblocks'}[];
  agent: StoryAgent | null;
  stage: {index: number; label: string};
  state: StoryState;
  state_label: string;
  now: string | null;
  last_activity: number | null;
  stalled: boolean;
  updated: number;
};
export type FailureAction = 'retry' | 'answer' | 'sign_in' | 'permission' | 'none';
export type Failure = {what_failed: string; intended: string; recoverable: boolean; tried: string[]; next: string; needs_you: boolean; action: FailureAction};
export type StoryNeed = {kind: 'answer' | 'approve' | 'sign_in' | 'permission'; text: string; task_id: string};
export type GoalStory = {
  goal_id: string;
  title: string;
  project: {id: string; name: string} | null;
  status: 'running' | 'needs_you' | 'queued' | 'completed' | 'blocked' | 'cancelled';
  status_label: string;
  started: number | null;
  updated: number | null;
  agents_working: number;
  progress: StoryProgress;
  summary: string;
  blocks: StoryBlock[];
  failure: Failure | null;
  needs_you: StoryNeed[];
};
export type RailStep = {label: string; state: 'done' | 'current' | 'todo' | 'failed'};
export type StoryUpdate = {t: number | null; text: string; tone: string};
export type BlockStory = {
  block: StoryBlock;
  what: string;
  why: string;
  connects_text: string;
  stage_rail: RailStep[];
  updates: StoryUpdate[];
  now: string | null;
  decisions: string[];
  failure: Failure | null;
  advanced: {
    attempts: {id: string; phase: string; provider: string; model: string; state: string; seconds: number | null}[];
    checks: {name: string; status: string}[];
    candidate: string | null;
    integrated_commit: string | null;
    scope: string[];
  };
};

export type Activity = {
  kind: string; // claude | codex | arena_build | arena_director | agent | dev_server | tests
  label: string;
  repo: string | null;
  project: string | {id?: string; name?: string} | null;
  pid?: number | null;
  started: number | null;
  last_activity: number | null;
  headless?: boolean;
  ports?: number[];
  evidence?: string[];
  arena?: {goal_id?: string | null; task_id?: string | null; attempt_id?: string | null} | null;
};
export type MacActivity = {
  generated?: number;
  // partial: part of the Mac did not answer in time; stale: an earlier look, served while a
  // new one is still running.
  partial?: boolean;
  stale?: boolean;
  groups?: {managed?: Activity[]; external?: Activity[]; unknown?: Activity[]};
  summary?: string[];
  limitations?: string[];
};

// Chip tone for a story or block state.
export const STORY_TONE: Record<string, Family> = {running: 'running', needs_you: 'decision', queued: 'waiting', waiting: 'waiting', completed: 'done', blocked: 'blocked', cancelled: 'off'};
// The agent's family name for a provider ("Claude (sonnet)"); an unknown provider keeps the
// account name the host recorded.
export const agentName = (agent: {provider?: string; name?: string} | null | undefined) =>
  agent?.provider === 'claude' ? 'Claude' : agent?.provider === 'codex' ? 'Codex' : agent?.name || 'Agent';

// How long a live agent the host calls stalled has been quiet, in minutes: since its
// transcript's last write, else since its attempt started (the host's rule, story.is_stalled).
export function quietMinutes(block: StoryBlock, attempts: RecordData[], now: number): number {
  const started = attempts.filter(a => a.task_id === block.id && ACTIVE_ATTEMPT.includes(a.state)).map(a => a.created as number).sort((a, b) => b - a)[0];
  const since = block.last_activity ?? started;
  return since ? Math.max(1, Math.floor((now - since) / 60)) : 0;
}
// A stalled block's line: what it last did and when, never presented as live.
export function quietLine(block: StoryBlock, attempts: RecordData[], now: number): string {
  const m = quietMinutes(block, attempts, now);
  if (block.last_activity) return `Last activity ${m} min ago${block.now ? `: ${block.now}` : ''}`;
  return m ? `No activity since it started ${m} min ago` : 'No activity recorded yet';
}

// A request is out of the normal views when it, or its project, is archived.
export const goalArchived = (goal: RecordData, snap: Snapshot): boolean =>
  !!goal.archived || isArchived(snap.projects.find(p => p.id === goal.project_id));

// A build with live work: something running, queued or integrating. A verified candidate on
// a project that integrates automatically is on its way to main, so it counts as live too,
// unless that merge failed: then nothing is happening and the build has stopped (the host's
// story says "blocked" for it, and Chat says the merge failed).
export function isLive(view: GoalView): boolean {
  if (goalClass(view) === 'active' || view.merging.size > 0) return true;
  const auto = view.project?.integration_policy === 'auto_local_main';
  if (!auto || view.tasks.some(t => t.state === 'awaiting_input' || t.state === 'needs_auth' || t.state === 'needs_permission')) return false;
  return view.tasks.some(t => t.state === 'ready_for_approval' && !view.stuck.has(t.id));
}
// A verified change of this build is being merged into main right now.
export const isMerging = (view: GoalView): boolean => view.merging.size > 0 || view.tasks.some(t => t.state === 'integrating' || (t.state === 'ready_for_approval' && !view.stuck.has(t.id)));
// Agents at work on a build right now, counted as its story counts them (agents_working): an
// attempt running or under its checks, on a part that is being worked on (not one still
// starting, not one whose guardian was lost).
export function agentsWorking(taskIds: Set<string>, snap: Snapshot): number {
  const working = new Set(snap.tasks.filter(t => taskIds.has(t.id) && ['running', 'verifying', 'reviewing'].includes(t.state)).map(t => t.id));
  return snap.attempts.filter(a => working.has(a.task_id) && (a.state === 'running' || a.state === 'verifying')).length;
}

// The host's story, mirrored over the snapshot (story.block_state and Records.stage), so the
// progress drawn before a story answers is the progress the story will report.
export function blockState(task: RecordData, approvals: RecordData[] | undefined, policy: string): StoryState {
  const s = task.state as string;
  if (ACTIVE_TASK.includes(s) || s === 'integrating') return 'running';
  if (s === 'waiting_dependency') return 'waiting';
  if (QUEUED_TASK.includes(s)) return 'queued';
  if (s === 'awaiting_input' || s === 'needs_auth' || s === 'needs_permission') return 'needs_you';
  if (s === 'ready_for_approval') {
    const approval = approvalOf(task, approvals);
    if (approval?.state === 'pending') return 'needs_you';
    if (approval?.publication?.state === 'failed') return 'blocked';
    if (!approval && policy !== 'auto_local_main') return 'needs_you';
    return 'running'; // accepted under the project's policy and merging now
  }
  if (s === 'completed') return 'completed';
  if (s === 'blocked' || s === 'interrupted') return 'blocked';
  if (s === 'cancelled') return 'cancelled';
  return 'queued';
}

// Why a block stopped, as the host's story classes it (story.classify and failure_class), from
// the recorded state, reason and last finished attempt only; null while it has not stopped.
const OWNER_REJECTED = ['Change rejected; staging and evidence preserved', 'Combined change not accepted'];
function classify(reason: string, state: string, status: string | undefined, phase: string | undefined, timedOut: boolean): string {
  const low = reason.toLowerCase();
  const has = (...parts: string[]) => parts.some(p => low.includes(p));
  if (state === 'needs_auth' || status === 'needs_auth') return 'needs_auth';
  if (state === 'needs_permission' || status === 'needs_permission') return 'needs_permission';
  if (status === 'rate_limited' || state === 'cooldown') return 'rate_limited';
  if (status === 'transient') return 'connection';
  if (status === 'model_unavailable' || has('no director model is available')) return 'model_unavailable';
  if (has('no supported model route', 'no healthy eligible slot')) return 'no_agent';
  if (state === 'interrupted' || status === 'interrupted' || has('interrupted worker', 'recovery budget exhausted', 'could not confirm its process')) return 'interrupted';
  if (timedOut || has('timeout=true', 'took longer than')) return 'timeout';
  if (has('checks did not all pass', 'staging checks failed')) return 'checks';
  if (has('review rejected')) return phase === 'direct' ? 'director_rejected' : 'review_rejected';
  if (has('no candidate changes')) return 'no_changes';
  if (has('valid json object', 'nonempty file operations', 'does not match the required schema', 'lacks an explicit structured approval')) return 'bad_response';
  if (has('exceeded its declared file scope')) return 'scope';
  if (low.startsWith('integration preserved for inspection')) return 'merge_conflict';
  if (OWNER_REJECTED.includes(reason.trim())) return 'owner_rejected';
  return 'unknown';
}
export function stopClass(task: RecordData, attempts: RecordData[], approvals: RecordData[] | undefined): string | null {
  const approval = approvalOf(task, approvals);
  if (task.state === 'ready_for_approval' && approval?.publication?.state === 'failed') return 'merge_conflict';
  if (!['blocked', 'interrupted', 'needs_auth', 'needs_permission'].includes(task.state)) return null;
  const finished = attempts.filter(a => !ACTIVE_TASK.includes(a.state));
  const last = finished[finished.length - 1];
  const result = last?.result || {};
  const cls = classify(String(task.reason || ''), task.state, result.status || last?.state, last?.phase, !!result.invocation?.timed_out);
  if (cls === 'unknown' && (approval?.state === 'reject' || approval?.state === 'request_changes')) return 'owner_rejected';
  return cls;
}
// The host's short phrase for each class (story.STOPPED), for "Stopped: …".
const STOPPED_PHRASE: Record<string, string> = {
  timeout: 'the agent ran out of time',
  checks: 'the checks did not pass',
  review_rejected: 'the reviewer asked for changes',
  director_rejected: 'the director asked for changes',
  needs_auth: 'an agent account needs signing in',
  needs_permission: 'the Mac refused a permission',
  rate_limited: 'the agent hit its usage limit',
  connection: 'the agent lost its connection',
  model_unavailable: 'the AI model was not available',
  interrupted: 'the agent was interrupted',
  no_agent: 'no agent was available',
  no_changes: 'the agent changed nothing',
  bad_response: "the agent's answer could not be used",
  scope: 'the agent touched files it was not allowed to',
  merge_conflict: 'the change could not be merged',
  owner_rejected: 'you did not accept the change',
  unknown: 'an error Arena could not classify',
};
// "Stopped: the agent ran out of time" for a stopped block; "Cancelled" for one stopped on
// purpose. Never the raw reason.
export function stopLine(task: RecordData, snap: Snapshot): string {
  if (task.state === 'cancelled') return 'Cancelled';
  const attempts = snap.attempts.filter(a => a.task_id === task.id).sort((a, b) => (a.created || 0) - (b.created || 0));
  const cls = stopClass(task, attempts, snap.approvals);
  return cls ? `Stopped: ${STOPPED_PHRASE[cls] || STOPPED_PHRASE.unknown}` : stateLabel(task.state);
}
// A verified change's state in plain words (the Review list and the review dialog).
export function approvalLabel(a: RecordData): {label: string; tone: Family} {
  const pub = a.publication?.state;
  if (pub === 'integrated') return {label: 'Merged into main', tone: 'done'};
  if (pub === 'failed') return {label: 'Merge failed', tone: 'blocked'};
  if (pub === 'queued') return {label: 'Merging into main', tone: 'running'};
  if (a.state === 'pending') return {label: 'Waiting for your decision', tone: 'decision'};
  if (a.state === 'accept') return {label: 'Accepted · not merged yet', tone: 'decision'};
  if (a.state === 'reject') return {label: 'Not accepted', tone: 'off'};
  if (a.state === 'request_changes') return {label: 'You asked for changes', tone: 'off'};
  if (a.state === 'superseded') return {label: 'Replaced by a newer change', tone: 'off'};
  return {label: cap(human(a.state || '')), tone: 'off'};
}

const RAIL_OF = [0, 1, 2, 3, 4, 4, 4]; // stageIndex() → rail position (Design 0 … Merge 4)
const PHASE_RAIL: Record<string, number> = {plan: 0, replan: 0, build: 1, review: 3, direct: 3};
// The rail position (0 Design … 4 Merge) a block has reached (story.Records.stage): a stopped
// block sits where it stopped (a failed or refused merge is Merge, failing checks are Test,
// any other stop the phase of its last finished attempt).
export function railStage(task: RecordData, view: GoalView, snap: Snapshot): number {
  if (task === view.plan) return 0;
  const attempts = snap.attempts.filter(a => a.task_id === task.id).sort((a, b) => (a.created || 0) - (b.created || 0));
  const cls = stopClass(task, attempts, snap.approvals);
  if (cls) {
    if (cls === 'merge_conflict' || cls === 'owner_rejected') return 4;
    if (cls === 'checks') return 2;
    const finished = attempts.filter(a => !ACTIVE_TASK.includes(a.state));
    if (finished.length) return PHASE_RAIL[finished[finished.length - 1].phase] ?? 1;
  }
  if (task.state === 'recovering') return PHASE_RAIL[task.phase] ?? 1;
  const index = stageIndex(task);
  if (index < 0) return PHASE_RAIL[attempts.length ? attempts[attempts.length - 1].phase : task.phase] ?? 1;
  return RAIL_OF[index];
}

// Work done of a build from the snapshot, reckoned exactly as the host's story does
// (story.progress) over the same blocks (GoalView.shown, the plan counted while it plans):
// done of the blocks not cancelled; under way, the blocks being worked on or merged now;
// stopped, the blocks that stopped; and a fraction in which a finished block counts whole and
// every other block the share of the five-step rail it has reached. Shown only until the
// build's story answers, so the bar does not jump when it does.
export function workProgress(view: GoalView, snap: Snapshot): StoryProgress {
  const policy = view.project?.integration_policy || 'manual';
  const counted = view.shown.map(t => ({t, state: blockState(t, snap.approvals, policy)})).filter(b => b.state !== 'cancelled');
  const done = counted.filter(b => b.state === 'completed').length;
  const partial = counted.filter(b => b.state !== 'completed').reduce((n, b) => n + railStage(b.t, view, snap) / 5, 0);
  const fraction = counted.length ? Math.min(1, (done + partial) / counted.length) : 0;
  return {
    done,
    total: counted.length,
    in_progress: counted.filter(b => b.t !== view.plan && b.state === 'running').length,
    stopped: counted.filter(b => b.state === 'blocked').length,
    fraction: Math.round(fraction * 1000) / 1000,
  };
}
// The host's progress once the build's story has answered (its blocks give the stopped
// count), else the snapshot's reckoning; an older host's missing fields come from the latter.
export function storyProgress(story: GoalStory | null | undefined, local: StoryProgress): StoryProgress {
  const p = story?.progress;
  if (!p) return local;
  return {
    ...p,
    fraction: typeof p.fraction === 'number' ? p.fraction : local.fraction,
    in_progress: typeof p.in_progress === 'number' ? p.in_progress : local.in_progress,
    stopped: Array.isArray(story.blocks) ? story.blocks.filter(b => b.state === 'blocked').length : local.stopped,
  };
}
export const progressFraction = (p: StoryProgress): number =>
  typeof p.fraction === 'number' && isFinite(p.fraction) ? Math.min(1, Math.max(0, p.fraction)) : p.total > 0 ? p.done / p.total : 0;
// "0 of 15 done · 2 under way · 13 waiting": waiting is what is neither done, under way nor
// stopped.
export function progressLabel(p: StoryProgress): string {
  const under = p.in_progress || 0;
  const waiting = Math.max(0, p.total - p.done - under - (p.stopped || 0));
  return `${p.done} of ${p.total} done${under ? ` · ${under} under way` : ''}${waiting ? ` · ${waiting} waiting` : ''}`;
}

// When a request last changed (its newest task update, else its creation).
export const lastChange = (view: GoalView): number => Math.max((view.goal.created as number) || 0, ...view.tasks.map(t => (t.updated as number) || 0));
// When its last work block finished (never the plan), and when its last block stopped.
export const finishedAt = (view: GoalView): number => Math.max(0, ...view.shown.filter(t => t !== view.plan && t.state === 'completed').map(t => (t.updated as number) || 0));
export const stoppedAt = (view: GoalView): number => Math.max(0, ...view.shown.filter(t => isStopped(view, t)).map(t => (t.updated as number) || 0));

// What became of a finished or stopped request, in plain words.
export function outcomeLine(view: GoalView, now: number): {label: string; tone: Family} {
  const cls = goalClass(view);
  const when = ago(lastChange(view), now);
  if (cls === 'done') return {label: `${view.tasks.some(t => t.integrated_commit) ? 'Merged into main' : 'Finished'} ${when}`, tone: 'done'};
  if (cls === 'needs') return {label: 'Waiting for you', tone: 'decision'};
  if (cls === 'active') return {label: 'Building now', tone: 'running'};
  const stopped = view.shown.some(t => isStopped(view, t));
  return {label: `${stopped ? 'Stopped' : 'Cancelled'} ${when}`, tone: stopped ? 'blocked' : 'off'};
}

// What a failed request means to the owner, in one plain sentence: the host's own message
// when it is already plain English, else what to do. Never "TypeError: Failed to fetch".
const PLAIN = /^[^{}<>[\]=_\/\\|`$#@]{1,200}$/;
export function plainError(e: unknown): string {
  const err = (e || {}) as {status?: number; message?: string; name?: string};
  const status = typeof err.status === 'number' ? err.status : 0;
  const message = String(err.message ?? e ?? '').trim();
  const sentence = (s: string) => (/[.!?]$/.test(s) ? s : s + '.');
  const plain = PLAIN.test(message) && !/\bHTTP\b|error:|exception|traceback|\b[0-9a-f]{7,}\b/i.test(message);
  if (status === 401 || /pairing required/i.test(message)) return 'This phone needs pairing with your Mac again.';
  if (!status) {
    if (err.name === 'TypeError' || /failed to fetch|network|load failed|offline|reconnect|abort/i.test(message)) return "Couldn't reach your Mac — check the connection and try again.";
    return plain ? sentence(message) : 'Something went wrong — try again.';
  }
  if (status === 503 && plain) return sentence(message); // the host's own "busy" sentence
  if (status >= 500) return "Your Mac couldn't do that just now — try again in a moment.";
  if (status === 403) return 'Your Mac refused that change — reload Arena and try again.';
  if (status === 404) return 'Arena could not find that on your Mac any more.';
  if (plain) return sentence(message);
  if (status === 409) return 'Something changed on your Mac meanwhile — try again.';
  return 'Something went wrong — try again.';
}

// A local repository on the Mac that the owner wants to build in but has not let Arena into
// (a director reply's `offer_enroll`, from a read-only check on the Mac). One tap enrols it
// when it is ready (`clean`, no `reason`); when only unsaved work on main is in the way
// (`can_checkpoint`) a second, explicit tap saves it as a commit first.
export type EnrollOffer = {name: string; repo_name: string; clean: boolean; branch: string | null; reason: string | null; can_checkpoint?: boolean};
// POST /api/v1/projects/enroll-local
export type EnrollResult = {ok: boolean; project_id: string; name: string; already: boolean; checkpoint?: {sha: string; files: number} | null};
export const enrollOffer = (m: ChatMessage): EnrollOffer | null => (m as ChatMessage & {offer_enroll?: EnrollOffer | null}).offer_enroll || null;
// Whether a non-archived project already builds in the repository with this folder name.
export const enrolledRepo = (snap: Snapshot, folder: string): boolean =>
  snap.projects.some(p => !isArchived(p) && String(p.repo || '').replace(/\/+$/, '').split('/').pop()?.toLowerCase() === folder.toLowerCase());
