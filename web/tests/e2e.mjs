// 端到端：经 Vite 代理 (/api) 调真实后端，校验 SSE 事件形状 + 422 + 续跑接口
// 用法：node tests/e2e.mjs [question]   需先启动 uvicorn(8000) 和 npm run dev(5173)
import { parseBlock } from '../src/api.ts'
const BASE = process.env.WEB ?? 'http://localhost:5173/api'
const q = process.argv[2] ?? 'RAG 中混合检索和重排序能带来多大提升？'
let fail = 0
const ok = (c, m) => { console.log((c ? '  ✓ ' : '  ✗ ') + m); if (!c) fail++ }

async function sse(path, body) {
  const t0 = Date.now(), evs = []
  const res = await fetch(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body && JSON.stringify(body) })
  if (!res.ok) return { status: res.status, body: await res.json(), evs }
  const rd = res.body.getReader(), dec = new TextDecoder(); let buf = ''
  for (;;) {
    const { value, done } = await rd.read(); if (done) break
    buf += dec.decode(value, { stream: true }).replace(/\r\n?/g, '\n')
    let i; while ((i = buf.indexOf('\n\n')) >= 0) {
      const e = parseBlock(buf.slice(0, i)); buf = buf.slice(i + 2)
      if (e) { e.t = (Date.now() - t0) / 1000; evs.push(e)
        console.log(`    [${e.t.toFixed(1)}s] ${e.event}${e.data.node ? ' ' + e.data.node : ''}`
          + (e.data.score !== undefined ? ` ${e.data.score}/10 passed=${e.data.passed}` : '')
          + (e.data.sources !== undefined ? ` sources=${e.data.sources}` : '')) }
    }
  }
  return { status: res.status, evs }
}

console.log('1) /health'); ok((await fetch(BASE + '/health')).ok, 'health 经代理可达')
console.log('2) 非法输入')
for (const [b, why] of [[{ question: ' ' }, '空问题'], [{ question: 'x'.repeat(501) }, '超长'], [{}, '缺字段']]) {
  const r = await sse('/research/stream', b); ok(r.status === 422 && Array.isArray(r.body.detail), `${why} → 422 (${r.body?.detail?.[0]?.type})`)
}
console.log('3) 续跑不存在的任务'); { const r = await sse('/research/nope000/resume'); ok(r.status === 404, `404 ${r.body?.detail}`) }
console.log('4) 完整研究：' + q)
const r = await sse('/research/stream', { question: q })
const names = r.evs.map(e => e.event === 'node' ? e.data.node : e.event)
const id = r.evs[0]?.data.thread_id
ok(names[0] === 'start' && id, 'start 带 thread_id')
ok(names[1] === 'planner' && r.evs[1].data.sub_questions?.length >= 3, `planner 子问题 ${r.evs[1]?.data.sub_questions?.length} 个`)
ok(r.evs.filter(e => e.data.node === 'researcher').every(e => typeof e.data.question === 'string' && Number.isInteger(e.data.sources)), 'researcher 事件字段齐全')
ok(r.evs.some(e => e.data.node === 'writer' && e.data.report), 'writer 带 report')
ok(r.evs.some(e => e.data.node === 'reviewer' && typeof e.data.score === 'number' && Array.isArray(e.data.follow_up)), 'reviewer 带 score/follow_up')
const done = r.evs.at(-1); ok(done?.event === 'done' && done.data.report.length > 200, `done 报告 ${done?.data.report.length} 字，总耗时 ${done?.t.toFixed(1)}s`)
const refs = (done?.data.report.match(/^\s*[-*]?\s*\[\d+\].*https?:\/\//gm) || []).length
ok(refs > 0, `参考资料可解析出 ${refs} 条（前端引用可点击）`)
console.log('5) 已完成任务再续跑'); { const x = await sse(`/research/${id}/resume`); ok(x.status === 400, `400 ${x.body?.detail}`) }
console.log('6) GET 快照'); { const s = await (await fetch(`${BASE}/research/${id}`)).json(); ok(s.finished && s.report === done.data.report, 'finished=true，报告一致') }
console.log(fail ? `失败 ${fail} 项` : '全部通过'); process.exit(fail ? 1 : 0)
