// One JSON resource kept fresh for a screen: fetched when the screen opens, again when
// `signal` changes (the caller scopes it to its own records), and every `every` ms while
// `live`. Nothing is fetched while the page is hidden: a fetch that falls due then is made
// once, at once, when the page is shown again. A signal never reloads within SETTLE ms of the
// last fetch; it waits for that and then reloads once, so a burst of changes costs one
// request. One request at a time: a slow answer is never thrown away for a newer poll, and a
// change that arrives meanwhile is read right after it. The last answer stays on screen while
// the next loads, so nothing flickers; when a refresh fails, `error` is set beside the old
// `data`, and `at` says when that data arrived.
import {useCallback, useEffect, useRef, useState} from 'react';
import {useArena, type PostError} from './App';

export type Polled<T> = {
  data: T | null;
  error: PostError | null;
  loading: boolean; // nothing has answered yet
  busy: boolean; // a request is in flight
  at: number | null; // when `data` arrived (ms, this device's clock)
  reload: () => Promise<void>;
};

const SETTLE = 3000;
const hidden = () => typeof document !== 'undefined' && document.visibilityState === 'hidden';

type State<T> = {path: string | null; data: T | null; error: PostError | null; loading: boolean; busy: boolean; at: number | null};

export function usePolled<T>(path: string | null, live: boolean, signal: unknown, every = 3000): Polled<T> {
  const {get} = useArena();
  const getRef = useRef(get);
  getRef.current = get;
  const [state, setState] = useState<State<T>>({path, data: null, error: null, loading: !!path, busy: false, at: null});
  const seq = useRef(0);
  const last = useRef(0); // when the newest fetch started
  const owed = useRef(false); // a fetch fell due while the page was hidden
  const trailing = useRef(0); // a signal reload waiting for SETTLE
  const inflight = useRef(false);
  const again = useRef(false); // asked for while a request was in flight

  const load = useCallback(async (): Promise<void> => {
    if (!path) return;
    window.clearTimeout(trailing.current);
    trailing.current = 0;
    if (hidden()) {
      owed.current = true;
      return;
    }
    if (inflight.current) {
      again.current = true;
      return;
    }
    inflight.current = true;
    owed.current = false;
    last.current = Date.now();
    const n = ++seq.current;
    setState(s => (s.path === path ? {...s, busy: true} : s));
    try {
      const data = (await getRef.current(path)) as T;
      if (n !== seq.current) return; // the screen moved on to another resource
      setState({path, data, error: null, loading: false, busy: false, at: Date.now()});
    } catch (e) {
      if (n !== seq.current) return;
      setState(s => (s.path === path ? {...s, error: e as PostError, loading: false, busy: false} : {path, data: null, error: e as PostError, loading: false, busy: false, at: null}));
    } finally {
      if (n === seq.current) {
        inflight.current = false;
        if (again.current) {
          again.current = false;
          load();
        }
      }
    }
  }, [path]);

  useEffect(() => {
    setState(s => (s.path === path ? s : {path, data: null, error: null, loading: !!path, busy: false, at: null}));
    // A request for another resource still in flight no longer counts.
    seq.current += 1;
    inflight.current = false;
    again.current = false;
    load();
    return () => {
      window.clearTimeout(trailing.current);
      trailing.current = 0;
    };
  }, [path]);

  // A change in the caller's records: reload, but not within SETTLE of the last fetch.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const wait = SETTLE - (Date.now() - last.current);
    if (wait <= 0) load();
    else if (!trailing.current) trailing.current = window.setTimeout(() => load(), wait);
  }, [signal]);

  useEffect(() => {
    if (!path) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible' && (owed.current || live)) load();
    };
    document.addEventListener('visibilitychange', onVisible);
    // A poll that a signal reload just made is not repeated.
    const timer = live && every > 0 ? window.setInterval(() => !inflight.current && Date.now() - last.current >= every - 250 && load(), every) : 0;
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.clearInterval(timer);
    };
  }, [path, live, every, load]);

  const mine = state.path === path;
  return {
    data: mine ? state.data : null,
    error: mine ? state.error : null,
    loading: mine ? state.loading : !!path,
    busy: mine ? state.busy : false,
    at: mine ? state.at : null,
    reload: load,
  };
}
