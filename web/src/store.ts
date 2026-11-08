import { reactive } from 'vue'
import { streamSse, getResearch, ApiError, type SseEvent } from './api.ts'

export type Phase = 'idle' | 'running' | 'done' | 'error'
export type NodeStatus = 'wait' | 'active' | 'done'

export interface Worker { id: number; question: string; round: number; status: NodeStatus
  findings: number; sources: number; local?: number; statsKnown?: boolean }
export interface Review { score: number; passed: boolean; issues: string[]; follow_up: string[] }
export interface LogItem { t: number; kind: string; text: string }
export interface HistoryItem { id: string; question: string; at: number; status: Phase }

const MAX_ROUNDS = 2
const HKEY = 'dr-history'

export const state = reactive({
  phase: 'idle' as Phase,
  question: '',
  threadId: '',
  round: 1,
  planner: 'wait' as NodeStatus,
  writer: 'wait' as NodeStatus,
  reviewer: 'wait' as NodeStatus,
  workers: [] as Worker[],
  reviews: [] as Review[],
  report: '',
  logs: [] as LogItem[],
  error: '',
  resumable: false,
  startedAt: 0,
  elapsed: 0,
  history: load(),
})

function load(): HistoryItem[] {
  try { return JSON.parse(localStorage.getItem(HKEY) || '[]') } catch { return [] }
}
function saveHistory() { localStorage.setItem(HKEY, JSON.stringify(state.history.slice(0, 20))) }
function upsertHistory(status: Phase) {
  if (!state.threadId) return
  const i = state.history.findIndex(h => h.id === state.threadId)
  const item = { id: state.threadId, question: state.question, at: Date.now(), status }
  if (i >= 0) state.history.splice(i, 1)
  state.history.unshift(item)
  saveHistory()
}
export function removeHistory(id: string) {
  state.history = state.history.filter(h => h.id !== id)
  saveHistory()
}

function log(kind: string, text: string) {
  state.logs.push({ t: state.startedAt ? (Date.now() - state.startedAt) / 1000 : 0, kind, text })
}

let wid = 0
function addWorkers(qs: string[], round: number) {
  for (const q of qs) state.workers.push({ id: ++wid, question: q, round, status: 'active', findings: 0, sources: 0 })
}

function reset(question: string) {
  Object.assign(state, { phase: 'running', question, threadId: '', round: 1, planner: 'active',
    writer: 'wait', reviewer: 'wait', workers: [], reviews: [], report: '', logs: [], error: '',
    resumable: false, startedAt: Date.now(), elapsed: 0 })
}

let timer: number | undefined
function tick(on: boolean) {
  clearInterval(timer)
  if (on) timer = window.setInterval(() => { state.elapsed = (Date.now() - state.startedAt) / 1000 }, 100)
}

export function handle(e: SseEvent) {
  const d = e.data
  if (e.event === 'start') {
    state.threadId = d.thread_id
    upsertHistory('running')
    log('start', `任务已创建 #${d.thread_id}`)
  } else if (e.event === 'node') {
    onNode(d)
  } else if (e.event === 'done') {
    state.report = d.report || state.report
    state.phase = 'done'
    state.planner = 'done'; state.writer = 'done'; state.reviewer = 'done'
    state.workers.forEach(w => { if (w.status === 'active') w.status = 'done' })
    log('done', `研究完成，用时 ${state.elapsed.toFixed(1)}s`)
    upsertHistory('done')
  } else if (e.event === 'error') {
    fail(d.error || '未知错误', !!d.resumable)
  }
}

function onNode(d: any) {
  switch (d.node) {
    case 'planner':
      state.planner = 'done'
      addWorkers(d.sub_questions || [], 1)
      log('planner', `规划完成：拆成 ${d.sub_questions?.length ?? 0} 个子问题，并行派发检索 Agent`)
      break
    case 'researcher': {
      // 断点续跑会重放已完成节点，不能把结果填给第一个 active 卡片。
      const w = state.workers.find(x => x.question === d.question)
      if (w) { w.status = 'done'; w.findings = d.findings; w.sources = d.sources; w.local = d.local ?? 0; w.statsKnown = true }
      else state.workers.push({ id: ++wid, question: d.question, round: state.round, status: 'done',
        findings: d.findings, sources: d.sources, local: d.local ?? 0, statsKnown: true })
      log('researcher', `检索完成「${short(d.question)}」：${d.sources} 个来源${d.local ? `（本地 ${d.local}）` : ''}，${d.findings} 条发现`)
      if (state.workers.length && state.workers.every(x => x.status === 'done')) {
        state.writer = 'active'
        log('writer', state.round > 1 ? '写手根据审稿意见修订报告…' : '写手开始撰写报告…')
      }
      break
    }
    case 'writer':
      state.writer = 'done'
      state.report = d.report
      state.reviewer = 'active'
      log('writer', `报告完成（${d.report?.length ?? 0} 字），提交审稿`)
      break
    case 'reviewer': {
      const r: Review = { score: d.score, passed: d.passed, issues: d.issues || [], follow_up: d.follow_up || [] }
      state.reviews.push(r)
      state.reviewer = 'done'
      log('reviewer', `第 ${state.round} 轮审稿：${r.score}/10，${r.passed ? '通过 ✓' : '打回补查'}`)
      if (!r.passed && r.follow_up.length && state.round <= MAX_ROUNDS) {
        const seen = new Set(state.workers.map(w => w.question))
        const qs = r.follow_up.slice(0, 3).filter(q => !seen.has(q))
        if (qs.length) {
          state.round++
          state.writer = 'wait'; state.reviewer = 'wait'
          addWorkers(qs, state.round)
          log('loop', `反思回路 → 第 ${state.round} 轮，补查 ${qs.length} 个问题`)
        }
      }
      break
    }
  }
}

function short(s: string, n = 22) { return s.length > n ? s.slice(0, n) + '…' : s }

function fail(msg: string, resumable: boolean) {
  state.phase = 'error'
  state.error = msg
  state.resumable = resumable && !!state.threadId
  state.workers.forEach(w => { if (w.status === 'active') w.status = 'wait' })
  if (state.planner === 'active') state.planner = 'wait'
  if (state.writer === 'active') state.writer = 'wait'
  if (state.reviewer === 'active') state.reviewer = 'wait'
  log('error', msg)
  upsertHistory('error')
  tick(false)
}

let ctrl: AbortController | null = null
let lastNext: string[] = []
let op = 0

/** 作废进行中的客户端读取。不能据此认为服务端图已经停止。同时停掉本页计时，避免旧 interval 继续改 elapsed。 */
function supersede(): number {
  op += 1
  const prev = ctrl
  ctrl = null
  prev?.abort()
  tick(false)
  return op
}

function current(gen: number) { return gen === op }

async function run(gen: number, path: string, body: unknown) {
  if (!current(gen)) return
  const ac = new AbortController()
  ctrl = ac
  tick(true)
  try {
    await streamSse(path, body, (e) => { if (current(gen)) handle(e) }, ac.signal)
    if (!current(gen)) return
    if (state.phase === 'running') fail('连接意外中断', true)
  } catch (err: any) {
    if (!current(gen)) return
    if (err?.name === 'AbortError') return
    if (err instanceof ApiError) {
      fail(err.message, false)
    } else {
      fail('无法连接后端服务，请检查服务状态后重试', !!state.threadId)
    }
  } finally {
    if (!current(gen)) return
    tick(false)
    if (state.startedAt) state.elapsed = (Date.now() - state.startedAt) / 1000
    if (ctrl === ac) ctrl = null
  }
}

export function startResearch(question: string) {
  const text = question.trim()
  // 与 ResearchRequest 一致：2～500 字。进行中直接返回，避免同一轮发出两次 POST。
  if (state.phase === 'running' || text.length < 2 || text.length > 500) return
  const gen = supersede()
  reset(text)
  log('start', '提交研究问题…')
  return run(gen, '/research/stream', { question: text })
}

export function cancel() {
  if (state.phase !== 'running') return
  supersede()
  fail('已停止本页接收，服务端任务可能仍在运行；可从断点续跑', true)
}

/** 先占用进行中状态，再 GET 快照，然后从 checkpoint 续跑。 */
export async function resume(id = state.threadId) {
  if (state.phase === 'running' || !id) return
  const gen = supersede()
  state.phase = 'running'
  state.error = ''
  state.resumable = false
  const ok = await loadThread(id, true, gen)
  // loadThread 在 await 期间会更新共享状态，不能沿用调用前的 running 收窄。
  if (!current(gen) || !ok || (state.phase as Phase) === 'done') return
  Object.assign(state, { phase: 'running', error: '', resumable: false, startedAt: Date.now(), elapsed: 0 })
  // 按快照里的 next 点亮即将执行的节点
  if (state.planner !== 'done') state.planner = 'active'
  if (lastNext.includes('researcher')) state.workers.forEach(w => { if (w.status === 'wait') w.status = 'active' })
  if (lastNext.includes('writer')) state.writer = 'active'
  if (lastNext.includes('reviewer')) state.reviewer = 'active'
  log('start', `从断点续跑 #${id}`)
  return run(gen, `/research/${id}/resume`, undefined)
}

/** 从后端读取一个任务的快照。晚返回的旧请求不得覆盖当前任务。 */
export async function loadThread(id: string, quiet = false, gen?: number): Promise<boolean> {
  const owned = gen === undefined
  if (owned) gen = supersede()
  if (owned) {
    // 快照没有耗时字段。先清掉上一任务的起点，避免等待期间仍显示它。
    state.startedAt = 0
    state.elapsed = 0
    if (!quiet) {
      state.phase = 'running'
      state.error = ''
    }
  }
  try {
    const v = await getResearch(id)
    if (!current(gen!)) return false
    Object.assign(state, { question: v.question, threadId: id, workers: [], reviews: [], logs: quiet ? state.logs : [],
      report: v.report || '', error: '', resumable: !v.finished, startedAt: 0, elapsed: 0 })
    const subs: string[] = v.sub_questions || []
    state.planner = subs.length ? 'done' : 'wait'
    const reviews = (v.reviews || []).map((r: any) => ({ score: r.score, passed: r.passed,
      issues: r.issues || [], follow_up: r.follow_up_questions || [] }))
    state.reviews = reviews
    // 未完成时当前轮 = 已审稿次数 + 1；已完成时 = 审稿次数
    state.round = Math.max(1, reviews.length + (v.finished ? 0 : 1))
    lastNext = v.next || []
    const notes: { question: string; findings: number; sources: number; local?: number }[] = v.research_notes || []
    const byQuestion = new Map(notes.map(note => [note.question, note]))
    // 旧接口只有 notes 数量时，部分完成的卡片身份未知，不能假设是前 N 个。
    for (const q of new Set([...subs, ...notes.map(note => note.question)])) {
      const note = byQuestion.get(q)
      state.workers.push({ id: ++wid, question: q, round: note ? 1 : state.round,
        status: note || v.finished ? 'done' : 'wait', findings: note?.findings ?? 0,
        sources: note?.sources ?? 0, local: note?.local ?? 0, statsKnown: !!note })
    }
    state.writer = v.report ? 'done' : 'wait'
    state.reviewer = reviews.length ? 'done' : 'wait'
    state.phase = v.finished ? 'done' : 'error'
    if (!v.finished) { state.error = `任务未完成（下一步：${(v.next || []).join(', ')}）`; state.resumable = true }
    if (!quiet) log('start', `已加载历史任务 #${id}`)
    upsertHistory(state.phase)
    return true
  } catch (err: any) {
    if (!current(gen!)) return false
    fail(err instanceof ApiError ? err.message : '无法连接后端服务', false)
    if (err instanceof ApiError && err.status === 404) removeHistory(id)
    return false
  }
}
