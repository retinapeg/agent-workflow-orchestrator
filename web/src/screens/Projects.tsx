import React from 'react';
import {useArena} from '../App';
import {clock, family, goalView, stateLabel, when} from '../types';
import {Chip, Empty, Eyebrow, Facts, Icon, Sha} from '../ui';

export function Projects() {
  const {data, disabled, action, setModal, openGoal} = useArena();
  return (
    <>
      <div className="heading">
        <div>
          <h1>Projects.</h1>
          <p>Enrolled repositories with explicit scope, frozen checks and a verified staging branch.</p>
        </div>
        <button className="btn primary" disabled={disabled || !data.projects.length} onClick={() => setModal({kind: 'goal'})}>
          <Icon name="plus" size={16} />
          New request
        </button>
      </div>
      <div className="cards two">
        {data.projects.map(p => {
          const goals = (data.goals || []).filter(g => g.project_id === p.id).map(g => goalView(g, data)).sort((a, b) => b.goal.created - a.goal.created);
          const outstanding = data.tasks.filter(t => t.project_id === p.id && !['completed', 'cancelled'].includes(t.state));
          const results = data.tasks.filter(t => t.project_id === p.id && t.integrated_commit).sort((a, b) => b.updated - a.updated).slice(0, 5);
          return (
            <article className="card project" key={p.id}>
              <div className="row between wrap">
                <Eyebrow>
                  Priority {p.priority} · {p.checks.length} frozen check{p.checks.length === 1 ? '' : 's'}
                </Eyebrow>
                <Chip tone={p.enabled ? 'running' : 'off'}>{p.enabled ? 'Active' : 'Paused'}</Chip>
              </div>
              <h2>{p.name}</h2>
              <p className="path">{p.repo}</p>
              <Facts
                rows={[
                  ['Scope', p.scope.join(', ')],
                  ['Checks', p.checks.map((c: any) => c.name).join(', ') || 'None'],
                  ['Base → head', <span className="num">{<Sha value={p.base} n={7} />} → {<Sha value={p.head} n={7} />}</span>],
                  ['Main', p.integrated_head ? <Sha value={p.integrated_head} /> : 'No integration yet'],
                ]}
              />
              <div className="project-controls">
                <select
                  aria-label={`${p.name} priority`}
                  value={p.priority}
                  disabled={disabled}
                  onChange={e => action(`projects/${p.id}/policy`, {revision: p.rev, priority: Number(e.target.value)}, 'Priority updated')}
                >
                  {[1, 2, 3].map(n => (
                    <option key={n} value={n}>
                      {n === 1 ? 'High priority' : n === 2 ? 'Normal priority' : 'Low priority'}
                    </option>
                  ))}
                </select>
                <button className="btn" disabled={disabled} onClick={() => action(`projects/${p.id}/${p.enabled ? 'pause' : 'resume'}`, {revision: p.rev}, 'Project dispatch updated')}>
                  <Icon name={p.enabled ? 'pause' : 'play'} size={14} />
                  {p.enabled ? 'Pause project' : 'Resume project'}
                </button>
              </div>

              <section className="sub">
                <div className="row between">
                  <Eyebrow>Goals</Eyebrow>
                  <span className="count num">{goals.length}</span>
                </div>
                {goals.slice(0, 5).map(v => (
                  <button className="list-row press" key={v.goal.id} onClick={() => openGoal(v.goal.id)}>
                    <Chip tone={v.tone}>{v.status}</Chip>
                    <div className="list-main">
                      <b className="clamp2">{v.goal.request}</b>
                      <span className="small muted">
                        {when(v.goal.created)} · {v.children.length || v.tasks.length} task{(v.children.length || v.tasks.length) === 1 ? '' : 's'}
                      </span>
                    </div>
                    <Icon name="arrow" size={16} />
                  </button>
                ))}
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
            </article>
          );
        })}
        {!data.projects.length && (
          <Empty title="No enrolled projects">Enroll a repository on the host with a reviewed project manifest. Requests can only touch enrolled paths and run their frozen checks.</Empty>
        )}
      </div>
      <p className="note">Enroll repositories on the host with a reviewed project manifest. Browser requests can select only enrolled paths and their frozen checks.</p>
    </>
  );
}
