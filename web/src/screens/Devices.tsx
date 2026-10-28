import React, {useEffect, useState} from 'react';
import {when, type RecordData} from '../types';
import {Chip, Eyebrow} from '../ui';

// Paired devices (30-day sliding sessions). Revoking one ends its session immediately.
export function Devices({action, disabled}: {action: (path: string, body: RecordData, success: string) => Promise<void>; disabled: boolean}) {
  const [devices, setDevices] = useState<RecordData[] | null>(null);
  const [error, setError] = useState('');
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
              paired {when(d.created)} · last seen {when(d.last_seen)} · renews on use, expires {when(d.expires)}
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
      <p className="small muted">Each trusted device pairs once. Sessions last 30 days and renew whenever the device is used; unpair here or revoke from any other paired device.</p>
    </section>
  );
}
