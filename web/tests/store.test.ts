// 用一段录制好的 SSE 事件序列回放给 store，验证界面状态机（不需要浏览器）
const mem: Record<string, string> = {}
;(globalThis as any).localStorage = { getItem: (k: string) => mem[k] ?? null, setItem: (k: string, v: string) => { mem[k] = v } }
;(globalThis as any).window = globalThis
const { state, handle, resume } = await import('../src/store.ts')
const { parseBlock } = await import('../src/api.ts')
let fail = 0
const ok = (c: boolean, m: string) => { console.log((c ? '  ✓ ' : '  ✗ ') + m); if (!c) fail++ }

state.phase = 'running'; state.planner = 'active'; state.startedAt = Date.now()
const evs = [
  ['start', { thread_id: 'abc' }],
  ['node', { node: 'planner', sub_questions: ['q1', 'q2', 'q3'] }],
  ['node', { node: 'researcher', question: 'q2', findings: 3, sources: 4 }],
  ['node', { node: 'researcher', question: 'q1 改写', findings: 2, sources: 3 }],
  ['node', { node: 'researcher', question: 'q3', findings: 0, sources: 0 }],
  ['node', { node: 'writer', report: '# r1 [1]' }],
  ['node', { node: 'reviewer', score: 6, passed: false, issues: ['缺数据'], follow_up: ['f1', 'f2'] }],
  ['node', { node: 'researcher', question: 'f1', findings: 1, sources: 2 }],
  ['node', { node: 'researcher', question: 'f2', findings: 1, sources: 2 }],
  ['node', { node: 'writer', report: '# r2' }],
  ['node', { node: 'reviewer', score: 8, passed: true, issues: [], follow_up: ['opt'] }],
  ['done', { thread_id: 'abc', report: '# r2' }],
] as const
for (const [event, data] of evs) {
  // 走一遍真实的 SSE 文本格式（sse-starlette 用 \r\n）
  const raw = `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n`.replace(/\r\n/g, '\n')
  handle(parseBlock(raw)!)
  if (event === 'node' && (data as any).node === 'planner') ok(state.workers.length === 3 && state.workers.every(w => w.status === 'active'), 'planner 后出现 3 个运行中的检索卡片')
  if (event === 'node' && (data as any).question === 'q3') ok(state.writer === 'wait', '改写问题未匹配时不猜测原卡片已完成或提前点亮 Writer')
  if (event === 'node' && (data as any).score === 6) ok(state.round === 2 && state.workers.filter(w => w.round === 2).length === 2, '审稿打回 → 进入第 2 轮，新增 2 个补查卡片')
}
ok(state.phase === 'done' && state.reviews.length === 2 && state.reviews[1].score === 8, '完成：2 次审稿，最终 8 分')
ok(state.workers.find(w => w.question === 'q2')!.sources === 4, '按问题文本匹配检索结果')
ok(state.workers.find(w => w.question === 'q1')!.sources === 0 && state.workers.find(w => w.question === 'q1 改写')!.sources === 3,
  '无法匹配的改写问题单列，原卡片统计不被覆盖')
ok(state.round === 2, '通过后不再新增轮次')
ok(JSON.parse(mem['dr-history'])[0].status === 'done', '历史记录写入 localStorage')
handle({ event: 'error', data: { thread_id: 'abc', error: '429', resumable: true } })
ok(state.phase === 'error' && state.resumable, 'error 事件 → 显示续跑按钮')

// 续跑：模拟"planner 完成后断开"的快照，检查界面恢复 + 续跑事件
const calls: string[] = []
;(globalThis as any).fetch = async (url: string) => {
  calls.push(url)
  if (url.endsWith('/resume')) {
    const body = ['start', { thread_id: 'zz' }, 'node', { node: 'researcher', question: 'a', findings: 1, sources: 2 },
      'node', { node: 'researcher', question: 'b', findings: 1, sources: 1 }, 'node', { node: 'writer', report: '# x' },
      'node', { node: 'reviewer', score: 8, passed: true, issues: [], follow_up: [] }, 'done', { thread_id: 'zz', report: '# x' }]
    let txt = ''; for (let i = 0; i < body.length; i += 2) txt += `event: ${body[i]}\r\ndata: ${JSON.stringify(body[i + 1])}\r\n\r\n`
    return new Response(txt, { status: 200 })
  }
  return new Response(JSON.stringify({ thread_id: 'zz', question: 'Q', finished: false, next: ['researcher', 'researcher'],
    sub_questions: ['a', 'b'], notes: 0, reviews: [], report: '' }), { status: 200 })
}
const p = resume('zz')
await new Promise(r => setTimeout(r, 0))
await p
ok(calls[0].endsWith('/research/zz') && calls[1].endsWith('/research/zz/resume'), '续跑先 GET 快照再 POST resume')
ok(state.phase === 'done' && state.workers.length === 2 && state.workers.every(w => w.status === 'done' && w.sources > 0), '续跑后 2 个检索卡片完成，报告到达')
ok(state.planner === 'done' && state.round === 1, '续跑不重跑 planner，轮次正确')
console.log(fail ? `失败 ${fail} 项` : '全部通过'); process.exit(fail ? 1 : 0)
