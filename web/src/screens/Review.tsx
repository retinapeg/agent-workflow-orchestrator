import React from 'react';
import {useArena} from '../App';
import {BLOCKED_TASK, cap, family, seconds, stateLabel, when, type RecordData} from '../types';
import {Chip, Empty, Eyebrow, Icon, Panel, Sha} from '../ui';

export function Review() {
  const {data, setModal, setEvidence} = useArena();
  const open = data.approvals.filter(a => a.state === 'pending' || (a.state === 'accept' && a.publication?.state !== 'integrated'));
  const history = data.approvals
    .filter(a => a.publication?.state === 'integrated' || ['reject', 'request_changes', 'superseded'].includes(a.state))
    .sort((a, b) => (b.publication?.receipt?.time || b.created) - (a.publication?.receipt?.time || a.created));
  const humans = data.tasks.filter(t => BLOCKED_TASK.includes(t.state) || t.state === 'awaiting_input');
  const taskOf = (a: RecordData) => data.tasks.find(t => t.id === a.task_id);
  const summaryOf = (a: RecordData) => {
    const t = taskOf(a);
    const director = t?.director_review_attempt && data.attempts.find(x => x.id === t.director_review_attempt);
    const review = t?.review_attempt && data.attempts.find(x => x.id === t.review_attempt);
    return director?.result?.review?.summary || review?.result?.review?.summary || review?.result?.reason || t?.reason || '';
  };
  return (
    <>
      <div className="heading">
        <div>
          <h1>Review & results.</h1>
          <p>Checks passing is different from permission to publish. Inspect the exact candidate, then decide.</p>
        </div>
      </div>

      <div className="cards two">
        {open.map(a => {
          const t = taskOf(a);
          const review = t?.review_attempt && data.attempts.find(x => x.id === t.review_attempt);
          const pub = a.publication;
          const tone = pub?.state === 'integrated' ? 'done' : pub?.state === 'failed' ? 'blocked' : 'decision';
          const label = pub ? cap(pub.state) : a.state === 'accept' ? 'Accepted · awaiting integration' : 'Ready to inspect';
          return (
            <article className="card approval" key={a.id}>
              <div className="row between wrap">
                <Chip tone={tone}>{label}</Chip>
                <span className="small muted num">{when(a.created)}</span>
              </div>
              <h2>{t?.title}</h2>
              <p className="summary clamp3">{summaryOf(a)}</p>
              <p className="path">
                Candidate <Sha value={a.candidate} /> · base <Sha value={a.base} />
                {a.covered_tasks?.length > 1 ? ` · covers ${a.covered_tasks.length} tasks` : ''}
              </p>
              <div className="checks">
                {a.checks.map((c: RecordData) => (
                  <Chip key={c.name} tone={c.status === 'PASS' ? 'pass' : 'fail'}>
                    {c.name} · {c.status} · {seconds(c.seconds)}
                  </Chip>
                ))}
                {!a.checks.length && <span className="small muted">No combined checks recorded</span>}
              </div>
              <p className="small muted">
                {review?.result?.review
                  ? `Independent review ${review.result.review.approved ? 'approved' : 'rejected'} · ${review.result.review.findings?.length || 0} findings · ${review.model}`
                  : t?.review === false
                    ? 'Independent review was explicitly omitted for this low-risk task.'
                    : 'Independent review evidence not recorded.'}
              </p>
              {pub?.reason && <p className="small muted">{pub.reason}</p>}
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
        <Panel title="Needs a human" aside={<span className="count num">{humans.length}</span>} className="mt">
          {humans.map(t => (
            <button className="list-row press" key={t.id} onClick={() => setModal({kind: 'task', id: t.id})}>
              <Chip tone={family(t.state)}>{stateLabel(t.state)}</Chip>
              <div className="list-main">
                <b>{t.title}</b>
                <span className="small muted">{t.question || t.reason}</span>
              </div>
              <Icon name="arrow" size={16} />
            </button>
          ))}
        </Panel>
      )}

      {!open.length && !humans.length && (
        <Empty title="Review queue clear">Verified changes appear here with their checks, the independent review and the exact commit.</Empty>
      )}

      <Panel title="Integrated results" aside={<Eyebrow>History</Eyebrow>} className="mt">
        {history.map(a => {
          const t = taskOf(a);
          const r = a.publication?.receipt;
          const integrated = a.publication?.state === 'integrated';
          return (
            <div className="list-row" key={a.id}>
              <Chip tone={integrated ? 'done' : 'off'}>{integrated ? 'Integrated' : cap(a.state.replaceAll('_', ' '))}</Chip>
              <div className="list-main">
                <b>{t?.title}</b>
                <span className="small muted">
                  {integrated ? (
                    <>
                      <Sha value={a.candidate} /> → {r?.branch || 'main'} · {when(r?.time || a.created)}
                    </>
                  ) : (
                    a.feedback || a.publication?.reason || when(a.created)
                  )}
                </span>
              </div>
              <button className="text-btn" onClick={() => setModal({kind: 'review', id: a.id})}>
                Receipt
              </button>
            </div>
          );
        })}
        {!history.length && <p className="panel-body muted small">Integrated commits and recorded decisions will be listed here.</p>}
      </Panel>
    </>
  );
}
