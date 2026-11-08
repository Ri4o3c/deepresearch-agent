# Langfuse 本地自部署与接入

本项目用 Langfuse（开源、可自部署）记录每次研究的完整调用链：规划/研究/撰写/审稿各节点、每次 LLM 调用的输入输出与 token、Tavily 网页检索和本地知识库检索。
编排文件来自官方仓库 github.com/langfuse/langfuse（docker/langfuse/docker-compose.yml），包含 web、worker、postgres、clickhouse、redis、minio（以及官方附带的 ai-gateway）。

## 1. 启动
1. 确认 Docker Desktop 左下角显示 Engine running。
2. 从仓库根目录执行以下首次配置步骤；只有目标 `.env` 不存在时才复制，已有文件绝不覆盖：

```powershell
if (-not (Test-Path -LiteralPath docker\langfuse\.env)) {
    Copy-Item -LiteralPath docker\langfuse\.env.example -Destination docker\langfuse\.env -ErrorAction Stop
    Write-Host '已复制占位模板；请先人工配置，尚不可直接部署。'
} else {
    Write-Host '已有 .env，跳过复制并保留原配置。'
}
```

复制结果只是占位模板，不表示密钥已经配置完成。首次部署须人工按说明填写非空真实值，并保持数据库连接串、存储凭证与对应服务密码一致；这里不提供替用户改 `.env` 的脚本。已有 `ENCRYPTION_KEY`、`SALT` 和各服务密码必须保留，绝不自动生成或轮换，也不要从示例覆盖现有值。实际 `.env` 应保持 ignored；ignore 不等于本机权限保护。

3. 启动：
```powershell
cd docker\langfuse
docker compose --env-file .env -f docker-compose.yml up -d
docker compose ps        # 等 postgres/clickhouse/redis/minio 显示 healthy
```
以上仅为获得启停授权后的运行说明，本次文档修正未执行；未完成首次配置不要启动。`--env-file` 不保证覆盖宿主同名变量，实际读取来源仍需运行验收。
4. 首次启动约 1~3 分钟，打开 http://localhost:3000/api/public/health 返回 `{"status":"OK"}` 即就绪。
5. 停止：`docker compose down`（数据保留在 Docker 卷里；加 `-v` 会清空数据）。

## 2. 首次登录与创建项目
1. 浏览器打开 http://localhost:3000 ，点 **Sign up** 注册本地管理员账号（邮箱密码只存在本机数据库）。
2. 按提示新建 Organization（如 `personal`），再新建 Project（如 `deep-research`）。
3. 进入项目 **Settings → API Keys → Create new API keys**，复制 Public Key（pk-lf-…）和 Secret Key（sk-lf-…，只显示一次）。

## 3. 配置环境变量（用户级）
在 PowerShell 中执行（把值换成自己的）：
```powershell
[Environment]::SetEnvironmentVariable('LANGFUSE_PUBLIC_KEY','pk-lf-xxx','User')
[Environment]::SetEnvironmentVariable('LANGFUSE_SECRET_KEY','sk-lf-xxx','User')
[Environment]::SetEnvironmentVariable('LANGFUSE_HOST','http://localhost:3000','User')
```
然后**重启 PyCharm**（或重新打开终端）让变量生效，再启动后端。

## 4. 看效果
发起一次研究后，在 Langfuse 的 **Tracing** 页面可以看到：
- 每次研究一条 trace，`session_id` = 前端的 thread_id，续跑会归到同一个 Session；
- 标签 `api/start`、`api/resume`、`cli`，元数据里带研究问题；
- 节点树里有 `tavily_search`（tool）和 `kb_search`（retriever）两类检索 span。

## 5. 不配置时
没设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 时 `app/observability.py` 全部为空操作；设置了但 Langfuse 没启动时，只会在日志里提示上报失败，研究照常完成。
## 2026-11-06 配置整理：现有密钥保持不变

敏感字段现在只接受必填环境引用，缺失或空值会在 Compose 配置阶段失败：DATABASE_URL、SALT、ENCRYPTION_KEY、CLICKHOUSE_PASSWORD、REDIS_AUTH、NEXTAUTH_SECRET、MINIO_ROOT_PASSWORD、POSTGRES_PASSWORD 及三类 LANGFUSE_S3_*_SECRET_ACCESS_KEY。其他公共服务名、端口等默认未删除。

未来获得启动授权后，在本目录说明对应的 docker/langfuse 工作目录使用 `docker compose --env-file .env -f docker-compose.yml up -d`。这个命令仅是运行说明，本次整理未执行启停。保留现有本地 .env，切勿从示例覆盖它，也不要为上传随机更换 SALT / ENCRYPTION_KEY 或任何密码。

.env.example 仅是干净示例，不能直接作为可运行安全配置。required 仅检查缺失/空值，非空占位仍需人工核实。实际 .env 内 DATABASE_URL 对 POSTGRES_PASSWORD、三类上传 secret 对 MINIO_ROOT_PASSWORD 的引用需保持一致；客户端和服务端不能单边更改。宿主同名环境变量可能优先于 --env-file，启动前应确认来源；本轮没有读取系统环境或验证运行时覆盖。

此修改只移除 tracked 配置中的敏感 fallback，不轮换本地值、不清理历史、不修改持久卷。加密密钥/salt 的将来迁移需先做受限备份并确认数据可读性，不能盲目换值；Postgres 等已有数据卷也不会仅因改初始化 env 就完成密码迁移。

离线机制检查：从仓库根目录运行 `.venv\Scripts\python -B docker/check_config_offline.py`。只用临时 dummy 配置运行本地 Compose config 与 YAML 检查；不启动服务、不拉镜像、不调用 LLM。不要使用该检查的 dummy 配置部署，也不要把离线通过当作线上运行验证。

## 2026-11-07 材料与核实边界
上文“已有密钥不覆盖、不自动轮换、`.env` 不存在才复制、宿主变量可能优先于 `--env-file`”仍然有效。
进度文档和面试笔记对 Langfuse 是否已经配置的说法不一致，都只是历史自报。本次没有读取真实环境，也没有启动服务，所以不判断当前缺不缺 Key，不生成、不申请、不覆盖任何密钥。配置、服务和 trace 留到最后统一核实。
当前云端快照没有 `docker/langfuse` 编排和真实 `.env`。缺失的是快照材料，不是一份新的部署结论。
