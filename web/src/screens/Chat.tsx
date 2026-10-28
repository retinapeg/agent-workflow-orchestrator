import React, {useEffect, useRef, useState} from 'react';
import {useArena} from '../App';
import {useSpeech} from '../speech';
import {ago, buildPhrase, clock, planTasks, seconds, type ChatMessage, type Conversation, type ConversationDetail, type PlanMilestone, type Spec} from '../types';
import {Chip, Eyebrow, Icon, LiveDot, Mark} from '../ui';

// Talk to the director like a colleague. Sending never starts a build: a build begins only
// when the host reports one (the owner's own explicit words and the director's agreement).
// The current thread id is remembered on this device so reopening lands in the same thread.
const KEY = 'arena.conversation';
const remembered = (): string | null => {
  try {
    return localStorage.getItem(KEY) || null;
  } catch {
    return null;
  }
};
const remember = (id: string | null) => {
  try {
    if (id) localStorage.setItem(KEY, id);
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: the thread is still reachable from Recent chats */
  }
};

const PROMPTS = ['What is running right now?', 'Which projects can you build in?', 'Help me plan a feature'];
const MAX_LINES = 6;

// Markdown-lite: paragraphs, "-"/"*" bullets, "1." lists, `code` spans, **bold** and fenced
// blocks. Everything is emitted as React text nodes, so nothing the model writes is injected.
function inline(text: string, key: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  const re = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('`')) out.push(<code key={`${key}-${i++}`}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith('**')) out.push(<b key={`${key}-${i++}`}>{tok.slice(2, -2)}</b>);
    else out.push(<i key={`${key}-${i++}`}>{tok.slice(1, -1)}</i>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function lines(block: string, key: string): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  block.split('\n').forEach((line, i) => {
    if (i) out.push(<br key={`${key}-br${i}`} />);
    out.push(...inline(line, `${key}-l${i}`));
  });
  return out;
}

export function Markdown({text}: {text: string}) {
  const nodes: React.ReactNode[] = [];
  const parts = text.replace(/\r\n/g, '\n').split(/```[^\n]*\n?/);
  parts.forEach((part, p) => {
    if (p % 2 === 1) {
      nodes.push(
        <pre key={`pre${p}`}>
          <code>{part.replace(/\n$/, '')}</code>
        </pre>,
      );
      return;
    }
    part
      .split(/\n\s*\n/)
      .map(b => b.trim())
      .filter(Boolean)
      .forEach((block, b) => {
        // Consecutive lines of one kind form a run: bullets → <ul>, numbers → <ol>, else <p>.
        const kind = (r: string) => (/^\s*[-*•]\s+/.test(r) ? 'ul' : /^\s*\d+[.)]\s+/.test(r) ? 'ol' : 'p');
        const runs: {kind: string; rows: string[]}[] = [];
        for (const r of block.split('\n')) {
          const k = kind(r);
          const last = runs[runs.length - 1];
          if (last && last.kind === k) last.rows.push(r);
          else runs.push({kind: k, rows: [r]});
        }
        runs.forEach((run, n) => {
          const key = `p${p}-${b}-${n}`;
          if (run.kind === 'p') nodes.push(<p key={key}>{lines(run.rows.join('\n'), key)}</p>);
          else {
            const items = run.rows.map((r, i) => <li key={i}>{inline(r.replace(/^\s*([-*•]|\d+[.)])\s+/, ''), `${key}-${i}`)}</li>);
            nodes.push(run.kind === 'ul' ? <ul key={key}>{items}</ul> : <ol key={key}>{items}</ol>);
          }
        });
      });
  });
  return <>{nodes}</>;
}

// Director's plan outline: milestone code + title, then task titles with acceptance counts
// and "waits on" references resolved to task titles. Read-only; the owner revises by talking.
function PlanOutline({milestones}: {milestones: PlanMilestone[]}) {
  const all = milestones.flatMap(m => m.tasks || []);
  return (
    <div className="plan-outline">
      {milestones.map(m => (
        <div key={m.code + m.title} className="plan-ms">
          <div className="ms-title">
            <span className="ms-code">{m.code}</span>
            <span className="ms-name">{m.title}</span>
          </div>
          {(m.tasks || []).length > 0 && (
            <ol className="plan-tasks">
              {(m.tasks || []).map((t, i) => {
                const waits = (t.dependencies || []).map(d => all[d]?.title).filter(Boolean) as string[];
                const n = (t.acceptance || []).length;
                return (
                  <li key={i}>
                    <span className="plan-task-title">{t.title}</span>
                    <span className="plan-task-meta small muted">
                      {n ? `${n} acceptance ${n === 1 ? 'criterion' : 'criteria'}` : 'no acceptance criteria yet'}
                      {t.complexity ? ` · ${t.complexity}` : ''}
                      {t.risk ? ` · ${t.risk} risk` : ''}
                    </span>
                    {waits.length > 0 && (
                      <span className="deps">
                        <span className="deps-label">
                          <Icon name="clock" size={13} />
                          waits on
                        </span>
                        {waits.map(w => (
                          <span key={w} className="dep">
                            <b>{w}</b>
                          </span>
                        ))}
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      ))}
    </div>
  );
}

function SpecSoFar({spec}: {spec: Spec}) {
  if (!spec || (!spec.outcome && !spec.acceptance?.length && !spec.constraints?.length && !spec.notes && !spec.plan?.milestones?.length)) return null;
  const tasks = planTasks(spec).length;
  return (
    <details className="disclosure chat-spec">
      <summary>
        Specification so far
        {tasks ? <span className="count num">{tasks} tasks planned</span> : null}
      </summary>
      <div className="disclosure-body spec-grid">
        {spec.outcome && (
          <div>
            <Eyebrow>Outcome</Eyebrow>
            <p className="lede">{spec.outcome}</p>
          </div>
        )}
        {spec.acceptance?.length ? (
          <div>
            <Eyebrow>Acceptance</Eyebrow>
            <ul>
              {spec.acceptance.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {spec.constraints?.length ? (
          <div>
            <Eyebrow>Constraints</Eyebrow>
            <ul>
              {spec.constraints.map((c, i) => (
                <li key={i}>{c}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {spec.notes && (
          <div>
            <Eyebrow>Notes</Eyebrow>
            <p className="small lede">{spec.notes}</p>
          </div>
        )}
        {spec.plan?.milestones?.length ? (
          <div>
            <Eyebrow>Plan</Eyebrow>
            <PlanOutline milestones={spec.plan.milestones} />
          </div>
        ) : null}
      </div>
    </details>
  );
}

function Meta({m}: {m: ChatMessage}) {
  const bits = [m.model, m.effort, typeof m.seconds === 'number' ? seconds(m.seconds) : '', m.inspected ? `inspected ${m.inspected.replace(/^\/Users\/[^/]+/, '~')}` : ''].filter(Boolean);
  return (
    <span className="msg-meta">
      {bits.length > 0 && <span className="num">{bits.join(' · ')}</span>}
      <time className="num">{clock(m.time)}</time>
      {m.project_selected && <Chip tone="done">Project selected: {m.project_selected}</Chip>}
    </span>
  );
}

function Bubble({m, spec, onSend, busy}: {m: ChatMessage; spec: Spec; onSend: (text: string) => void; busy: boolean}) {
  const {openGoal} = useArena();
  if (m.role === 'user')
    return (
      <div className="msg user">
        <div className="bubble">
          <Markdown text={m.text} />
        </div>
        <span className="msg-meta">
          <time className="num">{clock(m.time)}</time>
        </span>
      </div>
    );
  const phrase = buildPhrase(spec);
  return (
    <div className={'msg arena' + (m.error ? ' error' : '')}>
      <div className="bubble">
        <Markdown text={m.text} />
      </div>
      <Meta m={m} />
      {m.offer_build && (
        <div className="msg-actions">
          <button type="button" className="btn primary" disabled={busy} onClick={() => onSend(phrase)}>
            <Icon name="bolt" size={16} />
            {phrase}
          </button>
        </div>
      )}
      {m.build_started && (
        <div className="build-card">
          <div className="row">
            <LiveDot tone="live" />
            <b>Build started on {m.project || 'your project'}</b>
          </div>
          <p className="small muted">Your director is inspecting the project and planning the work. Progress is on the Build tab.</p>
          {m.goal_id && (
            <button type="button" className="btn" onClick={() => openGoal(m.goal_id!)}>
              Open build
              <Icon name="arrow" size={16} />
            </button>
          )}
        </div>
      )}
      {m.launch_error && <p className="warn-banner">{m.launch_error}</p>}
    </div>
  );
}

export function Chat() {
  const {data, now, online, disabled, post, get, notify, chatPing} = useArena();
  const [id, setId] = useState<string | null>(remembered);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [pending, setPending] = useState<ChatMessage | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [picking, setPicking] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const end = useRef<HTMLDivElement>(null);
  const picker = useRef<HTMLDialogElement>(null);
  const seen = useRef(0);
  const current = useRef<string | null>(id);
  current.current = id;
  const speech = useSpeech(text => setDraft(d => (d.trim() ? d.replace(/\s+$/, '') + ' ' : '') + text));

  const conversations: Conversation[] = data.conversations || [];
  const listed = id ? conversations.find(c => c.id === id) : undefined;
  const thinking = detail?.status === 'thinking' || (!!listed && listed.status === 'thinking');
  const projects = data.projects.filter(p => p.enabled !== false);
  const projectId = detail?.project_id ?? listed?.project_id ?? null;
  const project = detail?.project || (projectId ? data.projects.find(p => p.id === projectId) : null);
  const spec: Spec = detail?.spec ?? listed?.spec ?? null;

  // Load the thread: on mount, when the id changes, when the snapshot says it moved, when an
  // SSE conversation event names it, and every 2 s while the director is still answering.
  const load = async (which: string) => {
    try {
      const d = (await get(`conversations/${which}`)) as ConversationDetail;
      if (current.current !== which) return; // the owner switched threads meanwhile
      setDetail(d);
      setPending(p => (p && d.messages.some(m => m.role === 'user' && m.text === p.text && m.time >= p.time - 60) ? null : p));
    } catch (e) {
      const msg = String(e);
      if (/not found|404/i.test(msg)) {
        remember(null);
        setId(null);
        setDetail(null);
        notify('That conversation no longer exists on the host. Start a new chat.');
      }
    }
  };
  useEffect(() => {
    if (!id) {
      setDetail(null);
      return;
    }
    remember(id);
    setDetail(cur => (cur?.id === id ? cur : null));
    load(id);
  }, [id]);
  useEffect(() => {
    if (id && listed?.updated && listed.updated !== detail?.updated) load(id);
  }, [id, listed?.updated]);
  useEffect(() => {
    if (id && chatPing.n && (chatPing.id === id || !chatPing.id)) load(id);
  }, [chatPing.n]);
  useEffect(() => {
    if (!id || !thinking) return;
    const t = window.setInterval(() => load(id), 2000);
    return () => window.clearInterval(t);
  }, [id, thinking]);

  // Keep the newest message in view as the thread grows; honour reduced motion.
  const messages: ChatMessage[] = [...(detail?.messages || []), ...(pending ? [pending] : [])];
  useEffect(() => {
    const count = messages.length + (thinking ? 1 : 0);
    if (count === seen.current) return;
    const grew = count > seen.current;
    seen.current = count;
    if (!grew || !end.current) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    end.current.scrollIntoView({block: 'end', behavior: reduce ? 'auto' : 'smooth'});
  }, [messages.length, thinking]);

  // Composer grows to six lines, then scrolls.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = 'auto';
    const line = parseFloat(getComputedStyle(el).lineHeight) || 24;
    const pad = 20;
    el.style.height = Math.min(el.scrollHeight, line * MAX_LINES + pad) + 'px';
  }, [draft]);

  useEffect(() => {
    if (picking && !picker.current?.open) picker.current?.showModal();
    if (!picking) picker.current?.close();
  }, [picking]);

  const create = async (): Promise<ConversationDetail> => {
    const c = (await post('conversations', {revision: 0, text: ''})) as Conversation;
    const d: ConversationDetail = {...c, project: null, messages: []};
    seen.current = 0;
    setDetail(d);
    setId(c.id);
    remember(c.id);
    return d;
  };

  const send = async (raw: string) => {
    const text = raw.trim();
    if (!text || sending || !online) return;
    setSending(true);
    try {
      let cid = id;
      if (!cid) cid = (await create()).id;
      setPending({id: -1, time: now, role: 'user', text});
      setDraft('');
      await post(`conversations/${cid}/messages`, {text, revision: 0});
      await load(cid);
    } catch (e) {
      setPending(null);
      setDraft(d => d || raw);
      notify(String(e));
    } finally {
      setSending(false);
      box.current?.focus({preventScroll: true});
    }
  };

  const choose = async (projectId: string | null) => {
    setPicking(false);
    try {
      const cid = id || (await create()).id;
      await post(`conversations/${cid}/project`, {project_id: projectId, revision: 0});
      await load(cid);
      notify(projectId ? `Project selected: ${data.projects.find(p => p.id === projectId)?.name || ''}` : 'Project cleared · Arena will ask which one when it matters');
    } catch (e) {
      notify(String(e));
    }
  };

  const fresh = async () => {
    if (sending) return;
    setSending(true);
    try {
      await create();
      setPending(null);
      setDraft('');
      box.current?.focus({preventScroll: true});
    } catch (e) {
      notify(String(e));
    } finally {
      setSending(false);
    }
  };

  const coarse = typeof window !== 'undefined' && window.matchMedia('(hover: none) and (pointer: coarse)').matches;
  const canSend = !!draft.trim() && online;
  const title = detail?.title || listed?.title || (id ? 'Conversation' : 'New conversation');
  const recent = conversations.filter(c => c.id !== id).slice(0, 12);
  const empty = !messages.length && !thinking;

  return (
    <div className="chat" aria-labelledby="chat-title">
      <div className="chat-head">
        <Mark size={36} />
        <h2 id="chat-title" className="chat-title" title={title}>
          {title}
        </h2>
        <button type="button" className="btn" disabled={disabled || sending} onClick={fresh}>
          <Icon name="plus" size={16} />
          New chat
        </button>
      </div>
      <div className="chat-bar">
        <button type="button" className={'chip-btn chat-project' + (project ? ' on' : '')} aria-haspopup="dialog" aria-label={project ? `Project: ${project.name}. Change project` : 'No project selected. Choose a project'} onClick={() => setPicking(true)}>
          <Icon name="projects" size={16} />
          <span className="clamp1">{project ? project.name : 'No project selected'}</span>
        </button>
        {recent.length > 0 && (
          <details className="disclosure chat-recent">
            <summary>
              Recent chats <span className="count num">{recent.length}</span>
            </summary>
            <div className="chat-recent-list">
              {recent.map(c => {
                const p = c.project_id ? data.projects.find(x => x.id === c.project_id) : null;
                return (
                  <button key={c.id} type="button" className="list-row press" onClick={() => setId(c.id)}>
                    <Icon name="chat" size={18} />
                    <span className="list-main">
                      <b className="clamp1">{c.title}</b>
                      <span className="small muted">
                        {ago(c.updated, now)}
                        {p ? ` · ${p.name}` : ''}
                        {c.status === 'thinking' ? ' · answering' : ''}
                      </span>
                    </span>
                    <Icon name="arrow" size={18} />
                  </button>
                );
              })}
            </div>
          </details>
        )}
      </div>
      <SpecSoFar spec={spec} />

      <div className="chat-thread" role="log" aria-live="polite" aria-label="Conversation with Arena">
        {empty && (
          <div className="chat-empty">
            <Mark size={110} />
            <h3 className="hero-title">Talk to Arena.</h3>
            <p className="muted">Ask about a project, think through an idea, or refine a specification. Nothing is built until you say so in your own words.</p>
            <div className="chips" role="group" aria-label="Suggested openers">
              {PROMPTS.map(p => (
                <button
                  key={p}
                  type="button"
                  className="chip-btn"
                  onClick={() => {
                    setDraft(p);
                    box.current?.focus();
                  }}
                >
                  <span>{p}</span>
                  <Icon name="arrow" size={14} />
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map(m => (
          <Bubble key={m.id === -1 ? 'pending' : m.id} m={m} spec={spec} onSend={send} busy={disabled || sending || thinking} />
        ))}
        {thinking && (
          <div className="msg arena thinking" role="status">
            <div className="bubble">
              <LiveDot tone="live" />
              Arena is looking…
            </div>
          </div>
        )}
        <div ref={end} />
      </div>

      <div className="chat-compose">
        <form
          className="compose"
          onSubmit={e => {
            e.preventDefault();
            send(draft);
          }}
        >
          <label htmlFor="chat-text" className="sr-only">
            Message Arena
          </label>
          <textarea
            id="chat-text"
            ref={box}
            rows={1}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => {
              if (coarse || e.key !== 'Enter' || e.shiftKey || e.nativeEvent.isComposing) return;
              e.preventDefault();
              send(draft);
            }}
            placeholder={project ? `Message Arena about ${project.name}…` : 'Message Arena…'}
            enterKeyHint={coarse ? 'enter' : 'send'}
            autoComplete="off"
          />
          <div className="compose-actions">
            {speech.supported && (
              <button type="button" className={'mic chat-mic' + (speech.listening ? ' on' : '')} aria-pressed={speech.listening} aria-label={speech.listening ? 'Stop dictating' : 'Dictate'} title={speech.listening ? 'Stop dictating' : 'Dictate'} onClick={speech.toggle}>
                <Icon name={speech.listening ? 'stop' : 'mic'} size={18} />
              </button>
            )}
            <button type="submit" className="btn primary chat-send" disabled={!canSend}>
              <Icon name="send" size={16} />
              Send
            </button>
          </div>
        </form>
        {speech.listening && (
          <p className="listening" aria-live="polite">
            <LiveDot tone="live" />
            {speech.interim ? speech.interim : 'Listening…'}
          </p>
        )}
        {speech.error && <p className="field-help warn">{speech.error}</p>}
        {!online && <p className="field-help warn chat-offline">Reconnecting to the build host · messages can be sent once the link returns.</p>}
      </div>

      <dialog ref={picker} className="chat-picker" onCancel={() => setPicking(false)} onClose={() => picking && setPicking(false)} aria-label="Choose a project">
        <div className="dialog-head">
          <h2>Which project?</h2>
          <button type="button" className="close" aria-label="Close dialog" onClick={() => setPicking(false)}>
            <Icon name="x" size={18} />
          </button>
        </div>
        <div className="dialog-scroll">
          <p className="small muted">Naming a project in the conversation selects it too. Only enrolled projects can be built in.</p>
          <div className="panel chat-project-list">
            {projects.map(p => (
              <button key={p.id} type="button" className="list-row press" aria-pressed={projectId === p.id} onClick={() => choose(p.id)}>
                <Icon name="projects" size={18} />
                <span className="list-main">
                  <b>
                    {p.name}
                    {p.self ? ' (this app)' : ''}
                  </b>
                  <span className="small muted path">{p.repo}</span>
                </span>
                {projectId === p.id && <Icon name="check" size={18} />}
              </button>
            ))}
            <button type="button" className="list-row press" aria-pressed={!projectId} onClick={() => choose(null)}>
              <Icon name="x" size={18} />
              <span className="list-main">
                <b>None</b>
                <span className="small muted">Arena asks which project when it matters.</span>
              </span>
              {!projectId && <Icon name="check" size={18} />}
            </button>
            {!projects.length && <p className="panel-body small muted">No projects are enrolled on the Mac yet.</p>}
          </div>
        </div>
      </dialog>
    </div>
  );
}
