import React, {useEffect, useState} from 'react';
import {useArena} from '../App';
import {ago, goalArchived, goalClass, goalView, isArchived, isLive, lastChange, outcomeLine, plainError, type GoalView} from '../types';
import {Glyph, Icon} from '../ui';

function ScreenHead({title, sub, children}: {title: string; sub: string; children?: React.ReactNode}) {
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
      {children}
    </div>
  );
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

// Build history: every build not archived, newest change first, grouped by what became of it;
// a tap opens its story. "Select" ticks finished and stopped builds to archive them (never one
// that is still running or waiting for the owner: the host refuses those too). The technical
// build view is listed only in Advanced mode.
export function Builds() {
  const {data, now, openGoal, setTab, advanced, post, notify, disabled} = useArena();
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [archiving, setArchiving] = useState(false);
  const views = (data.goals || [])
    .filter(g => !goalArchived(g, data))
    .map(g => goalView(g, data))
    .sort((a, b) => lastChange(b) - lastChange(a));
  const groups: [string, GoalView[]][] = [
    ['Building now', views.filter(isLive)],
    ['Waiting for you', views.filter(v => !isLive(v) && goalClass(v) === 'needs')],
    ['Finished', views.filter(v => !isLive(v) && goalClass(v) === 'done')],
    ['Stopped', views.filter(v => !isLive(v) && goalClass(v) === 'history')],
  ];
  const canArchive = (v: GoalView) => !isLive(v) && ['done', 'history'].includes(goalClass(v));
  const selectable = views.filter(canArchive).map(v => v.goal.id);
  // A ticked build that starts again, or leaves the list, drops out of the selection.
  const selectableKey = selectable.join(',');
  useEffect(() => {
    const keep = new Set(selectable);
    setPicked(s => ([...s].every(id => keep.has(id)) ? s : new Set([...s].filter(id => keep.has(id)))));
  }, [selectableKey]);
  const chosen = [...picked].filter(id => selectable.includes(id));
  const toggle = (id: string) =>
    setPicked(s => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const stopSelecting = () => {
    setSelecting(false);
    setPicked(new Set());
  };
  const archiveSelected = async () => {
    if (archiving || !chosen.length) return;
    setArchiving(true);
    try {
      const r = await post('goals/archive', {ids: chosen});
      const archived = (r?.archived || []).length;
      const reasons = new Map<string, number>();
      for (const s of r?.skipped || []) reasons.set(s.reason || 'skipped', (reasons.get(s.reason || 'skipped') || 0) + 1);
      const left = [...reasons].map(([reason, n]) => `${plural(n, 'build')} ${reason}`);
      notify(`${archived ? `Archived ${plural(archived, 'build')}. Restore from Archived in the menu.` : 'Nothing was archived.'}${left.length ? ` Left alone: ${left.join('; ')}.` : ''}`);
      stopSelecting();
    } catch (e) {
      notify(plainError(e));
    } finally {
      setArchiving(false);
    }
  };
  return (
    <div className="screen history">
      <ScreenHead title="Build history" sub="Everything Arena has built or tried, newest first. Archived builds are under Archived.">
        {selectable.length > 0 && (
          <button type="button" className="text-btn head-action" onClick={selecting ? stopSelecting : () => setSelecting(true)}>
            {selecting ? 'Cancel' : 'Select'}
          </button>
        )}
      </ScreenHead>
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
                const body = (
                  <>
                    <Glyph tone={out.tone} size={30} />
                    <span className="home-row-main">
                      <b className="clamp2">{v.title}</b>
                      <span className="small muted clamp2">
                        {v.project ? `${v.project.name} · ` : ''}
                        <span className={'tone ' + out.tone}>{name === 'Building now' ? `Started ${ago(v.goal.created, now)}` : out.label}</span>
                      </span>
                    </span>
                  </>
                );
                if (!selecting)
                  return (
                    <button type="button" className="home-row finished-row" key={v.goal.id} onClick={() => openGoal(v.goal.id)}>
                      {body}
                      <Icon name="arrow" size={18} />
                    </button>
                  );
                if (canArchive(v))
                  return (
                    <label className="home-row finished-row selectable" key={v.goal.id}>
                      <input type="checkbox" checked={picked.has(v.goal.id)} onChange={() => toggle(v.goal.id)} aria-label={`Select ${v.title}`} />
                      {body}
                    </label>
                  );
                return (
                  <div className="home-row finished-row unselectable" key={v.goal.id}>
                    <input type="checkbox" disabled aria-label={`${v.title} is ${name === 'Building now' ? 'still running' : 'waiting for you'} and cannot be archived`} />
                    {body}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      {!views.length && (
        <div className="empty">
          <h3>No builds yet</h3>
          <p>Tell Arena what to build on Home; the build appears here.</p>
        </div>
      )}
      {selecting && (
        <div className="select-bar" role="group" aria-label="Archive selection">
          <button type="button" className="btn" onClick={() => setPicked(new Set(selectable))} disabled={!selectable.length}>
            Select all
          </button>
          <button type="button" className="btn primary" disabled={disabled || archiving || !chosen.length} onClick={archiveSelected}>
            <Icon name="archive" size={16} />
            Archive selected ({chosen.length})
          </button>
        </div>
      )}
      {advanced && !selecting && (
        <button type="button" className="btn technical-link" onClick={() => setTab('build')}>
          <Icon name="code" size={16} />
          Technical build view
        </button>
      )}
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
      const done = [restored && plural(restored, 'build'), restoredProjects && plural(restoredProjects, 'project')].filter(Boolean).join(' and ');
      const where = restored ? 'Build history' : 'the project pickers';
      const back = restored + restoredProjects === 1 ? `It is back in ${where}.` : `They are back in ${where}.`;
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
                <button type="button" className="open-btn" aria-label={`Open ${v.title}`} onClick={() => openGoal(v.goal.id)}>
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
              <label className="home-row ready-row selectable" key={p.id}>
                <input type="checkbox" checked={picked.has(key)} onChange={() => toggle(key)} aria-label={`Select project ${p.name}`} />
                <span className="tile-icon sm" aria-hidden="true">
                  <Icon name="build" size={18} />
                </span>
                <span className="home-row-main">
                  <b className="clamp1">{p.name}</b>
                  <span className="small muted clamp1">
                    {plural(count, 'build')} · archived {ago(p.archived_at, now) || 'earlier'}
                  </span>
                </span>
              </label>
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
