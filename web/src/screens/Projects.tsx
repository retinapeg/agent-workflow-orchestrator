import React, {useState} from 'react';
import {useArena} from '../App';
import {clock, family, goalClass, goalStatus, goalView, stateLabel, when} from '../types';
import {Chip, Empty, Eyebrow, Facts, Icon, LiveDot, Sha} from '../ui';

const basename = (repo: string) => (repo || '').replace(/\/+$/, '').split('/').pop() || repo;

export function Projects() {
  const {data, disabled, action, setModal, openGoal, search} = useArena();
  const [open, setOpen] = useState<string | null>(null);
  const q = search.trim().toLowerCase();
  const projects = data.projects.filter(p => !q || `${p.name} ${p.repo}`.toLowerCase().includes(q));
  return (
    <>
      <div className="page-head">
        <div>
          <h2 className="page-title">Projects</h2>
          <p className="page-sub">Enrolled repositories with explicit scope, frozen checks and a verified staging branch.</p>
        </div>
        <div className="row">
          <button className="btn primary" disabled={disabled || !data.projects.length} onClick={() => setModal({kind: 'goal'})}>
            <Icon name="plus" size={16} />
            New request
          </button>
          <button className="btn chrome" onClick={() => setModal({kind: 'enrol'})}>
            <Icon name="plus" size={16} />
            Add
          </button>
        </div>
      </div>
      <section className="panel project-list">
        {projects.map(p => {
          const views = (data.goals || []).filter(g => g.project_id === p.id).map(g => goalView(g, data)).sort((a, b) => b.goal.created - a.goal.created);
          const goals = views.filter(v => goalClass(v) !== 'history');
          const history = views.filter(v => goalClass(v) === 'history');
          const outstanding = data.tasks.filter(t => t.project_id === p.id && !['completed', 'cancelled', 'blocked', 'interrupted'].includes(t.state));
          const results = data.tasks.filter(t => t.project_id === p.id && t.integrated_commit).sort((a, b) => b.updated - a.updated).slice(0, 5);
          const running = data.tasks.some(t => t.project_id === p.id && ['leased', 'running', 'verifying', 'reviewing', 'recovering', 'integrating'].includes(t.state));
          const expanded = open === p.id;
          return (
            <article className={'project-row' + (expanded ? ' open' : '')} key={p.id}>
              <div className="project-line">
                <button className="project-main" onClick={() => setOpen(expanded ? null : p.id)} aria-expanded={expanded}>
                  <span className="tile-icon" aria-hidden="true">
                    {p.self ? <Icon name="build" size={22} /> : p.name.slice(0, 1).toUpperCase()}
                  </span>
                  <span className="list-main">
                    <b>
                      {p.name}
                      {p.self && <span className="tag core">Core</span>}
                    </b>
                    <span className="small muted clamp1">
                      {basename(p.repo)} · {p.integration_policy === 'auto_local_main' ? 'integrates automatically' : 'you accept each change'}
                    </span>
                  </span>
                </button>
                <select
                  className="priority"
                  aria-label={`${p.name} priority`}
                  value={p.priority}
                  disabled={disabled}
                  onChange={e => action(`projects/${p.id}/policy`, {revision: p.rev, priority: Number(e.target.value)}, 'Priority updated')}
                >
                  {[1, 2, 3].map(n => (
                    <option key={n} value={n}>
                      {n === 1 ? 'High' : n === 2 ? 'Normal' : 'Low'}
                    </option>
                  ))}
                </select>
                <LiveDot tone={!p.enabled ? 'warn' : running ? 'live' : 'ok'} />
                <button className="disclose" onClick={() => setOpen(expanded ? null : p.id)} aria-label={expanded ? `Collapse ${p.name}` : `Expand ${p.name}`} aria-expanded={expanded}>
                  <Icon name="arrow" size={18} />
                </button>
              </div>

              {expanded && (
                <div className="project-body">
                  <div className="row between wrap">
                    <Eyebrow>
                      Priority {p.priority} · {p.checks.length} frozen check{p.checks.length === 1 ? '' : 's'}
                    </Eyebrow>
                    <Chip tone={p.enabled ? 'running' : 'off'}>{p.enabled ? 'Active' : 'Paused'}</Chip>
                  </div>
                  <p className="path">{p.repo}</p>
                  <Facts
                    rows={[
                      ['Scope', p.scope.join(', ')],
                      ['Checks', p.checks.map((c: any) => c.name).join(', ') || 'None'],
                      ['Base → head', <span className="num">{<Sha value={p.base} n={7} />} → {<Sha value={p.head} n={7} />}</span>],
                      ['Main', p.integrated_head ? <Sha value={p.integrated_head} /> : 'No integration yet'],
                      ['Integration', p.integration_policy === 'auto_local_main' ? 'Automatic into local main after every gate passes' : 'Manual: you accept each verified change'],
                    ]}
                  />
                  <div className="project-controls">
                    <select
                      aria-label={`${p.name} integration policy`}
                      value={p.integration_policy || 'manual'}
                      disabled={disabled}
                      onChange={e => action(`projects/${p.id}/policy`, {revision: p.rev, integration_policy: e.target.value}, 'Integration policy updated')}
                    >
                      <option value="manual">Ask me before integrating</option>
                      <option value="auto_local_main">Integrate verified work automatically</option>
                    </select>
                    <button className="btn" disabled={disabled} onClick={() => action(`projects/${p.id}/${p.enabled ? 'pause' : 'resume'}`, {revision: p.rev}, 'Project dispatch updated')}>
                      <Icon name={p.enabled ? 'pause' : 'play'} size={14} />
                      {p.enabled ? 'Pause project' : 'Resume project'}
                    </button>
                  </div>

                  <section className="sub">
                    <div className="row between">
                      <Eyebrow>Requests</Eyebrow>
                      <span className="count num">{goals.length}</span>
                    </div>
                    {goals.slice(0, 5).map(v => {
                      const st = goalStatus(v);
                      return (
                        <button className="list-row press" key={v.goal.id} onClick={() => openGoal(v.goal.id)}>
                          <Chip tone={st.tone}>{st.label}</Chip>
                          <div className="list-main">
                            <b className="clamp2">{v.goal.request}</b>
                            <span className="small muted">
                              {when(v.goal.created)} · {v.children.length || v.tasks.length} task{(v.children.length || v.tasks.length) === 1 ? '' : 's'}
                            </span>
                          </div>
                          <Icon name="arrow" size={16} />
                        </button>
                      );
                    })}
                    {!goals.length && <p className="muted small">No requests yet.</p>}
                  </section>

                  <section className="sub">
                    <div className="row between">
                      <Eyebrow>Outstanding milestones</Eyebrow>
                      <span className="count num">{outstanding.length}</span>
                    </div>
                    {outstanding.map(t => (
                      <button className="list-row press" key={t.id} onClick={() => setModal({kind: 'task', id: t.id})}>
                        <Chip tone={family(t.state)}>{stateLabel(t.state)}</Chip>
                        <div className="list-main">
                          <b>{t.title}</b>
                          <span className="small muted">{t.reason}</span>
                        </div>
                        <Icon name="arrow" size={16} />
                      </button>
                    ))}
                    {!outstanding.length && <p className="muted small">Nothing outstanding.</p>}
                  </section>

                  <section className="sub">
                    <div className="row between">
                      <Eyebrow>Recent results</Eyebrow>
                      <span className="count num">{results.length}</span>
                    </div>
                    {results.map(t => (
                      <div className="list-row" key={t.id}>
                        <Sha value={t.integrated_commit} n={7} />
                        <div className="list-main">
                          <b>{t.title}</b>
                          <span className="small muted">Integrated into main · {when(t.updated)}</span>
                        </div>
                        <span className="num small muted">{clock(t.updated)}</span>
                      </div>
                    ))}
                    {!results.length && <p className="muted small">No integrated commits yet.</p>}
                  </section>

                  {history.length > 0 && (
                    <details className="sub earlier">
                      <summary>
                        History <span className="count num">{history.length}</span>
                      </summary>
                      {history.map(v => {
                        const st = goalStatus(v);
                        return (
                          <button className="list-row press" key={v.goal.id} onClick={() => openGoal(v.goal.id)}>
                            <Chip tone={st.tone}>{st.label}</Chip>
                            <div className="list-main">
                              <b className="clamp2">{v.goal.request}</b>
                              <span className="small muted">{when(v.goal.created)}</span>
                            </div>
                            <Icon name="arrow" size={16} />
                          </button>
                        );
                      })}
                    </details>
                  )}
                </div>
              )}
            </article>
          );
        })}
        {!projects.length && (
          <div className="panel-body">
            <Empty title={q ? 'No projects match' : 'No enrolled projects'}>{q ? 'Clear the search to see every project.' : 'Enroll a repository on the host with a reviewed project manifest. Requests can only touch enrolled paths and run their frozen checks.'}</Empty>
          </div>
        )}
      </section>
      <p className="note">Enroll repositories on the host with a reviewed project manifest. Browser requests can select only enrolled paths and their frozen checks.</p>
    </>
  );
}
