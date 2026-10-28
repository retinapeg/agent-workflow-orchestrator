import React from 'react';
import {useArena} from '../App';
import {ACTIVE_ATTEMPT, cap, clock, duration, human, num, providerGlyph, providerName, stateLabel, usageTotals, when} from '../types';
import {Chip, Eyebrow, Facts, Icon, LiveDot} from '../ui';

export function Agents() {
  const {data, now, disabled, action, setModal} = useArena();
  return (
    <>
      <div className="heading">
        <div>
          <h1>Agents.</h1>
          <p>Three supported subscription slots. Every invocation has a job, a limit and an owner. No API fallback.</p>
        </div>
      </div>
      <div className="cards three">
        {data.agents.map(agent => {
          const job = data.attempts.find(a => a.account_id === agent.id && ACTIVE_ATTEMPT.includes(a.state));
          const task = job && data.tasks.find(t => t.id === job.task_id);
          const cooling = (agent.cooldown_until || 0) > now;
          const state = job
            ? {tone: 'running' as const, label: `Running · ${cap(job.phase)}`}
            : !agent.enabled
              ? {tone: 'off' as const, label: 'Disabled'}
              : agent.health !== 'ready'
                ? {tone: 'blocked' as const, label: agent.health === 'needs_auth' ? 'Needs sign-in' : stateLabel(agent.health)}
                : cooling
                  ? {tone: 'waiting' as const, label: `Cooling down until ${clock(agent.cooldown_until)}`}
                  : {tone: 'neutral' as const, label: 'Available'};
          const usage = usageTotals(data.attempts.filter(a => a.account_id === agent.id));
          const finished = data.attempts.filter(a => a.account_id === agent.id && a.result?.seconds != null);
          return (
            <article className={'card agent ' + (job ? 'on' : '')} key={agent.id}>
              <div className="row between wrap">
                <div className="row">
                  <span className={'glyph lg ' + (job ? 'on' : '')}>{providerGlyph(agent.provider)}</span>
                  <div>
                    <h2>{agent.name}</h2>
                    <p className="small muted">
                      {providerName(agent.provider)} · <span className="num">{agent.version}</span>
                    </p>
                  </div>
                </div>
                <Chip tone={state.tone}>{state.label}</Chip>
              </div>

              <div className={'assignment ' + (job ? 'on' : '')}>
                <Eyebrow>Current assignment</Eyebrow>
                {job ? (
                  <>
                    <b>{task?.title || 'Task'}</b>
                    <span className="small">
                      <LiveDot tone="live" /> {cap(job.phase)} · {job.model} · {job.effort || 'provider default'} · <span className="num">{duration(now - job.created)}</span>
                    </span>
                    <span className="small muted">{job.routing?.reason || job.reason}</span>
                  </>
                ) : (
                  <>
                    <b>{!agent.enabled ? 'Disabled · no new assignments' : agent.health !== 'ready' ? `${stateLabel(agent.health)} on the Mac` : cooling ? `Cooling down until ${clock(agent.cooldown_until)}` : 'Idle · available for the next task'}</b>
                    <span className="small muted">{agent.reason}</span>
                  </>
                )}
              </div>

              <Facts
                rows={[
                  ['Enrolled model', `${agent.model} · ${agent.effort || 'provider default'}`],
                  ['Roles', (agent.roles || ['build', 'review']).join(', ')],
                  ['Health', `${human(agent.health)}${agent.last_health ? ' · checked ' + when(agent.last_health) : ''}`],
                  ['Quota', agent.quota == null ? 'Not reported' : JSON.stringify(agent.quota)],
                  ['Reset time', agent.reset_time == null ? 'Not reported' : when(agent.reset_time)],
                  ['Concurrency', '1 invocation per account'],
                ]}
              />

              <div className="usage">
                <div className="row between">
                  <Eyebrow>Recorded usage</Eyebrow>
                  <span className="small muted num">
                    {finished.length} attempt{finished.length === 1 ? '' : 's'}
                  </span>
                </div>
                {usage ? (
                  <div className="usage-grid">
                    <div>
                      <span className="k">calls</span>
                      <b className="num">{num(usage.calls)}</b>
                    </div>
                    {usage.fields.map(([k, v]) => (
                      <div key={k}>
                        <span className="k">{human(k)}</span>
                        <b className="num">{num(v)}</b>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="small muted">Not reported — the CLI stream has not recorded usage for this account.</p>
                )}
              </div>

              <button
                className="btn full"
                disabled={disabled || agent.health !== 'ready'}
                onClick={() => action(`agents/${agent.id}`, {revision: agent.rev, enabled: !agent.enabled}, 'Worker assignment policy updated')}
              >
                {agent.enabled ? 'Disable new assignments' : 'Enable assignments'}
              </button>
            </article>
          );
        })}
        {Array.from({length: Math.max(0, 3 - data.agents.length)}, (_, i) => (
          <article className="card agent empty-slot" key={'empty' + i}>
            <div className="row between">
              <span className="glyph lg">··</span>
              <Chip tone="off">Not enrolled</Chip>
            </div>
            <h2>Worker slot {data.agents.length + i + 1}</h2>
            <p className="small muted">No account or quota assumed. A separate supported login must be validated on the host before this slot can run.</p>
            <button className="btn full" onClick={() => setModal({kind: 'account'})}>
              Setup details
            </button>
          </article>
        ))}
      </div>

      <div className="card control-strip">
        <div className="field inline">
          <label htmlFor="capacity">Maximum simultaneous invocations</label>
          <select id="capacity" value={data.limit} disabled={disabled} onChange={e => action('control/capacity', {revision: data.revision, limit: Number(e.target.value)}, 'Concurrency ceiling updated')}>
            {[1, 2, 3].map(x => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </div>
        <p className="small muted">Reviewers use the same slots. Lowering the ceiling never kills useful running work.</p>
        <button className="btn danger" disabled={disabled} onClick={() => action('control/stop-all', {revision: data.revision}, 'Dispatch paused and all owned attempts asked to stop')}>
          <Icon name="stop" size={14} />
          Stop all workers
        </button>
      </div>
    </>
  );
}
