import React from 'react';
import {useArena} from '../App';
import {approvalLabel, family, goalArchived, isHistoryTask, needsYou, seconds, stateLabel, stopLine, stripLegacy, when, type RecordData} from '../types';
import {Chip, Empty, Eyebrow, Glyph, Icon, Panel, Sha} from '../ui';

// The technical record of one row (commits, check names, raw reasons, review findings),
// folded away: the row itself says what happened in plain English.
function RowAdvanced({children}: {children: React.ReactNode}) {
  return (
    <details className="row-advanced">
      <summary>Advanced</summary>
      <div className="row-advanced-body small muted">{children}</div>
    </details>
  );
}

export function Review() {
  const {data, setModal, setEvidence} = useArena();
  // The shared list: archived builds and projects never ask for anything.
  const needs = needsYou(data);
  const taskOf = (a: RecordData) => data.tasks.find(t => t.id === a.task_id);
  const shelved = (t: RecordData | undefined) => {
    const goal = t && (data.goals || []).find(g => g.id === t.goal_id);
    return !!goal && goalArchived(goal, data);
  };
  const accepted = data.approvals.filter(a => a.state === 'accept' && a.publication?.state !== 'integrated' && !shelved(taskOf(a)));
  const autoPending = data.approvals.filter(a => a.state === 'pending' && !needs.approvals.includes(a) && !shelved(taskOf(a)));
  const open = [...needs.approvals, ...accepted];
  const historyApprovals = data.approvals
    .filter(a => (a.publication?.state === 'integrated' || ['reject', 'request_changes', 'superseded'].includes(a.state)) && !shelved(taskOf(a)))
    .sort((a, b) => (b.publication?.receipt?.time || b.created) - (a.publication?.receipt?.time || a.created));
  const historyTasks = data.tasks.filter(t => isHistoryTask(t) && !shelved(t)).sort((a, b) => b.updated - a.updated);
  const humans = [...needs.answers, ...needs.access];
  const reviewOf = (t: RecordData | undefined) => (t?.review_attempt ? data.attempts.find(x => x.id === t.review_attempt) : undefined);
  const directorOf = (t: RecordData | undefined) => (t?.director_review_attempt ? data.attempts.find(x => x.id === t.director_review_attempt) : undefined);
  // What was verified, in counts and verdicts: "All 3 checks passed · Independent review approved".
  const verified = (a: RecordData) => {
    const t = taskOf(a);
    const checks: RecordData[] = Array.isArray(a.checks) ? a.checks : [];
    const passed = checks.filter(c => c.status === 'PASS').length;
    const review = reviewOf(t)?.result?.review;
    const parts = [
      !checks.length ? 'No combined checks recorded' : passed === checks.length ? (checks.length === 1 ? 'The check passed' : `All ${checks.length} checks passed`) : `${checks.length - passed} of ${checks.length} checks failed`,
      review ? (review.approved ? 'Independent review approved' : 'Independent review asked for changes') : t?.review === false ? 'Independent review skipped for this low-risk change' : 'No independent review recorded',
    ];
    return parts.join(' · ');
  };
  const whatNext = (a: RecordData) => {
    const pub = a.publication?.state;
    if (pub === 'failed') return 'Arena could not merge this change into main. Nothing on main was changed.';
    if (pub === 'queued') return 'Arena is merging it into main now.';
    if (a.state === 'accept') return 'You accepted it. Merge it into main when you are ready.';
    return 'Look at the change, then accept it or ask for changes.';
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h2 className="page-title">Review</h2>
          <p className="page-sub">Verified changes wait here for your decision. Open one to see its checks, the independent review and the exact change.</p>
        </div>
      </div>

      <div className="cards two">
        {open.map(a => {
          const t = taskOf(a);
          const review = reviewOf(t);
          const director = directorOf(t);
          const pub = a.publication;
          const status = approvalLabel(a);
          const summary = director?.result?.review?.summary || review?.result?.review?.summary || '';
          return (
            <article className="card approval" key={a.id}>
              <div className="row between wrap">
                <Chip tone={status.tone}>{status.label}</Chip>
                <span className="small muted num">{when(a.created)}</span>
              </div>
              <h2>{stripLegacy(t?.title || 'Verified change')}</h2>
              <p className="lede">{whatNext(a)}</p>
              <p className="small muted">{verified(a)}</p>
              <RowAdvanced>
                <p className="path">
                  Candidate <Sha value={a.candidate} /> · base <Sha value={a.base} />
                  {a.covered_tasks?.length > 1 ? ` · covers ${a.covered_tasks.length} tasks` : ''}
                </p>
                {a.checks?.length > 0 && (
                  <div className="checks">
                    {a.checks.map((c: RecordData) => (
                      <Chip key={c.name} tone={c.status === 'PASS' ? 'pass' : 'fail'}>
                        {c.name} · {c.status} · {seconds(c.seconds)}
                      </Chip>
                    ))}
                  </div>
                )}
                {review?.result?.review && (
                  <p>
                    Independent review {review.result.review.approved ? 'approved' : 'rejected'} · {review.result.review.findings?.length || 0} findings · {review.model}
                  </p>
                )}
                {summary && <p>{summary}</p>}
                {pub?.reason && <p className="prewrap">{pub.reason}</p>}
              </RowAdvanced>
              <button
                className="btn primary"
                onClick={() => {
                  setEvidence(null);
                  setModal({kind: 'review', id: a.id});
                }}
              >
                Inspect evidence & diff
              </button>
            </article>
          );
        })}
      </div>

      {humans.length > 0 && (
        <Panel title="Needs you" aside={<span className="count num">{humans.length}</span>} className="mt accent-amber">
          {humans.map(t => (
            <button className="list-row press" key={t.id} onClick={() => setModal({kind: 'task', id: t.id})}>
              <Glyph tone={family(t.state)} size={32} />
              <div className="list-main">
                <b>{stripLegacy(t.title)}</b>
                <span className="small muted">
                  <span className={'tone ' + family(t.state)}>{stateLabel(t.state)}</span> · {when(t.updated)} ·{' '}
                  {t.state === 'awaiting_input' ? t.question || 'The director has a question for you' : t.state === 'needs_auth' ? 'An agent needs you to sign in on the Mac' : 'An agent needs your permission on the Mac'}
                </span>
              </div>
              <Icon name="arrow" size={16} />
            </button>
          ))}
        </Panel>
      )}

      {autoPending.length > 0 && (
        <Panel title="Merging automatically" aside={<span className="count num">{autoPending.length}</span>} className="mt">
          {autoPending.map(a => (
            <button className="list-row press" key={a.id} onClick={() => setModal({kind: 'review', id: a.id})}>
              <Glyph tone="running" size={32} />
              <div className="list-main">
                <b>{stripLegacy(taskOf(a)?.title || 'Verified change')}</b>
                <span className="small muted">Verified · this project merges verified work into main without asking</span>
              </div>
              <Icon name="arrow" size={16} />
            </button>
          ))}
        </Panel>
      )}

      {!open.length && !humans.length && <Empty title="Nothing is waiting for you">Verified changes appear here with their checks and the independent review.</Empty>}

      <Panel title="History" aside={<Eyebrow>Records stay inspectable</Eyebrow>} className="mt">
        {historyApprovals.map(a => {
          const t = taskOf(a);
          const r = a.publication?.receipt;
          const status = approvalLabel(a);
          const title = stripLegacy(t?.title || 'Verified change');
          return (
            <div className="list-row review-row" key={a.id}>
              <Glyph tone={status.tone === 'done' ? 'done' : 'off'} size={32} />
              <div className="list-main">
                <b>{title}</b>
                <span className="small muted">
                  {status.label} · {when(r?.time || a.created)}
                  {a.feedback ? ` · “${a.feedback}”` : ''}
                </span>
                <RowAdvanced>
                  <p className="path">
                    {a.publication?.state === 'integrated' ? (
                      <>
                        Commit <Sha value={r?.candidate || a.candidate} /> → {r?.branch || 'main'}
                      </>
                    ) : (
                      <>
                        Candidate <Sha value={a.candidate} />
                      </>
                    )}
                  </p>
                  {a.publication?.reason && <p className="prewrap">{a.publication.reason}</p>}
                </RowAdvanced>
              </div>
              <button className="text-btn" aria-label={`Receipt for ${title}`} onClick={() => setModal({kind: 'review', id: a.id})}>
                Receipt
              </button>
            </div>
          );
        })}
        {historyTasks.map(t => {
          const title = stripLegacy(t.title);
          return (
            <div className="list-row review-row" key={t.id}>
              <Glyph tone="off" size={32} />
              <div className="list-main">
                <b>{title}</b>
                <span className="small muted">
                  {stopLine(t, data)} · {when(t.updated)}
                </span>
                {t.reason && (
                  <RowAdvanced>
                    <p className="prewrap">{t.reason}</p>
                  </RowAdvanced>
                )}
              </div>
              <button className="text-btn" aria-label={`Open ${title}`} onClick={() => setModal({kind: 'task', id: t.id})}>
                Open
              </button>
            </div>
          );
        })}
        {!historyApprovals.length && !historyTasks.length && <p className="panel-body muted small">Merged changes, your decisions and stopped work will be listed here.</p>}
      </Panel>
    </>
  );
}
