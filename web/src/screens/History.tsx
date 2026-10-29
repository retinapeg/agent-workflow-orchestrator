import React, {useState} from 'react';
import {useArena} from '../App';
import {ago, goalArchived, goalClass, goalView, isArchived, isLive, lastChange, outcomeLine, plainError, type GoalView} from '../types';
import {Glyph, Icon} from '../ui';

function ScreenHead({title, sub}: {title: string; sub: string}) {
  const {back} = useArena();
  return (
    <div className="screen-head">
      <button type="button" className="square-btn" aria-label="Back" onClick={back}>
        <Icon name="back" size={20} />
      </button>
      <div className="screen-head-main">
        <div className="screen-title-row">
          <h1 className="screen-title">{title}</h1>
        </div>
        <p className="screen-sub">{sub}</p>
      </div>
    </div>
  );
}

// Builds (history): every build not archived, newest change first, grouped by what became of
// it; a tap opens its story. The technical build view stays one tap away for the record.
export function Builds() {
  const {data, now, openGoal, setTab, search} = useArena();
  const q = search.trim().toLowerCase();
  const views = (data.goals || [])
    .filter(g => !goalArchived(g, data))
    .map(g => goalView(g, data))
    .filter(v => !q || `${v.title} ${v.project?.name || ''}`.toLowerCase().includes(q))
    .sort((a, b) => lastChange(b) - lastChange(a));
  const groups: [string, GoalView[]][] = [
    ['Building now', views.filter(isLive)],
    ['Waiting for you', views.filter(v => !isLive(v) && goalClass(v) === 'needs')],
    ['Finished', views.filter(v => !isLive(v) && goalClass(v) === 'done')],
    ['Stopped', views.filter(v => !isLive(v) && goalClass(v) === 'history')],
  ];
  return (
    <div className="screen history">
      <ScreenHead title="Builds" sub="Everything Arena has built or tried, newest first. Archived builds are under Archived." />
      {groups
        .filter(([, list]) => list.length)
        .map(([name, list]) => (
          <section key={name} className="home-section" aria-label={name}>
            <div className="section-head">
              <h2 className="section-label">
                <span className="section-name">{name}</span>
                <span className="count-pill num">{list.length}</span>
              </h2>
            </div>
            <div className="home-list">
              {list.map(v => {
                const out = outcomeLine(v, now);
                return (
                  <div className="home-row-wrap" key={v.goal.id}>
                    <button type="button" className="home-row finished-row" onClick={() => openGoal(v.goal.id)}>
                      <Glyph tone={out.tone} size={30} />
                      <span className="home-row-main">
                        <b className="clamp2">{v.title}</b>
                        <span className="small muted clamp2">
                          {v.project ? `${v.project.name} · ` : ''}
                          <span className={'tone ' + out.tone}>{name === 'Building now' ? `Started ${ago(v.goal.created, now)}` : out.label}</span>
                        </span>
                      </span>
                      <Icon name="arrow" size={18} />
                    </button>
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      {!views.length && (
        <div className="empty">
          <h3>{q ? 'No build matches the search' : 'No builds yet'}</h3>
          <p>{q ? 'Clear the search to see every build.' : 'Tell Arena what to build on Home; the build appears here.'}</p>
        </div>
      )}
      <details className="disclosure advanced">
        <summary>Advanced</summary>
        <div className="disclosure-body">
          <p className="small muted">The technical view shows each build's tasks, agents, checks and recorded events.</p>
          <button type="button" className="btn" onClick={() => setTab('build')}>
            <Icon name="code" size={16} />
            Technical view
          </button>
        </div>
      </details>
    </div>
  );
}

// Archived builds and projects, ticked and restored. Archiving never deleted anything, so a
// restore brings each back exactly as it was.
export function Archived() {
  const {data, now, post, notify, disabled, openGoal} = useArena();
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const builds = (data.goals || [])
    .filter(g => g.archived)
    .map(g => goalView(g, data))
    .sort((a, b) => (b.goal.archived_at || 0) - (a.goal.archived_at || 0));
  const projects = data.projects.filter(isArchived).sort((a, b) => (b.archived_at || 0) - (a.archived_at || 0));
  const toggle = (key: string) =>
    setPicked(s => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const restore = async () => {
    if (busy || !picked.size) return;
    setBusy(true);
    const goalIds = [...picked].filter(k => k.startsWith('g:')).map(k => k.slice(2));
    const projectIds = [...picked].filter(k => k.startsWith('p:')).map(k => k.slice(2));
    let restored = 0;
    let restoredProjects = 0;
    const failed: string[] = [];
    try {
      if (goalIds.length) {
        const r = await post('goals/unarchive', {ids: goalIds});
        restored = (r?.restored || []).length;
      }
      for (const id of projectIds) {
        const p = data.projects.find(x => x.id === id);
        if (!p) continue;
        try {
          await post(`projects/${id}/unarchive`, {revision: p.rev});
          restoredProjects += 1;
        } catch (e) {
          failed.push(`${p.name}: ${plainError(e).replace(/\.$/, '')}`);
        }
      }
      const done = [restored && `${restored} build${restored === 1 ? '' : 's'}`, restoredProjects && `${restoredProjects} project${restoredProjects === 1 ? '' : 's'}`].filter(Boolean).join(' and ');
      const back = restored + restoredProjects === 1 ? 'It is back on Home.' : 'They are back on Home.';
      notify(`${done ? `Restored ${done}. ${back}` : 'Nothing was restored.'}${failed.length ? ` Not restored: ${failed.join('; ')}.` : ''}`);
      setPicked(new Set());
    } catch (e) {
      notify(plainError(e));
    } finally {
      setBusy(false);
    }
  };
  const selectAll = () => setPicked(new Set([...builds.map(v => 'g:' + v.goal.id), ...projects.map(p => 'p:' + p.id)]));
  return (
    <div className="screen archived">
      <ScreenHead title="Archived" sub="Out of the way, never deleted: every commit, transcript and check is kept. Tick what you want back." />
      <section className="home-section" aria-label="Archived builds">
        <div className="section-head">
          <h2 className="section-label">
            <span className="section-name">Builds</span>
            <span className="count-pill num">{builds.length}</span>
          </h2>
        </div>
        <div className="home-list">
          {builds.map(v => {
            const key = 'g:' + v.goal.id;
            return (
              <div className="home-row-wrap" key={v.goal.id}>
                <label className="home-row finished-row selectable">
                  <input type="checkbox" checked={picked.has(key)} onChange={() => toggle(key)} aria-label={`Select ${v.title}`} />
                  <span className="home-row-main">
                    <b className="clamp2">{v.title}</b>
                    <span className="small muted clamp2">
                      {v.project ? `${v.project.name} · ` : ''}archived {ago(v.goal.archived_at, now) || 'earlier'}
                    </span>
                  </span>
                </label>
                <button type="button" className="play-btn" aria-label={`Open ${v.title}`} onClick={() => openGoal(v.goal.id)}>
                  <Icon name="arrow" size={18} />
                </button>
              </div>
            );
          })}
          {!builds.length && <p className="home-empty small muted">No archived builds.</p>}
        </div>
      </section>
      <section className="home-section" aria-label="Archived projects">
        <div className="section-head">
          <h2 className="section-label">
            <span className="section-name">Projects</span>
            <span className="count-pill num">{projects.length}</span>
          </h2>
        </div>
        <div className="home-list">
          {projects.map(p => {
            const key = 'p:' + p.id;
            const count = (data.goals || []).filter(g => g.project_id === p.id).length;
            return (
              <div className="home-row-wrap" key={p.id}>
                <label className="home-row ready-row selectable">
                  <input type="checkbox" checked={picked.has(key)} onChange={() => toggle(key)} aria-label={`Select project ${p.name}`} />
                  <span className="tile-icon sm" aria-hidden="true">
                    <Icon name="build" size={18} />
                  </span>
                  <span className="home-row-main">
                    <b className="clamp1">{p.name}</b>
                    <span className="small muted clamp1">
                      {count} build{count === 1 ? '' : 's'} · archived {ago(p.archived_at, now) || 'earlier'}
                    </span>
                  </span>
                </label>
              </div>
            );
          })}
          {!projects.length && <p className="home-empty small muted">No archived projects.</p>}
        </div>
      </section>
      {(builds.length > 0 || projects.length > 0) && (
        <div className="select-bar" role="group" aria-label="Restore selection">
          <button type="button" className="btn" onClick={selectAll}>
            Select all
          </button>
          <button type="button" className="btn primary" disabled={disabled || busy || !picked.size} onClick={restore}>
            <Icon name="undo" size={16} />
            Restore selected ({picked.size})
          </button>
        </div>
      )}
    </div>
  );
}
