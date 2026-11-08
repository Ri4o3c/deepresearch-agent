// 离线复现真实续跑：完成顺序不是规划顺序，且 SSE 会重放已完成节点。
import assert from 'node:assert/strict'

const saved = {
  fetch: globalThis.fetch,
  storage: Object.getOwnPropertyDescriptor(globalThis, 'localStorage'),
  window: Object.getOwnPropertyDescriptor(globalThis, 'window'),
}
const mem: Record<string, string> = {}
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
  getItem: (key: string) => mem[key] ?? null,
  setItem: (key: string, value: string) => { mem[key] = value },
} })
Object.defineProperty(globalThis, 'window', { configurable: true, value: globalThis })

try {
  const { state, loadThread, resume, handle } = await import('../src/store.ts')
  const notes = [
    { question: 'ReAct', sources: 5, local: 4, findings: 6 },
    { question: 'LangGraph', sources: 3, local: 1, findings: 7 },
  ]
  const snapshot = {
    thread_id: 'identity', question: '概念配合', finished: false,
    next: ['researcher', 'researcher'],
    sub_questions: ['RAG', 'ReAct', 'LangGraph', '组合'],
    notes: 2, research_notes: notes, reviews: [], report: '',
  }
  globalThis.fetch = async () => Response.json(snapshot)
  await loadThread('identity')
  assert.deepEqual(state.workers.filter(w => w.status === 'done').map(w => w.question), ['ReAct', 'LangGraph'])
  assert.deepEqual(state.workers.map(w => [w.question, w.sources, w.findings, w.local ?? 0]), [
    ['RAG', 0, 0, 0], ['ReAct', 5, 6, 4], ['LangGraph', 3, 7, 1], ['组合', 0, 0, 0],
  ])
  assert.ok(state.workers.filter(w => w.status === 'done').every(w => w.statsKnown))
  console.log('✓ 非顺序完成的快照按问题恢复状态及真实统计')

  const requests: string[] = []
  globalThis.fetch = async (input) => {
    const url = String(input)
    requests.push(url)
    if (!url.endsWith('/resume')) return Response.json(snapshot)
    const emit = (event: string, data: unknown) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
    const events = [
      emit('start', { thread_id: 'identity' }),
      ...notes.map(n => emit('node', { node: 'researcher', ...n })),
      emit('node', { node: 'researcher', question: '组合', sources: 4, local: 3, findings: 5 }),
      emit('node', { node: 'researcher', question: 'RAG', sources: 10, local: 2, findings: 6 }),
      emit('node', { node: 'writer', report: '# 完成' }),
      emit('node', { node: 'reviewer', score: 8, passed: true, issues: [], follow_up: [] }),
      emit('done', { thread_id: 'identity', report: '# 完成' }),
    ]
    return new Response(events.join(''))
  }
  await resume('identity')
  assert.equal(requests.length, 2)
  assert.equal(state.phase, 'done')
  assert.equal(state.workers.length, 4)
  assert.deepEqual(state.workers.map(w => [w.question, w.sources, w.findings, w.local]), [
    ['RAG', 10, 6, 2], ['ReAct', 5, 6, 4], ['LangGraph', 3, 7, 1], ['组合', 4, 5, 3],
  ])
  console.log('✓ 重放已完成节点和乱序新结果均不串卡片、不重复建卡')

  // 兼容只给 notes 数量的旧服务：不能猜测前 N 张卡片已完成。
  const { research_notes: _, ...legacy } = snapshot
  globalThis.fetch = async () => Response.json(legacy)
  await loadThread('identity')
  assert.ok(state.workers.every(w => w.status === 'wait' && !w.statsKnown))
  state.workers.forEach(w => { w.status = 'active' })
  for (const note of notes) handle({ event: 'node', data: { node: 'researcher', ...note } })
  assert.equal(state.workers.find(w => w.question === 'RAG')!.sources, 0)
  assert.equal(state.workers.find(w => w.question === '组合')!.sources, 0)
  assert.equal(state.workers.find(w => w.question === 'ReAct')!.sources, 5)
  assert.equal(state.workers.find(w => w.question === 'LangGraph')!.sources, 3)
  console.log('✓ 旧快照不按数量猜完成顺序，重放事件仍按问题匹配')

  handle({ event: 'node', data: { node: 'researcher', question: '旧版本改写的问题', sources: 99, local: 1, findings: 9 } })
  assert.equal(state.workers.find(w => w.question === 'RAG')!.sources, 0)
  assert.equal(state.workers.find(w => w.question === '组合')!.sources, 0)
  assert.equal(state.workers.find(w => w.question === '旧版本改写的问题')!.sources, 99)
  console.log('✓ 无法匹配的历史问题单列，不挪用另一张卡片')
} finally {
  globalThis.fetch = saved.fetch
  for (const [name, descriptor] of [['localStorage', saved.storage], ['window', saved.window]] as const) {
    if (descriptor) Object.defineProperty(globalThis, name, descriptor)
    else delete (globalThis as any)[name]
  }
}
