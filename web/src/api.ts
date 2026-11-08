// POST 型 SSE：EventSource 只支持 GET，所以用 fetch + ReadableStream 手动解析
export interface SseEvent { event: string; data: any }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

const BASE = '/api'

function explain(status: number, body: any): string {
  if (status === 422) {
    const d = body?.detail?.[0]
    if (d?.type === 'string_too_short') return '问题太短了，至少输入 2 个字'
    if (d?.type === 'string_too_long') return '问题太长了，最多 500 字'
    if (d?.type === 'missing') return '请先输入研究问题'
    return '输入格式不正确：' + (d?.msg ?? '请检查问题内容')
  }
  if (status >= 502 && status <= 504) return '后端服务暂时不可用，请稍后重试'
  if (typeof body?.detail === 'string') return body.detail
  return `请求失败（HTTP ${status}）`
}

/** 完整行结尾。缓冲以 CR 结束时先不切，避免它和下一块的 LF 被当成两次换行。 */
function lineBreak(buf: string, from: number, eof: boolean): { at: number; next: number } | null {
  for (let j = from; j < buf.length; j++) {
    const c = buf[j]
    if (c === '\n') return { at: j, next: j + 1 }
    if (c === '\r') {
      if (j + 1 < buf.length) return { at: j, next: buf[j + 1] === '\n' ? j + 2 : j + 1 }
      if (eof) return { at: j, next: j + 1 }
      return null
    }
  }
  return null
}

/**
 * 只在空行处派发。WHATWG 规定流结束时丢掉尚未遇到空行的 pending data，
 * 不能把未结束事件当成完整帧。EOF 上单独的 CR 仍是行结尾，因此 CR 形成的空行要派发。
 */
function takeFrame(buf: string, eof: boolean): { raw: string; rest: string } | null {
  let i = 0
  const lines: string[] = []
  while (i <= buf.length) {
    const br = lineBreak(buf, i, eof)
    if (!br) return eof ? { raw: '', rest: '' } : null
    const line = buf.slice(i, br.at)
    i = br.next
    if (line === '') return { raw: lines.join('\n'), rest: buf.slice(i) }
    lines.push(line)
    if (i >= buf.length) return eof ? { raw: '', rest: '' } : null
  }
  return null
}

function emitFrames(buf: string, onEvent: (e: SseEvent) => void, eof: boolean): string {
  let rest = buf
  for (;;) {
    const frame = takeFrame(rest, eof)
    if (!frame) return rest
    rest = frame.rest
    if (frame.raw) {
      const ev = parseBlock(frame.raw)
      if (ev) onEvent(ev)
    }
    if (!rest) return ''
  }
}

export async function streamSse(path: string, body: unknown, onEvent: (e: SseEvent) => void,
                                signal?: AbortSignal): Promise<void> {
  const res = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    let j: any = null
    try { j = await res.json() } catch { /* 非 JSON 错误体 */ }
    throw new ApiError(res.status, explain(res.status, j))
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buf = ''
  try {
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      buf = emitFrames(buf, onEvent, false)
    }
    buf += decoder.decode()
    emitFrames(buf, onEvent, true)
  } catch (err) {
    try { await reader.cancel() } catch { /* 取消失败不再覆盖原错误 */ }
    throw err
  } finally {
    try { reader.releaseLock() } catch { /* 锁已释放 */ }
  }
}

export function parseBlock(raw: string): SseEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of raw.split('\n')) {
    if (!line || line.startsWith(':')) continue // 注释 / 心跳 ping
    const i = line.indexOf(':')
    const field = i < 0 ? line : line.slice(0, i)
    const val = i < 0 ? '' : line.slice(i + 1).replace(/^ /, '')
    if (field === 'event') event = val
    else if (field === 'data') data.push(val)
  }
  if (!data.length) return null
  const text = data.join('\n')
  try { return { event, data: JSON.parse(text) } } catch { return { event, data: text } }
}

export async function getResearch(id: string) {
  const res = await fetch(`${BASE}/research/${id}`)
  if (!res.ok) {
    let j: any = null
    try { j = await res.json() } catch { /* ignore */ }
    throw new ApiError(res.status, explain(res.status, j))
  }
  return res.json()
}

export async function health(): Promise<boolean> {
  try { return (await fetch(`${BASE}/health`)).ok } catch { return false }
}
