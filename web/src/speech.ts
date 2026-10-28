// Browser dictation through the Web Speech API when the platform provides it.
// Typing always works; this only appends final transcripts to the draft.
import {useEffect, useRef, useState} from 'react';

type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: any) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: any) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};

function factory(): (new () => Recognition) | null {
  const w = window as any;
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

const ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone permission was denied. Your keyboard microphone still works.',
  'service-not-allowed': 'Dictation is not allowed on this device. Your keyboard microphone still works.',
  'audio-capture': 'No microphone was found.',
  network: 'Dictation needs a network connection on this platform.',
  'no-speech': 'No speech was detected.',
};

export function useSpeech(onFinal: (text: string) => void) {
  const supported = typeof window !== 'undefined' && factory() !== null;
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState('');
  const [error, setError] = useState('');
  const rec = useRef<Recognition | null>(null);
  const callback = useRef(onFinal);
  callback.current = onFinal;

  useEffect(() => () => rec.current?.abort(), []);

  const start = () => {
    const Ctor = factory();
    if (!Ctor) return;
    setError('');
    const r = new Ctor();
    r.lang = navigator.language || 'en-US';
    r.continuous = false;
    r.interimResults = true;
    r.onresult = (event: any) => {
      let finalText = '';
      let interimText = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        const transcript = result[0]?.transcript || '';
        if (result.isFinal) finalText += transcript;
        else interimText += transcript;
      }
      if (finalText.trim()) {
        callback.current(finalText.trim());
        setInterim('');
      } else setInterim(interimText);
    };
    r.onerror = (event: any) => {
      if (event?.error !== 'aborted') setError(ERRORS[event?.error] || `Dictation stopped (${event?.error || 'error'}).`);
    };
    r.onend = () => {
      setListening(false);
      setInterim('');
      rec.current = null;
    };
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch {
      setError('Dictation could not start. Your keyboard microphone still works.');
      rec.current = null;
    }
  };
  const stop = () => rec.current?.stop();
  return {supported, listening, interim, error, start, stop, toggle: () => (listening ? stop() : start())};
}
