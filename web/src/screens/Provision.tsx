import React from 'react';
import {useArena} from '../App';
import {ago, cap, type RecordData} from '../types';
import {Chip, Eyebrow, Glyph, Icon} from '../ui';

// New project from the phone. A proposal is only names (nothing is created until the owner
// approves it); a provision is the host's recorded run of the approved steps. Every step
// shown here is read from `provisions[].steps` in the overview snapshot, never predicted.

export type Proposal = {
  id: string;
  name: string;
  slug: string;
  description: string;
  local_path: string;
  github: {owner: string; full_name: string; url: string; private: boolean};
  notes: string[];
  created: number;
  expires: number;
};

// The host runs these in order; names not yet recorded are listed as not started.
const STEP_ORDER = ['local repository', 'github repository', 'verify connection', 'enrol in arena', 'first request'];
const STEP_LABEL: Record<string, string> = {
  'local repository': 'Local repository',
  'github repository': 'Private GitHub repository',
  'verify connection': 'Verify the connection',
  'enrol in arena': 'Enrol in Arena',
  'first request': 'First request to the director',
};
const stepLabel = (name: string) => STEP_LABEL[name] || cap(name);

export function ProposalCard({proposal, busy, onApprove, onChange}: {proposal: Proposal; busy: boolean; onApprove: () => void; onChange: () => void}) {
  return (
    <section className="card proposal" aria-labelledby={'proposal-' + proposal.id}>
      <div className="row between wrap">
        <Eyebrow>Proposed project</Eyebrow>
        <Chip tone="neutral">Nothing created yet</Chip>
      </div>
      <h3 id={'proposal-' + proposal.id}>{proposal.name}</h3>
      <dl className="facts">
        <div>
          <dt>Local</dt>
          <dd>
            <code className="mono path-line">{proposal.local_path}</code>
          </dd>
        </div>
        <div>
          <dt>GitHub</dt>
          <dd>
            <code className="mono path-line">{proposal.github.full_name}</code> ({proposal.github.private ? 'private' : 'public'})
          </dd>
        </div>
      </dl>
      {proposal.notes.length > 0 && (
        <ul className="notes small muted">
          {proposal.notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
      <p className="small muted">Approving creates this folder on the Mac and a private repository on GitHub, enrols the project and sends your description to the director.</p>
      <div className="btn-row">
        <button type="button" className="btn primary" disabled={busy} onClick={onApprove}>
          <Icon name="check" size={16} />
          Approve &amp; create
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onChange}>
          Change
        </button>
      </div>
    </section>
  );
}

function CopyPath({value}: {value: string}) {
  const {notify} = useArena();
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      notify('Local path copied');
    } catch {
      notify('Copy is not available here; the path is shown above');
    }
  };
  return (
    <div className="copy-line">
      <code className="mono path-line">{value}</code>
      <button type="button" className="btn sm" onClick={copy} aria-label={`Copy local path ${value}`}>
        Copy
      </button>
    </div>
  );
}

export function ProvisionCard({provision, onRetry, compact = false}: {provision: RecordData; onRetry?: (p: RecordData) => void; compact?: boolean}) {
  const {now, openGoal, disabled} = useArena();
  const steps: RecordData[] = Array.isArray(provision.steps) ? provision.steps : [];
  const recorded = new Set(steps.map(s => s.name));
  const settled = provision.state === 'done' || provision.state === 'failed';
  const pending = settled ? [] : STEP_ORDER.filter(n => !recorded.has(n));
  const tone = provision.state === 'done' ? 'done' : provision.state === 'failed' ? 'blocked' : provision.state === 'queued' ? 'waiting' : 'running';
  const label = provision.state === 'done' ? 'Created' : provision.state === 'failed' ? 'Failed' : provision.state === 'queued' ? 'Queued' : 'In progress';
  const github = provision.github || {};
  return (
    <section className={'card provision ' + tone} aria-labelledby={'provision-' + provision.id}>
      <div className="row between wrap">
        <Eyebrow>{compact ? 'New project' : 'Creating your project'}</Eyebrow>
        <Chip tone={tone}>{label}</Chip>
      </div>
      <h3 id={'provision-' + provision.id}>{provision.name}</h3>
      {!compact && provision.description && <p className="reason clamp2">{provision.description}</p>}
      <ol className="steps" aria-label="Provisioning steps">
        {steps.map((s, i) => {
          const t = s.state === 'done' ? 'done' : s.state === 'failed' ? 'blocked' : 'running';
          return (
            <li key={s.name + i} className={t}>
              <Glyph tone={t} size={26} />
              <span className="list-main">
                <b>{stepLabel(s.name)}</b>
                <span className="small muted">
                  {s.state === 'running' ? `running · started ${ago(s.started, now)}` : s.state === 'failed' ? s.detail || 'failed' : s.detail ? s.detail : 'done'}
                </span>
              </span>
            </li>
          );
        })}
        {pending.map(n => (
          <li key={n} className="off">
            <Glyph tone="off" size={26} />
            <span className="list-main">
              <b className="muted">{stepLabel(n)}</b>
              <span className="small muted">{provision.state === 'queued' && n === STEP_ORDER[0] ? 'queued on the Mac' : 'not started'}</span>
            </span>
          </li>
        ))}
      </ol>
      {provision.state === 'failed' && (
        <div className="failure">
          <Eyebrow>What happened</Eyebrow>
          <span className="small">{provision.error || 'The host recorded a failure without a reason.'}</span>
        </div>
      )}
      {provision.state === 'done' && (
        <>
          <dl className="facts">
            <div>
              <dt>GitHub</dt>
              <dd>
                {github.url ? (
                  <a href={github.url} target="_blank" rel="noopener noreferrer" className="link">
                    {github.full_name || github.url}
                  </a>
                ) : (
                  'Not recorded'
                )}
                {github.private ? ' (private)' : ''}
              </dd>
            </div>
          </dl>
          <CopyPath value={provision.local_path} />
        </>
      )}
      <div className="btn-row">
        {provision.state === 'done' && github.url && (
          <a className="btn" href={github.url} target="_blank" rel="noopener noreferrer">
            <Icon name="git" size={16} />
            Open on GitHub
          </a>
        )}
        {provision.state === 'done' && provision.goal_id && (
          <button type="button" className="btn primary" onClick={() => openGoal(provision.goal_id)}>
            <Icon name="build" size={16} />
            Open build
          </button>
        )}
        {provision.state === 'failed' && onRetry && (
          <button type="button" className="btn" disabled={disabled} onClick={() => onRetry(provision)}>
            Try again with a different name
          </button>
        )}
      </div>
    </section>
  );
}
