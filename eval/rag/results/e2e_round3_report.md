# 星河智能 Agent 平台超时与告警策略及基于 LangGraph checkpointer 的故障恢复设计

## 摘要

星河智能 Agent 平台（2025 年 3 月上线，代号"北斗"）目前公开的超时策略只有两级：单次 Agent 任务默认 45 秒、合同审查任务 120 秒，模型层在主模型 DeepSeek-V3 连续 3 次超时后自动切换备用模型 Qwen-Max；告警方面仅定义了 P0（5 分钟内响应）与 P1（30 分钟内响应）两级，值班热线内线 8812，连续两次 P0 需 48 小时内提交复盘报告 [1]。平台编排层基于 LangGraph 实现 [1]，因此可直接利用其 checkpointer 在每个 super-step 后自动保存状态、以 thread_id 为索引实现断点续跑 [2][10]。但需注意：checkpointer 只提供状态持久化，不提供故障检测与自动恢复编排，两个 worker 同时恢复同一线程会导致重复执行 [22]。建议在现有两级超时之上补齐"模型调用级/节点级/任务级"三层超时，用节点级 timeout 与 retry_policy 承接瞬时故障 [17]，用 interrupt + 幂等键处理不可重放的副作用 [11][19]，并将 checkpointer 后端从实验级迁移到 PostgresSaver 以支撑生产长任务 [2][7]。

---

## 一、星河平台现状：超时与告警的已知事实与信息缺口

### 1.1 已确认的超时配置

根据本地运维手册（一手材料），星河平台当前公开的超时参数如下 [1]：

| 维度 | 配置 | 来源 |
|---|---|---|
| 单次 Agent 任务默认超时 | 45 秒 | [1] |
| 合同审查任务超时 | 120 秒（例外放宽） | [1] |
| 并行工具调用并发上限 | 8 | [1] |
| 模型层切换规则 | 主模型 DeepSeek-V3 连续 3 次超时 → 自动切换备用 Qwen-Max | [1] |

这实际上构成了"按业务类型分级"的第一层超时：客服问答、周报生成走 45 秒档，合同审查走 120 秒档 [1]。

### 1.2 已确认的告警机制

- **告警分级与响应时限**：P0（平台整体不可用）5 分钟内响应；P1（单个业务不可用）30 分钟内响应 [1]。
- **值班安排**：值班表每周一上午 10 点轮换，值班热线为内线 8812 [1]。
- **升级/复盘**：连续两次 P0 故障需在 48 小时内提交复盘报告 [1]。

### 1.3 必须如实说明的信息缺口

以下内容在现有资料中**没有**给出，本报告不做推测性填充：

1. **超时层级不完整**：资料只给出"任务级"超时（45s/120s），未给出网关层、编排层、工具层、LLM 调用层各自的独立超时设置 [1]。
2. **告警渠道不明**：资料只提到值班热线内线 8812，未提及邮件、短信、IM 等推送渠道 [1]。
3. **告警升级规则缺失**：未描述 P1 升级为 P0 的触发条件，也未定义 P2/P3 级别 [1]。
4. **告警与自动恢复无联动**：资料未说明告警是否会触发自动切换备用模型、自动重跑等动作 [1]。
5. **运行时状态存储方案未知**：资料只说明向量库用 Chroma（每晚 2 点全量备份、保留 14 天），未说明 Agent 运行时状态/检查点的存储方案 [1]。
6. **checkpointer 后端选型未知**：检索资料中没有任何关于星河平台现有 checkpointer 后端的信息 [28][1]。

> 结论：星河平台"超时层级"与"告警渠道"两部分属于**资料不足**，下文方案中涉及这两部分的内容均标注为"建议"，而非对现状的描述。

---

## 二、LangGraph checkpointer 的能力边界：能做什么、不能做什么

### 2.1 机制要点

- **自动快照**：checkpointer 在每个 super-step（每个节点执行完成）后自动序列化并保存图状态，无需开发者手动 save；checkpoint tuple 包含 checkpoint 本体及关联的 config、metadata 和 pending writes [8][9][2][10]。
- **thread_id 语义**：是 checkpoint 存储的顶级索引 key（类似主键），用于区分会话/对话线程，**不是用户 ID**；一个用户可有多个线程，不同 thread_id 之间状态不会自动共享 [2][6]。
- **checkpoint_id 语义**：用于指定恢复到哪个历史版本；`get_tuple` 通过 config 中的 thread_id 与 checkpoint_id 获取检查点元组，`list` 用于 `get_state_history()` 列出历史快照 [5][2][6]。
- **断点续跑**：程序崩溃后用同一 thread_id 再次调用 `graph.stream(None, config)`，会从最近的检查点继续执行，已完成的节点不会重跑；同一 checkpoint_id 恢复两次结果相同（幂等性），可回溯任意历史 checkpoint [10][2]。

### 2.2 后端选型对比

| 后端 | 写入延迟 | 持久化 | 并发能力 | 定位 |
|---|---|---|---|---|
| MemorySaver（InMemorySaver） | <1ms | 进程重启即丢失 | 单进程 | 仅实验 [2][9] |
| SqliteSaver / AsyncSqliteSaver | 3-8ms | 是 | 写锁导致并发有限 | 实验与本地工作流 [2][9] |
| PostgresSaver / AsyncPostgresSaver | 5-15ms | 是 | 高并发，支持 DB 层多租户与 SQL 审计 | 生产 [2][7] |
| RedisSaver | 1-3ms | 是 | 高并发 | 部分资料提及 [2] |

### 2.3 关键限制（直接决定故障恢复设计）

1. **checkpointer 不是自动失败检测器**：状态会停留在崩溃节点之前，需自行实现"从断点恢复"逻辑（如 `get_state` 判断 current_node 后重新 invoke）[29]。
2. **检查点 ≠ 持久化执行**：PostgresSaver 只负责保存状态，故障检测、取检查点、用正确线程标识符触发恢复仍需人工或自建机制；**两个 worker 同时恢复同一线程会导致重复执行**——"框架提供持久化，你提供编排" [22]。
3. **interrupt 恢复会重跑节点开头逻辑**：图从该节点开头重新执行全部逻辑，而非从中断处继续；因此 interrupt 之前只能做可安全重放的操作 [12][15][11]。
4. **存储成本与安全**：每个 super-step 都保存 checkpoint 带来存储成本，需定期清理（如 `delete_thread`）；checkpoint 可能含用户输入、模型回复、工具结果，需加密、最小权限、备份与日志脱敏；不应把 checkpointer 当长期用户资料库 [6][7][4][2]。
5. **PostgresSaver 首次使用需 `setup()` 初始化表结构**；数据库连接生命周期需由应用管理，不应每请求重建连接 [7][2]。

---

## 三、融合超时分级、告警联动与断点续跑的故障恢复方案（建议）

> 说明：以下为基于通用实践与 LangGraph 官方能力推导的**建议方案**，非星河平台现状描述 [28][1]。

### 3.1 超时分级：从两级扩展为三层

在现有"任务级"（45s/120s）之上补齐两层 [1][17][18]：

| 层级 | 建议配置 | 实现手段 | 依据 |
|---|---|---|---|
| LLM 调用级 | 单次模型调用设 `run_timeout`，配合 `idle_timeout` 检测无进展 | 节点级 `TimeoutPolicy` | [17][18] |
| 节点/工具级 | 每个工具节点设 `run_timeout`；阻塞 I/O 用 `asyncio.to_thread` 包装 | `add_node(timeout=TimeoutPolicy(...))`，仅适用于异步节点 | [17] |
| 任务级 | 沿用现有 45s / 120s | 现有平台配置 | [1] |

补充硬约束：LangGraph 自身 `recursion_limit` 默认 25 步，用于防止反思回路死循环 [10]；建议在状态中记录轮次并设最大轮数 [10]。业界实践还建议增加 stalled watchdog，在 LLM 调用超时时自动标记 failed（如错误码 `LLM_STALLED`），避免系统无限停留在 running 状态 [27]。

### 3.2 重试与降级：按副作用等级分类

重试策略的地基是**按副作用等级给每一步分类** [19]：

- **read（纯读）**：重试永远安全。
- **idem_write（带幂等键的幂等写）**：重试安全。
- **non_idem_write（发消息、发起审批、触发付款等）**：重试会出问题，必须先确认真实状态再决定是否执行；判断标准是"接口没有查询能力就不要标成可重试" [19]。

在 LangGraph 中通过 `add_node` 的 `retry_policy=` 按异常类型与退避设置自动重跑（如 `max_attempts`、`delay`、`retry_on=TimeoutError/ConnectionError/RateLimitError`），`NodeTimeoutError` 默认可重试；超时与重试、错误处理器按固定顺序组合：先由重试策略决定是否重试，重试耗尽后才运行错误处理器 [16][17]。重试只用于瞬时错误而非关键性或持续性失败，配合 fallback 节点做优雅降级 [16]。

幂等键的稳妥构造是 `hash(task_id + step_seq + canonical(params))`：task_id 避免跨任务撞键，step_seq 用序号而非步骤名（同名步骤在循环中可能合法多次执行），canonical(params) 需剔除时间戳、随机数、请求 ID 等易变字段 [19]。

### 3.3 断点续跑：checkpoint 触发点与恢复编排

**触发点设计**：在 ReAct 主循环关键节点——LLM 调用完成后、工具执行完成后——触发 checkpoint，保存完整执行状态（迭代次数、消息历史、已完成工具调用、重试状态、Agent 上下文）；恢复时优先用缓存快照（KV），兜底用事件溯源（只能恢复到 step 级别）[27]。

**恢复编排（checkpointer 不自动完成的部分）** [22][29]：

1. 用 `get_state` 判断 current_node，再决定重新 invoke 还是 `update_state` 回滚到指定检查点重跑 [29]。
2. **thread_id 必须稳定**：每次刷新换 thread_id 则 checkpoint 形同虚设 [2][29]。
3. **防重复恢复**：两个 worker 同时恢复同一线程会导致重复执行，需加分布式锁或单 worker 归属机制 [22]。
4. **执行与推送分离**：用 BackgroundExecutor 独立执行、事件写 DB（streamEvents）作为唯一真相源，SSE 断开不影响执行，重连后 poll 增量事件，事件缓冲每 500ms 或 10 条 flush [27]。

**收益参考**：15 步选品任务第 12 步因外部服务超时失败，过去重跑全部 15 步约 6 分钟，断点恢复约 30 秒 [27]。

### 3.4 interrupt 与幂等设计

- `interrupt()` 会抛出 GraphInterrupt 暂停图执行，客户端用 `Command(resume=值)` 恢复；**图从该节点开头重新执行全部逻辑**，若节点内有多个 interrupt，按节点内顺序用 resume 值列表按 index 匹配 [12][15][10]。
- 因此：interrupt 之前只做可安全重放的操作（如用 upsert 写入 pending 状态，执行 1 次与 5 次效果相同），副作用尽量放到 interrupt 之后（如审批通过后再写审计日志），或将"中断"与"副作用"拆成两个节点 [11]。
- 节点内需要异常处理时只捕获关心的错误（如 NetworkError），其余必须原样重新抛出，否则会"吃掉" interrupt [11]。
- interrupt 依赖 checkpointer，未启用检查点则无法使用 [10]。

### 3.5 告警联动：补齐"告警 → 自动动作"链路（建议）

现有资料只定义了 P0/P1 的响应时限与值班热线，**未定义告警与自动恢复动作的联动规则** [1]。建议补充：

- **告警触发源**：模型层连续 3 次超时（已有切换规则 [1]）、任务级超时、checkpoint 长时间停留同一节点（watchdog [27]）、恢复失败次数超阈值。
- **自动动作分级**：P1 级（单业务不可用）可自动触发备用模型切换与断点重跑；P0 级（平台整体不可用）走人工值班热线 8812 [1]。
- **升级规则**：建议明确 P1 持续未恢复升级为 P0 的时间阈值（现有资料未定义 [1]）。
- **通知渠道**：现有资料仅提及内线 8812，建议补充 IM/短信等渠道（属资料缺口，需平台方确认 [1]）。

---

## 四、验证方法与演进路线（建议）

### 4.1 最小故障验证（上线前必做）

业界建议做小型故障验证：**在工具提交后强制退出再重启，看是否重复创建对象** [20]。具体可验证：

1. 同一 thread_id 崩溃后恢复，已完成节点不重跑 [10]。
2. 同一 checkpoint_id 恢复两次结果相同（幂等性）[2]。
3. 两个 worker 同时恢复同一线程是否产生重复副作用 [22]。
4. interrupt 节点重跑时，interrupt 之前的 upsert 操作是否幂等 [11]。

### 4.2 演进路线（分阶段）

| 阶段 | 动作 | 依据 |
|---|---|---|
| 阶段一：基线加固 | checkpointer 从 MemorySaver/SQLite 迁移到 PostgresSaver；`setup()` 初始化表结构；连接池由应用管理 | [2][7] |
| 阶段二：超时分层 | 补齐 LLM 调用级、节点级 timeout；配置 retry_policy 与 fallback 节点 | [17][16] |
| 阶段三：幂等与副作用治理 | 定义步骤表并标 effect_class；落只记不改的事件日志；加幂等键与去重表（从 non_idem_write 开始）；给非幂等写补 query | [19] |
| 阶段四：恢复编排 | 实现 get_state 判断 + 自动重 invoke；加分布式锁防重复恢复；执行与推送分离 | [29][22][27] |
| 阶段五：告警联动 | 补齐告警触发源、自动动作分级、升级规则、通知渠道 | [1]（现状缺口） |

> 落地顺序建议：先定义步骤表并标 effect_class → 落只记不改的事件日志 → 加幂等键与去重表（从 non_idem_write 开始）→ 给非幂等写补 query → 最后做补偿。常见做反的是先设计漂亮补偿流程，而八成"失败"其实靠回查就能确认成功 [19]。

### 4.3 何时需要考虑 Temporal 类持久化执行

有团队在 LangGraph + Redis 上构建生产 Agent 后评价"概念上很强大，但实践中很脆弱"，迁移到 Temporal 以消除自维护的重试与恢复逻辑 [22]。Temporal 的核心模型是 Workflow = 确定性逻辑（可重放）、Activity = 副作用操作（不可重放），崩溃后 Workflow 重放历史事件、Activity 结果从事件日志恢复；超时需配置 StartToClose + ScheduleToClose + HeartbeatTimeout [21]。若星河平台的长任务（如合同审查）对"精确步骤恢复、不重跑已完成工作"要求很高，且团队不愿自维护恢复编排，可评估 Temporal 类方案 [22][21]。但需注意：直接改代码会导致重放 Non-determinism 错误，需用 `workflow.GetVersion()` 做版本门控 [21]。

---

## 结论与建议

1. **现状结论**：星河平台公开的超时策略只有任务级两级（45s/120s），告警只有 P0/P1 两级响应时限与值班热线，**超时层级、告警渠道、告警升级规则、checkpointer 后端选型均属资料不足**，需平台方补充 [1]。
2. **技术可行性**：平台编排层基于 LangGraph [1]，checkpointer 可在每个 super-step 后自动保存状态、以 thread_id 为索引实现断点续跑，已完成的节点不会重跑 [10][2]。
3. **关键风险**：checkpointer 不是自动失败检测器，两个 worker 同时恢复同一线程会导致重复执行 [22][29]；interrupt 恢复会重跑节点开头逻辑，副作用必须幂等 [11]。
4. **按场景选择建议**：
   - **短任务（客服问答、周报生成，45s 档）**：节点级 timeout + retry_policy + fallback 节点即可，checkpointer 用 PostgresSaver 兜底 [17][16][2]。
   - **长任务（合同审查，120s 档）**：必须补齐幂等键与去重表、执行与推送分离、防重复恢复锁 [19][27][22]。
   - **对精确步骤恢复要求极高的场景**：评估 Temporal 类持久化执行方案 [22][21]。
5. **落地优先级**：先做最小故障验证（工具提交后强制退出再重启，看是否重复创建对象）[20]，再按"基线加固 → 超时分层 → 幂等治理 → 恢复编排 → 告警联动"五阶段推进。

---

## 参考资料

[1] 📄本地：xinghe_ops_handbook.txt 第1页 - /api/kb/docs/83540d3ecb/file#page=1
[2] 【AI面试八股文 Vol.1.1 | 专题6：Checkpoint 机制】Checkpoint机制：状态持久化与断点恢复 - aimagician - 博客园 - https://www.cnblogs.com/aimagician/p/19897304
[3] 【LangGraph】什么是检查点（Checkpointer） - https://blog.csdn.net/u013172930/article/details/148036704
[4] LangGraph RCE Chain: Checkpointer Flaw Enables Server Takeover – Lab Space - https://labs.cloudsecurityalliance.org/research/csa-research-note-langgraph-rce-chain-20260614-csa-styled
[5] 概述 - LangChain 框架 - https://langgraph.com.cn/concepts/persistence.1.html
[6] LangChain 对话记忆 — Checkpointer | 菜鸟教程 - https://www.runoob.com/langchain/langchain-checkpointer.html
[7] LangGraph - 03：持久化机制，让 AI 智能体拥有"记忆" - https://zhuanlan.zhihu.com/p/1972437682400519404
[8] langgraph.checkpoint | LangChain Reference - https://reference.langchain.com/python/langgraph.checkpoint
[9] Checkpointers - Docs by LangChain - https://docs.langchain.com/oss/python/langgraph/checkpointers
[10] 📄本地：langgraph_notes.md 第1页 - /api/kb/docs/07d79e09f9/file#page=1
[11] [LangGraph] 中断注意事项 - Zhentiw - 博客园 - https://www.cnblogs.com/Answer1215/p/19757800
[12] 【Agent的革命之路——LangGraph】人机交互中的interrupt&Command在 LangGraph 中， - 掘金 - https://juejin.cn/post/7458271395669147702
[13] LLM——浅谈 LangGraph 中断式工作流：构建一个可交互的问答流程 - https://blog.csdn.net/weixin_43844521/article/details/149982725
[14] LangGraph1.0速通指南（二）—— LangGraph1.0 条件边、记忆、人在回路 - https://zhuanlan.zhihu.com/p/1986064086958628943
[15] 类型 - LangChain 教程 - https://github.langchain.ac.cn/langgraph/reference/types
[16] langgraph-interview-questions/questions/advanced/q53-how-do-rollback-and-retry-mechanisms-work-in-langgraph.md at main · shahshrey/langgraph-interview-questions · GitHub - https://github.com/shahshrey/langgraph-interview-questions/blob/main/questions/advanced/q53-how-do-rollback-and-retry-mechanisms-work-in-langgraph.md
[17] Fault tolerance - Docs by LangChain - https://docs.langchain.com/oss/python/langgraph/fault-tolerance
[18] Fault Tolerance in LangGraph: Retries, Timeouts and Error Handlers - https://www.langchain.com/blog/fault-tolerance-in-langgraph
[19] Agent 的状态与持久化：断点续跑、幂等与副作用补偿-腾讯云开发者社区-腾讯云 - https://cloud.tencent.com/developer/article/2752149
[20] 让 Agent 可靠运行：幂等、持久化与失败恢复 - 哥不是小萝莉 - 博客园 - https://www.cnblogs.com/smartloli/p/23507748
[21] ai-agent-book/zh/Part7-生产架构/第21章：Temporal工作流.md at main · Kocoro-lab/ai-agent-book · GitHub - https://github.com/Kocoro-lab/ai-agent-book/blob/main/zh/Part7-%E7%94%9F%E4%BA%A7%E6%9E%B6%E6%9E%84/%E7%AC%AC21%E7%AB%A0%EF%BC%9ATemporal%E5%B7%A5%E4%BD%9C%E6%B5%81.md
[22] 为什么长任务 AI Agent 会在生产环境中失败（以及修复它们的底层架构） - https://tianpan.co/zh/blog/2025/10/28/async-ai-agents-long-horizon-tasks
[23] Durable Execution meets Durable Sessions: Resilient AI Agents with Temporal and Ably | SystemsDigest - https://systemsdigest.com/videos/durable-execution-meets-durable-sessions-resilient-ai-agents-temporal-and-ably
[24] Announcing our Investment in Temporal: Agents Make Mistakes. Your Workflows Can't. - Lightspeed Venture Partners - https://lsvp.com/stories/announcing-our-investment-in-temporal-agents-make-mistakes-your-workflows-cant
[25] Durable Execution Solutions | Temporal - https://temporal.io
[26] How We Keep Long-Running AI Agents Alive with Temporal - https://medium.com/@pankaj_pandey/how-we-keep-long-running-ai-agents-alive-with-temporal-d5299227f4e4
[27] 从 Prompt 到 Harness：企业级 Agent 工程的完整演进之路 - https://zhuanlan.zhihu.com/p/2065087443544565000
[28] Agent长任务可靠性设计，暂停、恢复、续跑与崩溃重启完整 ... - https://blog.csdn.net/u013970991/article/details/162673583
[29] LangGraph 状态管理实战：Checkpoint、Thread State 与失败恢复 | Easton - https://eastondev.com/blog/zh/posts/ai/20260424-langgraph-agent-architecture