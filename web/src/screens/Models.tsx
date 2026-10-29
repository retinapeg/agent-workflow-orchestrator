import React, {useEffect, useRef, useState} from 'react';
import {useArena, type PostError} from '../App';
import {plainError, type Snapshot} from '../types';

// Which model each agent uses for new work: Auto (Arena decides per task, the default) or one
// model and effort the owner pins. The host reports the pins in the snapshot
// (`routing_override`) and lists what can be pinned at GET routing/options; a host without
// them is older than this control and says so instead of offering a choice.
export type Provider = 'codex' | 'claude';
export type Pin = {model: string | null; effort?: string | null} | null;
export type RoutingOverride = Partial<Record<Provider, Pin>>;
type ModelOption = {slug: string; efforts?: string[]};
type RoutingOptions = Partial<Record<Provider, {models?: ModelOption[]}>> & {override?: RoutingOverride};

export const PROVIDERS: [Provider, string][] = [
  ['codex', 'Codex'],
  ['claude', 'Claude'],
];
export const OLDER_MAC = 'Your Mac needs the latest Arena to change this.';

// "gpt-6-astra" → "Astra", "claude-fable-5-1" → "Fable", "sonnet" → "Sonnet": the model's own
// word, not the vendor's or the version.
export function modelName(slug: string): string {
  const words = String(slug || '')
    .split(/[-_\s]+/)
    .filter(w => /^[a-z]+$/i.test(w) && !/^(gpt|claude|codex|openai|anthropic)$/i.test(w));
  const word = words[words.length - 1];
  return word ? word[0].toUpperCase() + word.slice(1) : String(slug || '');
}

const EFFORT: Record<string, string> = {minimal: 'Minimal', low: 'Low', medium: 'Medium', high: 'High', xhigh: 'Extra high', max: 'Max'};
const effortName = (e: string) => EFFORT[e] || (e ? e[0].toUpperCase() + e.slice(1) : e);

export const routingOverride = (data: Snapshot): RoutingOverride => (data as Snapshot & {routing_override?: RoutingOverride | null}).routing_override || {};

// The pinned agents, in menu order.
export function pins(o: RoutingOverride): {provider: Provider; name: string; model: string; effort: string}[] {
  return PROVIDERS.filter(([p]) => o[p]?.model).map(([p, name]) => ({provider: p, name, model: o[p]!.model as string, effort: o[p]!.effort || ''}));
}

// "Auto", or what is pinned: "Codex → Astra · low" (each pinned agent, comma separated).
export function modelsSummary(o: RoutingOverride): string {
  const list = pins(o);
  if (!list.length) return 'Auto';
  return list.map(x => `${x.name} → ${modelName(x.model)}${x.effort ? ` · ${x.effort}` : ''}`).join(', ');
}

// A host older than this control: the endpoint is not there, or the control is unknown to it.
const older = (e: unknown) => {
  const err = e as PostError;
  return err?.status === 404 || err?.status === 405 || (err?.status === 400 && /unsupported control/i.test(String(err.message)));
};

type Choice = {auto: boolean; model: string; effort: string};
// The effort a model starts on: the one already chosen when it has it, else high, else its first.
function effortFor(models: ModelOption[], slug: string, current: string): string {
  const efforts = models.find(m => m.slug === slug)?.efforts || [];
  if (efforts.includes(current)) return current;
  return efforts.includes('high') ? 'high' : efforts[0] || '';
}
const same = (a: Choice, b: Choice) => a.auto === b.auto && (a.auto || (a.model === b.model && a.effort === b.effort));

// The Models sheet (opened from the ☰ menu): per agent, Auto or a pinned model and effort.
export function ModelsSheet() {
  const {data, get, post, notify, setModal, disabled} = useArena();
  const [options, setOptions] = useState<RoutingOptions | null>(null);
  const [phase, setPhase] = useState<'loading' | 'ready' | 'older' | 'failed'>('loading');
  const [choice, setChoice] = useState<Partial<Record<Provider, Choice>>>({});
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const initial = useRef<Partial<Record<Provider, Choice>>>({});
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let live = true;
    setPhase('loading');
    (async () => {
      try {
        const o = (await get('routing/options')) as RoutingOptions;
        if (!live) return;
        if (!o || (!o.codex && !o.claude)) {
          setPhase('older');
          return;
        }
        const pinned = o.override || routingOverride(data);
        const start: Partial<Record<Provider, Choice>> = {};
        for (const [p] of PROVIDERS) {
          const models = o[p]?.models || [];
          const pin = pinned[p];
          // A pinned model the host no longer lists is still shown, so the pin is never hidden.
          if (pin?.model && !models.some(m => m.slug === pin.model)) models.push({slug: pin.model, efforts: pin.effort ? [pin.effort] : []});
          o[p] = {...(o[p] || {}), models};
          const model = pin?.model || models[0]?.slug || '';
          start[p] = {auto: !pin?.model, model, effort: effortFor(models, model, pin?.effort || '')};
        }
        initial.current = start;
        setChoice(start);
        setOptions(o);
        setPhase('ready');
      } catch (e) {
        if (!live) return;
        if (older(e)) setPhase('older');
        else {
          setError(plainError(e));
          setPhase('failed');
        }
      }
    })();
    return () => {
      live = false;
    };
  }, [attempt]);

  const set = (p: Provider, change: Partial<Choice>) => setChoice(c => ({...c, [p]: {...(c[p] as Choice), ...change}}));
  const changes = PROVIDERS.filter(([p]) => choice[p] && initial.current[p] && !same(choice[p] as Choice, initial.current[p] as Choice));

  // One change per agent (POST control/routing, model null for Auto). Each change moves the
  // host's revision on, so a second one is sent with the revision read after the first.
  const save = async () => {
    if (saving) return;
    if (!changes.length) {
      setModal(null);
      return;
    }
    setSaving(true);
    setError('');
    try {
      let revision = data.revision;
      for (const [i, [p]] of changes.entries()) {
        if (i > 0) revision = ((await get('overview')) as Snapshot).revision;
        const c = choice[p] as Choice;
        await post('control/routing', {provider: p, model: c.auto ? null : c.model, effort: c.auto ? null : c.effort || null, revision});
      }
      const next: RoutingOverride = {};
      for (const [p] of PROVIDERS) {
        const c = choice[p];
        next[p] = c && !c.auto ? {model: c.model, effort: c.effort || null} : null;
      }
      notify(`Models: ${modelsSummary(next)}.`);
      setModal(null);
    } catch (e) {
      setError(older(e) ? OLDER_MAC : plainError(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="models-sheet">
      <p className="small muted">Arena picks the model for each task. Pin one to use it for all new work by that agent, until you switch back to Auto.</p>
      {phase === 'loading' && <p className="small muted">Reading the models on your Mac…</p>}
      {phase === 'older' && (
        <p className="note" role="status">
          {OLDER_MAC}
        </p>
      )}
      {phase === 'failed' && (
        <div className="note" role="status">
          <p>{error}</p>
          <button type="button" className="btn" onClick={() => setAttempt(n => n + 1)}>
            Try again
          </button>
        </div>
      )}
      {phase === 'ready' &&
        options &&
        PROVIDERS.map(([p, name]) => {
          const models = options[p]?.models || [];
          const c = choice[p];
          if (!c) return null;
          const efforts = models.find(m => m.slug === c.model)?.efforts || [];
          return (
            <section key={p} className="model-choice" aria-labelledby={`models-${p}`}>
              <h3 id={`models-${p}`} className="section-title">
                {name}
              </h3>
              {models.length ? (
                <>
                  <label className="check-row">
                    <input type="checkbox" aria-label={`Use Auto for ${name}`} checked={c.auto} onChange={e => set(p, {auto: e.target.checked})} />
                    <span aria-hidden="true">Auto (Arena decides per task)</span>
                  </label>
                  {!c.auto && (
                    <div className="model-pickers">
                      <select aria-label={`${name} model`} value={c.model} onChange={e => set(p, {model: e.target.value, effort: effortFor(models, e.target.value, c.effort)})}>
                        {models.map(m => (
                          <option key={m.slug} value={m.slug}>
                            {modelName(m.slug)} ({m.slug})
                          </option>
                        ))}
                      </select>
                      {efforts.length > 0 && (
                        <select aria-label={`${name} effort`} value={c.effort} onChange={e => set(p, {effort: e.target.value})}>
                          {efforts.map(x => (
                            <option key={x} value={x}>
                              {effortName(x)} effort
                            </option>
                          ))}
                        </select>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <p className="small muted">Your Mac lists no {name} models to pin, so Arena decides per task.</p>
              )}
            </section>
          );
        })}
      {phase === 'ready' && error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      <div className="dialog-actions">
        <button type="button" className="btn" onClick={() => setModal(null)}>
          {phase === 'ready' ? 'Cancel' : 'Close'}
        </button>
        {phase === 'ready' && (
          <button type="button" className="btn primary" disabled={disabled || saving || !changes.length} onClick={save}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        )}
      </div>
    </div>
  );
}
