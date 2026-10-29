import React, {useEffect, useState} from 'react';
import {ago, when, type RecordData} from '../types';
import {Chip, Eyebrow, Facts} from '../ui';

// Paired devices (30-day sliding sessions). Revoking one ends its session immediately.
export function Devices({action, disabled, post}: {action: (path: string, body: RecordData, success: string) => Promise<void>; disabled: boolean; post: (path: string, body: RecordData) => Promise<any>}) {
  const [devices, setDevices] = useState<RecordData[] | null>(null);
  const [error, setError] = useState('');
  const [invite, setInvite] = useState<RecordData | null>(null);
  const load = async () => {
    try {
      const r = await fetch('/api/v1/devices', {cache: 'no-store'});
      if (!r.ok) throw Error(`Host answered ${r.status}`);
      setDevices(await r.json());
      setError('');
    } catch (e) {
      setError(String(e));
    }
  };
  useEffect(() => {
    load();
  }, []);
  return (
    <section className="sub">
      <div className="row between">
        <Eyebrow>Paired devices</Eyebrow>
        <span className="count num">{devices?.length ?? '…'}</span>
      </div>
      {error && <p className="small muted">{error}</p>}
      {devices?.map(d => (
        <div className="list-row" key={d.id}>
          <Chip tone={d.current ? 'running' : 'off'}>{d.current ? 'This device' : 'Paired'}</Chip>
          <div className="list-main">
            <b className="clamp1">{d.label}</b>
            <span className="small muted num">
              paired {d.created ? when(d.created) : 'before labels were recorded'} · last seen {d.last_seen ? when(d.last_seen) : 'not yet recorded'} · renews on use, expires {when(d.expires)}
            </span>
          </div>
          {!d.current && (
            <button
              className="btn ghost"
              disabled={disabled}
              onClick={async () => {
                await action(`devices/${d.id}/revoke`, {revision: 0}, 'Device revoked');
                load();
              }}
            >
              Revoke
            </button>
          )}
        </div>
      ))}
      <div className="dialog-actions">
        <button
          className="btn"
          disabled={disabled}
          onClick={async () => {
            try {
              setInvite(await post('devices/invite', {revision: 0}));
            } catch (e) {
              setError(String(e));
            }
          }}
        >
          Add another device
        </button>
      </div>
      {invite && (
        <div className="invite">
          <Eyebrow>Enter this on the new device within 30 minutes</Eyebrow>
          <p className="path">{invite.origin}</p>
          <p className="invite-code num">{invite.code}</p>
          <p className="small muted">One use. Issuing another code does not cancel this one. Expires {when(invite.expires)}.</p>
        </div>
      )}
      <p className="small muted">Each trusted device pairs once. Sessions last 30 days and renew whenever the device is used; unpair here or revoke from any other paired device.</p>
    </section>
  );
}

// Attempt workspaces on the Mac (GET /api/v1/system; the host walks them at most every five
// minutes). Removing them stays a Mac command by design: nothing on the phone deletes. The
// hint is the dry run, from the Arena folder (where .venv and .arena-control live).
type WorkspaceDisk = {count: number; bytes: number; prunable_count: number; prunable_bytes: number; oldest: number | null; days: number; measured: number};
const PRUNE_COMMAND = '.venv/bin/python -m agent_arena.control --home .arena-control prune-workspaces';
const size = (n: number) => (n >= 1073741824 ? `${(n / 1073741824).toFixed(1)} GB` : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);
const counted = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
// Non-breaking inside each phrase, so a narrow screen wraps only between phrases.
const keep = (text: string) => text.replaceAll(' ', '\u00a0');

function workspaceLine(w: WorkspaceDisk) {
  if (!w.count) return 'None';
  return `${keep(counted(w.count, 'workspace'))} · ${keep(size(w.bytes))} · ${keep(`${w.prunable_count} prunable`)} ${keep(`(older than ${counted(w.days, 'day')})`)}`;
}

// A shell command that wraps only between its words; the spaces and hyphens stay plain, so a
// copy pastes exactly the command.
function Command({text}: {text: string}) {
  return (
    <code>
      {text.split(' ').map((word, i) => (
        <React.Fragment key={i}>
          {i > 0 && ' '}
          <span className="nowrap">{word}</span>
        </React.Fragment>
      ))}
    </code>
  );
}

// The Device session facts with the workspaces line, measured when the dialog opens.
export function DeviceFacts({rows, now}: {rows: [string, React.ReactNode][]; now: number}) {
  const [disk, setDisk] = useState<WorkspaceDisk | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let open = true;
    fetch('/api/v1/system', {cache: 'no-store'})
      .then(r => {
        if (!r.ok) throw Error(`Host answered ${r.status}`);
        return r.json();
      })
      .then(j => open && setDisk(j.workspaces))
      .catch(() => open && setFailed(true));
    return () => {
      open = false;
    };
  }, []);
  return (
    <>
      <Facts rows={[...rows, ['Workspaces on the Mac', disk ? workspaceLine(disk) : failed ? 'Not available' : 'Measuring…']]} />
      {disk && disk.count > 0 && (
        <p className="small muted">
          Measured {ago(disk.measured, now)}; {disk.prunable_count ? `${size(disk.prunable_bytes)} can be freed.` : 'nothing to free yet.'} In the Arena folder on the Mac: <Command text={PRUNE_COMMAND} /> (dry run; add <Command text="--apply" /> to remove)
        </p>
      )}
    </>
  );
}
