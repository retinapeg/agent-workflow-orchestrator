// Live agent output: GET /api/v1/attempts/{id}/live polled while an attempt runs. The
// host builds it from the tail of the worker's transcript, so every line is something the
// agent actually did. An older host answers 404; that attempt is then never asked again
// and the screen falls back to the recorded events.
import {useEffect, useRef, useState} from 'react';
import {useArena, type PostError} from './App';
import type {LiveKind, LiveTail} from './types';

const unsupported = new Set<string>();

// `query` asks the host for more than the polled default (the Transcript view: up to 400
// lines from the last megabyte). Polling stops once the host reports the attempt done, or
// at once when `every` is 0.
export function useLiveTail(attemptId: string | null | undefined, every = 2500, query = ''): LiveTail | null {
  const {get} = useArena();
  const [tail, setTail] = useState<LiveTail | null>(null);
  const getRef = useRef(get);
  getRef.current = get;
  useEffect(() => {
    setTail(null);
    if (!attemptId || unsupported.has(attemptId)) return;
    let stopped = false;
    let timer = 0;
    const poll = async () => {
      let done = false;
      try {
        const r = (await getRef.current(`attempts/${attemptId}/live${query}`)) as LiveTail;
        if (stopped) return;
        if (r && Array.isArray(r.lines)) {
          setTail(r);
          done = !!r.done;
        }
      } catch (e) {
        if (stopped) return;
        const status = (e as PostError)?.status;
        if (status === 404 || status === 405) {
          unsupported.add(attemptId);
          return;
        }
      }
      if (every > 0 && !done) timer = window.setTimeout(poll, every);
    };
    poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [attemptId, every, query]);
  return tail;
}

// The Transcript view's request: the whole recorded transcript (bounded by the host).
export const TRANSCRIPT_QUERY = '?lines=400&window=1048576';

// `[hh:mm:ss] Codex · editing src/pomodoro/cli.py`
export const stamp = (epoch: number | null | undefined) => (epoch ? new Date(epoch * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false}) : '--:--:--');

const VERB: Record<LiveKind, string> = {
  assistant: '',
  tool: 'using',
  command: 'running',
  file: 'editing',
  test: 'testing',
  error: 'error',
  system: '',
};

export function liveLineText(line: {kind: LiveKind; text: string}, agent: string): string {
  const verb = VERB[line.kind] || '';
  const text = (line.text || '').replace(/\s+/g, ' ').trim();
  return `${agent} · ${verb ? verb + ' ' : ''}${text}`;
}

export const bytes = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);
