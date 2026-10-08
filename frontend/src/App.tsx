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

async function api(path: string, opts?: RequestInit, timeoutMs = 75000) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (API_KEY) headers['X-API-Key'] = API_KEY
  console.log('[ASHRA] request started', path)
  let r: Response
  try {
    r = await fetch(API + path, { ...opts, headers: { ...headers, ...(opts?.headers as Record<string, string> ?? {}) }, signal: AbortSignal.timeout(timeoutMs) })
  } catch (e) {
    console.log('[ASHRA ERROR]', e instanceof Error ? `${e.name}: ${e.message}` : e)
    if (e instanceof DOMException && e.name === 'TimeoutError') throw new ApiError(0, 'Request timed out after 75s — backend still working or network stalled. Check back in the chat; the reply lands on revisit.', null)
    throw new ApiError(0, 'Network failed to fetch — check connection / VPN / adblock.', null)
  }
  console.log('[ASHRA] response status:', r.status, path)
  if (!r.ok) await throwFor(r)
  return r.json()
}
async function throwFor(r: Response): Promise<never> {
  let raw = ''
  try { raw = await r.text() } catch { /* ignore */ }
  let detail = raw.slice(0, 300)
  try { const j = JSON.parse(raw); if (typeof j?.detail === 'string') detail = j.detail } catch { /* keep raw */ }
  let msg = `Request failed (HTTP ${r.status}): ${detail || '(empty body)'}`
  if (r.status === 429) msg = `Slow down — rate limit hit (429). Try again shortly. Body: ${detail || '(empty)'}`
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
  search: (<><circle cx="11" cy="11" r="7" /><path d="m20 20-3.8-3.8" /></>),
  x: (<><path d="M6 6l12 12M18 6 6 18" /></>),
  books: (<><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V4a2 2 0 0 0-2-2H6.5A2.5 2.5 0 0 0 4 4.5v15Z" /><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5" /><path d="M9 7h7" /></>),
  folder: (<><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z" /></>),
  clock: (<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>),
  grid: (<><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></>),
  plugin: (<><path d="M9 7V3m6 4V3M7 7h10v5a5 5 0 0 1-10 0V7Z" /><path d="M12 17v4" /></>),
  terminal: (<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3M12 15h5" /></>),
  external: (<><path d="M14 4h6v6M20 4 11 13" /><path d="M20 14v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5" /></>),
  dots: (<><circle cx="5" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="19" cy="12" r="1.4" /></>),
  brain: (<><circle cx="12" cy="12" r="8" /><path d="M12 4v16M8.5 9h7M8.5 15h7" /></>),
  bag: (<><path d="M6 8h12l1 12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L6 8Z" /><path d="M9 10V6a3 3 0 0 1 6 0v4" /></>),
  gear: (<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.09a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55h.09a1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.09a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1Z" /></>),
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
  h = h.replace(/!\[([^\]]*)\]\((https?:[^)]+)\)/g, '<img src="$2" alt="$1" loading="lazy" class="chatimg" />')
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
type View = 'chat' | 'library' | 'projects' | 'scheduled' | 'plugins' | 'build' | 'market' | 'memory' | 'goals' | 'you' | 'settings'
type Msg = { role: string; content: string; meta?: string; fresh?: boolean; attachment?: string; saved?: string[] }
type Conv = { id: string; title: string; created_at?: string; is_build?: boolean }
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
/* Home suggestions route straight into Build & Run — that's the product loop. */
const SUGGESTIONS: { t: string; s: string }[] = [
  { t: 'Build a business website', s: 'Describe your business — get a live site' },
  { t: 'Create a portfolio', s: 'Show your work in one page' },
  { t: 'Make a landing page', s: 'One focused page that converts' },
]

/* ---------- shared composer shell: Uiverse-style gradient composer,
   identical on every screen ---------- */
function ComposerShell({ lead, field, canSend, onSend, sendLabel, head }: {
  lead?: ReactNode; field: ReactNode; canSend: boolean
  onSend: () => void; sendLabel: string; boxExtra?: string; head?: ReactNode
}) {
  return (
    <div className="composer">
      {head}
      <div className="uv-chatbot">
        <div className="uv-chat-options">
          <div className="uv-chat">
            <div className="uv-chat-bot">{field}</div>
            <div className="uv-options">
              <div className="uv-btns-add">{lead}</div>
              <button className="uv-submit" disabled={!canSend} onClick={onSend} aria-label={sendLabel}>
                <i><Icon name="up" size={18} /></i>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ---------- shared thread shell: Chat AND Build & Run render through this,
   so both screens are pixel-identical ---------- */
function ThreadShell({ msgs, busy, status, empty, composer, footer, actionIndex, action }: {
  msgs: Msg[] | null; busy: boolean; status: string
  empty: ReactNode; composer: ReactNode; footer?: ReactNode
  actionIndex?: number | null; action?: ReactNode
}) {
  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs, busy])
  return (
    <>
      <div className="thread">
        <div className="thread-inner">
          {msgs === null ? (
            <div className="msg">
              <div className="body"><div className="typing"><i /><i /><i /></div></div>
            </div>
          ) : msgs.length === 0 && !busy ? empty : null}
          {(msgs ?? []).map((m, i) => m.role === 'user' ? (
            <div key={i} className={m.fresh ? 'msg userrow fresh' : 'msg userrow'}>
              <div className="body">
                {m.attachment && <div className="attchip"><Icon name="image" size={13} /> {m.attachment}</div>}
                {m.content}
              </div>
            </div>
          ) : (
            <div key={i} className={m.fresh ? 'msg fresh' : 'msg'}>
              <div className="body">
                <div className="md" dangerouslySetInnerHTML={{ __html: renderMd(m.content) }} />
                {m.saved?.length ? (
                  <div className="memnote">
                    <span className="memnote-t"><Icon name="memory" size={12} /> Saved to memory</span>
                    {m.saved.map((s, si) => <div key={si} className="memnote-b">{s}</div>)}
                  </div>
                ) : null}
                {actionIndex === i ? action : null}
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
              <div className="body">
                <div className="typing"><i /><i /><i /></div>
                <div className="lab" style={{ marginTop: 2 }}>{status}</div>
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
      <div className="composer-zone">
        {composer}
        {footer}
      </div>
    </>
  )
}

/* site-building intent detector for the Build & Run nudge popup */
const BUILD_HINT_RE = /(^|\b)(build|create|make|design|code|develop|generate|ship)\b[\s\S]{0,45}\b(site|website|webpage|web\s?page|landing|portfolio|blog|store|shop|web\s?app)\b|\b(build|make|create)\s+(me\s+)?a\s+(site|website)\b/i

function Chat({ convId, sessKey, sess, patchSess, onNewConv, refreshSidebar, onDraft, isBuild, goBuild }: {
  convId: string | null; sessKey: string; sess: Sess
  patchSess: (key: string, p: Partial<Sess>) => void
  onNewConv: (id: string) => void; refreshSidebar: () => void; onDraft: (title: string) => void
  isBuild?: boolean; goBuild: (prompt: string) => void
}) {
  const { msgs: cached, input, busy, busyLabel, err, attach } = sess
  const [morph, setMorph] = useState(false) /* gooey send-button stretch, one shot per send */
  const [deep, setDeep] = useState(() => {
    try { return localStorage.getItem('ashtra-deep') === 'on' } catch { return false }
  })
  /* 🔨 build chats: always offer Preview — html is re-fetched from the server */
  const [site, setSite] = useState<{ job_id: string; html: string } | null>(null)
  const [siteOpen, setSiteOpen] = useState(false)
  useEffect(() => {
    setSite(null); setSiteOpen(false)
    if (!isBuild || !convId) return
    let cancelled = false
    ;(async () => {
      try {
        const d = await api(`/build/conversation/${convId}`)
        if (!cancelled && d?.html) setSite(d)
      } catch { /* no build stored */ }
    })()
    return () => { cancelled = true }
  }, [convId, isBuild])
  async function downloadZip() {
    if (!site) return
    try {
      const headers: Record<string, string> = {}
      if (API_KEY) headers['X-API-Key'] = API_KEY
      const r = await fetch(API + '/build/zip/' + site.job_id, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const url = URL.createObjectURL(await r.blob())
      const a = document.createElement('a')
      a.href = url; a.download = 'website.zip'; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 5000)
    } catch { /* download failed */ }
  }
  const [phase, setPhase] = useState(0) /* rotating status while busy */
  const PHASES = ['Thinking', 'Recalling memories', 'Drafting reply', 'Polishing']
  /* site-building intent → gentle nudge to Build & Run (once per send attempt) */
  const [buildHint, setBuildHint] = useState(false)
  const hintDone = useRef(false)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!busy) { setPhase(0); return }
    const t = setInterval(() => setPhase(p => (p + 1) % 4), 4000)
    return () => clearInterval(t)
  }, [busy])

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
    if (!img && !isBuild && !hintDone.current && BUILD_HINT_RE.test(content)) { setBuildHint(true); return }
    if (img) URL.revokeObjectURL(img.url)
    const userMsg: Msg = { role: 'user', content, fresh: true, attachment: img ? img.file.name : undefined }
    patchSess(key, { msgs: [...base, userMsg], input: '', err: '', attach: null, busy: true, busyLabel: '' })
    if (!convId) onDraft(content)
    if (fileRef.current) fileRef.current.value = ''
    requestAnimationFrame(autosize)
    setMorph(true); setTimeout(() => setMorph(false), 400) /* fire the morph, then settle */
    /* vision link: image → text context for the text brain */
    let image_context: string | undefined
    let sawImage = false
    if (img) {
      patchSess(key, { busyLabel: 'Analyzing image…' })
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
          msgs: [...base, userMsg, { role: 'assistant', content: 'Sorry — ' + msg, fresh: true }],
          err: msg, busy: false, busyLabel: '',
        })
        return
      }
      patchSess(key, { busyLabel: '' })
    }
    try {
      console.log('[ASHRA] sending message')
      const j = await post('/chat', { user_id: USER, conversation_id: convId, message: content, image_context, deep_thinking: deep })
      console.log('[ASHRA] response parsed')
      if (!j || typeof j.reply !== 'string' || !j.reply.trim()) {
        console.log('[ASHRA ERROR] invalid response shape', JSON.stringify(j)?.slice(0, 200))
        throw new Error('Invalid response from backend (missing reply).')
      }
      console.log('[ASHRA] rendering response')
      const targetKey = !convId ? j.conversation_id : key
      if (!convId) { onNewConv(j.conversation_id); refreshSidebar() }
      const meta: string[] = []
      if (sawImage) meta.push('saw image')
      if (j.sources?.length) meta.push(`recalled ${j.sources.length}`)
      if (j.tool_calls?.length) meta.push('used ' + j.tool_calls.map((c: { tool: string }) => c.tool).join(', '))
      if (/\!\[[^\]]*\]\(https?:/.test(j.reply)) meta.push('images')
      if (j.adaptations_made?.length) meta.push('adapted')
      patchSess(targetKey, {
        msgs: [...base, userMsg, { role: 'assistant', content: j.reply, meta: meta.join(' · ') || undefined, fresh: true, saved: Array.isArray(j.saved) && j.saved.length ? j.saved : undefined }],
        busy: false, busyLabel: '',
      })
      console.log('[ASHRA] generation complete')
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Send failed.'
      console.log('[ASHRA ERROR]', msg)
      patchSess(key, {
        msgs: [...base, userMsg, { role: 'assistant', content: 'Sorry — ' + msg, fresh: true }],
        err: msg, busy: false, busyLabel: '',
      })
    }
  }

  const welcome = (
    <div className="welcome">
      <h1>What are we building today?</h1>
      <p>Describe your business. Get a live site.</p>
      <div className="suggest">
        {SUGGESTIONS.map(s => (
          <button key={s.t} className="sug" onClick={() => goBuild(s.t)}>
            <span>{s.t}<small>{s.s}</small></span>
          </button>
        ))}
      </div>
    </div>
  )
  const buildIdx = (site && cached)
    ? cached.map((m, i) => m.role === 'assistant' ? i : -1).filter(i => i >= 0).pop() ?? null
    : null
  return (
    <ThreadShell msgs={cached} busy={busy} status={busyLabel || `${PHASES[phase]}…`} empty={welcome}
      actionIndex={site ? buildIdx : null}
      action={site ? <button className="mini" onClick={() => setSiteOpen(true)}>Preview</button> : null}
      composer={<ComposerShell
        head={<>
          {buildHint && (
            <div className="buildhint" role="dialog" aria-label="Use Build and Run">
              <div className="bh-t">Building a website?</div>
              <div className="bh-b">Coding sites is what <b>Build &amp; Run</b> is for — it builds, previews and hands you the zip.</div>
              <div className="bh-a">
                <button className="mini" onClick={() => { setBuildHint(false); hintDone.current = true; send() }}>Skip</button>
                <button className="mini primary" onClick={() => { setBuildHint(false); hintDone.current = true; goBuild(input.trim()) }}>Go to Build &amp; Run</button>
              </div>
            </div>
          )}
          {err ? <div className="errbar"><div>{err}</div></div> : (attach ? (
          <div className="attachrow">
            <img src={attach.url} className="thumb" alt="" />
            <span className="t">{attach.file.name}</span>
            <button className="copybtn" onClick={clearAttach} aria-label="Remove image">×</button>
          </div>
        ) : null)}
        </>}
        boxExtra={morph ? 'gulp' : ''}
        lead={<>
          <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif"
            hidden onChange={pickImage} />
          <button className="plusbtn" onClick={() => fileRef.current?.click()} aria-label="Attach image">
            <Icon name="plus" size={18} />
          </button>
          <button className={`brainbtn${deep ? ' on' : ''}`} title="Complex thinking: forces maximum reasoning — slower but smarter. Off = automatic speed."
            onClick={() => { setDeep(v => { try { localStorage.setItem('ashtra-deep', v ? 'off' : 'on') } catch { /* ignore */ } return !v }) }} aria-label="Complex thinking">
            <Icon name="brain" size={18} />
          </button>
        </>}
        field={
          <textarea
            id="ashtra-composer" name="message"
            ref={taRef} rows={1} value={input} maxLength={MAXLEN + 100}
            onChange={e => { patchSess(sessKey, { input: e.target.value }); autosize() }}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            placeholder="Message AZX…" />
        }
        canSend={!(busy || cached === null || (!input.trim() && !attach))}
        onSend={() => send()} sendLabel="Send" />
      }
      footer={<>
        {siteOpen && site && <SiteModal html={site.html} onClose={() => setSiteOpen(false)} onDownload={downloadZip} />}
        <div className="hint">AZX can make mistakes. Memories are transparent and exportable.</div>
      </>} />
  )
}

/* ---------- sidebar ---------- */
function Sidebar({ view, setView, convId, setConvId, convs, onNew, onDelete, onDeleteMany, open, close, name, onBuild }: {
  view: View; setView: (v: View) => void
  convId: string | null; setConvId: (id: string | null) => void
  convs: Conv[]; onNew: () => void; onDelete: (id: string) => void
  onDeleteMany: (ids: string[]) => void
  open: boolean; close: () => void
  name: string; onBuild: () => void
}) {
  const [q, setQ] = useState('')
  /* long-press (or right-click) a chat → multi-select delete mode */
  const [selMode, setSelMode] = useState(false)
  const [sel, setSel] = useState<Set<string>>(() => new Set())
  const pressTimer = useRef<number | null>(null)
  const startPress = (id: string) => {
    if (selMode) return
    pressTimer.current = window.setTimeout(() => {
      setSelMode(true); setSel(new Set([id]))
      if (navigator.vibrate) navigator.vibrate(12)
    }, 550)
  }
  const cancelPress = () => {
    if (pressTimer.current !== null) { clearTimeout(pressTimer.current); pressTimer.current = null }
  }
  const toggleSel = (id: string) => setSel(s => {
    const n = new Set(s)
    if (n.has(id)) n.delete(id); else n.add(id)
    if (n.size === 0) setSelMode(false)
    return n
  })
  const go = (v: View) => { setView(v); close() }
  const row = (label: string, icon: keyof typeof PATHS, active: boolean, onClick: () => void) => (
    <button key={label} className={`mrow${active ? ' on' : ''}`} onClick={onClick}>
      <Icon name={icon} size={19} /><span className="t">{label}</span>
    </button>
  )
  const rows = convs.filter(c => c.id !== '__draft' && (!q.trim() || c.title.toLowerCase().includes(q.toLowerCase())))
  const isBuildConv = (c: Conv) => c.is_build || c.title.startsWith('🔨')
  return (
    <>
      {open && <div className="scrim" onClick={close} />}
      <div className={`side${open ? ' open' : ''}`}>
        <div className="drawer-top">
          <div className="wordmark">AZX</div>
          <div className="drawer-top-r">
            <button className="iconbtn" aria-label="Close menu" onClick={close}><Icon name="x" size={21} /></button>
          </div>
        </div>
        <nav className="mnav">
          {row('New chat', 'pen', false, () => { onNew(); close() })}
          {row('Build & Run', 'code', view === 'build', onBuild)}
          {row('Templates', 'grid', view === 'market', () => go('market'))}
          {row('Memory', 'memory', view === 'memory', () => go('memory'))}
        </nav>
        <div className="drawer-search"><Icon name="search" size={16} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search" aria-label="Search chats" />
        </div>
        <div className="sect">Recent chats</div>
        {selMode && (
          <div className="selbar">
            <span className="selcount">{sel.size} selected</span>
            <button className="mini" onClick={() => { setSelMode(false); setSel(new Set()) }}>Cancel</button>
            <button className="mini danger-solid" disabled={!sel.size}
              onClick={() => { const ids = [...sel]; setSelMode(false); setSel(new Set()); onDeleteMany(ids) }}>
              Delete{sel.size ? ` (${sel.size})` : ''}
            </button>
          </div>
        )}
        <div className="convlist">
          {rows.map(c => (
            <button key={c.id}
              className={`conv${convId === c.id && view === 'chat' && !selMode ? ' on' : ''}${selMode ? ' selmode' : ''}${sel.has(c.id) ? ' sel' : ''}`}
              onPointerDown={() => startPress(c.id)}
              onPointerUp={cancelPress} onPointerLeave={cancelPress} onPointerCancel={cancelPress}
              onContextMenu={e => { e.preventDefault(); cancelPress(); if (!selMode) { setSelMode(true); setSel(new Set([c.id])) } }}
              onClick={() => {
                if (selMode) { toggleSel(c.id); return }
                setConvId(c.id); setView('chat'); close()
              }}>
              {selMode && <span className="tick" aria-hidden="true">{sel.has(c.id) ? '✓' : ''}</span>}
              {!selMode && (isBuildConv(c)
                ? <span className="type-icon" title="Build & Run project"><Icon name="code" size={13} /></span>
                : <span className="type-dot" aria-hidden="true" />)}
              <span className="t">{c.title.replace(/^🔨\s*/, '') || 'New conversation'}</span>
              {!selMode && <span className="del" onClick={e => { e.stopPropagation(); onDelete(c.id) }}><Icon name="trash" size={15} /></span>}
            </button>
          ))}
          {rows.length === 0 && <div className="sect">No chats yet</div>}
        </div>
        <nav className="mnav mnav-more">
          {row('Goals', 'target', view === 'goals', () => go('goals'))}
          {row('Scheduled', 'clock', view === 'scheduled', () => go('scheduled'))}
          {row('You', 'user', view === 'you', () => go('you'))}
        </nav>
        <div className="drawer-profile" role="button" tabIndex={0}
          onClick={() => go('you')}
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') go('you') }}>
          <div className="dp-ava">{(name || 'A').slice(0, 1).toUpperCase()}</div>
          <div className="dp-meta"><div className="dp-name">{name || 'You'}</div></div>
          <button className="iconbtn" aria-label="Settings" onClick={e => { e.stopPropagation(); go('settings') }}><Icon name="gear" size={19} /></button>
        </div>
      </div>
    </>
  )
}

/* ---------- library (conversation history) ---------- */
function LibraryPanel({ convs, convId, setConvId, setView, onDelete, refresh }: {
  convs: Conv[]; convId: string | null; setConvId: (id: string | null) => void
  setView: (v: View) => void; onDelete: (id: string) => void; refresh: () => void
}) {
  const [q, setQ] = useState('')
  useEffect(() => { refresh() }, [])
  const rows = convs.filter(c => c.id !== '__draft' && (!q.trim() || c.title.toLowerCase().includes(q.toLowerCase())))
  return (
    <div className="panel">
      <h2>Library</h2>
      <input className="searchbox" autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="Search chats" />
      <div className="convlist">
        {rows.map(c => (
          <button key={c.id} className={`conv${convId === c.id ? ' on' : ''}`}
            onClick={() => { setConvId(c.id); setView('chat') }}>
            {(c.is_build || c.title.startsWith('🔨'))
              ? <span className="type-icon"><Icon name="code" size={13} /></span>
              : <span className="type-dot" aria-hidden="true" />}
            <span className="t">{c.title.replace(/^🔨\s*/, '') || 'New conversation'}</span>
            <span className="del" onClick={e => { e.stopPropagation(); onDelete(c.id) }}><Icon name="trash" size={15} /></span>
          </button>
        ))}
        {rows.length === 0 && <div className="sect">No chats yet</div>}
      </div>
    </div>
  )
}

/* ---------- placeholders ---------- */
function SoonPanel({ title, body }: { title: string; body: string }) {
  return (<div className="panel"><h2>{title}</h2><p className="desc">{body}</p></div>)
}

/* ---------- scheduled AI messages ---------- */
type SchedItem = { id: string; prompt: string; run_at: string; done: boolean }
function ScheduledPanel() {
  const [prompt, setPrompt] = useState('')
  const [when, setWhen] = useState('')
  const [items, setItems] = useState<SchedItem[]>([])
  async function load() {
    try { setItems(await api(`/schedule/${USER}`)) } catch { /* offline */ }
  }
  useEffect(() => { load() }, [])
  async function add() {
    if (!prompt.trim() || !when) return
    try {
      await post('/schedule', { user_id: USER, prompt: prompt.trim(), run_at: new Date(when).toISOString() })
      setPrompt(''); setWhen(''); load()
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Could not schedule.')
    }
  }
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Scheduled</h2>
      <p className="desc">Ask now — AZX replies in a new chat at the set time.</p>
      <div className="toolbar">
        <input value={prompt} maxLength={500} onChange={e => setPrompt(e.target.value)} placeholder="Message for later…" />
        <input type="datetime-local" value={when} onChange={e => setWhen(e.target.value)} aria-label="When" />
        <button className="mini primary" disabled={!prompt.trim() || !when} onClick={add}>Schedule</button>
      </div>
      {items.map(i => (
        <div key={i.id} className="card"><div className="row">
          <div className="grow"><span className="kind">{i.done ? 'done' : new Date(i.run_at).toLocaleString()}</span>{i.prompt}</div>
          {!i.done && <button className="mini danger" onClick={async () => { await api(`/schedule/${i.id}`, { method: 'DELETE' }); load() }}>Cancel</button>}
        </div></div>
      ))}
      {items.length === 0 && <div className="card">Nothing scheduled yet.</div>}
    </div></div>
  )
}

const PLUGINS: { t: string; d: string }[] = [
  { t: 'Calculator', d: 'Quick math inside chat.' },
  { t: 'Notes', d: 'Save and recall notes.' },
  { t: 'Files', d: 'Attach and read files.' },
  { t: 'Code', d: 'Run code snippets.' },
  { t: 'Search', d: 'Look things up on the web.' },
  { t: 'Calendar', d: 'Events and reminders.' },
  { t: 'Goals', d: 'Track progress over time.' },
]
function PluginsPanel() {
  return (
    <div className="panel"><h2>Plugins</h2>
      {PLUGINS.map(p => (<div key={p.t} className="plugrow"><div className="dp-name">{p.t}</div><div className="dp-sub">{p.d}</div></div>))}
    </div>
  )
}

/* ---------- build & run (AI website builder) ---------- */
/* ---------- dark browser preview modal (shared by Build & Run + 🔨 chats).
   Traffic lights are real: red = close, yellow = minimize, green = fullscreen
   (click again to exit). External links open in a new tab instead of turning
   the frame black (X-Frame-Options). ---------- */
function SiteModal({ html, onClose, onDownload }: {
  html: string; onClose: () => void; onDownload?: () => void
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const frameRef = useRef<HTMLIFrameElement>(null)
  const [full, setFull] = useState(false)
  const [min, setMin] = useState(false)
  useEffect(() => {
    const h = () => setFull(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', h)
    return () => document.removeEventListener('fullscreenchange', h)
  }, [])
  /* route in-frame link clicks to a new tab (frame navigation → black screen) */
  useEffect(() => {
    const f = frameRef.current
    if (!f) return
    const route = () => {
      try {
        f.contentDocument?.addEventListener('click', (e: MouseEvent) => {
          const a = (e.target as HTMLElement | null)?.closest?.('a')
          const href = a?.getAttribute('href') || ''
          if (/^https?:/i.test(href)) {
            e.preventDefault()
            window.open(href, '_blank', 'noopener')
          }
        })
      } catch { /* sandboxed — prompt asks for target="_blank" */ }
    }
    f.addEventListener('load', route)
    return () => { f.removeEventListener('load', route) }
  }, [html])
  const toggleFull = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
    else boxRef.current?.requestFullscreen().catch(() => {})
  }
  const toggleMin = () => {
    if (full) document.exitFullscreen().catch(() => {})
    setMin(m => !m)
  }
  return (
    <div className="bmodal-scrim" onClick={onClose}>
      <div className={`bmodal${min ? ' min' : ''}`} ref={boxRef} onClick={e => e.stopPropagation()}>
        <div className="b-top">
          <div className="b-circles">
            <button className="b-c b-ctl" aria-label="Close preview" title="Close" onClick={onClose} />
            <button className="b-c b-ctl" aria-label={min ? 'Restore preview' : 'Minimize preview'} title={min ? 'Restore' : 'Minimize'} onClick={toggleMin} />
            <button className={`b-c b-ctl${full ? ' on' : ''}`} aria-label={full ? 'Exit fullscreen' : 'Fullscreen preview'} title={full ? 'Exit fullscreen' : 'Fullscreen'} onClick={toggleFull} />
          </div>
          <div className="b-url">{min ? 'preview — minimized' : 'preview'}</div>
          <button className="iconbtn" aria-label="Close preview" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
        <iframe ref={frameRef} title="preview" className="b-body" srcDoc={html}
          sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox" />
        {min && <button className="b-restore" onClick={() => setMin(false)}>Restore preview</button>}
        {!min && onDownload && <div className="b-foot"><button className="mini" onClick={onDownload}>Download zip</button></div>}
      </div>
    </div>
  )
}

function BuildPanel({ seed, onConsumed }: { seed?: string; onConsumed?: () => void }) {
  const [prompt, setPrompt] = useState('')
  const [log, setLog] = useState<string[]>(() => {
    try {
      const raw = localStorage.getItem('ashtra-build-v1')
      if (raw) { const d = JSON.parse(raw); if (Array.isArray(d?.log)) return d.log.slice(-50) }
    } catch { /* ignore */ }
    return []
  })
  const [html, setHtml] = useState(() => {
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').html || '' } catch { return '' }
  })
  const [jobId, setJobId] = useState(() => {
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').jobId || '' } catch { return '' }
  })
  const [siteName, setSiteName] = useState(() => {
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').name || '' } catch { return '' }
  })
  /* publish state — the live URL of this build */
  const [pub, setPub] = useState<{ slug: string; url: string; name: string } | null>(() => {
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').pub || null } catch { return null }
  })
  /* arriving from the chat nudge: fresh build chat with the request typed in */
  const seeded = useRef(false)
  useEffect(() => {
    if (!seed || seeded.current) return
    seeded.current = true
    setLog([]); setHtml(''); setJobId(''); setPub(null); setSiteName(''); setPrompt(seed)
    onConsumed?.()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => {
    try {
      localStorage.setItem('ashtra-build-v1', JSON.stringify({
        log: log.slice(-50), html, jobId, name: siteName, pub,
      }))
    } catch { /* quota */ }
  }, [log, html, jobId, siteName, pub])
  const [busy, setBusy] = useState(false)
  const [editBusy, setEditBusy] = useState(false)
  const [pubBusy, setPubBusy] = useState(false)
  const push = (s: string) => setLog(l => [...l.slice(-50), s])
  async function run() {
    const p = prompt.trim()
    if (!p || busy) return
    setBusy(true); setPrompt('')
    // html/jobId are deliberately NOT cleared here — a failed build must not
    // destroy the only copy of the previous site.
    const baseLog = log
    const lines: string[] = []
    const say = (s: string) => { lines.push(s); setLog(l => [...l.slice(-50), s]) }
    const persist = (next: { html?: string; jobId?: string; name?: string }) => {
      try {
        localStorage.setItem('ashtra-build-v1', JSON.stringify({
          log: [...baseLog, ...lines].slice(-50),
          html: next.html ?? html,
          jobId: next.jobId ?? jobId,
          name: next.name ?? siteName,
          pub,
        }))
      } catch { /* quota */ }
    }
    say(`$ ${p.length > 80 ? p.slice(0, 80) + '…' : p}`)
    say('> coding…')
    const t1 = setTimeout(() => say('> polishing…'), 10000)
    const t2 = setTimeout(() => say('> final touches…'), 22000)
    try {
      const j = await api('/build/website', { method: 'POST', body: JSON.stringify({ user_id: USER, prompt: p }) }, 200000) as { status?: string; reply?: string; job_id: string; name: string; template: string; html: string; style_ref?: string }
      if (j.status === 'chat' || !j.job_id) {
        say(j.reply || 'Tell me what website you want built.')
        persist({})
      } else {
        say(`> done: ${j.name}`)
        persist({ html: j.html, jobId: j.job_id, name: j.name })
        setHtml(j.html); setJobId(j.job_id); setSiteName(j.name); setPub(null)
      }
    } catch (e) {
      say(`> error: ${e instanceof Error ? e.message : 'build failed'}`)
      persist({})
    }
    clearTimeout(t1); clearTimeout(t2)
    setBusy(false)
  }
  /* chat-style edit: "make the hero darker" — applied to the current build */
  async function editSite() {
    const ins = prompt.trim()
    if (!ins || !jobId || editBusy) return
    setEditBusy(true); setPrompt('')
    push(`$ ${ins.length > 80 ? ins.slice(0, 80) + '…' : ins}`)
    push('> applying change…')
    try {
      const j = await api('/build/edit', { method: 'POST', body: JSON.stringify({ job_id: jobId, instruction: ins }) }, 200000) as { html: string }
      setHtml(j.html)
      push('> updated — preview refreshed.')
    } catch (e) {
      push(`> edit failed: ${e instanceof Error ? e.message : 'error'}`)
    }
    setEditBusy(false)
  }
  /* publish: make the build live at /build/site/{slug} */
  async function publish() {
    if (!jobId || pubBusy) return
    setPubBusy(true); push('> publishing…')
    try {
      const j = await api('/build/publish', { method: 'POST', body: JSON.stringify({ job_id: jobId, user_id: USER }) }) as { slug: string; url: string; name: string }
      setPub(j)
      push(`> live at ${j.url}`)
    } catch (e) {
      push(`> publish failed: ${e instanceof Error ? e.message : 'error'}`)
    }
    setPubBusy(false)
  }
  async function download() {
    if (!jobId) return
    try {
      const headers: Record<string, string> = {}
      if (API_KEY) headers['X-API-Key'] = API_KEY
      const r = await fetch(API + '/build/zip/' + jobId, { headers })
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const url = URL.createObjectURL(await r.blob())
      const a = document.createElement('a')
      a.href = url; a.download = 'website.zip'; a.click()
      setTimeout(() => URL.revokeObjectURL(url), 5000)
    } catch { /* download failed */ }
  }
  const liveUrl = pub ? API + pub.url : ''
  /* no site yet → describe-and-build screen */
  if (!html) {
    const msgs: Msg[] = log.map(l => l.startsWith('$ ')
      ? { role: 'user', content: l.slice(2) }
      : { role: 'assistant', content: l })
    return (
      <ThreadShell msgs={log.length ? msgs : []} busy={busy} status="Working…"
        empty={<div className="welcome"><h1>Describe your business. Get a live site.</h1>
          <p>AZX codes it, you preview it, then publish with one click.</p></div>}
        composer={
          <ComposerShell
            field={
              <input id="build-input" name="build" value={prompt} maxLength={500}
                onChange={e => setPrompt(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') run() }}
                placeholder="e.g. bakery website for Nix Bakes in Pune…" />
            }
            canSend={!(busy || !prompt.trim())}
            onSend={run} sendLabel="Build" />
        } />
    )
  }
  /* built → preview-first workspace */
  return (
    <div className="bwrap">
      <div className="bprev">
        <iframe title="preview" srcDoc={html}
          sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox" />
      </div>
      <div className="bbar">
        <div className="bbar-meta">
          <div className="bbar-name">{siteName || 'Your site'}</div>
          <div className="bbar-sub">
            <span className="dot" />
            {pub
              ? <a href={liveUrl} target="_blank" rel="noreferrer" className="bbar-live">{liveUrl.replace(/^https?:\/\//, '')}</a>
              : <span>Live preview</span>}
          </div>
        </div>
        <div className="bbar-acts">
          <button className="pub-btn" disabled={pubBusy || !jobId} onClick={publish}>
            {pubBusy ? 'Publishing…' : pub ? 'Update' : 'Publish'}
          </button>
          <button className="mini" onClick={download}>Download ZIP</button>
          <button className="mini" onClick={() => {
            try { localStorage.removeItem('ashtra-build-v1') } catch { /* quota */ }
            setHtml(''); setJobId(''); setPub(null); setSiteName(''); setLog([])
          }}>New build</button>
        </div>
      </div>
      {(log.length > 0 || editBusy) && (
        <div className="blog">
          {log.slice(-6).map((l, i) => (
            <div key={i} className={l.startsWith('$ ') ? 'blogline u' : 'blogline'}>{l}</div>
          ))}
          {editBusy && <div className="blogline">{'>'} applying change…</div>}
        </div>
      )}
      <div className="bcomposer">
        <ComposerShell
          field={
            <input id="build-edit" name="edit" value={prompt} maxLength={500}
              onChange={e => setPrompt(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') editSite() }}
              placeholder="Tell AZX what to change…" />
          }
          canSend={!(editBusy || !prompt.trim() || !jobId)}
          onSend={editSite} sendLabel="Apply change" />
      </div>
    </div>
  )
}
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
      <p className="desc">Everything AZX remembers. Search, delete, reindex, or export.</p>
      <div className="toolbar">
        <input value={q} onChange={e => search(e.target.value)} placeholder="Search memories…" />
      </div>
      {rows.length === 0 && <div className="card">No memories yet. Chat, and AZX will remember.</div>}
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
        <ConfirmDialog title="Forget this memory?" body="AZX will no longer recall it. This cannot be undone."
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
/* ---------- web market: template showcase with live AI editing ---------- */
type MarketItem = { id: string; name: string; description: string }

/* templates ship as fixed-viewport designs (overflow:hidden) — when their
   content doesn't fit the window it clips with no scrollbar. This runs inside
   the preview and unlocks only the containers that actually overflow. */
const MK_SCROLL_FIX = `<script>(function(){function u(){try{document.querySelectorAll('*').forEach(function(el){var cs=getComputedStyle(el);if((cs.overflowY==='hidden'||cs.overflow==='hidden')&&el.scrollHeight>el.clientHeight+16){el.style.setProperty('overflow-y','auto','important');el.style.setProperty('overflow','auto','important');}})}catch(e){}}u();addEventListener('load',u);setTimeout(u,1200);setTimeout(u,4000);setTimeout(u,9000);})();</script>`
function mkScrollable(h: string): string {
  if (h.includes('mk-scroll-fix')) return h
  if (/<\/head>/i.test(h)) return h.replace(/<\/head>/i, MK_SCROLL_FIX + '</head>')
  return MK_SCROLL_FIX + h
}

/* srcDoc iframes resolve relative URLs against the app origin — pin them to the
   template's own directory so built Vite assets (t3/t9) load */
function mkBase(h: string, tid: string): string {
  const base = `<base href="${API}/market/file/${tid}/">`
  if (/<base\s/i.test(h)) return h
  if (/<head[^>]*>/i.test(h)) return h.replace(/<head[^>]*>/i, m => m + base)
  return base + h
}

function MarketFull({ item, onClose, onUse }: { item: MarketItem; onClose: () => void; onUse: (it: MarketItem) => void }) {
  const [html, setHtml] = useState('')
  const [loaded, setLoaded] = useState(false)
  const [note, setNote] = useState('')
  useEffect(() => {
    let dead = false
    ;(async () => {
      try {
        const headers: Record<string, string> = {}
        if (API_KEY) headers['X-API-Key'] = API_KEY
        const r = await fetch(`${API}/market/file/${item.id}/index.html`, { headers })
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const t = await r.text()
        if (!dead) setHtml(mkScrollable(mkBase(t, item.id)))
      } catch (e) {
        if (!dead) setNote(e instanceof Error ? e.message : 'Could not load template.')
      }
      if (!dead) setLoaded(true)
    })()
    return () => { dead = true }
  }, [item.id])
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])
  return (
    <div className="mfull">
      {loaded && html
        ? <iframe className="mfull-frame" title={item.name} srcDoc={html}
            sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox" />
        : <div className="mfull-load">{note ? `Load failed: ${note}` : `Loading ${item.name}…`}</div>}
      <button className="mclose" aria-label="Close preview" onClick={onClose}><Icon name="x" size={22} /></button>
      <div className="mtpl-bar">
        <div className="mtpl-meta">
          <div className="mtpl-name">{item.name}</div>
          <div className="mtpl-desc">{item.description}</div>
        </div>
        <button className="pub-btn" onClick={() => onUse(item)}>Use template</button>
      </div>
    </div>
  )
}

function MarketPanel({ onUse }: { onUse: (it: MarketItem) => void }) {
  const [items, setItems] = useState<MarketItem[]>([])
  const [open, setOpen] = useState<MarketItem | null>(null)
  useEffect(() => {
    let dead = false
    api('/market/list').then((d: MarketItem[]) => { if (!dead && Array.isArray(d)) setItems(d) })
      .catch(() => { /* offline */ })
    return () => { dead = true }
  }, [])
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Templates</h2>
      <p className="desc">Starting points — preview one, then use it in Build &amp; Run.</p>
      <div className="mk-grid">
        {items.map(it => (
          <button key={it.id} className="mk-card" onClick={() => setOpen(it)}>
            <div className="mk-thumb">
              <iframe title={it.name} src={`${API}/market/file/${it.id}/index.html`}
                loading="lazy" tabIndex={-1} sandbox="allow-scripts allow-popups" />
            </div>
            <div className="mk-meta">
              <div className="mk-name">{it.name}</div>
              <div className="mk-desc">{it.description}</div>
            </div>
          </button>
        ))}
        {items.length === 0 && <div className="card">No templates found — is the templates folder reachable?</div>}
      </div>
      {open && <MarketFull item={open} onClose={() => setOpen(null)} onUse={it => { setOpen(null); onUse(it) }} />}
    </div></div>
  )
}

function YouPanel({ name, onName }: { name: string; onName: (v: string) => void }) {
  const [model, setModel] = useState<{
    traits: Record<string, string>; adaptation: Record<string, string | number>;
    directives: string; recent_behaviour_patterns: string[]
  } | null>(null)
  async function load() {
    try { setModel(await api(`/profile/${USER}/model`)) } catch { /* offline */ }
  }
  useEffect(() => { load() }, [])
  async function patch(field: string, value: string) {
    try { await api(`/profile/${USER}`, { method: 'PATCH', body: JSON.stringify({ [field]: value }) }) } catch { /* offline */ }
    load()
  }
  const t = model?.traits ?? {}
  return (
    <div className="panel"><div className="panel-inner">
      <h2>You</h2>
      <p className="desc">How AZX adapts to you. Confidence {String(model?.adaptation.confidence ?? '…')}.</p>
      <div className="lab">What should we call you?</div>
      <input className="namepill" value={name} maxLength={32} placeholder="Type a name…"
        onChange={e => { onName(e.target.value); try { localStorage.setItem('ashtra-name', e.target.value) } catch { /* quota */ } }} />
      <div className="lab">Depth</div>
      <Seg options={['Short', 'Balanced', 'Deep']}
        value={({ concise: 'Short', balanced: 'Balanced', detailed: 'Deep' } as Record<string, string>)[t.explanation_depth ?? 'balanced'] ?? 'Balanced'}
        onPick={v => patch('explanation_depth', ({ Short: 'concise', Balanced: 'balanced', Deep: 'detailed' } as Record<string, string>)[v] ?? 'balanced')} />
      <div className="lab">Tone</div>
      <Seg options={['Casual', 'Formal']}
        value={(t.tone ?? 'casual') === 'formal' ? 'Formal' : 'Casual'}
        onPick={v => patch('tone', v.toLowerCase())} />
      <div className="lab">Format</div>
      <Seg options={['Chat', 'Bullets', 'Tutorial', 'Code-first']}
        value={(t.communication_format ?? 'chat') === 'code-first' ? 'Code-first'
          : (t.communication_format ?? 'chat').replace(/^./, c => c.toUpperCase())}
        onPick={v => patch('communication_format', v.toLowerCase())} />
    </div></div>
  )
}

/* ---------- settings ---------- */
function SettingsPanel({ fx, setFx }: { fx: boolean; setFx: (v: boolean) => void }) {
  const [confirm, setConfirm] = useState(false)
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Settings</h2>
      <div className="lab">Appearance</div>
      <div className="card setrow">
        <span>Animations &amp; effects</span>
        <button className={`toggle${fx ? ' on' : ''}`} role="switch" aria-checked={fx}
          onClick={() => { const v = !fx; setFx(v); try { localStorage.setItem('ashtra-fx', v ? 'on' : 'off') } catch { /* quota */ } }}>
          <i />
        </button>
      </div>
      <div className="lab">Local data</div>
      <div className="card setrow">
        <span>Clear saved chats &amp; drafts on this device</span>
        <button className="mini danger-solid" onClick={() => setConfirm(true)}>Clear</button>
      </div>
      <div className="lab">About</div>
      <div className="card">AZX · v1</div>
      {confirm && (
        <ConfirmDialog title="Clear local data?" body="Saved chats and build drafts stored on this device will be removed."
          confirmLabel="Clear" onCancel={() => setConfirm(false)}
          onConfirm={() => {
            setConfirm(false)
            try {
              localStorage.removeItem('ashtra-sessions-v1')
              localStorage.removeItem('ashtra-build-v1')
            } catch { /* quota */ }
            location.reload()
          }} />
      )}
    </div></div>
  )
}

/* ---------- shell ---------- */
export default function App() {
  const [view, setView] = useState<View>('chat')
  const [convId, setConvId] = useState<string | null>(null)
  const [convs, setConvs] = useState<Conv[]>([])
  const [sideOpen, setSideOpen] = useState(() => window.innerWidth > 760)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)
  const [userName, setUserName] = useState(() => {
    try { return localStorage.getItem('ashtra-name') || '' } catch { return '' }
  })
  /* fresh Build & Run chat seeded from the chat nudge (bump key → remount) */
  const [buildSeed, setBuildSeed] = useState<{ k: number; p: string }>({ k: 0, p: '' })
  /* session store: one entry per chat, survives switching + page reloads */
  const [sessions, setSessions] = useState<Record<string, Sess>>(() => {
    try {
      const raw = localStorage.getItem('ashtra-sessions-v1')
      if (!raw) return {}
      const data = JSON.parse(raw) as Record<string, { msgs: Msg[] }>
      const out: Record<string, Sess> = {}
      for (const [k, v] of Object.entries(data)) {
        if (k === '__draft' || k === 'new' || !Array.isArray(v?.msgs)) continue
        out[k] = { ...blankSess(), msgs: v.msgs.slice(-100).map(m => ({ ...m, fresh: false })) }
      }
      return out
    } catch { return {} }
  })
  useEffect(() => {
    try {
      const keys = Object.keys(sessions).filter(k => k !== '__draft' && k !== 'new').slice(-15)
      const slim: Record<string, { msgs: Msg[] }> = {}
      for (const k of keys) {
        const m = sessions[k]?.msgs
        if (m && m.length) slim[k] = { msgs: m.slice(-100).map(({ role, content, meta }) => ({ role, content, meta })) }
      }
      localStorage.setItem('ashtra-sessions-v1', JSON.stringify(slim))
    } catch { /* quota — skip */ }
  }, [sessions])
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

  async function refreshConvs() {
    try { setConvs(await api(`/chat/conversations/${USER}`)) } catch { /* offline */ }
  }
  /* optimistic sidebar entry so a new chat appears instantly, before the AI replies */
  function addDraftConv(title: string) {
    setConvs(c => [{ id: '__draft', title: title.slice(0, 40) || 'New conversation' },
      ...c.filter(x => x.id !== '__draft')])
  }
  useEffect(() => { refreshConvs() }, [])

  async function delConv(id: string) {
    await api(`/chat/conversations/${USER}/${id}`, { method: 'DELETE' })
    dropSess(id)
    if (convId === id) setConvId(null)
    refreshConvs()
  }
  /* bulk delete from multi-select mode */
  async function delConvs(ids: string[]) {
    for (const id of ids) {
      try { await api(`/chat/conversations/${USER}/${id}`, { method: 'DELETE' }) } catch { /* keep going */ }
      dropSess(id)
    }
    if (convId && ids.includes(convId)) setConvId(null)
    refreshConvs()
  }

  const sessKey = convId ?? 'new'
  const sess = sessions[sessKey] ?? blankSess()
  /* chats (other than the one viewed) still working in background */
  const busyOthers = Object.entries(sessions).filter(([k, s]) => s.busy && k !== sessKey)
  const busyTitle = (k: string) => convs.find(c => c.id === k)?.title || (k === 'new' ? 'new chat' : 'another chat')
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

  /* Build & Run always opens a brand-new build chat (like New chat) */
  function startFreshBuild() {
    try { localStorage.removeItem('ashtra-build-v1') } catch { /* quota */ }
    setBuildSeed(s => ({ k: s.k + 1, p: '' }))
    setView('build'); setSideOpen(false)
  }
  /* header mode selector: Chat ⇄ Build & Run (same workspace, different mode) */
  const [modeOpen, setModeOpen] = useState(false)
  useEffect(() => {
    if (!modeOpen) return
    const h = () => setModeOpen(false)
    window.addEventListener('click', h)
    return () => window.removeEventListener('click', h)
  }, [modeOpen])

  return (
    <div className={fx ? 'ash' : 'ash no-fx'}>
      <Sidebar view={view} setView={setView} convId={convId} setConvId={setConvId}
        convs={convs} onNew={() => { if (sessions['new']?.busy) return; setConvId(null); setView('chat') }}
        onDelete={(id) => setConfirmDel(id)} onDeleteMany={delConvs}
        open={sideOpen} close={() => setSideOpen(false)}
        name={userName} onBuild={startFreshBuild} />
      <div className="main">
        <div className="topbar">
          <button className="burger" onClick={() => setSideOpen(o => !o)} aria-label="Toggle sidebar"><Icon name="menu" size={22} /></button>
          <div className="mode-wrap" onClick={e => e.stopPropagation()}>
            <button className="mode-sel" aria-haspopup="menu" aria-expanded={modeOpen}
              onClick={() => setModeOpen(o => !o)}>
              AZX <span className="mode-caret">⌄</span>
            </button>
            {modeOpen && (
              <div className="mode-menu" role="menu">
                <button role="menuitem" className={view === 'chat' ? 'on' : ''}
                  onClick={() => { setModeOpen(false); setView('chat') }}>
                  {view === 'chat' && <span className="mode-check">✓</span>}<span className="mode-t">Chat</span>
                </button>
                <button role="menuitem" className={view === 'build' ? 'on' : ''}
                  onClick={() => { setModeOpen(false); startFreshBuild() }}>
                  {view === 'build' && <span className="mode-check">✓</span>}<span className="mode-t">Build &amp; Run</span>
                </button>
              </div>
            )}
          </div>
          <button className="iconbtn top-new" aria-label="New chat"
            onClick={() => { if (sessions['new']?.busy) return; setConvId(null); setView('chat') }}>
            <Icon name="plus" size={19} />
          </button>
        </div>
        {view === 'chat' && <Chat key={sessKey} convId={convId} sessKey={sessKey} sess={sess}
          patchSess={patchSess} onNewConv={onNewConv} refreshSidebar={refreshConvs} onDraft={addDraftConv}
          isBuild={convs.find(c => c.id === convId)?.is_build ?? false}
          goBuild={p => { setBuildSeed({ k: Date.now(), p }); setView('build') }} />}
        {view === 'library' && <LibraryPanel convs={convs} convId={convId} setConvId={setConvId} setView={setView} onDelete={(id) => setConfirmDel(id)} refresh={refreshConvs} />}
        {view === 'projects' && <SoonPanel title="Projects" body="Project workspaces are coming soon." />}
        {view === 'scheduled' && <ScheduledPanel />}
        {view === 'plugins' && <PluginsPanel />}
        {view === 'build' && <BuildPanel key={buildSeed.k} seed={buildSeed.p}
          onConsumed={() => setBuildSeed(s => ({ ...s, p: '' }))} />}
        {view === 'chat' && busyOthers.length > 0 && (
          <button className="workpill" onClick={() => {
            const k = busyOthers[0][0]
            setConvId(k === 'new' || k === '__draft' ? null : k); setView('chat')
          }}>
            <span className="dot" />
            AZX is working in {busyTitle(busyOthers[0][0])}
            {busyOthers.length > 1 ? ` (+${busyOthers.length - 1})` : ''}…
          </button>
        )}
        {view === 'memory' && <MemoryPanel />}
        {view === 'goals' && <GoalsPanel />}
        {view === 'you' && <YouPanel name={userName} onName={setUserName} />}
        {view === 'settings' && <SettingsPanel fx={fx} setFx={setFx} />}
        {view === 'market' && <MarketPanel onUse={it => {
          /* Use template → seed Build & Run with the template as reference */
          try { localStorage.removeItem('ashtra-build-v1') } catch { /* quota */ }
          setBuildSeed(s => ({ k: s.k + 1, p: `Customize the "${it.name}" template (id: ${it.id}) for my business: ` }))
          setView('build')
        }} />}
      </div>
      {confirmDel && (
        <ConfirmDialog title="Delete this chat?" body="The conversation and its messages will be removed."
          confirmLabel="Delete" onCancel={() => setConfirmDel(null)}
          onConfirm={() => { const id = confirmDel; setConfirmDel(null); delConv(id) }} />
      )}
    </div>
  )
}
