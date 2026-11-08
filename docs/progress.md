# 开发进度（2026-11-06 更新）

## 已完成
- MVP 多 Agent 研究流程 + 反思循环（61a8391 ~ 014657a）
- FastAPI SSE + 断点续跑 + 19 项接口测试
- 【历史版本 fc11b41，不是 2026-11-07 当前界面，也不能用这次未运行的新界面或 99 项旧证据代替】Vue3 前端：粒子背景、Agent 流程动画、报告打字机、续跑（fc11b41）
- RAG 本地知识库：向量+BM25+RRF+gte-rerank，上传/删除接口，前端面板；Hit@1 0.933，Ragas 忠实度 1.0（b5aa270）

- 续跑失败排查：根因是 8000 端口残留旧后端；测试加端口占用检测、服务端记录 traceback、网页检索降级。rag_fix_round1 19/19，test_rag 20/20，npm test 9/9，build 通过（按用户要求省 token 只跑 1 轮）

- 【历史运行自报，不证明当前 Langfuse 已配置、服务在线或 trace 仍在】Langfuse 可观测性：官方 compose 自部署（localhost:3000 已运行），observability.py 回调 + 检索 span，test_observability 4/4，test_rag 20/20。当前配置、服务和 trace 仍待最后统一核实

- 网页搜索替换：自部署 SearXNG（127.0.0.1:8080）+ ddgs 备用 + trafilatura 抓正文，Langfuse web_search span；test_search 8/8，test_rag 20/20，真实研究 108.8s

## 未完成（下次从这里继续）
- 速度与 Token 优化已实现（见下方本阶段记录），实际端到端收益与质量仍待验收
- Langfuse：旧进度写过“待注册并配置 API Key”，面试笔记另记过一次真实 trace。两者都是历史文档自报。本次没有读真实环境、没有启动服务，不能判断当前是否缺 Key。配置、服务和 trace 留到最后统一核实；不要申请、生成或覆盖密钥
- 重排把 Q1 挤出 Top5；评估集仅 15 题
- 后续：LightRAG/RAPTOR 对比实验仍是可选研究，没有做，也不作为当前架构
- 源码同步：53 个源码文件已推到私有仓库工作分支 `cursor/import-source-snapshot-41f1`，提交 `cf99145a64ba37813ff803a2b37aaec3903f507d`，PR https://github.com/Ri4o3c/deep-research/pull/1 仍打开、未合并。2026-11-07 的前端和文档改动还没提交或推送


## 2026-11-06 速度与 Token 优化（实现完成，待验收）
- 默认全文抓取 top 3 → top 2，可配置；单页 1500 字符、最终及相关性筛选资料区 6000 字符；本地资料优先并保留页码。详见 docs/search.md。
- 新增每次 researcher.research() 子问题内的有界 TTL/LRU 缓存、URL 去重、cache bypass；修复改写查询合并的来源 ID 冲突。缓存不跨任务/并行分支/续跑持久化，不保证实际命中率。
- 唯一一轮测试：2026-11-06 15:27:30.125735～15:27:48.370370（Asia/Shanghai），` .venv\Scripts\python tests/run_speed_round.py`；22 通过、0 失败、0 错误，退出码 0。测试主体 0.069s，包含导入的整轮墙钟约 18.245s，不是研究延迟。
- 全部离线 mock；真实研究请求 0，真实 LLM 调用 0。固定 8 条资料 fixture：旧资料拼接 12086 字符，新资料区 6000 字符；mock cold 搜索/抓页=1/2、hit/miss=0/1，同 session 重复查询后累计仍为 1/2、hit/miss=1/1。字符数不是 tokens；未实测 input/output/total tokens。
- 原始证据：docs/evidence/speed-token-20261106/{test-round.log,summary.json,fixture.json,exit-code.txt}。8000 无监听，未启动后端/5173，未杀进程；无 commit/push。
- 历史 108.8s / 46427 tokens 与 KB-only 41.9s / 18947 tokens 均非本轮受控基线。真实加速、token 降幅、质量影响均 pending，证据不足，留待验收；不声称省钱。
- SSE/resume 仅 mock 契约检查，真实强制中断/持久化续跑、真实 provider/KB/报告质量、Langfuse 上报均未执行；未重跑 Ragas/全量检索评测。所有既有 .idea 与 eval 未提交文件保留。


## 2026-11-06 单轮离线收尾验收（失败即停，未完成）
- 唯一命令：`.venv\Scripts\python -I -B tests/test_speed_final.py`；Asia/Shanghai 2026-11-06T18:07:25.198754+08:00 ～ 2026-11-06T18:07:28.247524+08:00，退出码 1，耗时 3.045336s。
- 实际进入断言 0 项（通过 0、断言失败 0），另有 1 个导入阶段 RuntimeError；不能写成 0 失败即通过。堆栈为 Chroma ONNXMiniLM_L6_V2 类定义 → pathlib.Path.home/expanduser。测试入口清空继承环境后无法推导 home 为静态推断，未修补或重跑核实。
- 尚未导入 app 执行对照或窄链路，配置对照数据、预算删源、writer/SSE/resume 均未覆盖；字符数与 tokens 未测，tokens=null。真实研究/LLM 0，网络/真实客户端/禁止文件边界命中均 0。
- 只新增本阶段单文件入口与最小证据；未改 app/docker/前端/.idea，未重跑旧 22/48 检查。证据：docs/evidence/speed-final-20261106/summary.json、round.log、exit.txt。失败已停止，不修补测试再跑；等待下一次明确授权。
- 本阶段不证明真实加速、省 token 或完整线上 SSE/resume。速度源码静态通过是任务书背景，本轮未重复静态审查，不将计划联测写成完成。




## 2026-11-07 开发与文档收尾（已实现，未运行验证）
- 测试政策：先完成开发和文档。非必要的阶段测试先停，最后做一次统一验收。本轮没有运行测试、构建、浏览器或服务。
- 上面 18:07 的导入失败记录保留，没有改写成成功。其后只在 `tests/test_speed_final.py` 的 chromadb 导入前加了 8 行，把 HOME、USERPROFILE、HOMEDRIVE、HOMEPATH 指到当次临时目录。清空继承环境、文件守卫、网络和客户端守卫、全部断言都还在。
- Windows 在 2026-11-06 23:31 跑了唯一的 run2：`.venv\Scripts\python.exe -I -B tests/test_speed_final.py`。Python 进程约 3.082 秒，测试主体约 2.723 秒，外层包装约 4.244 秒。99 项检查为真，失败 0，退出码 0。范围是合成输入和 mock 模型边界下的真实 graph、researcher 到 writer 的提示与引用编号、checkpoint 恢复、事件生成和 SSE 编码。
- 这次 99 项不是 HTTP/ASGI，不是真实 LLM，不是联网检索，不是 embedding 或 rerank，不是生产 token 时延，不是崩溃后重启持久化，也不是 reflection 追加轮次。guard 的 network、external_client、forbidden_file 计数为 0，不能当成系统级没有访问。summary 里的 real_llm_calls 和 real_research_requests 为 0 是固定声明，不是独立计量。不要用 108.8 秒、46427 tokens、Hit@1 或 Ragas 历史自报充当这次结果。
- 99 项发生在这次前端改动之前，不能证明新界面。当前前端是低噪声中文研究工作台：去掉粒子背景和鼠标高光，统一各区域以及空、加载、错误、禁用、完成状态。研究输入、SSE、引用、知识库和续跑仍走现有接口。状态：已实现，未运行验证。真实速度、token 和报告质量仍然未知。
- Q1 被重排挤出 Top5、评估集只有 15 题，仍是实证缺口。没有为这一题改检索权重，也没有新造 ground truth。LightRAG/RAPTOR 继续留作可选研究。
- 本轮又按源码补了前端生命周期，仍未运行：SSE 按 CR/LF/CRLF 分帧并保留跨 chunk 的回车与 UTF-8 尾字节；按 WHATWG，只有空行派发，流结束时丢弃尚未遇到空行的事件。新研究、续跑、切换历史和取消在发出请求前单飞。客户端中止只停止本页读取，不表示服务端图已停。这些都不在 99 项旧证据里。
- `web` 目录的 `npm test` 已串联 `node tests/final-offline.test.ts`。用例已编写，本轮没有运行，没有通过数。Node 能否直接执行这些 TypeScript 文件留到最后核实。
- 本轮改动尚未 commit 或 push。私有 PR https://github.com/Ri4o3c/deep-research/pull/1 仍未合并。云端快照不含 docker/、真实 .env、fixture 和旧 evidence。这只说明快照里没有，不说明某台机器上的部署状态。

- 最终离线回归已写入 `web/tests/final-offline.test.ts`，并接到现有 `web` 目录的 `npm test`（`node tests/store.test.ts && node tests/refs.test.ts && node tests/final-offline.test.ts`）。用例已编写，本轮没有运行，没有通过数。Node 能否直接执行这些 TypeScript 测试文件留到最后核实。99 项旧证据不覆盖这版界面，也不覆盖这些新用例。


## 2026-11-07 本机接手后的实现与受控验收

本节是 run12 交付之后的新记录；上文各 run 的“未运行”仍描述当时状态，不改写历史。

本轮已逐路径核实、备份并同步界面与文档候选。HOME 测试已等于候选，未重复写入或重跑；本地 README 原本不存在，按新增文件处理。保留本机 main 原 HEAD 与其他未提交修改，没有提交、推送或合并。

修复了 final-offline 等待器对 undefined 调用 .then、未受限 SSE 等待、失败时 read/GET 未收束与全局替身还原问题；另修正两处类型错误、全局 CSS 中的 :deep()、.ok/.bar 作用域、窄屏溢出与键盘焦点。取消文案说明只停止本页接收；历史快照缺失统计显示未知，列表读取失败不再显示空库结论。

最终结果：前端 50 项离线检查（store 12 + refs 9 + final-offline 29）通过；npm run build（含 vue-tsc）通过且无警告。真实 FastAPI/ASGI/SSE 路由的 25 项受控检查通过，但 graph/KB 为合成替身、SQLite 内存隔离、环境中无供应商配置。浏览器检查完成/错误/续跑/历史/离线/安全引用/键盘焦点，390 和 300 CSS 像素无横向溢出。两次故意失败的清理探针均保留原错误、恢复全局且无活跃 timeout。

本轮没有调用真实模型、搜索、embedding、rerank 或 Langfuse；没有读写真知识库/数据库，也没有测真实质量、token、成本或提速。Q1+15题、真实进程中断恢复、真实观测仍待明确样本及调用/费用上限。完整记录见本次交付的《DeepResearch-本轮完成记录.md》与证据包。旧99与本轮结果不相加。


## 2026-11-07 Q1 限额真实验收补记

仅Q1，未跑15题：真实embedding、rerank与deepseek-flash回答已执行，原评测库副本665块，原库指纹不变。Q1最终top5未命中DPR/BART原oracle，回答只给泛化模型类型，验收needs_rework；没有为单题调权或补答案。

共4次供应商尝试（含一次占位key401，不含用户有效key；三次Q1成功调用），成功调用按供应商usage及官方最高单价计费上界约0.0044075元，未超用户5元/30次上限，不是账单核销。embedding15、rerank3800、回答输入488/输出48 token。模型工厂修复旧名称下线兼容，默认deepseek-flash非思考并支持DEEPSEEK_MODEL、评测输出与重试上限。其余真实验收和原15题仍待后续范围。完整证据见本轮《DeepResearch-Q1-实测结果.md》。


## 2026-11-07 Q1 相邻上下文方案复测

尝试：重排前补齐同文档、同页的相邻片段，未改权重、模型、问题或答案。

结果：3项局部离线检查通过；仅做1次真实Q1复测。回答补出了DPR，但仍未说明BART。虽然原关键词OR规则出现命中，完整答案仍未通过人工核对。

扩展后前5条重排分数均低于现有0.25门槛，会影响实际知识库结果接收。因此该方案已撤回，生产检索代码恢复到尝试前原字节，失败候选和记录保留在同名复测目录中。没有降低门槛来凑通过。

本轮新增3次供应商请求；累计7/30次，按返回usage和最高公开单价估算的费用上界为0.0146006元，未超5元，不是账单核销金额。原评测库未改，没有跑完整15题、整套研究或重复前端测试。

当前状态：原Q1问题仍待修复。下一次应处理重排的证据选择问题，避免继续扩大输入使原有评分门槛失效。


## 2026-11-07 Q1 修复确认：单例通过

跨语言查询补充与qwen3-rerank已落地。问题/答案/k/召回上限/0.25门槛未改；真实复测首条是原论文第3页直接证据，回答“检索器用 DPR 初始化，生成器用 BART-large”。相邻片段扩展仍已撤回。局部离线检查4项；仅Q1，不跑其余14题。累计18次真实供应商尝试，费用保守上界0.0250634元（非账单），原评测库不变。单例通过不等于整体质量通过；整图、Ragas、Langfuse与完整评测仍未验。详见《DeepResearch-Q1-修复结果.md》。


## 2026-11-07 三题轻量回归与交付

Q6、Q4、Q13均第1条检索命中，人工核对支持题目要求；只检索，不生成回答、不跑Ragas。Q1仍保留此前单例真实回答通过结论。累计24次请求，费用保守上界0.0339936元（非账单）；原评测库未改。新增演示稿与验证记录，完整15题、真实整图/恢复、Langfuse仍待验。
