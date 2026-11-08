// 最终离线回归。调用生产 streamSse / store，不用替身解析器。
// fetch、时钟和字节流是可控替身。默认 fetch 拒绝外网。
const saved = {
  fetch: globalThis.fetch,
  setInterval: globalThis.setInterval,
  clearInterval: globalThis.clearInterval,
  setTimeout: globalThis.setTimeout,
  clearTimeout: globalThis.clearTimeout,
  now: Date.now,
  localStorage: Object.getOwnPropertyDescriptor(globalThis, 'localStorage'),
  window: Object.getOwnPropertyDescriptor(globalThis, 'window'),
}

const mem: Record<string, string> = {}
const storage = {
  getItem: (k: string) => mem[k] ?? null,
  setItem: (k: string, v: string) => { mem[k] = v },
}

let now = 1_000_000
const timers = new Map<number, () => void>()
let timerSeq = 1
const setTimer = (fn: () => void) => {
  const id = timerSeq++
  timers.set(id, fn)
  return id
}
const clearTimer = (id: number) => { timers.delete(id) }
function fireTimers() { for (const fn of [...timers.values()]) fn() }

function restoreGlobals() {
  globalThis.fetch = saved.fetch
  globalThis.setInterval = saved.setInterval
  globalThis.clearInterval = saved.clearInterval
  globalThis.setTimeout = saved.setTimeout
  globalThis.clearTimeout = saved.clearTimeout
  Date.now = saved.now
  for (const name of ['localStorage', 'window'] as const) {
    const descriptor = saved[name]
    if (descriptor) Object.defineProperty(globalThis, name, descriptor)
    else delete (globalThis as any)[name]
  }
  timers.clear()
}

const realSetTimeout = saved.setTimeout
const realClearTimeout = saved.clearTimeout
function bounded<T>(task: T | PromiseLike<T>, label: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = realSetTimeout(() => reject(new Error('有界等待超时: ' + label)), 1000)
    Promise.resolve(task).then(
      (value) => { realClearTimeout(timer); resolve(value) },
      (err) => { realClearTimeout(timer); reject(err) },
    )
  })
}

const tasks = new Set<Promise<unknown>>()
const readerCleanup = new Set<() => void>()
const fetchCleanup = new Set<() => void>()
let cancelActive: (() => void) | undefined
function track<T>(task: T | PromiseLike<T>): Promise<T> {
  const promise = Promise.resolve(task)
  tasks.add(promise)
  promise.then(() => tasks.delete(promise), () => tasks.delete(promise))
  return promise
}

const enc = new TextEncoder()
const u8 = (...parts: Uint8Array[]) => {
  const n = parts.reduce((s, p) => s + p.length, 0)
  const out = new Uint8Array(n)
  let o = 0
  for (const p of parts) { out.set(p, o); o += p.length }
  return out
}

type Waiter = { resolve: (v: { done: boolean; value?: Uint8Array }) => void; reject: (e: Error) => void }
function abortError() {
  const err = new Error('aborted')
  err.name = 'AbortError'
  return err
}
function deferredResponse(register: (resolve: (response: Response) => void) => void): Promise<Response> {
  return new Promise((resolve, reject) => {
    const cleanup = () => { fetchCleanup.delete(cleanup); reject(abortError()) }
    fetchCleanup.add(cleanup)
    register((response) => {
      fetchCleanup.delete(cleanup)
      resolve(response)
    })
  })
}
function openReader(signal?: AbortSignal) {
  const trace = { cancel: 0, release: 0 }
  let pending: Waiter | null = null
  let cancelError: Error | undefined
  const queue: { done: boolean; value?: Uint8Array }[] = []
  let queuedError: Error | undefined
  function failPending(err: Error) {
    const cur = pending
    pending = null
    if (cur) cur.reject(err)
    else queuedError = err
  }
  const onAbort = () => failPending(abortError())
  const cleanup = () => {
    failPending(abortError())
    signal?.removeEventListener('abort', onAbort)
  }
  readerCleanup.add(cleanup)
  signal?.addEventListener('abort', onAbort)
  return {
    trace,
    setCancelError(err: Error) { cancelError = err },
    push(value: Uint8Array) { this.settle({ done: false, value }) },
    finish() { this.settle({ done: true }) },
    fail(err: Error) { failPending(err) },
    settle(packet: { done: boolean; value?: Uint8Array }) {
      if (pending) {
        const cur = pending
        pending = null
        cur.resolve(packet)
      } else queue.push(packet)
    },
    reader: {
      read() {
        if (queuedError) return Promise.reject(queuedError)
        if (signal?.aborted) return Promise.reject(abortError())
        if (queue.length) return Promise.resolve(queue.shift()!)
        return new Promise((resolve, reject) => { pending = { resolve, reject } })
      },
      async cancel() {
        trace.cancel++
        if (pending) {
          const cur = pending
          pending = null
          cur.resolve({ done: true })
        }
        if (cancelError) throw cancelError
      },
      releaseLock() {
        trace.release++
        signal?.removeEventListener('abort', onAbort)
        readerCleanup.delete(cleanup)
      },
    },
  }
}

let fail = 0
const ok = (c: boolean, m: string) => { console.log((c ? '  ✓ ' : '  ✗ ') + m); if (!c) fail++ }
const blocked = async () => { throw new Error('offline test blocked a real network fetch') }
let streamSse: typeof import('../src/api.ts').streamSse

async function readSse(feed: (reader: ReturnType<typeof openReader>) => void) {
  const events: { event: string; data: any }[] = []
  let opened: ReturnType<typeof openReader> | undefined
  ;(globalThis as any).fetch = async (_url: string, init?: { signal?: AbortSignal }) => {
    opened = openReader(init?.signal)
    feed(opened)
    return { ok: true, body: { getReader: () => opened!.reader } }
  }
  await bounded(track(streamSse('/sse', undefined, (e) => events.push(e))), 'SSE 分块读取')
  return { events, trace: opened!.trace, thrown: null }
}

try {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, writable: true, value: storage })
  Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: globalThis })
  Date.now = () => now
  ;(globalThis as any).setInterval = setTimer
  ;(globalThis as any).clearInterval = clearTimer
  globalThis.fetch = blocked
  ;({ streamSse } = await import('../src/api.ts'))
  {
    const { events, trace, thrown } = await readSse((r) => {
      r.push(enc.encode('event: node\r'))
      r.push(enc.encode('\ndata: {"n":1}\r\n\r\n'))
      r.finish()
    })
    ok(thrown === null && events.length === 1 && events[0].event === 'node' && events[0].data.n === 1, 'CRLF 跨 chunk 仍是一帧 node')
    ok(trace.release === 1 && trace.cancel === 0, '正常结束后释放 reader，不调用 cancel')
  }
  {
    const { events } = await readSse((r) => { r.push(enc.encode('event: node\rdata: {"n":1}\r\r')); r.finish() })
    ok(events.length === 1 && events[0].event === 'node' && events[0].data.n === 1, '仅 CR 的空行派发完整事件')
  }
  {
    const { events } = await readSse((r) => { r.push(enc.encode('event: node\ndata: {"n":1}\n\n')); r.finish() })
    ok(events.length === 1 && events[0].event === 'node' && events[0].data.n === 1, '仅 LF 的空行派发完整事件')
  }
  {
    const { events } = await readSse((r) => { r.push(enc.encode('event: node\ndata: {"n":1}\n\r')); r.finish() })
    ok(events.length === 1 && events[0].event === 'node' && events[0].data.n === 1, 'EOF 前的 CR 若构成空行，仍派发已结束事件')
  }
  {
    const raw = ': keep-alive\nevent: node\ndata: hello\ndata: world\n\n'
    const { events } = await readSse((r) => { r.push(enc.encode(raw)); r.finish() })
    ok(events.length === 1 && events[0].event === 'node' && events[0].data === 'hello\nworld', '注释行忽略，多行 data 用 LF 拼接')
  }
  {
    const prefix = enc.encode('data: ')
    const ch = enc.encode('中')
    const suffix = enc.encode('\n\n')
    const { events } = await readSse((r) => {
      r.push(u8(prefix, ch.slice(0, 2)))
      r.push(u8(ch.slice(2), suffix))
      r.finish()
    })
    ok(events.length === 1 && events[0].data === '中', 'UTF-8 多字节跨 chunk 后，空行仍派发原字符')
  }
  {
    const { events } = await readSse((r) => {
      r.push(u8(enc.encode('data: '), new Uint8Array([0xE4, 0xB8])))
      r.finish()
    })
    ok(events.length === 0, 'EOF 丢弃未以空行结束的事件，即使解码器已刷出替换字符')
  }
  {
    const { events } = await readSse((r) => { r.push(enc.encode('event: node\ndata: {"n":1}')); r.finish() })
    ok(events.length === 0, 'EOF 丢弃没有终止空行的 pending data')
  }
  {
    const { events } = await readSse((r) => { r.push(enc.encode('event: node\ndata: {"n":1}\n')); r.finish() })
    ok(events.length === 0, '最后一行已结束但没有空行时，EOF 仍不派发')
  }

  {
    const upstream = new Error('upstream')
    let opened: ReturnType<typeof openReader> | undefined
    ;(globalThis as any).fetch = async (_url: string, init?: { signal?: AbortSignal }) => {
      opened = openReader(init?.signal)
      opened.setCancelError(new Error('from-cancel'))
      queueMicrotask(() => opened!.fail(upstream))
      return { ok: true, body: { getReader: () => opened!.reader } }
    }
    let thrown: any = null
    try { await bounded(track(streamSse('/sse', undefined, () => {})), 'SSE 读错误') } catch (err) { thrown = err }
    ok(thrown === upstream && opened!.trace.cancel === 1 && opened!.trace.release === 1, '读失败时保留原错误，cancel 的异常不替换它，并释放 reader')
  }
  {
    const aborted = abortError()
    let opened: ReturnType<typeof openReader> | undefined
    ;(globalThis as any).fetch = async (_url: string, init?: { signal?: AbortSignal }) => {
      opened = openReader(init?.signal)
      opened.setCancelError(new Error('from-cancel'))
      queueMicrotask(() => opened!.fail(aborted))
      return { ok: true, body: { getReader: () => opened!.reader } }
    }
    let thrown: any = null
    try { await bounded(track(streamSse('/sse', undefined, () => {})), 'SSE 取消错误') } catch (err) { thrown = err }
    ok(thrown === aborted && thrown.name === 'AbortError' && opened!.trace.cancel === 1 && opened!.trace.release === 1, '取消时保留 AbortError，并释放 reader')
  }

  const store = await import('../src/store.ts')
  const { state, cancel } = store
  cancelActive = cancel
  const startResearch = (...args: Parameters<typeof store.startResearch>) => track(store.startResearch(...args))
  const resume = (...args: Parameters<typeof store.resume>) => track(store.resume(...args))
  const loadThread = (...args: Parameters<typeof store.loadThread>) => track(store.loadThread(...args))

  function idle() {
    state.phase = 'idle'
    state.question = ''
    state.threadId = ''
    state.startedAt = 0
    state.elapsed = 0
    state.error = ''
    state.resumable = false
    state.report = ''
    state.workers = []
    state.logs = []
    state.reviews = []
    state.planner = 'wait'
    state.writer = 'wait'
    state.reviewer = 'wait'
    state.round = 1
  }
  function snap(question: string, finished: boolean) {
    return new Response(JSON.stringify({
      thread_id: 'x', question, finished, next: finished ? [] : ['writer'],
      sub_questions: ['子问题'], notes: finished ? 1 : 0, reviews: [], report: finished ? '报告' + question : '',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }

  {
    idle()
    let posts = 0
    const streams: ReturnType<typeof openReader>[] = []
    ;(globalThis as any).fetch = async (url: string, init?: { signal?: AbortSignal }) => {
      if (!String(url).endsWith('/research/stream')) throw new Error('unexpected ' + url)
      posts++
      const opened = openReader(init?.signal)
      streams.push(opened)
      return { ok: true, body: { getReader: () => opened.reader } }
    }
    const first = startResearch('问题甲甲')
    const second = startResearch('问题乙乙')
    await bounded(second, '第二次开始研究')
    ok(posts === 1 && state.question === '问题甲甲' && state.phase === 'running', '进行中时第二次开始研究不再 POST')
    cancel()
    await bounded(first, '第一次研究在取消后结束')
    ok(streams[0].trace.release === 1, '取消后第一次研究的 reader 已释放')
  }

  {
    idle()
    state.phase = 'error'
    state.threadId = 't1'
    state.resumable = true
    state.error = '未完成'
    let gets = 0
    let posts = 0
    let release: ((r: Response) => void) | undefined
    const postsStreams: ReturnType<typeof openReader>[] = []
    ;(globalThis as any).fetch = async (url: string, init?: { signal?: AbortSignal }) => {
      const u = String(url)
      if (u.endsWith('/resume')) {
        posts++
        const opened = openReader(init?.signal)
        postsStreams.push(opened)
        return { ok: true, body: { getReader: () => opened.reader } }
      }
      gets++
      return deferredResponse((res) => { release = res })
    }
    const first = resume('t1')
    const second = resume('t1')
    await bounded(second, '第二次续跑')
    ok(gets === 1 && posts === 0 && state.phase === 'running', '续跑在首次 await 前占住进行中，第二次调用不再 GET')
    release(snap('t1', false))
    for (let i = 0; i < 8 && posts < 1; i++) await Promise.resolve()
    ok(posts === 1 && timers.size === 1, '快照返回后只发一次 resume POST，并且新计时器已启动')
    cancel()
    await bounded(first, '第一次续跑在取消后结束')
    ok(postsStreams[0].trace.release === 1 && state.phase === 'error', '取消后续跑任务结束并释放 reader')
  }

  {
    idle()
    const pending: Record<string, (r: Response) => void> = {}
    ;(globalThis as any).fetch = async (url: string) => deferredResponse((res) => {
      pending[String(url).endsWith('/a') ? 'a' : 'b'] = res
    })
    const pa = loadThread('a')
    const pb = loadThread('b')
    pending.b(snap('B完成', true))
    pending.a(snap('A完成', true))
    await bounded(Promise.all([pa, pb]), '历史成功乱序')
    ok(state.question === 'B完成' && state.phase === 'done' && state.report === '报告B完成', '后发出的历史 GET 先返回时，早发出的成功结果不能覆盖')
    ok(state.workers.every(w => w.statsKnown === false), '历史快照没有来源统计时保留未知状态，不当作零来源')
  }

  {
    idle()
    const pending: Record<string, (r: Response) => void> = {}
    ;(globalThis as any).fetch = async (url: string) => deferredResponse((res) => {
      pending[String(url).endsWith('/a') ? 'a' : 'b'] = res
    })
    const pa = loadThread('a')
    const pb = loadThread('b')
    pending.b(snap('B仍在', false))
    pending.a(new Response(JSON.stringify({ detail: 'missing' }), { status: 404 }))
    await bounded(Promise.all([pa, pb]), '历史失败乱序')
    ok(state.question === 'B仍在' && state.phase === 'error' && state.resumable === true && state.error.includes('下一步'), '旧 GET 失败不能把已显示的新快照改成错误')
  }

  {
    idle()
    let live: ReturnType<typeof openReader> | undefined
    ;(globalThis as any).fetch = async (url: string, init?: { signal?: AbortSignal }) => {
      if (String(url).endsWith('/research/stream')) {
        live = openReader(init?.signal)
        return { ok: true, body: { getReader: () => live!.reader } }
      }
      return snap('已完成B', true)
    }
    now = 2_000_000
    const running = startResearch('计时中的问题')
    ok(timers.size === 1 && state.startedAt === 2_000_000, '研究开始后有一个计时器')
    now = 2_000_500
    fireTimers()
    ok(state.elapsed === 0.5, '计时器按起点增加耗时')
    for (let i = 0; i < 8 && !live; i++) await Promise.resolve()
    live!.push(enc.encode('event: start\ndata: {"thread_id":"A"}\n\n'))
    for (let i = 0; i < 8 && state.threadId !== 'A'; i++) await Promise.resolve()
    ok(state.threadId === 'A', '首帧已经写入当前任务')
    const loaded = loadThread('done-b')
    ok(state.elapsed === 0 && state.startedAt === 0 && timers.size === 0, '切到历史时立即停表并清掉上一任务耗时')
    await bounded(loaded, '切换到已完成历史')
    await bounded(running, '旧研究在切换后结束')
    now = 9_000_000
    fireTimers()
    ok(state.question === '已完成B' && state.phase === 'done' && state.elapsed === 0 && state.error === '' && timers.size === 0 && live!.trace.release === 1, '旧流结束后已完成快照的阶段、错误和耗时都不变')
  }

  {
    idle()
    let firstStream: ReturnType<typeof openReader> | undefined
    let resumeStream: ReturnType<typeof openReader> | undefined
    let gets = 0
    let posts = 0
    ;(globalThis as any).fetch = async (url: string, init?: { signal?: AbortSignal }) => {
      const u = String(url)
      if (u.endsWith('/research/stream')) {
        firstStream = openReader(init?.signal)
        return { ok: true, body: { getReader: () => firstStream!.reader } }
      }
      if (u.endsWith('/resume')) {
        posts++
        resumeStream = openReader(init?.signal)
        return { ok: true, body: { getReader: () => resumeStream!.reader } }
      }
      gets++
      return snap('待续跑', false)
    }
    now = 3_000_000
    const running = startResearch('准备取消的研究')
    for (let i = 0; i < 8 && !firstStream; i++) await Promise.resolve()
    firstStream!.push(enc.encode('event: start\ndata: {"thread_id":"live"}\n\n'))
    for (let i = 0; i < 8 && state.threadId !== 'live'; i++) await Promise.resolve()
    cancel()
    const resumed = resume('live')
    const again = resume('live')
    await bounded(again, '取消后的第二次续跑')
    await bounded(running, '被取消的旧研究结束')
    for (let i = 0; i < 8 && posts < 1; i++) await Promise.resolve()
    ok(gets === 1 && posts === 1 && state.phase === 'running' && timers.size === 1 && state.startedAt === 3_000_000, '旧 finally 结束后新续跑仍在进行，计时器活着，且没有重复 GET/POST')
    now = 3_000_400
    fireTimers()
    ok(state.elapsed === 0.4, '取消后立即续跑从新起点计时')
    cancel()
    await bounded(resumed, '新续跑在取消后结束')
    ok(resumeStream!.trace.release === 1 && firstStream!.trace.release === 1, '新旧 reader 都已释放')
  }
} finally {
  try {
    cancelActive?.()
    for (const cleanup of fetchCleanup) cleanup()
    for (const cleanup of readerCleanup) cleanup()
    await bounded(Promise.allSettled([...tasks]), '失败或完成后的任务清理')
    ok(tasks.size === 0 && fetchCleanup.size === 0 && readerCleanup.size === 0 && timers.size === 0, '没有残留任务、请求、reader 或计时器')
  } finally {
    restoreGlobals()
  }
}

if (fail) throw new Error('失败 ' + fail + ' 项')
console.log('全部通过')
