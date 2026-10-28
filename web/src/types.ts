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
  paused: boolean;
  limit: number;
  revision: number;
  heartbeat: number | null;
  server_time: number;
  version: string;
};

export type Tab = 'overview' | 'projects' | 'build' | 'agents' | 'review';

export const TABS: {key: Tab; label: string}[] = [
  {key: 'overview', label: 'Overview'},
  {key: 'projects', label: 'Projects'},
  {key: 'build', label: 'Build'},
  {key: 'agents', label: 'Agents'},
  {key: 'review', label: 'Review'},
];

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
export const STAGES_SHORT = ['Plan', 'Build', 'Verify', 'Review', 'Accept', 'Merge'];

export type GoalView = {
  goal: RecordData;
  project?: RecordData;
  plan?: RecordData;
  children: RecordData[];
  tasks: RecordData[];
  tone: Family;
  status: string;
  active: boolean;
};

export function goalView(goal: RecordData, snap: Snapshot): GoalView {
  const tasks = snap.tasks.filter(t => t.goal_id === goal.id).sort((a, b) => a.created - b.created);
  const plan = tasks.find(t => t.director && (t.phase === 'plan' || t.phase === 'replan'));
  const children = tasks.filter(t => t !== plan);
  const project = snap.projects.find(p => p.id === goal.project_id);
  const states = tasks.map(t => t.state as string);
  const live = tasks.filter(t => t.state !== 'cancelled');
  let tone: Family = 'waiting';
  let status = 'Queued';
  if (!tasks.length) {
    tone = 'off';
    status = 'No tasks';
  } else if (states.some(s => BLOCKED_TASK.includes(s))) {
    tone = 'blocked';
    status = 'Needs attention';
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
  }
  const active = tone !== 'done' && tone !== 'off';
  return {goal, project, plan, children, tasks, tone, status, active};
}

// Dependency lanes: a task's lane is one deeper than the deepest task it waits on.
export function lanes(tasks: RecordData[]): RecordData[][] {
  const byId = new Map(tasks.map(t => [t.id, t]));
  const depth = new Map<string, number>();
  const visit = (t: RecordData, seen: Set<string>): number => {
    if (depth.has(t.id)) return depth.get(t.id)!;
    if (seen.has(t.id)) return 0;
    seen.add(t.id);
    let d = 0;
    for (const id of t.dependencies || []) {
      const dep = byId.get(id);
      if (dep) d = Math.max(d, visit(dep, seen) + 1);
    }
    depth.set(t.id, d);
    return d;
  };
  tasks.forEach(t => visit(t, new Set()));
  const out: RecordData[][] = [];
  for (const t of tasks) {
    const d = depth.get(t.id) || 0;
    (out[d] ||= []).push(t);
  }
  return out.filter(Boolean);
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
export type NeedsYou = {answers: RecordData[]; access: RecordData[]; approvals: RecordData[]; count: number};
export function needsYou(snap: Snapshot): NeedsYou {
  const answers = snap.tasks.filter(t => t.state === 'awaiting_input');
  const access = snap.tasks.filter(t => t.state === 'needs_auth' || t.state === 'needs_permission');
  const manual = new Set(snap.projects.filter(p => p.integration_policy !== 'auto_local_main').map(p => p.id));
  const approvals = snap.approvals.filter(a => a.state === 'pending' && manual.has(snap.tasks.find(t => t.id === a.task_id)?.project_id));
  return {answers, access, approvals, count: answers.length + access.length + approvals.length};
}

// Front-page class of a request. "active" has live or queued work; "needs" is waiting on the
// owner; "done" finished cleanly; "history" is blocked, interrupted or cancelled work that is
// kept for inspection but never presented as something to act on.
export type GoalClass = 'active' | 'needs' | 'done' | 'history';
export function goalClass(view: GoalView): GoalClass {
  const states = view.tasks.map(t => t.state as string);
  if (states.some(s => ACTIVE_TASK.includes(s) || s === 'integrating' || QUEUED_TASK.includes(s))) return 'active';
  if (states.some(s => s === 'awaiting_input' || s === 'needs_auth' || s === 'needs_permission' || s === 'ready_for_approval')) return 'needs';
  const live = view.tasks.filter(t => t.state !== 'cancelled');
  if (live.length && live.every(t => t.state === 'completed')) return 'done';
  return 'history';
}

// Status line for a request on the front page and in history, from recorded states only.
export function goalStatus(view: GoalView): {label: string; tone: Family} {
  const cls = goalClass(view);
  if (cls === 'history') {
    const last = [...view.tasks].sort((a, b) => b.updated - a.updated).find(t => HISTORY_TASK.includes(t.state));
    return {label: last ? stateLabel(last.state) : 'Cancelled', tone: 'off'};
  }
  if (cls === 'done') return {label: view.tasks.some(t => t.integrated_commit) ? 'Integrated' : 'Completed', tone: 'done'};
  if (cls === 'needs') {
    const t = view.tasks.find(t => ['awaiting_input', 'needs_auth', 'needs_permission', 'ready_for_approval'].includes(t.state));
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
