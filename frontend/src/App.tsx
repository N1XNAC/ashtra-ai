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
/* POST + read a server-sent-events stream (build/edit, build/website) */
async function apiSSE(path: string, body: unknown, timeoutMs: number,
  onEvent: (ev: { type?: string; payload?: unknown; detail?: string; label?: string; text?: string; sources?: unknown }) => void) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (API_KEY) headers['X-API-Key'] = API_KEY
  let r: Response
  try {
    r = await fetch(API + path, { method: 'POST', headers, body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs) })
  } catch (e) {
    console.log('[ASHRA ERROR]', e instanceof Error ? `${e.name}: ${e.message}` : e)
    if (e instanceof DOMException && e.name === 'TimeoutError') throw new ApiError(0, 'Request timed out — the build may still finish; reopen Build & Run shortly.', null)
    throw new ApiError(0, 'Network failed to fetch — check connection / VPN / adblock.', null)
  }
  console.log('[ASHRA] response status:', r.status, path)
  if (!r.ok) await throwFor(r)
  if (!r.body) throw new ApiError(0, 'Empty response from server.', null)
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      for (const line of block.split('\n')) {
        if (!line.startsWith('data: ')) continue
        try { onEvent(JSON.parse(line.slice(6))) } catch { /* partial frame */ }
      }
    }
  }
}
/* backend build phase → human label for the progress row (truthful: what the
   build pipeline is actually doing right now — no fake/repeating fillers) */
const PHASE_LABELS: Record<string, string> = {
  understand: 'reading your request…', plan: 'reading your request…',
  style: 'picking design style…',
  engine: 'prompt engine working…',
  images: 'finding images…',
  generate: 'writing your page…',
  lint: 'checking links & buttons…',
  repair: 'fixing issues…',
  save: 'saving your site…',
  customize: 'customizing template…',
  diff: 'writing patch…', rewrite: 'rewriting full page (slow)…',
  understanding: 'understanding…', searching: 'searching…',
  reading: 'reading sources…', writing: 'writing answer…',
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

/* SSE chat: reads delta/done/error events; returns the final ChatResponse payload.
   onDelta gets the accumulated text so far (caller throttles flushes). */
type ChatPayload = {
  conversation_id?: string; reply?: string
  sources?: unknown[]; tool_calls?: { tool: string }[]
  adaptations_made?: string[]; saved?: string[]
  study?: { visual?: { title: string; thumb: string }[]; visual_svg?: string; visual_cards?: VCards; actions?: string[]; visual_available?: boolean; expect?: string }
}
async function readChatStream(r: Response, onDelta: (acc: string) => void,
  onEvent?: (ev: { type?: string; images?: { title: string; thumb: string }[]; svg?: string; cards?: VCards; markdown?: string }) => void): Promise<ChatPayload> {
  const reader = r.body!.getReader()
  const dec = new TextDecoder()
  let buf = ''
  let acc = ''
  let done: ChatPayload | null = null
  for (;;) {
    const { done: fin, value } = await reader.read()
    if (fin) break
    buf += dec.decode(value, { stream: true })
    let i: number
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const block = buf.slice(0, i); buf = buf.slice(i + 2)
      for (const line of block.split('\n')) {
        if (!line.startsWith('data:')) continue
        let ev: { type?: string; text?: string; payload?: ChatPayload; detail?: string }
        try { ev = JSON.parse(line.slice(5).trim()) } catch { continue }
        if (ev.type === 'delta') { acc += ev.text || ''; onDelta(acc) }
        else if (ev.type === 'done') done = ev.payload ?? null
        else if (ev.type === 'visual') { try { onEvent?.(ev) } catch { /* ignore */ } }
        else if (ev.type === 'visual_svg') { try { onEvent?.(ev) } catch { /* ignore */ } }
        else if (ev.type === 'visual_cards') { try { onEvent?.(ev) } catch { /* ignore */ } }
        else if (ev.type === 'images') { try { onEvent?.(ev) } catch { /* ignore */ } }
        else if (ev.type === 'error') {
          if (acc) return { reply: acc } /* keep the text already on screen */
          throw new ApiError(0, ev.detail || 'Stream failed.', null)
        }
      }
    }
  }
  if (done) return done
  if (acc) return { reply: acc } // connection cut mid-text — keep what arrived
  throw new ApiError(0, 'Backend sent an empty response.', null)
}

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
  stop: (<><rect x="7" y="7" width="10" height="10" rx="2.5" fill="currentColor" stroke="none" /></>),
  expand: (<><path d="M8 3H5a2 2 0 0 0-2 2v3" /><path d="M21 8V5a2 2 0 0 0-2-2h-3" /><path d="M16 21h3a2 2 0 0 0 2-2v-3" /><path d="M3 16v3a2 2 0 0 0 2 2h3" /></>),
  copy: (<><rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></>),
  chat: (<><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" /></>),
  code: (<><path d="m16 18 6-6-6-6M8 6l-6 6 6 6" /></>),
  calendar: (<><rect x="3" y="4" width="18" height="18" rx="2" /><path d="M16 2v4M8 2v4M3 10h18" /></>),
  plus: (<><path d="M12 5v14M5 12h14" /></>),
  mic: (<><rect x="9" y="2.5" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0" /><path d="M12 18v3.5" /></>),
  globe: (<><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3a14.5 14.5 0 0 1 0 18 14.5 14.5 0 0 1 0-18" /></>),
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
const MD_CACHE = new Map<string, string>()
function renderMd(src: string) {
  const hit = MD_CACHE.get(src)
  if (hit !== undefined) return hit
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
  if (MD_CACHE.size > 500) MD_CACHE.clear()
  MD_CACHE.set(src, html)
  return html
}

/* ---------- types ---------- */
type View = 'chat' | 'library' | 'projects' | 'plugins' | 'build' | 'market' | 'memory' | 'you' | 'settings' | 'answer'
type VCards = { title: string; flow: boolean; items: { icon: string; label: string; text: string }[] }
type Msg = { role: string; content: string; meta?: string; fresh?: boolean; attachment?: string; saved?: string[]; study?: { visual?: { title: string; thumb: string }[]; visual_svg?: string; visual_cards?: VCards; actions?: string[]; visual_available?: boolean; expect?: string } }
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
/* Home 2x2 action grid — REAL AZX features only (reference geometry, honest labels).
   Build & Run stays discoverable here and in the drawer; no invented features. */
const HOME_ACTIONS = [
  { icon: 'code', label: 'Build & Run', act: 'build' },
  { icon: 'globe', label: 'Web Search', act: '/websearch ' },
  { icon: 'books', label: 'Study & Learn', act: '/study ' },
  { icon: 'search', label: 'Answers', act: 'answer' },
] as const

/* assistant identity orb — home + empty states (real SVG, no image asset) */
function Orb() {
  return (
    <div className="orb" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2c.7 5.1 4.2 8.6 9.3 9.3-5.1.7-8.6 4.2-9.3 9.3-.7-5.1-4.2-8.6-9.3-9.3C7.8 10.6 11.3 7.1 12 2Z" /></svg>
    </div>
  )
}

/* ---------- shared composer shell: black pill control row,
   identical on every screen — plus · input · mic · send ---------- */

/* real dictation via the browser Web Speech API (no fake loading states);
   the button only renders where the API actually exists */
type DictationCtor = new () => {
  lang: string; interimResults: boolean; maxAlternatives: number
  onresult: ((e: { results: { [i: number]: { [j: number]: { transcript: string } } } }) => void) | null
  onerror: (() => void) | null
  onend: (() => void) | null
  start: () => void; stop: () => void
}
function useDictation(onText: (t: string) => void) {
  const [listening, setListening] = useState(false)
  const recRef = useRef<InstanceType<DictationCtor> | null>(null)
  const w = typeof window !== 'undefined'
    ? window as unknown as { SpeechRecognition?: DictationCtor; webkitSpeechRecognition?: DictationCtor }
    : undefined
  const Ctor = w?.SpeechRecognition ?? w?.webkitSpeechRecognition
  useEffect(() => () => { try { recRef.current?.stop() } catch { /* ignore */ } }, [])
  const toggle = () => {
    if (!Ctor) return
    if (listening) { try { recRef.current?.stop() } catch { /* ignore */ } setListening(false); return }
    const rec = new Ctor()
    rec.lang = navigator.language || 'en-US'
    rec.interimResults = false
    rec.maxAlternatives = 1
    rec.onresult = e => { const t = e?.results?.[0]?.[0]?.transcript; if (t) onText(t) }
    rec.onerror = () => setListening(false)
    rec.onend = () => setListening(false)
    recRef.current = rec
    setListening(true)
    try { rec.start() } catch { setListening(false) }
  }
  return { supported: !!Ctor, listening, toggle }
}

function ComposerShell({ lead, field, canSend, onSend, sendLabel, head, streaming, onStop, onVoice }: {
  lead?: ReactNode; field: ReactNode; canSend: boolean
  onSend: () => void; sendLabel: string; boxExtra?: string; head?: ReactNode
  streaming?: boolean; onStop?: () => void; onVoice?: (transcript: string) => void
}) {
  const dictation = useDictation(t => onVoice?.(t))
  return (
    <div className="composer">
      {head}
      <div className="uv-chatbot">
        <div className="uv-chat-options">
          <div className="uv-chat">
            <div className="uv-btns-add uv-lead">{lead}</div>
            <div className="uv-chat-bot">{field}</div>
            <div className="uv-options">
              {onVoice && dictation.supported && (
                <button type="button" className={`uv-mic${dictation.listening ? ' on' : ''}`}
                  aria-pressed={dictation.listening}
                  aria-label={dictation.listening ? 'Stop dictation' : 'Dictate with your voice'}
                  onClick={dictation.toggle}>
                  <i><Icon name={dictation.listening ? 'stop' : 'mic'} size={17} /></i>
                </button>
              )}
              {streaming ? (
                <button className="uv-submit stop" onClick={onStop} aria-label="Stop generating">
                  <i><Icon name="stop" size={16} /></i>
                </button>
              ) : canSend ? (
                <button className="uv-submit ready" onClick={onSend} aria-label={sendLabel}>
                  <i><Icon name="up" size={18} /></i>
                </button>
              ) : null /* empty state matches the reference: mic only, no dimmed send */}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/* ---------- liquid-glass segmented pills (aspect switcher, fullscreen) ---------- */
function PillSeg({ options, value, onChange, ariaLabel }: {
  options: string[]; value: string; onChange: (v: string) => void; ariaLabel?: string
}) {
  const i = Math.max(0, options.indexOf(value))
  return (
    <div className="pillseg" role="tablist" aria-label={ariaLabel}>
      <span className="pillseg-thumb" aria-hidden
        style={{ width: `calc((100% - 6px) / ${options.length})`, transform: `translateX(${i * 100}%)` }} />
      {options.map(o => (
        <button key={o} role="tab" aria-selected={o === value} className={o === value ? 'on' : ''}
          onClick={() => onChange(o)}>{o}</button>
      ))}
    </div>
  )
}

/* ---------- shared thread shell: Chat AND Build & Run render through this,
   so both screens are pixel-identical ---------- */
function ThreadShell({ msgs, busy, status, empty, composer, footer, actionIndex, action, onAction }: {
  msgs: Msg[] | null; busy: boolean; status: string
  empty: ReactNode; composer: ReactNode; footer?: ReactNode
  actionIndex?: number | null; action?: ReactNode
  onAction?: (cmd: string) => void
}) {
  const bottomRef = useRef<HTMLDivElement>(null)
  /* instant while streaming (smooth would queue up and jank), smooth otherwise */
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: busy ? 'auto' : 'smooth' }) }, [msgs, busy])
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
                {(() => { const sm = m.content.match(/^\/(\w+)\s+([\s\S]*)$/); return sm ? (<><span className="slashchip">/{sm[1]}</span> {sm[2]}</>) : m.content })()}
              </div>
            </div>
          ) : (
            <div key={i} className={m.fresh ? 'msg fresh' : 'msg'}>
              <div className="body">
                <AssistantBody m={m} onAction={c => onAction?.('/' + c)} />
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
const BUILD_HINT_RE = /(^|\b)(build|create|make|design|develop|generate)\b[\s\S]{0,30}\b(site|website|webpage|web\s?page|landing|portfolio|blog|store|shop|web\s?app)\b|\b(code|develop)\b[\s\S]{0,20}\b(website|web\s?app|site)\b|\b(build|make|create)\s+(me\s+)?a\s+(site|website|web\s?page)\b/i

/* ---------- slash commands (/study …) ---------- */
const SLASH_COMMANDS = [
  { cmd: 'study', hint: '<topic>', desc: 'Study mode — explainer first, then pick flashcards, quiz, visuals' },
  { cmd: 'flashcards', hint: '[topic]', desc: 'Flashcards for the topic — saved to notes' },
  { cmd: 'quiz', hint: '[topic]', desc: 'Quiz me — 3 questions, reply to score' },
  { cmd: 'visuals', hint: '[topic]', desc: 'Show pictures and diagrams' },
  { cmd: 'websearch', hint: '<query>', desc: 'Search the web — cited answer with sources' },
]
function parseStudy(content: string, expect = ''): { body: string; flashcards: { q: string; a: string }[]; quiz: string[] } {
  const clean = (s: string) => s.replace(/^[\d.)\-\*\s>]+/, '').replace(/^[`*"']+|[`*"']+$/g, '').trim()
  const lines = content.split('\n')
  const isFc = (l: string) => /^\s*\**\s*FLASHCARDS?\s*\**:?\s*$/.test(l)
  const isQz = (l: string) => /^\s*\**\s*QUIZ(ZES)?\s*\**:?\s*$/.test(l)
  let fi = -1, qi = -1
  lines.forEach((l, i) => { if (fi < 0 && isFc(l)) fi = i; else if (qi < 0 && isQz(l)) qi = i })
  const parseFc = (ls: string[]) => {
    const out: { q: string; a: string }[] = []
    for (const line of ls) {
      const t = line.trim()
      if (t.length < 3) continue
      let q = '', a = ''
      if (t.includes('|')) { const p = t.split('|'); q = p[0]; a = p.slice(1).join('|') }
      else {
        const m = t.match(/^(.*?\?)\s*(?:A\s*:\s*)?(.+)$/) || t.match(/^(.*?)\bA\s*:\s*(.+)$/i)
        if (!m) continue
        q = m[1]; a = m[2]
      }
      q = clean(q.replace(/^Q\s*:\s*/i, '')); a = clean(a.replace(/^A\s*:\s*/i, ''))
      if (q && a) out.push({ q: q.slice(0, 200), a: a.slice(0, 300) })
    }
    return out.slice(0, 6)
  }
  const parseQz = (ls: string[]) => {
    const out: string[] = []
    for (const line of ls) {
      const t = clean(line)
      if (t.length >= 4 && !/^(quiz|flashcards?)\b/i.test(t)) out.push(t.slice(0, 250))
      if (out.length >= 5) break
    }
    return out.slice(0, 5)
  }
  if (fi < 0 && qi < 0) {
    // format-only replies often skip the header — recover by shape
    if (expect === 'flashcards') {
      const bare = parseFc(lines)
      if (bare.length) return { body: '', flashcards: bare, quiz: [] }
    }
    if (expect === 'quiz') {
      const numbered = lines.map(l => l.trim()).filter(l => /^[\d]+[.)]\s+\S/.test(l)).map(l => clean(l).slice(0, 250)).slice(0, 5)
      if (numbered.length) return { body: '', flashcards: [], quiz: numbered }
    }
    return { body: content, flashcards: [], quiz: [] }
  }
  const section = (idx: number) => {
    const next = [fi, qi].filter(x => x > idx)
    return lines.slice(idx + 1, next.length ? Math.min(...next) : lines.length)
  }
  const first = [fi, qi].filter(x => x >= 0)
  const body = lines.slice(0, Math.min(...first)).join('\n').trim()
  return {
    body,
    flashcards: fi >= 0 ? parseFc(section(fi)) : [],
    quiz: qi >= 0 ? parseQz(section(qi)) : [],
  }
}
function VisualCards({ cards }: { cards: VCards }) {
  return (
    <div className="vcards">
      {cards.title ? <div className="vcap">{cards.title}</div> : null}
      <div className={`vlist${cards.flow ? ' flow' : ''}`}>
        {cards.items.map((it, i) => (
          <div key={i} className="vcard" style={{ animationDelay: `${Math.min(i, 8) * 90}ms` }}>
            {cards.flow && <span className="vnum">{i + 1}</span>}
            <span className="vicon">{it.icon}</span>
            <span className="vbody"><b>{it.label}</b><span>{it.text}</span></span>
          </div>
        ))}
      </div>
    </div>
  )
}
function SvgBlock({ svg }: { svg: string }) {  // Pure animation (SMIL/CSS) renders inline. Anything scripted — inline
  // <script>, on* handlers, javascript: URIs — runs isolated in a sandbox
  // frame: JS executes but cannot touch the app (no allow-same-origin).
  const active = /<script[\s>]|\son\w+\s*=|javascript\s*:/i.test(svg)
  if (active) {
    return <iframe className="svgframe" title="interactive visual" sandbox="allow-scripts" srcDoc={svg} />
  }
  return <div className="svgblock" dangerouslySetInnerHTML={{ __html: svg }} />
}
function FlashCard({ q, a }: { q: string; a: string }) {  const [flip, setFlip] = useState(false)
  return (
    <button className={`fcard${flip ? ' flip' : ''}`} onClick={() => setFlip(v => !v)} title="Tap to flip">
      <span className="fq">{q}</span>
      <span className="fa">{flip ? a : 'Tap to reveal…'}</span>
    </button>
  )
}
const ACTION_LABELS: Record<string, string> = { flashcards: 'Flashcards', quiz: 'Quiz me', visuals: 'Show visuals' }
function AssistantBody({ m, onAction }: { m: Msg; onAction: (cmd: string) => void }) {
  const visual = m.study?.visual
  const svg = m.study?.visual_svg
  const cards = m.study?.visual_cards
  const actions = m.study?.actions ?? []
  const st = parseStudy(m.content, m.study?.expect ?? '')
  const marked = (visual?.length ?? 0) > 0 || !!svg || !!cards || st.flashcards.length > 0 || st.quiz.length > 0 || actions.length > 0
  if (!marked) return <div className="md" dangerouslySetInnerHTML={{ __html: renderMd(m.content) }} />
  return (<>
    {st.body ? <div className="md" dangerouslySetInnerHTML={{ __html: renderMd(st.body) }} /> : null}
    {cards ? <VisualCards cards={cards} /> : null}
    {svg ? <SvgBlock svg={svg} /> : null}
    {visual && visual.length > 0 && (
      <div className="scards">
        {visual.map((v, i) => (
          <figure key={i} className="scard">
            <img src={v.thumb} alt={v.title} loading="lazy" />
            <figcaption>{v.title}</figcaption>
          </figure>
        ))}
      </div>
    )}
    {st.flashcards.length > 0 && (
      <div className="fcards">
        <div className="fcap">Flashcards — saved to notes, tap to flip</div>
        {st.flashcards.map((f, i) => <FlashCard key={i} q={f.q} a={f.a} />)}
      </div>
    )}
    {st.quiz.length > 0 && (
      <div className="quizblock">
        <div className="fcap">Quick quiz — reply with your answers</div>
        <ol>{st.quiz.map((q, i) => <li key={i}>{q}</li>)}</ol>
      </div>
    )}
    {actions.length > 0 && (
      <div className="studyacts">
        {actions.map(a => (
          <button key={a} className="mini" onClick={() => onAction(a)}>{ACTION_LABELS[a] ?? a}</button>
        ))}
      </div>
    )}
  </>)
}

function Chat({ convId, sessKey, sess, patchSess, onNewConv, refreshSidebar, onDraft, isBuild, goBuild, name, onAnswer }: {
  convId: string | null; sessKey: string; sess: Sess
  patchSess: (key: string, p: Partial<Sess>) => void
  onNewConv: (id: string) => void; refreshSidebar: () => void; onDraft: (title: string) => void
  isBuild?: boolean; goBuild: (prompt: string) => void
  name?: string; onAnswer?: () => void
}) {
  const { msgs: cached, input, busy, busyLabel, err, attach } = sess
  const [morph, setMorph] = useState(false) /* gooey send-button stretch, one shot per send */
  const [streaming, setStreaming] = useState(false) /* send button ⇄ stop while the reply streams */
  const abortRef = useRef<AbortController | null>(null)
  const [deep, setDeep] = useState(() => {
    try { return localStorage.getItem('ashtra-deep') === 'on' } catch { return false }
  })
  /* build chats open in the Build & Run workspace — Chat is plain chat only */
  /* honest status: what the request is actually doing (no fake rotating labels) */
  const lastMsg = cached && cached.length ? cached[cached.length - 1] : null
  const aiStatus = !busy ? '' : busyLabel || ((lastMsg?.role === 'assistant' && lastMsg.content) ? 'Writing…' : 'Thinking…')
  /* site-building intent → gentle nudge to Build & Run (once per send attempt) */
  const [buildHint, setBuildHint] = useState<string | null>(null)
  const hintDone = useRef(false)
  /* slash commands: /stud… → popup, Tab/↑↓ to pick */
  const [slashIdx, setSlashIdx] = useState(0)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

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
    /* advisory only: the message always sends — Build & Run is just a suggestion */
    if (!img && !isBuild && !hintDone.current && BUILD_HINT_RE.test(content)) setBuildHint(content)
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
    let cap = 0
    try {
      console.log('[ASHRA] sending message')
      /* Stream first: deltas render as they arrive (TTFT ≈ first token), and the
         done event carries the same ChatResponse payload as the JSON path.
         A non-SSE answer (older backend) falls back to plain JSON. */
      const payload = { user_id: USER, conversation_id: convId, message: content, image_context, deep_thinking: deep }
      const sh: Record<string, string> = { 'Content-Type': 'application/json' }
      if (API_KEY) sh['X-API-Key'] = API_KEY
      const ac = new AbortController()
      abortRef.current = ac
      cap = window.setTimeout(() => ac.abort(), 120000)
      setStreaming(true)
      const r = await fetch(API + '/chat', {
        method: 'POST', headers: sh,
        body: JSON.stringify({ ...payload, stream: true }),
        signal: ac.signal,
      })
      if (!r.ok) await throwFor(r)
      let j: ChatPayload
      let studyVisual: { title: string; thumb: string }[] | null = null
      let studySvg: string | null = null
      let studyCards: VCards | null = null
      let lateImagesMd = ''
      if ((r.headers.get('content-type') || '').includes('text/event-stream') && r.body) {
        let last = 0
        j = await readChatStream(r, acc => {
          const now = performance.now()
          if (now - last < 60) return /* batch flushes so React re-renders stay cheap */
          last = now
          patchSess(key, { msgs: [...base, userMsg, { role: 'assistant', content: acc, fresh: true }], busyLabel: '' })
        }, ev => { if (ev.type === 'visual' && Array.isArray(ev.images)) studyVisual = ev.images; if (ev.type === 'visual_svg' && typeof ev.svg === 'string') studySvg = ev.svg; if (ev.type === 'visual_cards' && ev.cards && typeof ev.cards === 'object') studyCards = ev.cards as VCards; if (ev.type === 'images' && typeof ev.markdown === 'string') lateImagesMd += ev.markdown })
      } else {
        j = await r.json()
      }
      console.log('[ASHRA] response parsed')
      if (!j || typeof j.reply !== 'string' || !j.reply.trim()) {
        console.log('[ASHRA ERROR] invalid response shape', JSON.stringify(j)?.slice(0, 200))
        throw new Error('Invalid response from backend (missing reply).')
      }
      console.log('[ASHRA] rendering response')
      const jConv = typeof j.conversation_id === 'string' ? j.conversation_id : ''
      const targetKey = !convId && jConv ? jConv : key
      if (!convId && jConv) { onNewConv(jConv); refreshSidebar() }
      const meta: string[] = []
      const fullReply = (j.reply || '') + lateImagesMd
      if (sawImage) meta.push('saw image')
      if (j.sources?.length) meta.push(`recalled ${j.sources.length}`)
      if (j.tool_calls?.length) meta.push('used ' + j.tool_calls.map((c: { tool: string }) => c.tool).join(', '))
      if (/\!\[[^\]]*\]\(https?:/.test(fullReply)) meta.push('images')
      if (j.adaptations_made?.length) meta.push('adapted')
      patchSess(targetKey, {
        msgs: [...base, userMsg, { role: 'assistant', content: fullReply, meta: meta.join(' · ') || undefined, fresh: true, saved: Array.isArray(j.saved) && j.saved.length ? j.saved : undefined, study: (() => {
          const st: NonNullable<Msg['study']> = {}
          if (studyVisual) st.visual = studyVisual
          else if (j.study?.visual?.length) st.visual = j.study.visual
          if (studySvg) st.visual_svg = studySvg
          else if (j.study?.visual_svg) st.visual_svg = j.study.visual_svg
          if (studyCards) st.visual_cards = studyCards
          else if (j.study?.visual_cards) st.visual_cards = j.study.visual_cards
          if (j.study?.actions?.length) st.actions = j.study.actions
          if (typeof j.study?.visual_available === 'boolean') st.visual_available = j.study.visual_available
          if (typeof j.study?.expect === 'string') st.expect = j.study.expect
          return Object.keys(st).length ? st : undefined
        })() }],
        busy: false, busyLabel: '',
      })
      console.log('[ASHRA] generation complete')
    } catch (e) {
      const aborted = (e instanceof Error || e instanceof DOMException) && e.name === 'AbortError'
      if (aborted) {
        /* user pressed Stop: keep the partial reply that already streamed in */
        console.log('[ASHRA] stopped by user')
        patchSess(key, { busy: false, busyLabel: '', err: '' })
      } else {
        const msg = e instanceof Error ? e.message : 'Send failed.'
        console.log('[ASHRA ERROR]', msg)
        patchSess(key, {
          msgs: [...base, userMsg, { role: 'assistant', content: 'Sorry — ' + msg, fresh: true }],
          err: msg, busy: false, busyLabel: '',
        })
      }
    } finally {
      if (cap) window.clearTimeout(cap)
      abortRef.current = null
      setStreaming(false)
    }
  }

  function stopGen() {
    abortRef.current?.abort()
  }

  /* slash autocomplete: "/st" → [study] */
  const slashQ = (() => { const m = /^\s*\/(\w*)$/.exec(input); return m ? m[1].toLowerCase() : null })()
  const slashOpts = slashQ === null ? [] : SLASH_COMMANDS.filter(c => c.cmd.startsWith(slashQ))
  const sIdx = slashOpts.length ? Math.min(slashIdx, slashOpts.length - 1) : 0
  const completeSlash = (cmd: string) => {
    patchSess(sessKey, { input: `/${cmd} ` }); setSlashIdx(0)
    requestAnimationFrame(() => { taRef.current?.focus(); autosize() })
  }

  function runHome(act: string) {
    if (act === 'build') { goBuild(''); return }
    if (act === 'answer') { onAnswer?.(); return }
    patchSess(sessKey, { input: act })
    requestAnimationFrame(() => { taRef.current?.focus(); autosize() })
  }
  const who = (name ?? '').trim() || 'there'
  const welcome = (
    <div className="welcome">
      <Orb />
      <h1>Hi {who}, how can I help you today?</h1>
      <div className="wspace" />
      <div className="suggest" role="list">
        {HOME_ACTIONS.map(a => (
          <button key={a.label} className="sug" role="listitem" onClick={() => runHome(a.act)}>
            <span className="sug-ic"><Icon name={a.icon} size={18} /></span>
            <span>{a.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
  return (
    <ThreadShell msgs={cached} busy={busy} status={aiStatus} empty={welcome}
      onAction={c => send(c)}
      composer={<ComposerShell
        head={<>
          {buildHint !== null && (
            <div className="buildhint" role="dialog" aria-label="Use Build and Run">
              <div className="bh-t">Building a website?</div>
              <div className="bh-b">Coding sites is what <b>Build &amp; Run</b> is for — it builds, previews and hands you the zip.</div>
              <div className="bh-a">
                <button className="mini" onClick={() => { setBuildHint(null); hintDone.current = true }}>Dismiss</button>
                <button className="mini primary" onClick={() => { const t = buildHint; setBuildHint(null); hintDone.current = true; goBuild(t) }}>Go to Build &amp; Run</button>
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
          {slashOpts.length > 0 && (
            <div className="slashpop slashpop-top" role="listbox" aria-label="Commands">
              {slashOpts.map((c, i) => (
                <button key={c.cmd} role="option" aria-selected={i === sIdx}
                  className={`slashitem${i === sIdx ? ' on' : ''}`}
                  onMouseDown={e => e.preventDefault()}
                  onClick={() => completeSlash(c.cmd)}>
                  <span className="slashcmd">/{c.cmd}</span>
                  <span className="slashhint">{c.hint}</span>
                  <span className="slashdesc">{c.desc}</span>
                </button>
              ))}
            </div>
          )}
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
            onKeyDown={e => {
              if (slashOpts.length > 0) {
                if (e.key === 'ArrowDown') { e.preventDefault(); setSlashIdx(i => (i + 1) % slashOpts.length); return }
                if (e.key === 'ArrowUp') { e.preventDefault(); setSlashIdx(i => (i - 1 + slashOpts.length) % slashOpts.length); return }
                if (e.key === 'Tab') { e.preventDefault(); completeSlash(slashOpts[sIdx].cmd); return }
              }
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
            }}
            placeholder="Ask anything…" />
        }
        canSend={!(busy || cached === null || (!input.trim() && !attach))}
        onSend={() => send()} sendLabel="Send"
        streaming={streaming} onStop={stopGen}
        onVoice={t => {
          const cur = input.trimEnd()
          patchSess(sessKey, { input: (cur ? cur + ' ' : '') + t })
          requestAnimationFrame(() => { taRef.current?.focus(); autosize() })
        }} />
      }
      footer={<div className="hint">AZX can make mistakes. Memories are transparent and exportable.</div>} />
  )
}

/* ---------- sidebar ---------- */
function Sidebar({ view, setView, convId, setConvId, convs, onNew, onDelete, onDeleteMany, open, close, name, onBuild, onOpenBuild }: {
  view: View; setView: (v: View) => void
  convId: string | null; setConvId: (id: string | null) => void
  convs: Conv[]; onNew: () => void; onDelete: (id: string) => void
  onDeleteMany: (ids: string[]) => void
  open: boolean; close: () => void
  name: string; onBuild: () => void; onOpenBuild: (convId: string) => void
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
            <button className="iconbtn plain" aria-label="Close menu" onClick={close}><Icon name="x" size={21} /></button>
          </div>
        </div>
        <nav className="mnav">
          {row('New chat', 'pen', false, () => { onNew(); close() })}
          {row('Build & Run', 'code', view === 'build', onBuild)}
          {row('Templates', 'grid', view === 'market', () => go('market'))}
          {row('Answers', 'search', view === 'answer', () => go('answer'))}
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
              className={`conv${convId === c.id && (view === 'chat' || view === 'build') && !selMode ? ' on' : ''}${selMode ? ' selmode' : ''}${sel.has(c.id) ? ' sel' : ''}`}
              onPointerDown={() => startPress(c.id)}
              onPointerUp={cancelPress} onPointerLeave={cancelPress} onPointerCancel={cancelPress}
              onContextMenu={e => { e.preventDefault(); cancelPress(); if (!selMode) { setSelMode(true); setSel(new Set([c.id])) } }}
              onClick={() => {
                if (selMode) { toggleSel(c.id); return }
                close()
                /* build chats reopen in the Build & Run workspace (preview-first) */
                if (isBuildConv(c)) { onOpenBuild(c.id); return }
                setConvId(c.id); setView('chat')
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
        <div className="drawer-profile" role="button" tabIndex={0}
          onClick={() => go('you')}
          onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') go('you') }}>
          <div className="dp-ava">{(name || 'P').slice(0, 1).toUpperCase()}</div>
          <div className="dp-meta"><div className="dp-name">{name || 'Profile'}</div></div>
          <button className="iconbtn plain" aria-label="Settings" onClick={e => { e.stopPropagation(); go('settings') }}><Icon name="gear" size={19} /></button>
        </div>
      </div>
    </>
  )
}

/* ---------- library (conversation history) ---------- */
function LibraryPanel({ convs, convId, setConvId, setView, onDelete, refresh, onOpenBuild }: {
  convs: Conv[]; convId: string | null; setConvId: (id: string | null) => void
  setView: (v: View) => void; onDelete: (id: string) => void; refresh: () => void
  onOpenBuild: (convId: string) => void
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
            onClick={() => {
              if (c.is_build || c.title.startsWith('🔨')) { onOpenBuild(c.id); return }
              setConvId(c.id); setView('chat')
            }}>
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
/* Real device viewport sizes (CSS px) for the preview aspect switcher —
   the frame renders at exact 1:1 device size, scaled to fit the panel. */
const DEV_SIZES = { mobile: [390, 844], tablet: [820, 1180], desktop: [1440, 900] } as const
/* ---------- dark browser preview modal (shared by Build & Run + 🔨 chats).
   Traffic lights are real: red = close, yellow = minimize, green = fullscreen
   (click again to exit). External links open in a new tab instead of turning
   the frame black (X-Frame-Options). ---------- */
function BuildPanel({ seed, onConsumed, conversationId, onBuilt, onNewBuild }: {
  seed?: string; onConsumed?: () => void
  conversationId?: string | null; onBuilt?: (convId: string) => void
  onNewBuild?: () => void
}) {
  const [prompt, setPrompt] = useState('')
  const [log, setLog] = useState<string[]>(() => {
    if (conversationId) return [] /* server is the source of truth for saved builds */
    try {
      const raw = localStorage.getItem('ashtra-build-v1')
      if (raw) { const d = JSON.parse(raw); if (Array.isArray(d?.log)) return d.log.slice(-50) }
    } catch { /* ignore */ }
    return []
  })
  const [html, setHtml] = useState(() => {
    if (conversationId) return ''
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').html || '' } catch { return '' }
  })
  const [jobId, setJobId] = useState(() => {
    if (conversationId) return ''
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').jobId || '' } catch { return '' }
  })
  const [siteName, setSiteName] = useState(() => {
    if (conversationId) return ''
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').name || '' } catch { return '' }
  })
  /* publish state — the live URL of this build */
  const [pub, setPub] = useState<{ slug: string; url: string; name: string } | null>(() => {
    if (conversationId) return null
    try { return JSON.parse(localStorage.getItem('ashtra-build-v1') || '{}').pub || null } catch { return null }
  })
  /* reopening a saved build chat: hydrate from the server (survives refresh).
     No ref-guard: StrictMode double-invokes effects and a guard would leave
     the first (cancelled) run as the only one, pinning the loading state. */
  const [loadingConv, setLoadingConv] = useState(!!conversationId)
  useEffect(() => {
    if (!conversationId) return
    if (html && jobId) return /* fresh local build — keep its chat log, don't refetch/flash */
    setLoadingConv(true)
    let dead = false
    ;(async () => {
      try {
        const d = await api(`/build/conversation/${conversationId}`, {}, 30000)
        if (dead) return
        setHtml(d.html || ''); setJobId(d.job_id || ''); setSiteName(d.name || ''); setPub(d.pub || null)
        /* saved builds have no client log — seed one line so chat isn't empty */
        setLog(prev => (prev.length ? prev : [`> Built: ${d.name || 'your site'}`]))
      } catch { /* no build stored */ }
      if (!dead) setLoadingConv(false)
    })()
    return () => { dead = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId])
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
    if (conversationId) return /* saved builds live on the server, not in the draft slot */
    try {
      localStorage.setItem('ashtra-build-v1', JSON.stringify({
        log: log.slice(-50), html, jobId, name: siteName, pub,
      }))
    } catch { /* quota */ }
  }, [log, html, jobId, siteName, pub, conversationId])
  const [busy, setBusy] = useState(false)
  const [editBusy, setEditBusy] = useState(false)
  const [pubBusy, setPubBusy] = useState(false)
  /* post-build workspace: pill Chat ⇄ Preview — Preview ALWAYS opens the
     fullscreen device overlay; closing it (X / Esc) returns to Chat */
  const [mode, setMode] = useState<'chat' | 'preview'>('chat')
  const [aspect, setAspect] = useState<'mobile' | 'tablet' | 'desktop'>(() => {
    try {
      const v = localStorage.getItem('ashtra-aspect')
      if (v === 'mobile' || v === 'tablet' || v === 'desktop') return v
    } catch { /* ignore */ }
    return 'desktop'
  })
  useEffect(() => {
    try { localStorage.setItem('ashtra-aspect', aspect) } catch { /* quota */ }
  }, [aspect])
  /* fullscreen preview: device at 1:1 scaled to the window, Esc closes */
  const [fsScale, setFsScale] = useState(1)
  useEffect(() => {
    if (mode !== 'preview') return
    const [w, h] = DEV_SIZES[aspect]
    const fit = () => setFsScale(Math.min((window.innerWidth - 48) / w, (window.innerHeight - 120) / h, 1))
    fit()
    window.addEventListener('resize', fit)
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMode('chat') }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('resize', fit); window.removeEventListener('keydown', onKey) }
  }, [mode, aspect])
  /* build/edit progress: elapsed timer + live phase label */
  const [phase, setPhase] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const elapsedRef = useRef<number | null>(null)
  const startProg = () => {
    setElapsed(0); setPhase('starting…')
    const t0 = Date.now()
    if (elapsedRef.current !== null) clearInterval(elapsedRef.current)
    elapsedRef.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 500)
  }
  const stopProg = () => {
    if (elapsedRef.current !== null) { clearInterval(elapsedRef.current); elapsedRef.current = null }
    setPhase(null)
  }
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
    /* live progress = one truthful status line (phase events) — no canned
       "coding…/polishing…" messages stacked into the log */
    startProg()
    try {
      const res: {
        j: { status?: string; reply?: string; job_id: string; name: string; template: string; html: string; style_ref?: string; conversation_id?: string } | null
        err: string | null
      } = { j: null, err: null }
      await apiSSE('/build/website', { user_id: USER, prompt: p }, 300000, (ev) => {
        if (ev.type === 'done') res.j = ev.payload as typeof res.j
        if (ev.type === 'error') res.err = String(ev.detail ?? 'build failed')
        if (ev.type === 'phase' && ev.label) setPhase(PHASE_LABELS[ev.label] ?? ev.label)
      })
      if (res.err) throw new Error(res.err)
      const j = res.j
      if (!j) throw new Error('build ended without a result — try again')
      if (j.status === 'chat' || !j.job_id) {
        say(j.reply || 'Tell me what website you want built.')
        persist({})
      } else {
        say(`> Built: ${j.name}`)
        persist({ html: j.html, jobId: j.job_id, name: j.name })
        setHtml(j.html); setJobId(j.job_id); setSiteName(j.name); setPub(null)
        setMode('preview') /* fresh build → jump straight into fullscreen preview */
        if (j.conversation_id) onBuilt?.(j.conversation_id)
      }
    } catch (e) {
      say(`> error: ${e instanceof Error ? e.message : 'build failed'}`)
      persist({})
    }
    stopProg()
    setBusy(false)
  }
  /* chat-style edit: "make the hero darker" — applied to the current build */
  async function editSite() {
    const ins = prompt.trim()
    if (!ins || !jobId || editBusy) return
    setEditBusy(true); setPrompt('')
    push(`$ ${ins.length > 80 ? ins.slice(0, 80) + '…' : ins}`)
    /* progress lives in the live status row (real phase events), not as log lines */
    startProg()
    try {
      const res: { html: string | null; err: string | null } = { html: null, err: null }
      await apiSSE('/build/edit', { job_id: jobId, instruction: ins }, 300000, (ev) => {
        if (ev.type === 'done') res.html = (ev.payload as { html: string }).html
        if (ev.type === 'error') res.err = String(ev.detail ?? 'edit failed')
        if (ev.type === 'phase' && ev.label) setPhase(PHASE_LABELS[ev.label] ?? ev.label)
      })
      if (res.err) throw new Error(res.err)
      if (!res.html) throw new Error('edit ended without a result — try again')
      setHtml(res.html)
      push(`> Applied: ${ins.length > 80 ? ins.slice(0, 80) + '…' : ins}`)
    } catch (e) {
      push(`> edit failed: ${e instanceof Error ? e.message : 'error'}`)
    }
    stopProg()
    setEditBusy(false)
  }
  /* publish: make the build live at /build/site/{slug} */
  async function publish() {
    if (!jobId || pubBusy) return
    setPubBusy(true)
    try {
      const j = await api('/build/publish', { method: 'POST', body: JSON.stringify({ job_id: jobId, user_id: USER }) }) as { slug: string; url: string; name: string }
      setPub(j)
      push(`> Published — live at ${j.url}`)
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
  /* hydrating a saved build: don't flash the empty screen */
  if (loadingConv) {
    return (
      <div className="bwrap">
        <div className="bprev bprev-load">Loading your site…</div>
      </div>
    )
  }
  /* no site yet → describe-and-build screen */
  if (!html) {
    const msgs: Msg[] = log.map(l => l.startsWith('$ ')
      ? { role: 'user', content: l.slice(2) }
      : { role: 'assistant', content: l })
    return (
      <ThreadShell msgs={log.length ? msgs : []} busy={busy}
        status={busy ? `${phase ?? 'starting…'}${elapsed > 0 ? ` ${elapsed}s` : ''}` : 'Working…'}
        empty={<div className="welcome">
          <Orb />
          <h1>Describe your business. Get a live site.</h1>
          <p>AZX codes it, you preview it, then publish with one click.</p>
        </div>}
        composer={
          <ComposerShell
            field={
              <input id="build-input" name="build" value={prompt} maxLength={500}
                onChange={e => setPrompt(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') run() }}
                placeholder="e.g. bakery website for Nix Bakes in Pune…" />
            }
            canSend={!(busy || !prompt.trim())}
            onSend={run} sendLabel="Build"
            onVoice={t => setPrompt(p => (p.trimEnd() ? p.trimEnd() + ' ' : '') + t)} />
        } />
    )
  }
  /* built → two-mode workspace: pill (upper middle) slides Chat ⇄ Preview */
  const buildMsgs: Msg[] = log.map(l => l.startsWith('$ ')
    ? { role: 'user', content: l.slice(2) }
    : { role: 'assistant', content: l })
  const liveStatus = `${phase ?? 'working…'}${elapsed > 0 ? ` ${elapsed}s` : ''}`
  const siteActions = (
    <>
      {(busy || editBusy) && (
        <div className="bprog">
          <div className="bprog-top"><span>{liveStatus}</span></div>
          <div className="bprog-track"><div className="bprog-fill" /></div>
        </div>
      )}
      <div className="bbar">
        <div className="bbar-meta">
          <div className="bbar-name">{siteName || 'Your site'}</div>
          <div className="bbar-sub">
            <span className="dot" />
            {pub
              ? <a href={liveUrl} target="_blank" rel="noreferrer" className="bbar-live">{liveUrl.replace(/^https?:\/\//, '')}</a>
              : <span>Ready</span>}
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
            onNewBuild?.()
          }}>New build</button>
        </div>
      </div>
    </>
  )
  const editComposer = (
    <ComposerShell
      head={siteActions}
      field={
        <input id="build-edit" name="edit" value={prompt} maxLength={500}
          onChange={e => setPrompt(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') editSite() }}
          placeholder="Tell AZX what to change…" />
      }
      canSend={!(editBusy || !prompt.trim() || !jobId)}
      onSend={editSite} sendLabel="Apply change"
      onVoice={t => setPrompt(p => (p.trimEnd() ? p.trimEnd() + ' ' : '') + t)} />
  )
  return (
    <div className="bwrap bwrap-done">
      <div className="pillbar" role="tablist" aria-label="Workspace view">
        <span className={`pillthumb ${mode}`} aria-hidden />
        <button role="tab" aria-selected={mode === 'chat'} className={mode === 'chat' ? 'on' : ''}
          onClick={() => setMode('chat')}>Chat</button>
        <button role="tab" aria-selected={mode === 'preview'} className={mode === 'preview' ? 'on' : ''}
          onClick={() => setMode('preview')}>Preview</button>
      </div>
      <ThreadShell msgs={buildMsgs} busy={busy || editBusy} status={liveStatus}
        empty={<div className="welcome">
          <h2>Talk to AZX about your site</h2>
          <p>Ask for changes — “make the hero darker”, “add a pricing section”, “change the brand to blue”. Hit <b>Preview</b> to see it fullscreen.</p>
        </div>}
        composer={<div className="bcomposer">{editComposer}</div>} />
      {mode === 'preview' && (
        <div className="fsprev">
          <div className="fspill glass">
            <PillSeg ariaLabel="Device size"
              options={['Mobile', 'Tablet', 'Desktop']}
              value={aspect === 'mobile' ? 'Mobile' : aspect === 'tablet' ? 'Tablet' : 'Desktop'}
              onChange={v => setAspect(v.toLowerCase() as 'mobile' | 'tablet' | 'desktop')} />
            <span className="aspect-dims">
              {DEV_SIZES[aspect][0]}×{DEV_SIZES[aspect][1]}
              {fsScale < 0.995 ? ` · ${Math.round(fsScale * 100)}%` : ''}
            </span>
            <button className="fs-x" onClick={() => setMode('chat')} aria-label="Close preview">
              <Icon name="x" size={15} />
            </button>
          </div>
          <div className="fsframe"
            style={{
              width: Math.round(DEV_SIZES[aspect][0] * fsScale),
              height: Math.round(DEV_SIZES[aspect][1] * fsScale),
            }}>
            <div className={`device dev-${aspect}`}
              style={{
                width: DEV_SIZES[aspect][0],
                height: DEV_SIZES[aspect][1],
                transform: `scale(${fsScale})`,
              }}>
              <iframe title="fullscreen preview" srcDoc={html}
                sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox" />
            </div>
          </div>
        </div>
      )}
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
  function patch(field: string, value: string) {
    /* optimistic: switch flips instantly, server syncs in the background */
    setModel(m => (m ? { ...m, traits: { ...m.traits, [field]: value } } : m))
    api(`/profile/${USER}`, { method: 'PATCH', body: JSON.stringify({ [field]: value }) })
      .then(() => load())
      .catch(() => load()) /* revert to server truth on failure */
  }
  const t = model?.traits ?? {}
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Profile</h2>
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
function SettingsPanel({ fs, setFs, onExport, canExport }: {
  fs: string; setFs: (v: string) => void; onExport: () => void; canExport: boolean
}) {
  const [confirm, setConfirm] = useState(false)
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Settings</h2>
      <div className="lab">Reading</div>
      <Seg options={['Normal', 'Large']}
        value={fs === 'large' ? 'Large' : 'Normal'}
        onPick={v => setFs(v === 'Large' ? 'large' : 'normal')} />
      <div className="lab">Chat history</div>
      <div className="card setrow">
        <span>Export open chat as markdown</span>
        <button className="mini" disabled={!canExport} onClick={onExport}>Export</button>
      </div>
      <div className="lab">Local data</div>
      <div className="card setrow">
        <span>Clear saved chats &amp; drafts on this device</span>
        <button className="mini danger-solid" onClick={() => setConfirm(true)}>Clear</button>
      </div>
      <div className="lab">About</div>
      <div className="card">
        <div>AZX · v1.1 pre-release</div>
        <div>Made by team NACX</div>
        <div>Main dev <a className="bbar-live" href="https://instagram.com/nikusurv1" target="_blank" rel="noreferrer">@nikusurv1</a></div>
      </div>
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

/* ---------- perplexity-lite answers ---------- */
type ASrc = { n: number; title: string; url: string; snippet: string }
type AThread = { id: string; question: string; focus: string; created_at: string | null }
function renderCited(src: string, nSrc: number): string {
  return renderMd(src).replace(/\[(\d+)\]/g, (m, d) => {
    const n = parseInt(d, 10)
    return (n >= 1 && n <= nSrc) ? `<a class="cite" href="#asrc-${n}">[${n}]</a>` : m
  })
}
function AnswerPanel() {
  const [q, setQ] = useState('')
  const [focus, setFocus] = useState<'all' | 'web' | 'academic'>('all')
  const [busy, setBusy] = useState(false)
  const [phase, setPhase] = useState<string | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<string | null>(null)
  const [sources, setSources] = useState<ASrc[]>([])
  const [followups, setFollowups] = useState<string[]>([])
  const [err, setErr] = useState('')
  const [threads, setThreads] = useState<AThread[]>([])
  const timer = useRef<number | null>(null)
  const stopTimer = () => {
    if (timer.current !== null) { clearInterval(timer.current); timer.current = null }
  }
  const loadThreads = () => {
    api(`/answer/threads?user_id=${USER}`).then((d: AThread[]) => {
      if (Array.isArray(d)) setThreads(d)
    }).catch(() => { /* offline */ })
  }
  useEffect(() => { loadThreads() }, [])
  useEffect(() => stopTimer, [])
  async function ask(question: string) {
    const qq = question.trim()
    if (!qq || busy) return
    setBusy(true); setErr(''); setAnswer(''); setSources([]); setFollowups([])
    setQuestion(qq); setQ(''); setPhase('starting…'); setElapsed(0)
    const t0 = Date.now()
    stopTimer()
    timer.current = window.setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 500)
    try {
      let acc = ''
      await apiSSE('/answer/ask', { user_id: USER, question: qq, focus }, 300000, (ev) => {
        if (ev.type === 'phase' && ev.label) setPhase(PHASE_LABELS[ev.label] ?? ev.label)
        if (ev.type === 'sources' && Array.isArray(ev.sources)) setSources(ev.sources as ASrc[])
        if (ev.type === 'delta' && typeof ev.text === 'string') { acc += ev.text; setAnswer(acc) }
        if (ev.type === 'done') {
          const p = ev.payload as { answer: string; sources: ASrc[]; followups: string[] }
          acc = p.answer; setAnswer(p.answer); setSources(p.sources ?? []); setFollowups(p.followups ?? [])
        }
        if (ev.type === 'error') setErr(String(ev.detail ?? 'answer failed'))
      })
      if (!acc && !err) setErr('Empty answer — try again.')
      loadThreads()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'answer failed')
    }
    stopTimer(); setBusy(false); setPhase(null)
  }
  async function openThread(id: string) {
    if (busy) return
    try {
      const d = await api(`/answer/threads/${id}`) as { question: string; answer: string; sources: ASrc[]; followups: string[] }
      setQuestion(d.question); setAnswer(d.answer); setSources(d.sources ?? []); setFollowups(d.followups ?? []); setErr('')
    } catch { /* offline */ }
  }
  return (
    <div className="panel"><div className="panel-inner">
      <h2>Answers</h2>
      <div className="seg">
        {(['all', 'web', 'academic'] as const).map(f => (
          <button key={f} className={focus === f ? 'on' : ''} onClick={() => setFocus(f)}>
            {f === 'all' ? 'Web + Academic' : f[0].toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>
      <div className="arow">
        <input value={q} maxLength={500} onChange={e => setQ(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') ask(q) }}
          placeholder="Ask anything — answers come with sources…" aria-label="Ask a question" />
        <button className="mini primary" disabled={busy || !q.trim()} onClick={() => ask(q)}>
          {busy ? 'Asking…' : 'Ask'}
        </button>
      </div>
      {busy && (
        <div className="bprog">
          <div className="bprog-top"><span>{phase ?? 'working…'}</span><span>{elapsed}s</span></div>
          <div className="bprog-track"><div className="bprog-fill" /></div>
        </div>
      )}
      {err && <div className="errbar"><div>{err}</div></div>}
      {answer !== null && answer !== '' && (
        <div className="ablock">
          <div className="aq">{question}</div>
          <div className="md" dangerouslySetInnerHTML={{ __html: renderCited(answer, sources.length) }} />
          {sources.length > 0 && (
            <div className="asrcs">
              <div className="asrc-h">Sources</div>
              {sources.map(s => (
                <a key={s.n} id={`asrc-${s.n}`} className="asrc" href={s.url} target="_blank" rel="noreferrer">
                  <span className="asrc-n">[{s.n}]</span>
                  <span className="asrc-t">{s.title}</span>
                </a>
              ))}
            </div>
          )}
          {followups.length > 0 && (
            <div className="afol">
              {followups.map((f, i) => (
                <button key={i} className="mini" disabled={busy} onClick={() => ask(f)}>{f}</button>
              ))}
            </div>
          )}
        </div>
      )}
      {threads.length > 0 && (
        <div className="athreads">
          <div className="asrc-h">Recent</div>
          {threads.map(t => (
            <button key={t.id} className="athread" onClick={() => openThread(t.id)}>
              <span className="athread-q">{t.question}</span>
              <span className="athread-f">{t.focus}</span>
            </button>
          ))}
        </div>
      )}
    </div></div>
  )
}

/* ---------- shell ---------- */
export default function App() {
  /* refresh keeps you where you were — view + open chat survive reloads */
  const [view, setView] = useState<View>(() => {
    try {
      const v = localStorage.getItem('ashtra-view')
      return (['chat', 'library', 'projects', 'plugins', 'build', 'market', 'memory', 'you', 'settings', 'answer'] as View[]).includes(v as View) ? (v as View) : 'chat'
    } catch { return 'chat' }
  })
  const [convId, setConvId] = useState<string | null>(() => {
    try { return localStorage.getItem('ashtra-conv') } catch { return null }
  })
  useEffect(() => {
    try {
      localStorage.setItem('ashtra-view', view)
      if (convId) localStorage.setItem('ashtra-conv', convId)
      else localStorage.removeItem('ashtra-conv')
    } catch { /* ignore */ }
  }, [view, convId])
  const [convs, setConvs] = useState<Conv[]>([])
  const [sideOpen, setSideOpen] = useState(() => window.innerWidth > 760)
  const [confirmDel, setConfirmDel] = useState<string | null>(null)
  const [userName, setUserName] = useState(() => {
    try { return localStorage.getItem('ashtra-name') || '' } catch { return '' }
  })
  /* fresh Build & Run chat seeded from the chat nudge (bump key → remount) */
  const [buildSeed, setBuildSeed] = useState<{ k: number; p: string }>({ k: 0, p: '' })
  /* open a saved build chat in the Build & Run workspace (survives refresh) */
  const [buildConvId, setBuildConvId] = useState<string | null>(null)
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
  /* Debounced: streaming + multi-patch turns used to re-serialize every session
     on each state change. Write once things settle (and on tab close). */
  useEffect(() => {
    const save = () => {
      try {
        const keys = Object.keys(sessions).filter(k => k !== '__draft' && k !== 'new').slice(-15)
        const slim: Record<string, { msgs: Msg[] }> = {}
        for (const k of keys) {
          const m = sessions[k]?.msgs
          if (m && m.length) slim[k] = { msgs: m.slice(-100).map(({ role, content, meta, study }) => ({ role, content, meta, study })) }
        }
        localStorage.setItem('ashtra-sessions-v1', JSON.stringify(slim))
      } catch { /* quota — skip */ }
    }
    const t = window.setTimeout(save, 700)
    window.addEventListener('pagehide', save)
    return () => { window.clearTimeout(t); window.removeEventListener('pagehide', save) }
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
  const [fs, setFsState] = useState(() => {
    try { return localStorage.getItem('ashtra-fs') || 'normal' } catch { return 'normal' }
  })
  function setFs(v: string) {
    setFsState(v)
    try { localStorage.setItem('ashtra-fs', v) } catch { /* quota */ }
  }
  /* export the open chat as markdown */
  function exportChat() {
    const msgs = (sessions[convId ?? 'new']?.msgs ?? []).filter(m => (m.content || '').trim())
    if (!msgs.length) return
    const md = `# AZX chat — ${new Date().toLocaleString()}\n\n` +
      msgs.map(m => m.role === 'user' ? `## You\n${m.content}` : `## AZX\n${m.content}`).join('\n\n')
    const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }))
    const a = document.createElement('a')
    a.href = url; a.download = 'ashtra-chat.md'; a.click()
    setTimeout(() => URL.revokeObjectURL(url), 5000)
  }

  async function refreshConvs() {
    try { setConvs(await api(`/chat/conversations/${USER}`)) } catch { /* offline */ }
  }
  /* optimistic sidebar entry so a new chat appears instantly, before the AI replies */
  function addDraftConv(title: string) {
    setConvs(c => [{ id: '__draft', title: title.slice(0, 40) || 'New conversation' },
      ...c.filter(x => x.id !== '__draft')])
  }
  useEffect(() => { refreshConvs() }, [])
  /* Keep the Render free instance warm while the tab is open — a cold start
     costs the first request ~42s of spin-up. */
  useEffect(() => {
    let alive = true
    const ping = () => {
      if (!alive) return
      fetch(API + '/health', { cache: 'no-store' }).catch(() => { /* cold or offline */ })
    }
    const iv = window.setInterval(ping, 10 * 60 * 1000)
    return () => { alive = false; window.clearInterval(iv) }
  }, [])

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
    setBuildConvId(null)
    setView('build'); setSideOpen(false)
  }
  /* reopen a saved build chat: if the server has the site → Build & Run,
     otherwise fall back to the chat thread (old 🔨 builds with no stored html) */
  function openBuildConv(id: string) {
    setSideOpen(false)
    let dead = false
    ;(async () => {
      try {
        const d = await api(`/build/conversation/${id}`, {}, 20000)
        if (dead) return
        if (d?.html) {
          setBuildConvId(id)
          setBuildSeed(s => ({ k: s.k + 1, p: '' }))
          setView('build')
          return
        }
      } catch { /* no stored build for this chat */ }
      if (!dead) { setConvId(id); setView('chat') }
    })()
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
    <div className={`${fx ? 'ash' : 'ash no-fx'}${fs === 'large' ? ' text-lg' : ''}`}>
      <Sidebar view={view} setView={setView} convId={convId} setConvId={setConvId}
        convs={convs} onNew={() => { if (sessions['new']?.busy) return; setConvId(null); setView('chat') }}
        onDelete={(id) => setConfirmDel(id)} onDeleteMany={delConvs}
        open={sideOpen} close={() => setSideOpen(false)}
        name={userName} onBuild={startFreshBuild} onOpenBuild={openBuildConv} />
      <div className="main">
        <div className="topbar">
          <button className="burger" style={{ visibility: sideOpen ? 'hidden' : 'visible', opacity: sideOpen ? 0 : 1 }} onClick={() => setSideOpen(o => !o)} aria-label="Open menu"><Icon name="menu" size={22} /></button>
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
          <button className="iconbtn plain top-new" aria-label="New chat"
            onClick={() => { if (sessions['new']?.busy) return; setConvId(null); setView('chat') }}>
            <Icon name="plus" size={19} />
          </button>
        </div>
        {view === 'chat' && <Chat key={sessKey} convId={convId} sessKey={sessKey} sess={sess}
          patchSess={patchSess} onNewConv={onNewConv} refreshSidebar={refreshConvs} onDraft={addDraftConv}
          isBuild={convs.find(c => c.id === convId)?.is_build ?? false}
          name={userName} onAnswer={() => setView('answer')}
          goBuild={p => {
            /* a fresh build prompt always opens a NEW Build & Run chat */
            try { localStorage.removeItem('ashtra-build-v1') } catch { /* quota */ }
            setBuildConvId(null)
            setBuildSeed({ k: Date.now(), p }); setView('build')
          }} />}
        {view === 'library' && <LibraryPanel convs={convs} convId={convId} setConvId={setConvId} setView={setView} onDelete={(id) => setConfirmDel(id)} refresh={refreshConvs} onOpenBuild={openBuildConv} />}
        {view === 'projects' && <SoonPanel title="Projects" body="Project workspaces are coming soon." />}
        {view === 'plugins' && <PluginsPanel />}
        {view === 'build' && <BuildPanel key={buildSeed.k} seed={buildSeed.p}
          conversationId={buildConvId}
          onConsumed={() => setBuildSeed(s => ({ ...s, p: '' }))}
          onBuilt={id => { setBuildConvId(id); refreshConvs() }}
          onNewBuild={() => { setBuildConvId(null); setBuildSeed(s => ({ k: s.k + 1, p: '' })) }} />}
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
        {view === 'answer' && <AnswerPanel />}
        {view === 'you' && <YouPanel name={userName} onName={setUserName} />}
        {view === 'settings' && <SettingsPanel fs={fs} setFs={setFs} onExport={exportChat}
          canExport={(sessions[convId ?? 'new']?.msgs ?? []).some(m => (m.content || '').trim())} />}
        {view === 'market' && <MarketPanel onUse={it => {
          /* Use template → always a NEW Build & Run chat (never the old conv) */
          try { localStorage.removeItem('ashtra-build-v1') } catch { /* quota */ }
          setBuildConvId(null)
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
