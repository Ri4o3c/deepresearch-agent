# 星河智能 Agent 平台超时与告警策略及基于 LangGraph Checkpointer 的故障恢复设计

## 摘要

星河智能 Agent 平台（代号"北斗"，2025 年 3 月上线）的超时策略目前只有任务级配置：单次 Agent 任务默认超时 45 秒，合同审查例外放宽至 120 秒；主模型 DeepSeek-V3 连续 3 次超时后自动切换备用模型 Qwen-Max；并行工具调用并发上限为 8 [1]。告警方面仅有 P0（平台整体不可用，5 分钟内响应）与 P1（单个业务不可用，30 分钟内响应）两级分级，连续两次 P0 需 48 小时内提交复盘报告，值班每周一 10 点轮换、热线内线 8812 [1]。请求超时、节点/步骤超时、工具调用超时的具体配置项与默认值，以及告警阈值的量化配置和通知渠道，在现有资料中均属缺口，不能以第三方文档替代 [1][6][7][8][9]。LangGraph 的 checkpointer 以 thread_id 为主键、按 super-step 粒度自动保存状态快照，配合同一 thread_id 重放即可实现断点续跑，但其恢复是从中断节点开头重跑整个节点，interrupt 之前的副作用会被重复执行，且无自动故障检测与内置 fallback [11][12][15][18]。因此故障恢复设计应以"超时分层 + 幂等/补偿 + checkpoint 落点 + 恢复前副作用判定"为主线，而非单纯依赖 checkpointer 重放。

## 一、平台定位与运行时架构

星河智能 Agent 平台是公司内部业务支撑型平台，负责客服问答、合同审查和周报生成三类业务 [1]。架构分为网关层、编排层（基于 LangGraph）、工具层和模型层四层；模型层主力为 DeepSeek-V3，备用为通义千问 Qwen-Max [1]。向量库为 Chroma，知识库切块 600 字、重叠 100 字 [1]。

需要特别区分：网页资料中的"星智 Agent 平台"（星河智源）是面向专利研发/科创知识产权的统一任务入口，依托自研 MindFlow 科创大模型，采用"标准化 Skill + Agent 按需规划"双路径执行模型 [2][3]。它与本报告讨论的星河智能内部平台并非同一产品，其资料不能用于推断星河智能的配置默认值。

## 二、超时策略：现状与缺口

### 2.1 已有配置（事实）

| 维度 | 现状 | 来源 |
|---|---|---|
| 任务总时长 | 默认 45 秒；合同审查 120 秒 | [1] |
| 模型切换 | 主模型连续 3 次超时自动切换 Qwen-Max | [1] |
| 并发限制 | 并行工具调用上限 8 | [1] |

### 2.2 资料缺口（如实说明）

本地运维手册未涉及以下内容，且网页资料 [6][7][8][9] 为通用最佳实践或第三方平台文档，不能作为星河智能的默认值采信 [1]：

- 请求超时（HTTP/网关层）的具体配置项与默认值；
- 节点/步骤级超时的配置项与默认值；
- 工具调用超时的配置项与默认值（仅知并发上限为 8）；
- 告警阈值的量化配置项（如错误率、延迟分位数阈值）；
- 告警通知渠道（短信、邮件、IM）的具体配置——资料仅提到值班表每周一上午 10 点轮换、值班热线内线 8812 [1]。

### 2.3 超时语义的关键约束

超时只证明没有收到确定结果，不证明操作没有完成 [32][36]。因此超时策略必须与重试策略绑定：读操作可有限重试；写操作必须先查结果，仅在幂等保证或对账确认不会重复产生副作用时才重试 [32][36]。重试还需按错误类别分流：暂态 429 降低并发、遵守 Retry-After、有上限指数退避加抖动；额度耗尽/欠费停止自动尝试转人工；401/403 暂停并检查凭证；本地预算/轮数/截止时间耗尽则停止自动执行并保留进度 [32]。每条调用链应指定唯一重试负责人，总尝试数需涵盖 SDK、工具包装、模型重规划和备用模型调用 [32]。

## 三、告警策略：现状与缺口

### 3.1 已有配置（事实）

- P0 告警（平台整体不可用）：5 分钟内响应 [1]；
- P1 告警（单个业务不可用）：30 分钟内响应 [1]；
- 连续两次 P0 故障需在 48 小时内提交复盘报告 [1]；
- 值班表每周一上午 10 点轮换，值班热线内线 8812 [1]。

### 3.2 资料缺口

资料未定义 P2/P3 级别，也未给出告警到具体止损动作的映射 [1]。可观测性方面，业界建议调用记录至少关联任务、尝试、租户、Agent、模型、供应商请求 ID、用量等字段，并把告警接到具体动作；依赖熔断与预算停止要分开处理——前者可在冷却后用少量探测检查恢复，后者不能因依赖恢复就自动追加额度 [32]。LangSmith 可自动捕获每个 span、thread、subagent 的 LLM 追踪、token 用量、成本与延迟，无需手工埋点，可作为告警/监控的落点参考 [27]。

## 四、LangGraph Checkpointer 的断点续跑原理与限制

### 4.1 工作原理（事实）

- **thread_id 是主键**：调用带 checkpointer 的图时必须在 config 的 configurable 中指定 thread_id，恢复时必须提供相同 thread_id；没有 thread_id 就无法保存状态或恢复 [11][15][17]。
- **checkpoint 粒度为 super-step**：每个 super-step 结束时运行时自动保存图状态快照，无需手动 save；每个 checkpoint 由 thread_id、checkpoint_id（单调递增）和 parent_checkpoint_id（串成链）标识，存储主键为 (thread_id, checkpoint_ns, checkpoint_id) [12][15][16][17]。
- **断点续跑方式**：程序崩溃后用同一 thread_id 再次调用 graph.stream(None, config) 或 graph.invoke，即从最近检查点继续，已完成的节点不会重跑；同一 checkpoint_id 恢复两次得到相同结果（幂等性），并支持回溯任意历史 checkpoint 做时间旅行与回放 [12][14][18]。
- **interrupt/resume**：interrupt() 本质是抛出异常，人工确认后用 Command(resume=值) 从断点继续；该能力依赖 checkpointer，无 checkpointer 时 interrupt 及 interrupt_before/interrupt_after 均失效 [11][14][18]。
- **持久化后端可插拔**：MemorySaver（进程内存，重启即丢，仅测试）、AsyncSqliteSaver（本地单机持久化）、AsyncPostgresSaver/PostgresSaver（生产、多实例共享），另有 Redis 等可选；需先调用 checkpointer.setup() 初始化 [11][12][17]。

### 4.2 已知限制（事实）

1. "断点续跑"是从中断的 node 重新执行整个节点，而非从 interrupt() 那一行开始，interrupt 之前的副作用操作（API 调用、数据库写入）会被重复执行 [11][12][16][18]；
2. MemorySaver 对长会话内存线性增长，默认不限制 checkpoint 数量，需配置 max_versions 或手动清理 [11][12][16][18]；
3. 无自动故障检测/监督机制，进程崩溃后需外部触发恢复，重试耗尽后无内置 fallback 路由或死信队列 [11][12][16][18]；
4. LangGraph 自身有 recursion_limit（默认 25 步）[11][12][16][18]。

### 4.3 节点级超时与重试（工程模式）

LangGraph 支持在 addNode 时配置 timeout：runTimeout 为单次尝试的硬性墙钟上限，超限抛出 NodeTimeoutError 并清除该次尝试的写入；idleTimeout 为进度重置型上限，节点在指定时长内无可观察进展才触发；可用 setNodeDefaults 统一配置，节点级 timeout 与错误处理器需 @langchain/langgraph>=1.4.0 [26]。retryPolicy 按异常类型与退避自动重跑失败尝试，执行顺序固定为：节点抛异常（含 NodeTimeoutError）→ retry policy 决定是否重试 → 重试耗尽后才运行错误处理器 [25][26]。另有 Graceful shutdown 机制用于在 super-step 边界干净停止并在之后恢复 [18][26]。

## 五、业界方案对比（有限信息）

- LangGraph 以图方式显式表达循环、条件分支与检查点，执行状态被持久化，这是断点续跑的基础；LangSmith 负责运行后的可观测性（追踪、延迟、成本）[29][27]。
- Temporal 对单个 payload blob 限制 2 MB、每条 gRPC 消息限制 4 MB，Temporal Cloud 上不可配置，官方建议以引用方式传递大 payload；LangGraph 无此上限 [30]。
- 在 Temporal 或 CrewAI 中，跨多智能体的失败处理通常需手写重试逻辑，而 LangGraph 内置多智能体协调与整图失败处理 [30]。
- **资料不足**：现有横评仅涉及上手体验、核心能力、定价、生态、落地案例等维度评分（Dify 43、Coze 40.5、LangGraph 40），未提供 Dify、Coze 在超时、告警、断点续跑方面的具体机制，不足以支撑这三项能力的对比 [28]。

## 六、面向星河平台的故障恢复设计建议

### 6.1 超时分层

在现有任务级超时（45 秒 / 合同审查 120 秒）之上补齐节点级与工具级超时：节点级可用 LangGraph 的 runTimeout 与 idleTimeout 组合，工具级超时需结合并发上限 8 单独设定 [1][26]。注意资料未给出星河平台更细的分层设计，需另行补充 [1]。

### 6.2 Checkpoint 落点

持久化单位应是工具调用而非会话结束——每次工具调用前持久化当前计划、提示词前缀、工具名、参数、幂等键和步骤序号 [35]。需注意每次调用前 Checkpoint 会带来几十到一百毫秒延迟与存储成本，应在设计阶段纳入预算，避免生产环境为达标而降低频率 [35]。六个崩溃窗口决定落点设计 [33]：

| 窗口 | 场景 | 对策 |
|---|---|---|
| A | 接收后保存前 | durable inbox，先保存再返回已受理 |
| B | 保存后派发前 | durable queue 或数据库扫描重派 |
| C | 派发后工具执行前 | step attempt 状态 + 稳定 operation ID |
| D | 工具生效后结果保存前（最危险） | 外部幂等键/远端查询/对账/人工确认 |
| E | 结果保存后 checkpoint 前 | 持久结果缓存，恢复时直接推进图状态 |
| F | checkpoint 后客户端收到前 | outbox + 有序事件日志 + cursor replay |

窗口 D 是分水岭：若工具系统既不支持幂等键也无法查询历史请求，就不存在普遍安全的自动重试 [33]。

### 6.3 幂等与补偿

执行结果状态应为 success / confirmed_failure / unknown 三态，UNKNOWN 时不能猜也不能直接重复执行 [34][32][36]。恢复动作按外部能力分支：支持稳定幂等键则用同一业务键重试；支持结果查询则先查后重试；两者都支持则查询优先；两者都不支持则标记 UNKNOWN 转人工或补偿 [34][32][36]。幂等键应代表一次逻辑操作（如一次逻辑退款）而非一次 HTTP 请求，并需核实其作用域、有效期与参数规则 [34][32][36]。

补偿不等于回滚：操作应分为可精确回滚、可业务补偿、不可补偿三类，并在工具元数据中声明补偿等级与补偿动作（如 write_file→restore_file_version、publish_article→unpublish_article、send_email→不可逆）；不可补偿操作不应轻易自动执行 [37]。

针对 LangGraph 的节点重跑特性，interrupt 前只做可安全重放的操作（如 upsert 保证幂等），副作用尽量放到 interrupt 之后，或把中断与副作用拆成两个节点 [19][22][24]。

### 6.4 恢复后的状态一致性校验

恢复前必须先判断未决的外部副作用，再决定查询、幂等重试、补偿还是转人工；数据库事务只能保证本地一致，不能把第三方 API 纳入同一原子事务 [34][35]。对副作用外部且不可逆的任务，更安全的策略是带用户通知的故障安全中止（fail-safe abort），把部分状态写入对账队列交操作员处理，而非默认前向重放 [34][35]。

### 6.5 告警与可观测性

在现有 P0/P1 两级基础上补充 P2/P3 及告警到止损动作的映射（资料缺口，需补充）[1]。调用记录至少关联任务、尝试、租户、Agent、模型、供应商请求 ID、用量等字段 [32]。由于 LangGraph 无自动故障检测/监督机制，进程崩溃后需外部触发恢复，重试耗尽后无内置 fallback 路由或死信队列，需在平台侧自建监督与死信机制 [11][12][16][18]。

### 6.6 演练与验证

资料提到持久化执行框架（Temporal、Restate、Azure Durable Task、LangGraph、微软 Foundry Agent Service 的跨区域复制与手动切换指南）可作为参考，并强调需在设计阶段评估 Checkpoint 延迟与存储预算 [35]。但检索资料未提供星河平台具体的故障演练方案（如混沌演练、恢复演练频次与验收标准），此部分信息不足 [35]。

## 结论与建议

1. **超时策略按"任务级已有、节点/工具级待补"推进**：保留 45 秒/120 秒任务级超时与主模型 3 次超时切换 [1]，在 LangGraph 编排层用 runTimeout + idleTimeout 补齐节点级超时，并为工具调用单独设定超时（并发上限 8 已定）[26][1]。超时必须与重试策略绑定，写操作先查后重试 [32][36]。

2. **告警策略先补分级与渠道**：现有 P0/P1 响应时限与复盘要求可沿用 [1]，但需补充 P2/P3、量化阈值与通知渠道（资料缺口），并将告警接到具体止损动作 [32]。

3. **故障恢复以"checkpoint + 幂等 + 补偿"三层设计，而非单纯依赖重放**：checkpointer 用 PostgresSaver 做生产多实例共享 [11][12][17]；checkpoint 落点按六个崩溃窗口设计，重点处理窗口 D [33]；执行结果三态化，UNKNOWN 转人工或补偿 [34][32][36]；工具元数据声明补偿等级 [37]。

4. **按场景选择恢复策略**：
   - 纯查询/可幂等重放场景：同一 thread_id 重放即可 [12][14][18]；
   - 有稳定幂等键的写操作：用同一业务键重试 [34]；
   - 可查询结果的写操作：先查后重试 [34]；
   - 不可逆且无法查询的副作用：fail-safe abort + 对账队列 + 用户通知 [34][35]；
   - 人工审批节点：interrupt 前只做可重放操作，副作用后置或拆分节点 [19][22][24]。

5. **补齐监督与演练**：LangGraph 无自动故障检测与内置 fallback，需平台侧自建外部触发恢复、死信队列与监督机制 [11][12][16][18]；故障演练方案（混沌演练、恢复演练频次与验收标准）资料不足，建议作为下一阶段专项补充 [35]。

## 参考资料

[1] 📄本地：xinghe_ops_handbook.txt 第1页 - /api/kb/docs/74dc9d6c92/file#page=1
[2] Agent平台｜星智系列｜星河智源 - https://www.mindflowing.cn/official/ai/xingzhi/agent-platform
[3] 星河智源发布星智Agent平台，打造科创知识产权智能作业系统 - 新华网客户端 - https://app.xinhuanet.com/news/article.html?articleId=20260910bd1a6182dc2c4ef7ba169a9f2de5e462
[4] AI Agent Architecture Guide | Artificial Intelligent Framework Explained - https://ahex.co/ai-agent-architecture-guide
[5] AI Agent Runtime & Capability Fabric | Createting - https://createting.com/agent-runtime
[6] 配置超时、自动重试和备用模型 - 文档中心 - https://docs.infini-ai.com/agent-works/build/fault-tolerance.html
[7] Agent 请求超时与响应慢优化｜devcfg - https://devcfg.com/agent/timeout-retry
[8] 09-超时配置最佳实践.md - https://github.com/cookiespiggy/learn-temporal-ai-agent/blob/main/02-%E6%A0%B8%E5%BF%83%E6%A8%A1%E5%BC%8F/09-%E8%B6%85%E6%97%B6%E9%85%8D%E7%BD%AE%E6%9C%80%E4%BD%B3%E5%AE%9E%E8%B7%B5.md
[9] AI Agent生产环境监控清单：10个核心指标与告警阈值设置 - https://blog.csdn.net/2401_85133351/article/details/160372560
[10] 通过AI agent+通用大模型，实现海量告警研判分析实践 - 安全内参 | 决策者的网络安全知识库 - https://www.secrss.com/articles/91501
[11] LangGraph工作流编排 - 李乾坤的博客 - https://qiankunli.github.io/2024/05/16/langchain_graph.html
[12] 【AI面试八股文 Vol.1.1 | 专题6：Checkpoint 机制】Checkpoint机制：状态持久化与断点恢复 - aimagician - 博客园 - https://www.cnblogs.com/aimagician/p/19897304
[13] 别再混淆！一文看懂LangGraph 的短期记忆与长期记忆：原理 - https://aistudio.baidu.com/blog/detail/737084298395525
[14] LangGraph 中的 Checkpointer 与 Store：两种持久化机制的深度对比 - https://zhuanlan.zhihu.com/p/2010735018591397391
[15] Checkpointers - Docs by LangChain - https://docs.langchain.com/oss/python/langgraph/checkpointers
[16] Why Checkpoints Aren't Durable Execution: LangGraph - https://www.diagrid.io/blog/checkpoints-are-not-durable-execution-why-langgraph-crewai-google-adk-and-others-fall-short-for-production-agent-workflows
[17] LangGraph 是怎么让 Agent "记住"对话的？拆解 thread 与 Checkpoint-腾讯云开发者社区-腾讯云 - https://cloud.tencent.com/developer/article/2697407
[18] 📄本地：langgraph_notes.md 第1页 - /api/kb/docs/58e54a145a/file#page=1
[19] 破除AI Agent自主操控风险：万字解读LangGraph“人工干预”机制 ，附零基础实战 - https://zhuanlan.zhihu.com/p/1983908115285046678
[20] LLM——浅谈 LangGraph 中断式工作流：构建一个可交互的问答流程 - https://blog.csdn.net/weixin_43844521/article/details/149982725
[21] 如何使用interrupt 等待用户输入 - https://www.aidoczh.com/langgraph/how-tos/human_in_the_loop/wait-user-input
[22] LangChain 人工介入 | 菜鸟教程 - https://www.runoob.com/langchain/langchain-human-in-the-loop.html
[23] 人工干预 - LangChain 教程 - https://github.langchain.ac.cn/langgraphjs/concepts/human_in_the_loop
[24] [LangGraph] 中断注意事项 - Zhentiw - 博客园 - https://www.cnblogs.com/Answer1215/p/19757800
[25] A Beginner’s Guide to Handling Errors in LangGraph with Retry Policies - DEV Community - https://dev.to/aiengineering/a-beginners-guide-to-handling-errors-in-langgraph-with-retry-policies-h22
[26] Fault tolerance - Docs by LangChain - https://docs.langchain.com/oss/javascript/langgraph/fault-tolerance
[27] LangGraph vs Temporal: AI Agent Orchestration Compared - https://www.langchain.com/resources/langgraph-vs-temporal
[28] 6 大 AI Agent 平台横评：Coze、Dify、百炼、千帆、Copilot Studio、LangGraph 谁最能打？-腾讯云开发者社区-腾讯云 - https://cloud.tencent.com/developer/article/2674338
[29] LangChain vs LangGraph vs LangSmith: What's the Difference in 2026 - https://www.truefoundry.com/blog/langchain-vs-langgraph-vs-langsmith
[30] LangChain Alternatives: A Full Comparison for Agent Engineering - https://www.langchain.com/resources/langchain-alternatives
[31] Agent崩溃恢复：幂等性与持久化检查点设计 - https://blog.csdn.net/weixin_32667955/article/details/166076691
[32] AI Agent 生产事故典型成因与治理方案 | ReelOS.ai - https://www.reelos.ai/articles/ai-agent-production-incidents-governance
[33] Agent 崩溃恢复机制：从 Checkpoint 到副作用一致性本文从故障语义出发，拆解 Agent 崩溃后的状态保存 - 掘金 - https://juejin.cn/post/7664806921777791039
[34] AI Agent 面试题：如何设计可暂停、可恢复、可重试的执行引擎？面试中，如果只把 Agent 描述成“模型调用工具， - 掘金 - https://juejin.cn/post/7680067373006307328
[35] 智能体灾难恢复：当工作记忆随区域一同失效时 - https://tianpan.co/zh/blog/2026-04-28-agent-dr-working-memory-region-failover
[36] 2026年7月最新ChatGPT Plus / Pro 与 Codex：当 AI Agent 最新5.6版本来袭，必须理解事务、幂等与补偿_python_dunge2026-DeepSeek技术社区 - https://deepseek.csdn.net/6a566a4310ee7a33f28d7bc3.html
[37] 2026年7月最新ChatGPT Plus / Pro 与 Codex：当 AI Agent 最新5.6版本来袭，必须理解事务、幂等与补偿_python_dunge2026-AI Agent技术社区 - https://agent.csdn.net/6a566a43662f9a54cb8f589e.html