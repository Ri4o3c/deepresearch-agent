# 网页搜索：自部署 SearXNG + ddgs 备用

Tavily 免费额度用完后，网页检索改为可插拔的 provider（`app/search/`）：
- **searxng**（主）：本机 Docker 自部署的元搜索，聚合 Bing、搜狗等，免费、无额度限制；
- **ddgs**（备）：DuckDuckGo 等的 Python 客户端，SearXNG 报错或无结果时自动切换；
- **tavily**（可选）：有额度时可加回，`SEARCH_PROVIDERS=tavily,searxng,ddgs`。

顺序由环境变量 `SEARCH_PROVIDERS` 控制，默认 `searxng,ddgs`。

## 启动 SearXNG

以下是获得启停授权后的日常步骤；本次文档修正不执行这些命令，也未做运行验证。

1. 从仓库根目录进入脚本所在目录；仅在 `.env` 不存在时复制示例，已有文件绝不覆盖：

```powershell
cd docker\searxng
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env -ErrorAction Stop
    Write-Host '已复制占位模板；尚未完成密钥配置，请先人工配置。'
} else {
    Write-Host '已有 .env，跳过复制并保留原配置。'
}
```

2. 首次复制结果只是占位模板，不代表签名密钥已配置完成。首次部署须人工配置非空真实 `SEARXNG_SECRET`；已有 `.env` 保留原签名值，不自动生成或轮换。未完成配置不要继续。
3. 仍在 `docker/searxng` 目录，每次都通过仓库现有 wrapper 启动，先生成 runtime，再由 wrapper 调用 Compose；失败必须停止：

```powershell
powershell -NoProfile -File .\start.ps1
if ($LASTEXITCODE -ne 0) { throw '启动步骤失败；停止，不要绕过生成器继续启动。' }
```

不要把裸 Compose 启动命令作为日常入口：runtime 不存在时，直接启动可能把挂载源创建成目录；即使已有 runtime，也不能保证与当前 `.env` 一致。每次都应先走 wrapper 的 prepare，失败不得继续启动。

两个来源要分清：`prepare_settings.py` 只从该目录 `.env` 读取 `SEARXNG_SECRET`，不自动展开变量，并写入 ignored runtime。Compose 自身插值则仍可能优先使用宿主同名 `SEARXNG_SECRET`，`--env-file` 不强制覆盖宿主；`x-required-signing-key` 门控仅检查非空，不比较 runtime 是否匹配。不要为此修改系统环境；日常统一使用上述 wrapper，不依赖镜像环境变量覆盖挂载 YAML。

`settings.yml` 是启用 JSON 输出、关闭限流的模板；生成后的 runtime 才由 Compose 显式挂载，端口声明仅绑定 127.0.0.1:8080。ignore 不替代 NTFS 权限保护，详见后文；这些说明不是运行验收结论。

## 国内网络说明
实测 Bing、搜狗可直接用；百度、DuckDuckGo 会触发验证码，Google 被拒绝。若要让国外引擎可用，把 `settings.yml` 中 `outgoing.proxies` 的注释取消，走 Windows 本机代理 `http://host.docker.internal:1088`；获得启停授权后仍按上述 wrapper 入口重新生成 runtime 再启动，不用直接 restart 代替 prepare。

## 统一返回结构与打分
每条结果：`{id, title, url, content, score, type="web", provider}`。
- SearXNG 的 score 是多引擎合并分、量纲不固定：`0.5 × 归一化分 + 0.5 × 名次分`（名次分 1.0 → 0.5），落在 0~1，原有 `score>=0.3` 粗筛继续有效；
- ddgs 没有分数，只用名次分。
- SearXNG 只给摘要，所以默认对前 2 条用 trafilatura 抓正文（超时 6s、4 并发、截断 1500 字），抓取失败保留摘要。

## 可观测性
每次搜索在 Langfuse 里是一个 `web_search` 节点（tool 类型），输入里带 `provider`，降级时能看到先失败的 provider 和后成功的 provider。

## 测试
```powershell
.venv\Scripts\python -m unittest tests.test_search   # mock HTTP，不联网
.venv\Scripts\python eval\smoke_search.py            # 每个 provider 在线搜一次，不调大模型
```
## 速度与 Token 预算（2026-11-06）

以下环境变量在进程启动/模块导入时读取（缓存容量与 TTL 在每次 research 创建 session 时读取）：

| 键 | 默认 | 说明 |
|---|---|---|
| `SEARCH_FETCH_TOP` | `2`（原为 3） | 每次成功搜索只抓前 N 个候选的全文；0 禁用，负数按 0；已有 full 的候选不抓 |
| `SEARCH_CONTENT_LIMIT` | `1500` | 单个网页返回内容、最终研究资料每条内容的字符上限；最小 1 |
| `RESEARCH_CONTEXT_LIMIT` | `6000` | 每次研究员最终检索上下文总字符上限，包含标题/编号/分隔符；相关性筛选资料区也限此值 |
| `SEARCH_CACHE_SIZE` | `32` | 每个 research() 内缓存最多条目数，LRU 淘汰；0 禁用 |
| `SEARCH_CACHE_TTL` | `120` | 成功搜索结果 TTL 秒数，使用 monotonic，自搜索完成计时；0 禁用 |
| `SEARCH_FETCH_TIMEOUT` | `6`（未变） | 每页 HTTP 请求超时秒数；不是整次研究的 deadline |

缓存仅在一次 **researcher.research() 子问题调用**内使用，不跨图的并行分支、补查或用户任务共享；退出（包括异常）清空，不持久化、不改 checkpoint schema。并发访问同一显式 SearchSession 受锁保护，独立 session 不共享锁。此保守范围不会使通常各不相同的查询自动命中，因此不能据此声称整体加速。

缓存 key 覆盖原 query（不改写/归一化）、max_results、provider 顺序/类型、SearXNG 端点、fetch 开关/条数/超时、内容上限；自定义 provider 按实例隔离。不缓存异常或空结果；成功的 fallback 可缓存，provider 元数据不变。返回深拷贝，避免下游编号/裁剪污染缓存。显式调用 `web_search(..., bypass_cache=True)` 可获取新结果，同时失效该 key 的旧缓存，不写入新缓存；没有新增 HTTP 请求字段。

网页 URL 去重仅去除 #fragment，保留 query；本地文档保留 fragment（页码身份）。最终资料优先保留本地证据，返回给图的 sources 与实际供给模型的资料一致，保留来源编号、URL、页码及其他元数据。重写查询的新增来源在相关性筛选前使用唯一编号，避免跨查询的 [1] 冲突。预算可能减少覆盖率，须验收报告质量。

预算度量为 Python 字符数，**不是 token 上限**；限制的是检索资料区，系统提示、问题、模型输出、writer/reviewer 不受该配置限制。未新增 tokenizer 或更换 RAG 架构。SEARCH_CONTENT_LIMIT=1500 对最终本地资料也生效。

在应用已有 INFO 日志打开时，每次 research 退出记录 `research_metrics`：search_calls（provider 尝试含失败）、fetch_calls（抓页尝试）、cache_hits/misses/bypass、context_chars 和整次调用 elapsed_s。未在该汇总新增 query、URL 或凭证。原 Langfuse 模型 callback 保留；本阶段无真实模型调用，未取得 input/output/total tokens，不能用字符差推算 token 或成本。

唯一一轮离线回归由 `tests/run_speed_round.py` 执行，合并既有搜索测试、观测性 no-op 测试及本阶段窄回归。它禁用网络连接/真实模型/真实 Langfuse，mock Chroma 并将 SQLite 放入临时目录。不要运行包含真实 DashScope/LLM 的 eval/test_rag.py 作为本阶段补测。原始结果见 `docs/evidence/speed-token-20261106/`。

## 2026-11-06 必须先生成私有 runtime 配置

本节补充前文已统一的 wrapper 启动步骤；日常请按“启动 SearXNG”节进入 `docker/searxng` 后调用 `start.ps1`。此 wrapper 先用项目 .venv 的 `prepare_settings.py` 从现有 ignored `docker/searxng/.env` 读取 SEARXNG_SECRET，再经 YAML 序列化写入 ignored `settings.runtime.yml`，成功后才执行 Compose。此前配置整理与本次文档修正均未运行 wrapper，也未用真实值生成 runtime；未做运行验证。

Compose 显式将 `./settings.runtime.yml` 挂载到容器 `/etc/searxng/settings.yml`。tracked `settings.yml` 仅为无秘密模板；生成器保留其中非秘密设置，替换 server.secret_key；最终签名不依赖镜像对 SEARXNG_SECRET 的未知覆盖，也不依赖挂载 YAML 自动展开 ${VAR}。Compose 保留顶层 x-required-signing-key 必填输入校验，移除传给容器的 SEARXNG_SECRET 环境项，避免宿主同名变量触发镜像的未知覆盖；签名的明确配置来源为挂载文件。

缺 .env、缺/空 SEARXNG_SECRET、未解析表达式、多行值或值等于模板占位时失败；不调用 Compose。生成器仅输出 readiness bool，不输出值、异常摘录或完整 YAML；不接收命令行密钥、不自动生成密钥。默认先写同目录临时文件再原子替换 runtime，异常清理仅针对自己创建的临时文件。失败时旧 runtime 可仍存在，但 wrapper 会停止，不会拿旧文件继续启动。

实际 .env、settings.runtime.yml 及生成临时文件不得提交/上传；runtime 含敏感值。POSIX mode 0600 是尽力限制，Windows 上不能替代 NTFS ACL，应在私有用户目录控制访问；不能保证阻止其他同权限程序读取。直接 Compose 命令可能使用已有旧 runtime，因此日常启动应走 wrapper。模板更新后也需重新生成。现有服务和数据本轮未变；runtime 生成/挂载/启动的真实兼容性尚未验证。

不要复制示例覆盖真实 .env；保留现有签名值，本轮没有轮换。镜像不升级。离线检查合并于 `docker/check_config_offline.py`，仅使用 dummy fixture；通过不等于容器运行通过，速度优化源码和真实收益仍未验收。

辅助脚本依赖已在本机存在的 python-dotenv / PyYAML，版本记录在 `docker/searxng/requirements-config.txt`；启动器不会安装依赖。新环境缺依赖时应停止，另行授权安装。本轮未安装或升级任何包。

## 2026-11-07 材料与验证边界
上文的启动约束保持不变：已有 `.env` 不覆盖，日常入口是 `start.ps1`，prepare 失败就停止，宿主同名变量仍可能优先于 `--env-file`，门控不比较 runtime 内容。
当前云端仓库快照没有 `docker/`、真实 `.env` 或 `settings.yml`。这只表示快照未包含这些文件，不能据此编写或判断本机部署是否存在。本轮没有重新运行 wrapper，也没有把离线检查写成容器已通过。
