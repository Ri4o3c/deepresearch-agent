// node tests/refs.test.ts —— 引用解析：网页 + 本地知识库来源
import { parseRefs, linkCitations } from '../src/refs.ts'

let pass = 0, fail = 0
function ok(cond: boolean, name: string) { if (cond) { pass++; console.log('  ✓', name) } else { fail++; console.log('  ✗', name) } }

const report = `## 摘要
合同审查超时 120 秒[1]，LangGraph 支持断点续跑[2][3]。

# 参考资料
[1] 📄本地：运维手册.txt 第1页 - /api/kb/docs/ab12cd/file#page=1
[2] LangGraph Docs - https://langchain-ai.github.io/langgraph/
- [3] 📄本地：notes.pdf 第3页 - /api/kb/docs/ef34/file#page=3`

const refs = parseRefs(report)
ok(refs.size === 3, '解析出 3 条参考资料')
ok(refs.get(1)!.local && refs.get(1)!.url === '/api/kb/docs/ab12cd/file#page=1', '本地来源识别 + 带页码链接')
ok(!refs.get(2)!.local, '网页来源 local=false')
ok(refs.get(3)!.local, '列表符号开头的本地来源')
const out = linkCitations(report, refs)
ok(out.includes('[\\[1\\]](/api/kb/docs/ab12cd/file#page=1 "📄本地：运维手册.txt 第1页")'), '正文本地引用转为带 📄 标题的链接')
ok(out.includes('[\\[2\\]](https://langchain-ai.github.io/langgraph/ "LangGraph Docs")'), '正文网页引用不变')
ok(out.includes('[打开原文](/api/kb/docs/ab12cd/file#page=1)'), '参考资料行的本地链接可点击')
ok(out.includes('[2] LangGraph Docs - https://'), '参考资料行的网页链接保持原样')
const r2 = parseRefs('[1] doc 第2页 - /api/kb/docs/x/file#page=2')
ok(linkCitations('见[1]', r2).includes('"📄本地：doc 第2页"'), '缺少前缀时自动补 📄本地 标记')
console.log(`\n${pass}/${pass + fail} 通过`)
if (fail) process.exit(1)
