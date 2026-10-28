import React, {useState} from 'react';
import {useArena} from '../App';
import {useSpeech} from '../speech';
import {ACTIVE_ATTEMPT, RETRYABLE, UNSTOPPABLE, attemptTone, cap, clock, duration, family, human, seconds, stateLabel, when, type RecordData} from '../types';
import {Chip, Eyebrow, Facts, Icon, LiveDot, Sha} from '../ui';
import {Devices} from './Devices';

function lineClass(line: string) {
  if (line.startsWith('+++') || line.startsWith('---')) return 'd-file';
  if (line.startsWith('diff ') || line.startsWith('index ')) return 'd-file';
  if (line.startsWith('@@')) return 'd-hunk';
  if (line.startsWith('+')) return 'd-add';
  if (line.startsWith('-')) return 'd-del';
  return '';
}

function EvidenceView() {
  const {evidence, setEvidence} = useArena();
  if (!evidence) return null;
  return (
    <div className="evidence">
      <div className="row between">
        <Eyebrow>{evidence.label}</Eyebrow>
        <button className="text-btn" onClick={() => setEvidence(null)}>
          Hide
        </button>
      </div>
      {evidence.warn && <p className="warn-banner">{evidence.warn}</p>}
      <pre className={'code ' + evidence.kind}>
        {evidence.kind === 'diff'
          ? evidence.text.split('\n').map((l, i) => (
              <span key={i} className={lineClass(l)}>
                {l}
                {'\n'}
              </span>
            ))
          : evidence.text}
      </pre>
    </div>
  );
}

function NewRequest() {
  const {data, action, disabled} = useArena();
  const [draft, setDraft] = useState('');
  const [project, setProject] = useState(data.projects[0]?.id || '');
  const speech = useSpeech(text => setDraft(d => (d.trim() ? d.replace(/\s+$/, '') + ' ' : '') + text));
  return (
    <form
      onSubmit={async e => {
        e.preventDefault();
        const p = data.projects.find(p => p.id === project)!;
        await action(`projects/${p.id}/requests`, {revision: p.rev, instructions: draft}, 'Your director is inspecting the project and preparing the work.', true);
      }}
    >
      <div className="field">
        <label htmlFor="project">Your project</label>
        <select id="project" name="project" value={project} onChange={e => setProject(e.target.value)}>
          {data.projects.map(p => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <div className="field-row">
          <label htmlFor="instructions">What would you like to change?</label>
          {speech.supported && (
            <button type="button" className={'mic' + (speech.listening ? ' on' : '')} aria-pressed={speech.listening} onClick={speech.toggle}>
              <Icon name={speech.listening ? 'stop' : 'mic'} size={15} />
              {speech.listening ? 'Stop' : 'Dictate'}
            </button>
          )}
        </div>
        <textarea id="instructions" name="instructions" required rows={6} value={draft} onChange={e => setDraft(e.target.value)} placeholder="Make my application tracker easier to use." />
        {speech.listening && (
          <p className="listening" aria-live="polite">
            <LiveDot tone="live" />
            Listening{speech.interim ? ` · ${speech.interim}` : ' · speak now; the transcript is appended when you pause.'}
          </p>
        )}
        {speech.error && <p className="field-help warn">{speech.error}</p>}
      </div>
      <p className="field-help">
        Describe the result in your own words. Type here{speech.supported ? ', tap Dictate,' : ''} or use your phone keyboard's microphone. Your director inspects the project, plans the work and coordinates the agents.
      </p>
      <div className="dialog-actions">
        <button className="btn primary" disabled={disabled}>
          Build this for me
        </button>
      </div>
    </form>
  );
}

function TaskDialog({task}: {task: RecordData}) {
  const {data, now, action, disabled, showEvidence, setEvidence} = useArena();
  const [answer, setAnswer] = useState('');
  const [steer, setSteer] = useState('');
  const attempts = data.attempts.filter(a => a.task_id === task.id).sort((a, b) => a.created - b.created);
  const live = attempts.find(a => ACTIVE_ATTEMPT.includes(a.state));
  const agent = live && data.agents.find(a => a.id === live.account_id);
  const deps = (task.dependencies || []).map((id: string) => data.tasks.find(t => t.id === id)?.title || id);
  const frozen = UNSTOPPABLE.includes(task.state);
  return (
    <>
      <div className="row wrap">
        <Chip tone={family(task.state)}>{stateLabel(task.state)}</Chip>
        <span className="tag">
          {cap(task.phase)}
          {task.director ? ' · director' : ''}
        </span>
        {task.cancel_requested && <Chip tone="blocked">Stop requested</Chip>}
      </div>
      <p className="reason">{task.reason}</p>
      <Facts
        rows={[
          ['Scope', task.scope.join(', ')],
          ['Repairs', <span className="num">{task.repairs}/{task.max_repairs}</span>],
          deps.length ? ['Waits on', deps.join(', ')] : null,
          live
            ? [
                'Worker',
                <span>
                  <LiveDot tone="live" /> {agent?.name || 'Worker'} · {live.model} · {live.effort || 'provider default'} · <span className="num">{duration(now - live.created)}</span>
                </span>,
              ]
            : null,
          task.cooldown_until > now ? ['Cooldown until', clock(task.cooldown_until)] : null,
          task.candidate ? ['Candidate', <Sha value={task.candidate} />] : null,
          task.integrated_commit ? ['Integrated', <Sha value={task.integrated_commit} />] : null,
        ]}
      />
      {task.question && (
        <div className="question">
          <Eyebrow>Your preference</Eyebrow>
          <p>{task.question}</p>
          <label htmlFor="director-answer">Your answer</label>
          <textarea id="director-answer" rows={3} value={answer} onChange={e => setAnswer(e.target.value)} />
          <button className="btn primary" disabled={disabled} onClick={() => action(`tasks/${task.id}/answer`, {revision: task.rev, text: answer}, 'Your answer is with the director.', true)}>
            Continue with my answer
          </button>
        </div>
      )}
      {task.specification && (
        <details className="spec">
          <summary>What your director decided</summary>
          <p>
            <b>{task.specification.outcome}</b>
          </p>
          <p>{task.specification.summary}</p>
          <ul>
            {(task.specification.acceptance || []).map((x: string) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
          <p className="small muted">{task.specification.strategy === 'parallel' ? 'Independent improvements can run together.' : 'Work is sequenced to keep the changes safe.'}</p>
        </details>
      )}
      {task.instructions_pending?.length > 0 && (
        <div className="note">
          <Eyebrow>Queued instructions</Eyebrow>
          <ul>
            {task.instructions_pending.map((x: any, i: number) => (
              <li key={i}>{typeof x === 'string' ? x : x.text || JSON.stringify(x)}</li>
            ))}
          </ul>
        </div>
      )}
      <h3 className="section-title">Attempts & evidence</h3>
      <ul className="attempts">
        {attempts.map(a => (
          <li key={a.id}>
            <div className="row between wrap">
              <div className="row wrap">
                <Chip tone={attemptTone(a.state)}>{cap(human(a.state))}</Chip>
                <span className="small">
                  {cap(a.phase)} · {a.model}
                  {a.effort ? ' · ' + a.effort : ''} · <span className="num">{a.result?.seconds != null ? seconds(a.result.seconds) : duration(now - a.created)}</span>
                </span>
              </div>
              <span className="small muted num">{when(a.created)}</span>
            </div>
            {a.result?.reason && <p className="small muted">{a.result.reason}</p>}
            {a.result?.checks?.length > 0 && (
              <div className="checks">
                {a.result.checks.map((c: RecordData) => (
                  <Chip key={c.name} tone={c.status === 'PASS' ? 'pass' : 'fail'}>
                    {c.name} · {c.status} · {seconds(c.seconds)}
                  </Chip>
                ))}
              </div>
            )}
            <div className="btn-row">
              <button className="btn sm" onClick={() => showEvidence(a.id, 'transcript.jsonl')}>
                Transcript
              </button>
              <button className="btn sm" onClick={() => showEvidence(a.id)}>
                Diff
              </button>
              <button className="btn sm" onClick={() => setEvidence({label: 'Checks', kind: 'text', text: JSON.stringify(a.result?.checks || [], null, 2)})}>
                Checks
              </button>
            </div>
          </li>
        ))}
        {!attempts.length && <li className="small muted">No attempts yet.</li>}
      </ul>
      <EvidenceView />
      <div className="field">
        <label htmlFor="steer">Anything to change?</label>
        <textarea id="steer" rows={3} value={steer} onChange={e => setSteer(e.target.value)} />
      </div>
      <div className="dialog-actions">
        <button className="btn danger" disabled={disabled || frozen} onClick={() => action(`tasks/${task.id}/cancel`, {revision: task.rev}, 'Cancellation requested; owned worker will stop', true)}>
          Stop task
        </button>
        <button className="btn" disabled={disabled || frozen} onClick={() => action(`tasks/${task.id}/instructions`, {revision: task.rev, text: steer}, 'Instruction saved for the next safe boundary', true)}>
          Queue instruction
        </button>
        {RETRYABLE.includes(task.state) && (
          <button className="btn" disabled={disabled} onClick={() => action(`tasks/${task.id}/retry`, {revision: task.rev}, 'Bounded retry queued', true)}>
            Retry
          </button>
        )}
      </div>
    </>
  );
}

function ReviewDialog({review, task}: {review: RecordData; task: RecordData}) {
  const {data, action, disabled, showApprovalDiff, showEvidence} = useArena();
  const [feedback, setFeedback] = useState('');
  const independent = task.review_attempt && data.attempts.find(a => a.id === task.review_attempt);
  const director = task.director_review_attempt && data.attempts.find(a => a.id === task.director_review_attempt);
  const pub = review.publication;
  const summary = director?.result?.review?.summary || independent?.result?.review?.summary || independent?.result?.reason || task.reason;
  const build = task.build_attempt && data.attempts.find(a => a.id === task.build_attempt);
  return (
    <>
      <div className="row wrap">
        <Chip tone={pub?.state === 'integrated' ? 'done' : pub?.state === 'failed' ? 'blocked' : 'decision'}>
          {pub ? cap(pub.state) : review.state === 'accept' ? 'Accepted' : review.state === 'pending' ? 'Exact staged candidate' : cap(human(review.state))}
        </Chip>
        {review.covered_tasks?.length > 1 && <span className="tag">Covers {review.covered_tasks.length} tasks</span>}
      </div>
      <p className="path">
        Candidate <Sha value={review.candidate} n={40} />
        <br />
        Base <Sha value={review.base} n={40} />
      </p>
      <p className="lede">{summary}</p>
      {build?.result?.summary && (
        <details className="spec">
          <summary>Worker summary</summary>
          <p>{build.result.summary}</p>
          {build.result.changed?.length > 0 && <p className="path">Changed: {build.result.changed.join(', ')}</p>}
        </details>
      )}

      <h3 className="section-title">Test evidence</h3>
      <table className="checks-table">
        <tbody>
          {review.checks.map((c: RecordData) => (
            <tr key={c.name}>
              <td>{c.name}</td>
              <td>
                <Chip tone={c.status === 'PASS' ? 'pass' : 'fail'}>{c.status}</Chip>
              </td>
              <td className="num">{seconds(c.seconds)}</td>
            </tr>
          ))}
          {!review.checks.length && (
            <tr>
              <td className="muted small">No combined checks recorded.</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">Independent review</h3>
      {independent?.result?.review ? (
        <div className="review-block">
          <div className="row wrap">
            <Chip tone={independent.result.review.approved ? 'pass' : 'fail'}>{independent.result.review.approved ? 'Approved' : 'Not approved'}</Chip>
            <span className="small muted">
              {independent.model} · {seconds(independent.result.seconds)}
            </span>
          </div>
          <ul className="findings">
            {(independent.result.review.findings || []).map((f: string, i: number) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="small muted">{task.review === false ? 'Independent review was explicitly omitted for this low-risk task.' : 'Independent review evidence is not recorded for this candidate.'}</p>
      )}
      {director?.result?.review && (
        <div className="review-block">
          <div className="row wrap">
            <Chip tone={director.result.review.approved ? 'pass' : 'fail'}>{director.result.review.approved ? 'Director approved' : 'Director rejected'}</Chip>
            <span className="small muted">{director.model}</span>
          </div>
          {director.result.review.findings?.length > 0 && (
            <ul className="findings">
              {director.result.review.findings.map((f: string, i: number) => (
                <li key={i}>{f}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <h3 className="section-title">Integration</h3>
      {pub ? (
        <Facts
          rows={[
            ['Status', `${cap(pub.state)} · ${pub.reason || ''}`],
            pub.receipt?.branch ? ['Branch', pub.receipt.branch] : null,
            pub.receipt?.candidate ? ['Commit', <Sha value={pub.receipt.candidate} n={40} />] : null,
            pub.receipt?.time ? ['Time', when(pub.receipt.time)] : null,
            pub.requested ? ['Requested', when(pub.requested)] : null,
          ]}
        />
      ) : (
        <p className="small muted">{review.state === 'accept' ? 'Accepted locally. Nothing is published until you authorise integration.' : 'Accept the exact staged commit first; integration into local main is a separate decision.'}</p>
      )}

      <div className="btn-row">
        <button className="btn" onClick={() => showApprovalDiff(review.id)}>
          <Icon name="code" size={14} />
          Read combined diff
        </button>
        {task.review_attempt && (
          <button className="btn" onClick={() => showEvidence(task.review_attempt, 'transcript.jsonl')}>
            <Icon name="doc" size={14} />
            Read independent review
          </button>
        )}
      </div>
      <EvidenceView />

      {review.state === 'pending' && (
        <div className="field">
          <label htmlFor="feedback">Requested change or rejection reason</label>
          <textarea id="feedback" rows={3} value={feedback} onChange={e => setFeedback(e.target.value)} />
        </div>
      )}
      <div className="dialog-actions">
        {review.state === 'pending' &&
          ['request_changes', 'reject', 'accept'].map(d => (
            <button
              className={'btn ' + (d === 'accept' ? 'primary' : d === 'reject' ? 'danger' : 'ghost')}
              key={d}
              disabled={disabled}
              onClick={() =>
                action(
                  `approvals/${review.id}/decision`,
                  {revision: review.rev, decision: d, candidate: review.candidate, base: review.base, text: feedback},
                  d === 'accept' ? 'Exact staging commit accepted locally. Nothing published.' : 'Decision recorded; project paused. Evidence preserved. Queue a scoped correction and resume when ready.',
                  true,
                )
              }
            >
              {d === 'accept' ? 'Accept staged change' : d === 'reject' ? 'Reject' : 'Request changes'}
            </button>
          ))}
        {review.state === 'accept' && (
          <button
            className="btn primary"
            disabled={disabled || ['queued', 'integrated'].includes(pub?.state)}
            onClick={() => action(`approvals/${review.id}/integrate`, {revision: review.rev, candidate: review.candidate, base: review.base}, 'Exact commit authorised for integration into local main', true)}
          >
            {pub?.state === 'integrated' ? 'Integrated into main' : pub?.state === 'queued' ? 'Integration queued' : 'Integrate into main'}
          </button>
        )}
      </div>
    </>
  );
}

export function DialogBody() {
  const {data, now, modal, setModal, action, disabled, unpair, link, lastSeen, post} = useArena();
  const task = modal?.kind === 'task' ? data.tasks.find(t => t.id === modal.id) : null;
  const review = modal?.kind === 'review' ? data.approvals.find(a => a.id === modal.id) : null;
  const reviewedTask = review ? data.tasks.find(t => t.id === review.task_id) : null;
  const title = modal?.kind === 'goal' ? 'What would you like to improve?' : modal?.kind === 'device' ? 'Device session' : modal?.kind === 'account' ? 'Worker slot setup' : task?.title || reviewedTask?.title || 'Build control';
  const heartbeatAge = data.heartbeat ? now - data.heartbeat : null;
  return (
    <>
      <div className="dialog-head">
        <h2>{title}</h2>
        <button className="close" aria-label="Close dialog" onClick={() => setModal(null)}>
          <Icon name="x" size={18} />
        </button>
      </div>
      <div className="dialog-scroll">
        {modal?.kind === 'goal' && <NewRequest />}
        {task && <TaskDialog task={task} />}
        {review && reviewedTask && <ReviewDialog review={review} task={reviewedTask} />}
        {modal?.kind === 'account' && (
          <>
            <p>
              Enroll a named subscription account on the Mac using <code>enroll-account</code>. Codex accepts a separate, already authenticated <code>CODEX_HOME</code>.
            </p>
            <p className="note">
              A second Claude account needs its own config directory signed in on the Mac (CLAUDE_CONFIG_DIR=~/.claude-second claude auth login --email …), then enroll-account --config-home for that directory; enrolment refuses a directory that holds the same account as an existing slot. Never paste account tokens here.
            </p>
          </>
        )}
        {modal?.kind === 'device' && (
          <>
            <Facts
              rows={[
                ['Link', link === 'live' ? `Live · last snapshot ${lastSeen}` : link === 'polling' ? `Polling every 5s · ${lastSeen}` : `Reconnecting · last seen ${lastSeen}`],
                ['Scheduler', heartbeatAge === null ? 'Not running' : heartbeatAge > 30 ? `Stale · last heartbeat ${when(data.heartbeat)}` : `Healthy · heartbeat ${Math.round(heartbeatAge)}s ago`],
                ['Service', data.version],
                ['Server time', when(data.server_time)],
              ]}
            />
            <Devices action={action} disabled={disabled} post={post} />
            <p className="small muted">A sleeping Mac cannot execute tasks. Stopping all workers pauses dispatch and asks every owned attempt to stop.</p>
            <div className="dialog-actions">
              <button className="btn danger" disabled={disabled} onClick={() => action('control/stop-all', {revision: data.revision}, 'Dispatch paused and all owned attempts asked to stop', true)}>
                Stop all workers
              </button>
              <button className="btn" onClick={unpair}>
                Unpair this device
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
