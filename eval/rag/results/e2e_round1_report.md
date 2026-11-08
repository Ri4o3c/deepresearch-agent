# 星河智能 Agent 平台超时与告警策略及基于 LangGraph Checkpointer 的故障恢复设计

## 摘要

星河智能 Agent 平台的超时与告警信息仅来自一份自述为“内部虚构示例文档”的运维手册：单次任务默认超时 45 秒、合同审查任务 120 秒，主模型连续 3 次超时自动切换至 Qwen-Max；告警侧只定义了 P0/P1 分级与响应时限（5 分钟 / 30 分钟），**未给出任何告警指标阈值、通知渠道清单或抑制规则**，因此不能视为权威的官方定义 [1][2]。LangGraph 的 checkpointer 在每个 super-step 结束后持久化完整 State，以 `thread_id` 定位线程、`checkpoint_id` 定位版本，恢复时从最近检查点续跑、已完成节点不重跑 [8][10][15]。资料中**不存在**“平台超时/告警事件自动触发 LangGraph 挂起或续跑”的现成机制，本文给出的联动方案是由超时配置、告警分级、checkpoint 恢复与 interrupt/Command 等独立能力拼接推导出的**设计建议**，而非资料记载的事实 [1][2][8][23][24][26]。设计核心是：以持久化 checkpointer（生产用 Postgres）为恢复底座，把平台超时视为“可恢复的中断信号”而非失败，配合节点级 RetryPolicy、幂等副作用与人工介入点，实现超时挂起—告警分级—自动或人工续跑的闭环。

---

## 一、平台超时策略：已知事实与资料缺口

### 1.1 已明确的超时基线

| 项目 | 取值 | 来源 |
|---|---|---|
| 单次 Agent 任务默认超时 | 45 秒 | [1][2] |
| 合同审查任务超时 | 120 秒（例外放宽） | [1][2] |
| 模型层自动切换 | 主模型 DeepSeek-V3 连续 3 次超时 → 备用模型 Qwen-Max | [1][2] |

需要强调来源可靠性：两份资料内容完全一致（同一份运维手册第 1 页），且该文档自述为“内部虚构示例文档，仅用于知识库测试”，**并非正式官方产品文档**，上述数字只能作为设计输入参考，不能作为权威依据 [1][2]。

### 1.2 资料不足的部分（如实说明缺口）

- **配置项字段名缺失**：资料未给出 timeout 参数在配置文件中的具体写法，也未说明节点级（node-level）超时能否单独配置 [1][2]。
- **超时后行为未定义**：资料未说明任务超时后是重试、中断还是标记失败，仅提及模型连续超时触发模型切换 [1][2]。
- **无节点级超时语义**：平台层只有“任务级”超时，LangGraph 侧的节点级超时需自行用 `signal.alarm` 等方式兜底 [28]。

> 结论：平台超时策略目前只能确认“任务级 45s/120s + 模型级 3 次切换”这一层，节点粒度与超时后处置语义属于空白，需在设计中自行补齐（见第四章）。

---

## 二、平台告警策略：分级已知，指标与抑制缺失

### 2.1 已明确的告警分级与响应要求

- **P0**（平台整体不可用）：5 分钟内响应；
- **P1**（单个业务不可用）：30 分钟内响应；
- 值班表每周一上午 10 点轮换，值班热线内线 8812；
- 连续两次 P0 故障需在 48 小时内提交复盘报告 [1][2]。

### 2.2 资料不足的部分

本地手册**未给出**任何告警指标（超时率、失败率、延迟）的阈值、统计粒度、通知渠道清单或抑制规则配置，也未说明告警策略在哪个控制台或配置文件中定义 [1][2]。手册中的 45s/120s 与“3 次超时切换”是任务/模型层参数，**不是告警阈值** [1][2]。

### 2.3 可借鉴的通用告警配置范式（非星河平台）

由于平台侧告警配置细节缺失，以下通用范式可作为补齐时的参考模板：

- **触发条件构成**：指标 + 比较关系 + 阈值 + 统计粒度 + 持续 N 个数据点。示例：CPU 利用率 > 80%、粒度 5 分钟、持续 2 个数据点触发；重复通知可选不重复 / 5 分钟 / 10 分钟 / 周期指数递增，24 小时内按设定频率重复、满 24 小时后每天一次 [3]。
- **通知渠道与优先级匹配**：每个告警策略最多绑定三个通知模板；高优先级用电话、短信等强打扰渠道，低优先级用邮件、IM 等弱打扰渠道 [3][6]。
- **抑制规则**：新建 Inhibit Rules，设置源（Source Matcher）、目标（Target Matcher）与条件（Equal，源与目标须具有相同标签值）；示例为 CPU 负载 >90% 抑制同一 instance 上响应时间 >500ms 的告警；需避免某条告警同时匹配源与目标导致自我抑制 [5]。
- **阈值与收敛**：阈值应结合历史数据、业务规律与响应能力设置，周期性指标可用动态阈值；抑制可基于优先级、依赖关系与故障对象语义设计，高优先级或上游对象告警时降低下游通知强度；计划内维护窗口用静默（保留记录、仅停止通知）[6]。
- **AI 应用专项**：生产核心应用建议同时配置错误率、响应耗时、Token 消耗与流量异常告警；设阈值前先看数据预览或历史曲线，避免阈值过低产生噪音 [7]。

> 说明：以上均为通用平台经验，**不能反推为星河平台的实际配置**，仅作为补齐缺口时的设计参照。

---

## 三、LangGraph Checkpointer 的恢复机制

### 3.1 快照粒度与定位键

- **粒度**：checkpointer 在每个执行步骤（step / super-step）结束后自动把完整 State 序列化为 Checkpoint，即每个节点执行完就拍一张快照，而非任务结束后才保存 [8][9][10][14][15]。
- **thread_id**：恢复时的定位指针，通过 `config={"configurable": {"thread_id": ...}}` 指定加载哪份状态；复用同一 thread_id 恢复该线程最新 checkpoint，换新值则开启全新空状态线程；未配置 checkpointer 时传 thread_id 也无记忆 [8][10][11][14]。
- **checkpoint_id**：单调递增，配合 parent_checkpoint_id 串成链；可向前续跑或回溯到更早版本重走分支，同一 checkpoint_id 恢复两次结果相同（幂等）；存储主键为三元组 `(thread_id, checkpoint_ns, checkpoint_id)` [8][10][14]。

### 3.2 存储后端选择

| 后端 | 特性 | 适用场景 |
|---|---|---|
| MemorySaver | 进程内存，重启即丢 | 测试 / demo [8][10][11] |
| AsyncSqliteSaver | 本地 SQLite 文件，单机持久化（DeepAgents CLI 默认，落盘 `~/.deepagents/sessions.db`） | 单机 [8][10][11] |
| AsyncPostgresSaver | 生产、多实例共享 | 生产环境 [8][10][11] |

生产环境应使用持久化 checkpointer [8][10][11]。

### 3.3 中断与恢复的工作方式

- 节点内调用 `interrupt()` 会挂起图执行并保存当前图状态（含错误状态），payload 需 JSON 可序列化；前提是配置 checkpointer 并在 config 中提供 thread_id [11][15]。
- 人工确认后用 `Command(resume=值)` 恢复；程序崩溃后用同一 thread_id 再次调用 `graph.stream(None, config)` 即从最近检查点继续，已完成节点不会重跑；无 checkpointer 时无法使用 interrupt [13][15]。

---

## 四、故障恢复设计建议（推导方案，非资料记载事实）

> 资料缺口声明：检索资料中**没有任何一条**明确描述“平台超时/告警事件自动触发 LangGraph 挂起或自动续跑”的现成机制或代码；以下方案由超时配置、告警分级、checkpoint 恢复、interrupt/Command 等独立能力拼接推导，属于**设计建议** [1][2][8][23][24][25][26][28]。

### 4.1 总体思路：把超时当作“可恢复的中断信号”

平台任务级超时（45s / 120s）触发时，不应直接标记任务失败，而应视为一次“挂起”信号：由于 checkpointer 已在每个节点后写入快照，只要保留同一 thread_id，即可在后续从断点续跑，无需用户重新描述问题 [8][25]。这要求：

1. **生产环境必须使用持久化 checkpointer**（AsyncPostgresSaver），否则进程重启或跨实例调度时状态丢失，恢复能力失效 [8][10][11]。
2. **thread_id 与平台任务 ID 绑定**，作为跨系统关联键，使平台侧超时事件能定位到对应 checkpoint [8][29]。

### 4.2 超时后的处置分层

| 层级 | 处置方式 | 依据 |
|---|---|---|
| 模型层 | 主模型连续 3 次超时自动切换 Qwen-Max（平台已有能力） | [1][2] |
| 节点层 | 用 RetryPolicy 按节点配置指数退避、可选 jitter 与可重试异常 predicate；可用 `set_node_defaults` 设默认错误处理器，节点级 `error_handler=` 优先且不会无限递归 | [22] |
| 任务层 | 超时即挂起，保留 checkpoint，等待自动续跑或人工介入 | [8][25] 推导 |

### 4.3 自动续跑的实现

- 以 `input=None` 调用 `graph.invoke(None, config)`（或 stream）表示“从已保存状态继续”，config 需带正确的 thread_id 与 checkpoint_id；也可用 SDK 的 `client.runs.wait/stream` 配合 thread_id + checkpoint_id 恢复 [24][26]。
- 启用持久化执行后，可从最后一个成功的 checkpoint 自动恢复（例如 LLM 提供方故障后），向同一线程标识执行并传入 None 作为输入；恢复起点是执行停止的那个“节点”的开头，若节点内调用子图，父图从调用子图的父节点开始、子图内从停止的具体节点开始 [20]。
- 长任务分段执行：可用 `signal.alarm` 对单段执行设超时并抛 TimeoutError，恢复时把 checkpoint 中的 intermediate_data、partial_output **合并**回当前上下文（不全量覆盖，以保留用户重试时的新参数），并记录 `_resumed_from` 段号 [28]。

### 4.4 告警与恢复流程的联动（设计建议）

将平台告警分级作为“是否升级到人工介入”的判定依据 [1][2]：

1. **超时事件产生** → 平台任务超时（45s/120s）或模型 3 次超时切换 [1][2]；
2. **自动续跑** → 若为瞬时故障（如模型提供方抖动），以同一 thread_id + checkpoint_id 传 None 自动续跑 [20][24][26]；
3. **告警分级** → 若续跑仍失败或影响面扩大：P1（单个业务不可用）30 分钟内响应，P0（平台整体不可用）5 分钟内响应，值班热线内线 8812 [1][2]；
4. **人工介入点** → 编译图时用 `interrupt_before` / `interrupt_after` 指定节点，执行到该处中断并记录 checkpoint；恢复时用 `Command(resume=...)` 并传入与首次调用相同的 config；也可用 `get_state_history` 定位断点前的 checkpoint 作为恢复目标 [23][25][26]；
5. **复盘** → 连续两次 P0 故障 48 小时内提交复盘报告 [1][2]。

**关键警示**：降级与告警必须显式联动。资料中的示例是 Redis 连接超时 500ms 后降级到 MemorySaver 并打一条告警，但降级期间状态不跨进程保留——说明若告警事件触发 checkpoint 后端切换而不做显式处理，恢复能力会静默失效 [8]。因此降级到 MemorySaver 应视为“恢复能力临时丧失”，需在告警中明确标注。

### 4.5 幂等性与副作用隔离（恢复正确性的前提）

由于恢复会重跑节点（interrupt 恢复时运行时把整个节点从头执行，interrupt 之前的代码会再次执行），必须遵守：

- **interrupt 前只做可安全重放的准备操作**，真实副作用放到 interrupt 之后，或拆分到独立节点（如 review_node 只准备审核数据，execute_node 执行真实动作）[20][17][18]。
- **不要把 interrupt 包在 try/catch 中**；不要在同一节点内重排 interrupt 调用顺序（多个 interrupt 的 resume 值按索引严格匹配）[11][21][17]。
- **人工输入校验**：每个节点调用只调用一次 `interrupt()`，把错误信息存入 state 后返回，用条件边循环回该节点，避免 `while True + interrupt()` 导致循环体指数级重复执行 [11][21]。
- **确定性重放**：把非确定性操作（随机数）与有副作用操作（文件写入、API 调用）包装在任务或节点中，使其结果从持久化层检索而不重复执行；尽量使用幂等操作、幂等键或校验已有结果 [19]。
- **幂等性密钥**：每个工具调用携带由工作流 ID 与步骤编号派生的唯一密钥，重放时外部服务识别重复密钥并返回缓存结果；按读写分离对工具分类，读操作可安全重试，写操作需幂等保护 [29]。
- **多步外部副作用**：单纯整体重试会破坏状态（如已订座但支付失败），应逐步单独重试，某步重试耗尽时只回滚已执行步骤（含失败步骤，因其结果未知），即 SAGA 模式 [22][30][32]。

### 4.6 可观测性埋点

- 上下文关联需在执行时由调用方/Agent/工具侧保存（事后生成的 Trace ID 无法补回远端调用关系），并区分业务 Session 与 Agent 原生 Session [31]。
- 增量采集需持久化采集游标与去重状态，游标只推进到完整记录边界并处理重启、截断、轮转、末尾半条记录；游标已保存不等于后端已收到数据，需更强交付保证时应另设持久化待发送队列、交付确认与后端幂等 [31]。
- 验收应覆盖多轮交互、并行工具、取消、错误、子 Agent 与重启恢复 [31]。

### 4.7 是否值得引入持久化执行的判断

WAL/持久化执行并非普适：单次 LLM 调用且无副作用的简单 Agent 用带指数退避的 try-catch 即可；当工作流超过 3–5 步、步骤有昂贵或不幂等副作用、执行超过几分钟、含人工审批暂停或故障成本高时才值得引入，决策依据是估算从头重跑的成本（token 成本 + 重复副作用 + 不一致处理工时）是否超过基础设施成本 [29]。星河平台的合同审查任务（120s 超时、多步审查）属于典型值得引入的场景。

---

## 结论与建议

**关于平台超时与告警（事实层面）**：目前只能确认任务级超时 45s（合同审查 120s）与模型连续 3 次超时切换 Qwen-Max，以及 P0/P1 分级响应时限（5 分钟 / 30 分钟）、值班热线 8812、连续两次 P0 需 48 小时内复盘 [1][2]。**告警指标阈值、统计粒度、通知渠道清单、抑制规则均无资料**，且来源为自述虚构的测试文档，建议向平台方索取正式产品文档后再定稿 [1][2]。

**关于故障恢复设计（建议层面，按场景选择）**：

1. **简单单步 Agent（无副作用、单次 LLM 调用）**：不必引入 checkpointer，用带指数退避的 try-catch 即可 [29]。
2. **多步、有副作用、含人工审批的任务（如合同审查）**：生产环境使用 AsyncPostgresSaver，thread_id 与平台任务 ID 绑定；超时视为挂起信号，以 `invoke(None, config)` 自动续跑；节点级用 RetryPolicy 处理瞬时故障；interrupt 前只做幂等准备操作，真实副作用后置或拆分节点 [8][10][11][20][22]。
3. **告警联动**：以 P1/P0 分级作为自动续跑失败后升级人工的判定线；人工介入点用 `interrupt_before`/`interrupt_after` + `Command(resume=...)`，恢复时务必传入相同 config [1][2][23][25][26]。
4. **补齐告警配置**：参照通用范式为超时率、失败率、延迟、Token 消耗设阈值（指标 + 比较关系 + 阈值 + 统计粒度 + 持续 N 点），高优先级走电话/短信、低优先级走邮件/IM，并配置基于优先级与依赖关系的抑制规则 [3][6][7]。
5. **风险提示**：任何降级到 MemorySaver 的操作都会使恢复能力临时失效，必须在告警中显式标注并尽快恢复持久化后端 [8]。

---

## 参考资料

[1] 📄本地：xinghe_ops_handbook.txt 第1页 - /api/kb/docs/8661213913/file#page=1
[2] 📄本地：xinghe_ops_handbook.txt 第1页 - /api/kb/docs/dc5ce0ceb1/file#page=1
[3] 配置告警策略 - https://www.tencentcloud.com/zh/document/product/214/8886
[4] 配置指标告警策略 - https://docsv4.qingcloud.com/user_guide/container/qke/manual/warm_monitor/cfgstrategy
[5] 告警抑制 - https://www.tencentcloud.com/zh/document/product/248/63520
[6] 如何实现好的告警收敛效果？ - 快猫星云Flashcat - https://flashcat.cloud/blog/alarm-shoulian
[7] 配置AI 应用告警规则-云监控(CMS) - 阿里云文档 - https://help.aliyun.com/zh/cms/cloudmonitor-2-0/configure-alert-rules-for-ai-applications
[8] 【AI面试八股文 Vol.1.1 | 专题6：Checkpoint 机制】Checkpoint机制：状态持久化与断点恢复 - aimagician - 博客园 - https://www.cnblogs.com/aimagician/p/19897304
[9] LangGraph 中的Checkpointer 与Store：两种持久化机制的 ... - https://zhuanlan.zhihu.com/p/2010735018591397391
[10] LangGraph 是怎么让 Agent "记住"对话的？拆解 thread 与 Checkpoint-腾讯云开发者社区-腾讯云 - https://cloud.tencent.com/developer/article/2697407
[11] Interrupts - Docs by LangChain - https://docs.langchain.com/oss/python/langgraph/interrupts
[12] LangGraph 高级实战：让 AI 会记忆、能暂停、可插手的断点恢复与流式控制 - https://zhuanlan.zhihu.com/p/1972816854629237051
[13] 浅谈LangGraph 中断式工作流：构建一个可交互的问答流程 - https://blog.csdn.net/weixin_43844521/article/details/149982725
[14] LangChain 对话记忆 — Checkpointer | 菜鸟教程 - https://www.runoob.com/langchain/langchain-checkpointer.html
[15] 📄本地：langgraph_notes.md 第1页 - /api/kb/docs/7e4c428b32/file#page=1
[16] 📄本地：langgraph_notes.md 第1页 - /api/kb/docs/04a8008a50/file#page=1
[17] [LangGraph] 中断注意事项 - Zhentiw - 博客园 - https://www.cnblogs.com/Answer1215/p/19757800
[18] ai-agents-from-zero/25-LangGraph高级特性.md at main · didilili/ai-agents-from-zero · GitHub - https://github.com/didilili/ai-agents-from-zero/blob/main/25-LangGraph%E9%AB%98%E7%BA%A7%E7%89%B9%E6%80%A7.md
[19] 概述 - LangChain 教程 - https://github.langchain.ac.cn/langgraph/concepts/durable_execution
[20] langGraph V1.0使用指南 (三)_java_悟乙己-AI Agent技术社区 - https://agent.csdn.net/6a4f36bf662f9a54cb8cd24b.html
[21] Interrupts - Docs by LangChain - https://docs.langchain.com/oss/javascript/langgraph/interrupts
[22] Fault Tolerance in LangGraph: Retries, Timeouts and Error Handlers - https://www.langchain.com/blog/fault-tolerance-in-langgraph
[23] 破除AI Agent自主操控风险：万字解读LangGraph“人工干预”机制 ，附零基础实战 - https://zhuanlan.zhihu.com/p/1983908115285046678
[24] 时间旅行 - LangChain 框架 - https://langgraph.com.cn/cloud/how-tos/human_in_the_loop_time_travel/index.html
[25] LangGraph: LangChain Agent 的殺手鐧 (進階) · YWC 科技筆記 - https://ywctech.net/ml-ai/langchain-langgraph-agent-part2
[26] Time Travel in Agentic AI. Time travel in agentic AI refers to the… | by DhanushKumar | Towards AI - https://pub.towardsai.net/time-travel-in-agentic-ai-3063c20e5fe2
[27] 【LangGraph】什么是检查点（Checkpointer） - https://blog.csdn.net/u013172930/article/details/148036704
[28] Agent 长任务的分段执行与 Checkpoint：避免超时丢进度_云原生_键盘鼓手苏苏-AI Agent技术社区 - https://agent.csdn.net/6a8120fc10ee7a33f29b8b43.html
[29] AI Agent 的预写日志：借鉴数据库恢复模式实现崩溃安全执行 - https://tianpan.co/zh/blog/2026/04/12/write-ahead-logging-for-ai-agents-crash-safe-execution
[30] 介绍一下seata的saga模式- ~码铃薯~ - https://www.cnblogs.com/dongyaotou/p/23053744
[31] ai-agent-handbook/04-governance/第 13 章　Agent 的可观测性.md at main · aliyun/ai-agent-handbook · GitHub - https://github.com/aliyun/ai-agent-handbook/blob/main/04-governance/%E7%AC%AC%2013%20%E7%AB%A0%E3%80%80Agent%20%E7%9A%84%E5%8F%AF%E8%A7%82%E6%B5%8B%E6%80%A7.md
[32] 5.3.3 SAGA | 深入架构原理与实践 - https://www.thebyte.com.cn/distributed-system/Saga.html