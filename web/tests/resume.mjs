// 断点续跑：中途断开连接 → GET 快照应为未完成 → resume 应从 checkpoint 继续直到 done
import { parseBlock } from '../src/api.ts'
const BASE = process.env.WEB ?? 'http://localhost:5173/api'
let fail = 0
const ok = (c, m) => { console.log((c ? '  ✓ ' : '  ✗ ') + m); if (!c) fail++ }
async function sse(path, body, stopWhen) {
  const ctrl = new AbortController(), evs = [], t0 = Date.now()
  const res = await fetch(BASE + path, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: body && JSON.stringify(body), signal: ctrl.signal })
  if (!res.ok) return { status: res.status, evs }
  const rd = res.body.getReader(), dec = new TextDecoder(); let buf = ''
  try {
    for (;;) {
      const { value, done } = await rd.read(); if (done) break
      buf += dec.decode(value, { stream: true }).replace(/\r\n?/g, '\n')
      let i; while ((i = buf.indexOf('\n\n')) >= 0) {
        const e = parseBlock(buf.slice(0, i)); buf = buf.slice(i + 2)
        if (!e) continue
        evs.push(e); console.log(`    [${((Date.now() - t0) / 1000).toFixed(1)}s] ${e.event} ${e.data.node ?? ''}`)
        if (stopWhen?.(evs)) { ctrl.abort(); return { status: res.status, evs, aborted: true } }
      }
    }
  } catch (e) { if (e.name !== 'AbortError') throw e }
  return { status: res.status, evs, t: (Date.now() - t0) / 1000 }
}
console.log('1) 开始研究，收到 planner 后立刻断开（模拟关页面/断网）')
const a = await sse('/research/stream', { question: '2026 年国产大模型 API 价格对比' },
  evs => evs.some(e => e.data.node === 'planner'))
const id = a.evs[0].data.thread_id
ok(a.aborted, `已断开 #${id}`)
await new Promise(r => setTimeout(r, 4000))
const s = await (await fetch(`${BASE}/research/${id}`)).json()
ok(!s.finished, `快照未完成 next=${JSON.stringify(s.next)} notes=${s.notes} 子问题=${s.sub_questions.length}`)
console.log('2) 续跑')
const b = await sse(`/research/${id}/resume`)
const names = b.evs.map(e => e.event === 'node' ? e.data.node : e.event)
ok(names[0] === 'start' && names.at(-1) === 'done', `续跑完成，事件：${names.join(' → ')}，耗时 ${b.t?.toFixed(1)}s`)
ok(!names.includes('planner'), '续跑没有重复执行 planner（从 checkpoint 继续）')
const s2 = await (await fetch(`${BASE}/research/${id}`)).json()
ok(s2.finished && s2.report.length > 200, `最终完成，报告 ${s2.report.length} 字`)
console.log(fail ? `失败 ${fail} 项` : '全部通过'); process.exit(fail ? 1 : 0)
