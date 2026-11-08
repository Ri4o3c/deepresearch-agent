# LangGraph 核心概念学习笔记

## 1. StateGraph 与状态
LangGraph 用 StateGraph 描述一个有状态的工作流。状态通常用 TypedDict 定义，每个节点（node）是一个普通的 Python 函数，接收当前状态，返回需要更新的字段。节点之间用边（edge）连接，START 和 END 是两个特殊节点。

## 2. Reducer：并行结果如何合并
当多个节点同时写同一个字段时，需要 reducer 决定如何合并。最常见的写法是 Annotated[list, operator.add]，表示把各个并行分支返回的列表拼接起来，而不是互相覆盖。如果没有 reducer，并行写同一个键会抛出 InvalidUpdateError。

## 3. Send API：动态扇出
Send API 用于 map-reduce 场景：在条件边里返回一组 Send("节点名", 子状态) 对象，LangGraph 会为每个 Send 启动一个并行的节点实例。子任务数量在运行时才确定，这正是"一个子问题派一个检索 Agent"的实现方式。并发度可以用 max_concurrency 配置项限制。

## 4. 条件边与循环
add_conditional_edges 接收一个路由函数，根据状态返回下一个节点名或 END。把边指回前面的节点就形成循环，例如"审稿不通过 → 回到检索"的反思回路。为防止死循环，应在状态里记录轮次，并设置最大轮数；LangGraph 自身也有 recursion_limit，默认值为 25 步。

## 5. Checkpointer 与断点续跑
编译图时传入 checkpointer（如 MemorySaver、SqliteSaver、PostgresSaver），每执行完一个 super-step 就把状态快照保存下来。调用时通过 config 里的 thread_id 区分会话。程序崩溃后，用同一个 thread_id 再次调用 graph.stream(None, config)，就会从最近的检查点继续执行，已经完成的节点不会重跑。

## 6. Human-in-the-loop
在节点里调用 interrupt() 可以暂停图的执行并把数据抛给调用方，人工确认后用 Command(resume=值) 恢复。这个能力依赖 checkpointer，没有 checkpointer 时无法使用 interrupt。

## 7. 流式输出
graph.stream 支持多种 stream_mode：values 每步输出完整状态；updates 只输出每个节点的增量更新；messages 逐 token 输出大模型消息；custom 用于节点内自定义事件。前端做进度展示时通常用 updates 模式。
