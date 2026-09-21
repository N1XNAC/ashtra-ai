import { useEffect, useRef, useState, type ReactNode } from 'react'

const API = (import.meta as unknown as { env: Record<string, string | undefined> }).env?.VITE_API_URL ?? 'http://localhost:8000'
const USER = 'master-001'
const API_KEY = (import.meta as unknown as { env: Record<string, string | undefined> }).env?.VITE_API_KEY ?? ''
const MAXLEN = 4000

class ApiError extends Error {
  status: number
  retryAfter: string | null
  constructor(status: number, msg: string, retryAfter: string | null) {
    super(msg); this.status = status; this.retryAfter = retryAfter
  }
}

async function api(path: string, opts?: RequestInit) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (API_KEY) headers['X-API-Key'] = API_KEY
  let r: Response
  try {
    r = await fetch(API + path, { ...opts, headers: { ...headers, ...(opts?.headers as Record<string, string> ?? {}) }, signal: AbortSignal.timeout(45000) })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'TimeoutError') throw new ApiError(0, 'Request timed out after 45s — network stall or sleeping backend. Wait for Render Live and retry.', null)
    throw new ApiError(0, 'Network failed to fetch — check connection / VPN / adblock.', null)
  }
  if (!r.ok) await throwFor(r)
  return r.json()
}
async function throwFor(r: Response): Promise<never> {
  let raw = ''
  try { raw = await r.text() } catch { /* ignore */ }
  let detail = raw.slice(0, 300)
  try { const j = JSON.parse(raw); if (typeof j?.detail === 'string') detail = j.detail } catch { /* keep raw */ }
  let msg = `Request failed (HTTP ${r.status}): ${detail || '(empty body)'}`
  if (r.status === 429) msg = `Slow down, master — rate limit hit (429). Try again shortly. Body: ${detail || '(empty)'}`
  if (r.status === 401) msg = `Backend needs an API key (401): ${detail || 'check VITE_API_KEY matches Render API_KEY'}`
  if (r.status === 502) msg = msg + ' (vision link not configured — see backend/.env.example)'
  throw new ApiError(r.status, msg, r.headers.get('Retry-After'))
}
/* multipart (image upload) — no JSON content-type */
async function apiForm(path: string, form: FormData) {
  const headers: Record<string, string> = {}
  if (API_KEY) headers['X-API-Key'] = API_KEY
  const r = await fetch(API + path, { method: 'POST', headers, body: form })
  if (!r.ok) await throwFor(r)
  return r.json()
}
const post = (path: string, body: unknown) =>
  api(path, { method: 'POST', body: JSON.stringify(body) })

/* ---------- minimal stroke icons (ChatGPT-style, no emoji) ---------- */
const PATHS: Record<string, ReactNode> = {
  pen: (<><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></>),
  trash: (<><path d="M3 6h18" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></>),
  memory: (<><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14a9 3 0 0 0 18 0V5" /><path d="M3 12a9 3 0 0 0 18 0" /></>),
  target: (<><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" /></>),
  user: (<><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></>),
  sliders: (<><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3" /><path d="M1 14h6M9 8h6M17 16h6" /></>),
  menu: (<><path d="M4 6h16M4 12h16M4 18h16" /></>),
  up: (<><path d="M12 19V5M5 12l7-7 7 7" /></>),
  copy: (<><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>),
  chat: (<><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" /></>),
  code: (<><path d="m16 18 6-6-6-6M8 6l-6 6 6 6" /></>),
  calendar: (<><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>),
  plus: (<><path d="M12 5v14M5 12h14" /></>),
  image: (<><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="9" cy="9" r="2" /><path d="m21 15-5-5L5 21" /></>),
}
function Icon({ name, size = 17 }: { name: keyof typeof PATHS; size?: number }) {
  return (
    <svg className="ic" width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  )
}

/* ---------- markdown-lite (no deps): fences, inline code, bold, italic, lists, quotes, links ---------- */
function esc(s: string) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}
function inline(s: string) {
  let h = esc(s)
  h = h.replace(/`([^`]+)`/g, '<code>$1</code>')
  h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  h = h.replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  h = h.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
  return h
}
function renderMd(src: string) {
  const lines = src.split('\n')
  let html = '', inFence = false, inList: string | null = null
  const closeList = () => { if (inList) { html += `</${inList}>`; inList = null } }
  for (const line of lines) {
    if (line.trimStart().startsWith('```')) {
      if (inFence) html += '</code></pre>'
      else { closeList(); html += '<pre><code>' }
      inFence = !inFence
      continue
    }
    if (inFence) { html += esc(line) + '\n'; continue }
    const h3 = line.match(/^###\s+(.*)/)
    const h2 = line.match(/^##\s+(.*)/)
    const h1 = line.match(/^#\s+(.*)/)
    const ul = line.match(/^\s*[-*]\s+(.*)/)
    const ol = line.match(/^\s*\d+[.)]\s+(.*)/)
    const q = line.match(/^&gt;|^>\s?(.*)/)
    if (h3 || h2 || h1) { closeList(); const m = (h3 ?? h2 ?? h1)!; const tag = h3 ? 'h3' : 'h2'; html += `<${tag}>${inline(m[1])}</${tag}>` }
    else if (ul) { if (inList !== 'ul') { closeList(); html += '<ul>'; inList = 'ul' } html += `<li>${inline(ul[1])}</li>` }
    else if (ol) { if (inList !== 'ol') { closeList(); html += '<ol>'; inList = 'ol' } html += `<li>${inline(ol[1])}</li>` }
    else if (q) { closeList(); html += `<blockquote>${inline(q[1] ?? '')}</blockquote>` }
    else if (!line.trim()) { closeList() }
    else { closeList(); html += `<p>${inline(line)}</p>` }
  }
  closeList()
  if (inFence) html += '</code></pre>'
  return html
}

/* ---------- types ---------- */
type View = 'chat' | 'memory' | 'goals' | 'you'
type Msg = { role: string; content: string; meta?: string; fresh?: boolean; attachment?: string }
type Conv = { id: string; title: string; created_at?: string }
/* Per-chat session: drafts, attachments, messages and working state live here,
   keyed by conversation id ('new' for the unsent draft). Switching chats can
   never leak one chat's state into another; background completions write to
   their own key and surface as a sidebar spinner. */
type Sess = {
  msgs: Msg[] | null /* null = not loaded yet */
  input: string
  busy: boolean
  busyLabel: string
  err: string
  attach: { file: File; url: string } | null
}
const blankSess = (): Sess => ({ msgs: null, input: '', busy: false, busyLabel: '', err: '', attach: null })

/* ---------- glass confirm dialog (event-driven sheet) ---------- */
function ConfirmDialog({ title, body, confirmLabel, onConfirm, onCancel }: {
  title: string; body: string; confirmLabel: string
  onConfirm: () => void; onCancel: () => void
}) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onCancel])
  return (
    <div className="dialog-scrim" onClick={onCancel}>
      <div className="dialog glass" onClick={e => e.stopPropagation()} role="dialog" aria-modal="true">
        <div className="dialog-title">{title}</div>
        <div className="dialog-body">{body}</div>
        <div className="dialog-actions">
          <button className="mini" onClick={onCancel}>Cancel</button>
          <button className="mini danger-solid" onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  )
}

/* ---------- chat ---------- */
const SUGGESTIONS: { t: string; s: string; icon: 'chat' | 'code' | 'calendar' | 'target' }[] = [
  { t: 'Explain a concept', s: 'e.g. how does attention work in transformers?', icon: 'chat' },
  { t: 'Write code', s: 'e.g. a FastAPI rate limiter in Python', icon: 'code' },
  { t: 'Plan my day', s: 'e.g. what is on my plate?', icon: 'calendar' },
  { t: 'Set a goal', s: 'e.g. add a goal to learn piano', icon: 'target' },
]

function Chat({ convId, sessKey, sess, patchSess, onNewConv, refreshSidebar }: {
  convId: string | null; sessKey: string; sess: Sess
  patchSess: (key: string, p: Partial<Sess>) => void
  onNewConv: (id: string) => void; refreshSidebar: () => void
}) {
  const { msgs: cached, input, busy, busyLabel, err, attach } = sess
  const msgs = cached ?? []
  const [morph, setMorph] = useState(false) /* gooey send-button stretch, one shot per send */
  const bottomRef = useRef<HTMLDivElement>(null)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [cached, busy])

  /* Hydrate once per session: store is truth; API only when never loaded.
     Late API replies are cancelled so fast chat-hopping can't cross-pollinate. */
  useEffect(() => {
    if (sess.msgs !== null) {
      if (sess.msgs.some(m => m.fresh)) patchSess(sessKey, { msgs: sess.msgs.map(m => ({ ...m, fresh: false })) })
      return
    }
    if (!convId) { patchSess(sessKey, { msgs: [] }); return }
    let cancelled = false
    ;(async () => {
      try {
        const d = await api(`/chat/conversations/${USER}/${convId}`)
        if (!cancelled) patchSess(sessKey, {
          msgs: (d.messages ?? []).map((m: { role: string; content: string }) => ({ role: m.role, content: m.content, fresh: false })),
          err: '',
        })
      } catch (e) {
        if (!cancelled) patchSess(sessKey, { err: e instanceof Error ? e.message : 'Could not load chat.' })
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessKey])

  function autosize() {
    const ta = taRef.current
    if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 200) + 'px' }
  }

  function clearAttach() {
    if (sess.attach) URL.revokeObjectURL(sess.attach.url)
    patchSess(sessKey, { attach: null })
    if (fileRef.current) fileRef.current.value = ''
  }

  function pickImage(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0]
    if (!f) return
    if (!/^image\/(png|jpeg|webp|gif)$/.test(f.type)) { patchSess(sessKey, { err: 'Only PNG / JPEG / WEBP / GIF images.' }); return }
    if (f.size > 5_000_000) { patchSess(sessKey, { err: 'Image too large (max 5MB).' }); return }
    if (sess.attach) URL.revokeObjectURL(sess.attach.url)
    patchSess(sessKey, { err: '', attach: { file: f, url: URL.createObjectURL(f) } })
  }

  async function send(text?: string) {
    const key = sessKey /* pinned: completion always lands in ITS chat, even if you switched away */
    const base = sess.msgs ?? []
    let content = (text ?? input).trim()
    const img = sess.attach
    if ((!content && !img) || sess.busy || sess.msgs === null) return
    if (!content && img) content = 'What do you see in this image?'
    if (content.length > MAXLEN) { patchSess(key, { err: `Message too long (max ${MAXLEN} chars).` }); return }
    if (img) URL.revokeObjectURL(img.url)
    const userMsg: Msg = { role: 'user', content, fresh: true, attachment: img ? img.file.name : undefined }
    patchSess(key, { msgs: [...base, userMsg], input: '', err: '', attach: null, busy: true, busyLabel: '' })
    if (fileRef.current) fileRef.current.value = ''
    requestAnimationFrame(autosize)
    setMorph(true); setTimeout(() => setMorph(false), 400) /* fire the morph, then settle */
    /* vision link: image → text context for the text brain */
    let image_context: string | undefined
    let sawImage = false
    if (img) {
      patchSess(key, { busyLabel: 'Seeing image…' })
      try {
        const form = new FormData()
        form.append('user_id', USER)
        form.append('message', content)
        form.append('f', img.file)
        const d = await apiForm('/vision/describe', form)
        image_context = d.description
        sawImage = true
      } catch (e) {
        const msg = e instanceof Error ? e.message : 'Image read failed.'
        patchSess(key, {
          msgs: [...base, userMsg, { role: 'assistant', content: 'Sorry, master — ' + msg, fresh: true }],
          err: msg, busy: false, busyLabel: '',
        })
        return
      }
      patchSess(key, { busyLabel: '' })
    }
    try {
      const j = await post('/chat', { user_id: USER, conversation_id: convId, message: content, image_context })
      if (!convId) { onNewConv(j.conversation_id); refreshSidebar() }
      const meta: string[] = []
      if (sawImage) meta.push('saw image')
      if (j.sources?.length) meta.push(`recalled ${j.sources.length}`)
      if (j.tool_calls?.length) meta.push('used ' + j.tool_calls.map((c: { tool: string }) => c.tool).join(', '))
      if (j.adaptations_made?.length) meta.push('adapted')
      patchSess(key, {
        msgs: [...base, userMsg, { role: 'assistant', content: j.reply, meta: meta.join(' · ') || undefined, fresh: true }],
        busy: false, busyLabel: '',
      })
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Send failed.'
      patchSess(key, {
        msgs: [...base, userMsg, { role: 'assistant', content: 'Sorry, master — ' + msg, fresh: true }],
        err: msg, busy: false, busyLabel: '',
      })
    }
  }

  return (
    <>
      <div className="thread">
        <div className="thread-inner">
          {cached === null ? (
            <div className="msg">
              <div className="avatar">A</div>
              <div className="body"><div className="typing"><i /><i /><i /></div></div>
            </div>
          ) : msgs.length === 0 && !busy ? (
            <div className="welcome">
              <h1>What can I do for you, master?</h1>
              <p>Ashtra remembers, plans, and acts — powered by open-source AI.</p>
              <div className="suggest">
                {SUGGESTIONS.map(s => (
                  <button key={s.t} className="sug" onClick={() => send(s.s)}>
                    <span className="sug-ic"><Icon name={s.icon} size={18} /></span>
                    <span>{s.t}<small>{s.s}</small></span>
                  </button>
                ))}
              </div>
            </div>
          ) : null}
          {msgs.map((m, i) => m.role === 'user' ? (
            <div key={i} className={m.fresh ? 'msg userrow fresh' : 'msg userrow'}>
              <div className="body">
                {m.attachment && <div className="attchip"><Icon name="image" size={13} /> {m.attachment}</div>}
                {m.content}
              </div>
            </div>
          ) : (
            <div key={i} className={m.fresh ? 'msg fresh' : 'msg'}>
              <div className="avatar">A</div>
              <div className="body">
                <div className="who">Ashtra</div>
                <div className="md" dangerouslySetInnerHTML={{ __html: renderMd(m.content) }} />
                {(m.meta || true) && (
                  <div className="meta">
                    {m.meta && <span>{m.meta}</span>}
                    <button className="copybtn" title="Copy" onClick={() => navigator.clipboard?.writeText(m.content)}>
                      <Icon name="copy" size={12} />
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
          {busy && (
            <div className="msg">
              <div className="avatar">A</div>
              <div className="body">
                <div className="typing"><i /><i /><i /></div>
                {busyLabel && <div className="lab" style={{ marginTop: 2 }}>{busyLabel}</div>}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
      <div className="composer-zone">
        {err && <div className="errbar"><div>{err}</div></div>}
        <div className="composer">
          {attach && (
            <div className="attachrow">
              <img src={attach.url} className="thumb" alt="" />
              <span className="t">{attach.file.name}</span>
              <button className="copybtn" onClick={clearAttach} aria-label="Remove image">×</button>
            </div>
          )}
          <div className={morph ? 'composer-box gulp' : 'composer-box'}>
            <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif"
              hidden onChange={pickImage} />
            <button className="plusbtn" onClick={() => fileRef.current?.click()} aria-label="Attach image">
              <Icon name="plus" size={18} />
            </button>
            <textarea
              id="ashtra-composer" name="message"
              ref={taRef} rows={1} value={input} maxLength={MAXLEN + 100}
              onChange={e => { patchSess(sessKey, { input: e.target.value }); autosize() }}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
              placeholder="Message Ashtra…" />
            <button className={morph ? 'sendbtn morph' : 'sendbtn'} disabled={busy || cached === null || (!input.trim() && !attach)} onClick={() => send()} aria-label="Send">
              <Icon name="up" size={18} />
            </button>
          </div>
          <div className="hint">Ashtra can make mistakes. Memories are transparent and exportable.</div>
        </div>
      </div>
    </>
  )
}

/* ---------- sidebar ---------- */
function Sidebar({ view, setView, convId, setConvId, convs, busyIds, onNew, onDelete, open, close, fx, onToggleFx }: {
  view: View; setView: (v: View) => void
  convId: string | null; setConvId: (id: string | null) => void
  convs: Conv[]; busyIds: string[]; onNew: () => void; onDelete: (id: string) => void
  open: boolean; close: () => void; fx: boolean; onToggleFx: () => void
}) {
  const [q, setQ] = useState('')
  const rows = convs.filter(c => !q.trim() || c.title.toLowerCase().includes(q.toLowerCase()))
  return (
    <>
      {open && <div className="scrim" onClick={close} />}
      <div className={`side${open ? ' open' : ''}`}>
        <div className="side-top">
          <button className="newchat" onClick={() => { onNew(); close() }}><Icon name="pen" size={16} /> New chat</button>
        </div>
        <input className="searchbox" value={q} onChange={e => setQ(e.target.value)} placeholder="Search chats" />
        <div className="sect">Chats</div>
        <div className="convlist">
          {rows.map(c => (
            <button key={c.id} className={`conv${convId === c.id && view === 'chat' ? ' on' : ''}`}
              onClick={() => { setConvId(c.id); setView('chat'); close() }}>
              <span className="t">{c.title || 'New conversation'}</span>
              {busyIds.includes(c.id)
                ? <span className="spin" title="Ashtra is working here…" />
                : <span className="del" onClick={e => { e.stopPropagation(); onDelete(c.id) }}><Icon name="trash" size={15} /></span>}
            </button>
          ))}
          {rows.length === 0 && <div className="sect">No chats yet</div>}
        </div>
        <div className="side-foot">
          <button className={`navbtn${view === 'memory' ? ' on' : ''}`} onClick={() => { setView('memory'); close() }}><Icon name="memory" size={17} /> Memory</button>
          <button className={`navbtn${view === 'goals' ? ' on' : ''}`} onClick={() => { setView('goals'); close() }}><Icon name="target" size={17} /> Goals</button>
          <button className={`navbtn${view === 'you' ? ' on' : ''}`} onClick={() => { setView('you'); close() }}><Icon name="user" size={17} /> You</button>
          <button className="fxtoggle" onClick={onToggleFx} title="Toggle glass + morph effects (low-performance fallback)">
            <Icon name="sliders" size={15} /> {fx ? 'Effects on' : 'Effects off'}
          </button>
          <div className="modeltag"><span className="dot" /> gpt-oss-20b · open-source</div>
        </div>
      </div>
    </>
  )
}

/* ---------- memory panel ---------- */
type Mem = { id: string; kind: string; content: string; importance: string; memory_type: string }
function MemoryPanel() {
  const [q, setQ] = useState('')
  const [mems, setMems] = useState<Mem[]>([])
  const [hits, setHits] = useState<{ content: string; kind: string; score: number }[] | null>(null)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)
  async function load() {
    try { setMems(await api(`/memories/${USER}`)) } catch { /* offline */ }
  }
  useEffect(() => { load() }, [])
  async function search(v: string) {
    setQ(v)
    if (!v.trim()) { setHits(null); return }
    try { setHits(await post('/memories/search', { user_id: USER, query: v, top_k: 8 })) } catch { /* offline */ }
  }
  const rows = hits ?? mems
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Memory</h2>
      <p className="desc">Everything Ashtra remembers. Search, delete, reindex, or export.</p>
      <div className="toolbar">
        <input value={q} onChange={e => search(e.target.value)} placeholder="Search memories…" />
      </div>
      {rows.length === 0 && <div className="card">No memories yet, master. Chat, and Ashtra will remember.</div>}
      {rows.map((m, i) => (
        <div key={hits ? i : (m as Mem).id} className="card">
          <div className="row">
            <div className="grow"><span className="kind">{m.kind}</span>{m.content}</div>
            {!hits && <button className="mini danger" onClick={() => setConfirmDel((m as Mem).id)}>Delete</button>}
          </div>
          {'score' in m && <div className="lab">score {(m as { score: number }).score.toFixed(2)}</div>}
        </div>
      ))}
      <div className="toolbar">
        <button className="mini" onClick={async () => { await post(`/memories/${USER}/reindex`, {}); load() }}>Reindex vectors</button>
        <button className="mini" onClick={async () => {
          const d = await api(`/memories/${USER}/export`)
          const a = document.createElement('a')
          a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: 'application/json' }))
          a.download = 'ashtra-export.json'; a.click()
        }}>Export JSON</button>
      </div>
      {confirmDel && (
        <ConfirmDialog title="Forget this memory?" body="Ashtra will no longer recall it. This cannot be undone."
          confirmLabel="Forget" onCancel={() => setConfirmDel(null)}
          onConfirm={async () => { await api(`/memories/${confirmDel}`, { method: 'DELETE' }); setConfirmDel(null); load() }} />
      )}
    </div></div>
  )
}

/* ---------- goals panel ---------- */
type Goal = { id: string; title: string; status: string; progress: number }
function GoalsPanel() {
  const [goals, setGoals] = useState<Goal[]>([])
  const [avg, setAvg] = useState(0)
  const [draft, setDraft] = useState('')
  async function load() {
    try {
      const d = await api(`/goals/${USER}/dashboard`)
      setGoals(d.goals); setAvg(d.avg_progress)
    } catch { /* offline */ }
  }
  useEffect(() => { load() }, [])
  const active = goals.filter(g => g.status !== 'done')
  const done = goals.filter(g => g.status === 'done')
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Goals</h2>
      <p className="desc">Track what matters. Chat also understands “add a goal …”.</p>
      <div className="card"><div className="lab">Average progress</div>
        <div className="big">{avg}%</div><div className="pbar"><div style={{ width: `${avg}%` }} /></div>
      </div>
      <div className="toolbar">
        <input value={draft} onChange={e => setDraft(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && draft.trim() && post(`/goals/${USER}`, { title: draft.trim() }).then(() => { setDraft(''); load() })}
          placeholder="New goal…" />
        <button className="mini primary" onClick={async () => {
          if (!draft.trim()) return
          await post(`/goals/${USER}`, { title: draft.trim() }); setDraft(''); load()
        }}>Add</button>
      </div>
      {active.map(g => (
        <div key={g.id} className="card"><div className="row">
          <div className="grow">{g.title}<div className="pbar"><div style={{ width: `${g.progress}%` }} /></div></div>
          <span className="lab">{g.progress}%</span>
          <button className="mini" onClick={async () => {
            await api(`/goals/${g.id}`, { method: 'PATCH', body: JSON.stringify({ status: 'done' }) }); load()
          }}>Done</button>
        </div></div>
      ))}
      {done.length > 0 && <><div className="lab">Completed</div>
        {done.map(g => (
          <div key={g.id} className="card"><div className="row">
            <div className="grow" style={{ textDecoration: 'line-through', color: 'var(--muted)' }}>{g.title}</div>
            <button className="mini" onClick={async () => {
              await api(`/goals/${g.id}`, { method: 'PATCH', body: JSON.stringify({ status: 'active' }) }); load()
            }}>Reopen</button>
          </div></div>
        ))}</>}
    </div></div>
  )
}

/* ---------- you panel ---------- */
function Seg({ options, value, onPick }: { options: string[]; value: string; onPick: (v: string) => void }) {
  return (
    <div className="seg">
      {options.map(o => (
        <button key={o} className={o === value ? 'on' : ''} onClick={() => onPick(o)}>{o}</button>
      ))}
    </div>
  )
}
function YouPanel() {
  const [model, setModel] = useState<{
    traits: Record<string, string>; adaptation: Record<string, string | number>;
    directives: string; recent_behaviour_patterns: string[]
  } | null>(null)
  const [digest, setDigest] = useState('')
  const [local, setLocal] = useState<{ trained?: boolean; val_perplexity?: number; train_windows?: number }>({})
  async function load() {
    try { setModel(await api(`/profile/${USER}/model`)) } catch { /* offline */ }
    try { setDigest((await api(`/learn/digest?user_id=${USER}`)).digest ?? '') } catch { /* offline */ }
    try { setLocal(await api('/model/status')) } catch { /* offline */ }
  }
  useEffect(() => { load() }, [])
  async function patch(field: string, value: string) {
    await api(`/profile/${USER}`, { method: 'PATCH', body: JSON.stringify({ [field]: value }) })
    load()
  }
  const t = model?.traits ?? {}
  return (
    <div className="panel"><div className="panel-inner">
      <h2>You</h2>
      <p className="desc">How Ashtra adapts to you. Confidence {String(model?.adaptation.confidence ?? '…')}.</p>
      <div className="card">
        <div className="chips">
          {[t.explanation_depth, t.teaching_style, t.expertise_level, t.communication_format, t.tone]
            .filter(Boolean).map((c, i) => <span key={i} className="chip">{c}</span>)}
        </div>
        <div className="lab">{model?.directives}</div>
      </div>
      <div className="lab">Depth</div>
      <Seg options={['concise', 'balanced', 'detailed']} value={t.explanation_depth ?? 'balanced'}
        onPick={v => patch('explanation_depth', v)} />
      <div className="lab">Tone</div>
      <Seg options={['casual', 'formal']} value={t.tone ?? 'casual'} onPick={v => patch('tone', v)} />
      <div className="lab">Format</div>
      <Seg options={['chat', 'bullets', 'tutorial', 'code-first']} value={t.communication_format ?? 'chat'}
        onPick={v => patch('communication_format', v)} />
      <div className="lab">Learning digest</div>
      <div className="card">{digest || 'No digest yet, master.'}
        <div className="toolbar">
          <button className="mini" onClick={async () => { await post(`/learn/consolidate?user_id=${USER}&days=30`, {}); load() }}>
            Consolidate now</button>
        </div>
      </div>
      <div className="lab">Local model</div>
      <div className="card">
        {local.trained ? `Trained · ppl ${Number(local.val_perplexity).toFixed(2)} · ${local.train_windows} windows` : 'Not trained yet'}
        <div className="toolbar">
          <button className="mini" onClick={async () => { await post('/model/fine-tune', { steps: 50 }); load() }}>
            Fine-tune on latest chats</button>
        </div>
      </div>
    </div></div>
  )
}

/* ---------- shell ---------- */
export default function App() {
  const [view, setView] = useState<View>('chat')
  const [convId, setConvId] = useState<string | null>(null)
  const [convs, setConvs] = useState<Conv[]>([])
  const [sideOpen, setSideOpen] = useState(false)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)
  /* session store: one entry per chat, survives switching */
  const [sessions, setSessions] = useState<Record<string, Sess>>({})
  function patchSess(key: string, p: Partial<Sess>) {
    setSessions(s => ({ ...s, [key]: { ...(s[key] ?? blankSess()), ...p } }))
  }
  function dropSess(key: string) {
    setSessions(s => {
      const cur = s[key]
      if (cur?.attach) { try { URL.revokeObjectURL(cur.attach.url) } catch { /* ignore */ } }
      if (!cur) return s
      const n = { ...s }
      delete n[key]
      return n
    })
  }
  const [fx, setFx] = useState(() => {
    try { return localStorage.getItem('ashtra-fx') !== 'off' } catch { return true }
  })
  function toggleFx() {
    setFx(v => {
      try { localStorage.setItem('ashtra-fx', v ? 'off' : 'on') } catch { /* ignore */ }
      return !v
    })
  }

  async function refreshConvs() {
    try { setConvs(await api(`/chat/conversations/${USER}`)) } catch { /* offline */ }
  }
  useEffect(() => { refreshConvs() }, [])

  async function delConv(id: string) {
    await api(`/chat/conversations/${USER}/${id}`, { method: 'DELETE' })
    dropSess(id)
    if (convId === id) setConvId(null)
    refreshConvs()
  }

  const sessKey = convId ?? 'new'
  const sess = sessions[sessKey] ?? blankSess()
  const busyIds = Object.keys(sessions).filter(k => sessions[k].busy)
  /* a fresh draft chat becomes real: carry its session over to the new id */
  function onNewConv(id: string) {
    setSessions(s => {
      const cur = s['new']
      if (!cur) return s
      const n = { ...s }
      delete n['new']
      n[id] = cur
      return n
    })
    setConvId(id)
  }

  const titles: Record<View, string> = { chat: 'Ashtra', memory: 'Memory', goals: 'Goals', you: 'You' }

  return (
    <div className={fx ? 'ash' : 'ash no-fx'}>
      <Sidebar view={view} setView={setView} convId={convId} setConvId={setConvId}
        convs={convs} busyIds={busyIds} onNew={() => { setConvId(null); setView('chat') }} onDelete={(id) => setConfirmDel(id)}
        open={sideOpen} close={() => setSideOpen(false)} fx={fx} onToggleFx={toggleFx} />
      <div className="main">
        <div className="topbar">
          <button className="burger" onClick={() => setSideOpen(true)} aria-label="Open sidebar"><Icon name="menu" size={22} /></button>
          <div>
            <div className="name">{titles[view]}</div>
            {view === 'chat' && <div className="sub"><span className="dot" /> gpt-oss-20b · remembers you</div>}
          </div>
        </div>
        {view === 'chat' && <Chat key={sessKey} convId={convId} sessKey={sessKey} sess={sess}
          patchSess={patchSess} onNewConv={onNewConv} refreshSidebar={refreshConvs} />}
        {view === 'memory' && <MemoryPanel />}
        {view === 'goals' && <GoalsPanel />}
        {view === 'you' && <YouPanel />}
      </div>
      {confirmDel && (
        <ConfirmDialog title="Delete this chat?" body="The conversation and its messages will be removed."
          confirmLabel="Delete" onCancel={() => setConfirmDel(null)}
          onConfirm={() => { const id = confirmDel; setConfirmDel(null); delConv(id) }} />
      )}
    </div>
  )
}
