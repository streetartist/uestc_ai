# UESTC AI 社 Flask API

后端负责赛事、题目、报名提交、作品快照、评审成绩和资讯内容的记录与流转。可选的独立评测工作进程运行受信任的适配器镜像；Web API 本身不执行参赛代码。

## 本地运行

先在仓库根目录复制 `.env.example` 为 `.env`，再运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
python backend/app.py
```

API 默认监听 `http://127.0.0.1:5000`，健康检查位于 `GET /api/health`。

## 配置

| 环境变量 | 用途 | 生产要求 |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy 数据库 URL | 使用 PostgreSQL |
| `SECRET_KEY` | 验证码散列与 Flask 密钥 | 使用长随机值 |
| `CORS_ORIGINS` | 逗号分隔的允许来源 | 设置精确 HTTPS 来源 |
| `SESSION_COOKIE_SECURE` | 仅通过 HTTPS 发送会话 Cookie | 设为 `1` |
| `SESSION_COOKIE_SAMESITE` | 会话 Cookie SameSite 策略 | 按部署拓扑设置 |
| `AUTO_CREATE_SCHEMA` | 启动时调用 `db.create_all()` | 设为 `0` |
| `SEED_DATABASE` | 写入演示赛事和账户 | 设为 `0` |
| `INITIAL_ADMIN_*` | 演示管理员邮箱和密码 | 初始化后移除 |
| `INITIAL_REVIEWER_*` | 演示评委邮箱和密码 | 初始化后移除 |
| `ENFORCE_COMPETITION_DEADLINES` | 强制报名和提交截止时间 | 设为 `1` |
| `RESEND_API_KEY` | Resend 邮件服务凭据 | 通过密钥服务注入 |
| `RESEND_FROM` | 验证邮件发件人 | 使用已验证域名 |
| `EXPOSE_VERIFICATION_CODE` | 在 API 响应中返回验证码 | 设为 `0` |
| `EVALUATION_WORKER_TOKEN` | 独立工作进程领取任务的密钥 | 由密钥服务注入 |
| `EVALUATION_ENABLED_ADAPTERS` | 已部署评测器 ID，逗号分隔 | 只列出有运行端的适配器 |
| `EVALUATION_ADAPTER_MANIFEST_DIR` | 受信任的自定义评测器 JSON 清单目录 | 仅管理员可写 |
| `EVALUATION_API_URL` / `EVALUATION_API_KEY` | 固定上游模型 API 地址和密钥 | 通过密钥服务注入 |

未设置 `AUTO_CREATE_SCHEMA` 和 `SEED_DATABASE` 时，两者默认关闭。启用演示数据时必须同时提供 `INITIAL_ADMIN_PASSWORD` 与 `INITIAL_REVIEWER_PASSWORD`。

## 数据库

本地模板使用 SQLite，生产环境建议使用 PostgreSQL，并通过 Flask-Migrate（Alembic）管理结构：

```bash
python -m flask --app backend/app.py db upgrade
```

生产环境不应依赖自动建表。修改模型后生成迁移，并在提交前检查升级和回滚逻辑。

## 邮件与注册

校内邮箱可以请求验证码注册，校外邮箱需要管理员生成的一次性邀请。没有配置 `RESEND_API_KEY` 时邮件不会发送；只有测试或明确设置 `EXPOSE_VERIFICATION_CODE=1` 的本地环境才会在响应中返回验证码。

## 上传与评分

Markdown 编辑器的单个资源默认限制为 8 MB，作品附件默认限制为 20 MB。后端会校验文件类型和大小，但生产部署仍应增加恶意文件扫描、对象存储权限和保留策略。

外部评测方可以调用 `POST /api/scores/import`。CSV 接口为 `POST /api/scores/import-csv`，multipart 字段包括 `competition_id`、`problem_id`、`source`、`label` 和 `file`。CSV 必填列为 `submission_version_id,total_score`，可选列为 `metric_<name>` 与 `feedback_md`。

平台拒绝草稿版本和跨题目成绩。公开榜单在赛事 `ends_at` 前不会返回成绩；赛事结束后，只有外部评分与在线评审满足当前权重配置的作品才进入最终排名。

## 可复用赛题与提交材料

管理页“新建赛题”可载入“无界探索”或“通用智能体 / 研究挑战”模板。模板提供题面、研究报告结构、表单、附件要求、评分项和可选程序评测配置，载入后仍可编辑，默认保存为草稿。现有题目的提交规则可在管理页单独编辑，无需新增数据库列或页面。

`submission_schema.fields` 继续兼容旧的字段名列表；`field_definitions` 按字段名声明 `label`、`type`（`text` / `url` / `textarea`）、`required`、`help` 和 `placeholder`。`readme_template` 提供参赛者的初始作品内容。`attachments` 声明附件 `key`、`label`、`extensions`、`min_count`、`max_count`，ZIP 可用 `required_files` 声明必需文件，例如 `agent.py` 与 `config.json`。正式提交由 API 检查必填文字、HTTP/HTTPS 地址、附件数量和 ZIP 文件清单；草稿允许缺少材料。附件数量按格式匹配，因此多个用途相同格式的材料应合并为一条要求。文件清单检查不执行代码，也不检查研究报告内容质量；运行端负责代码协议与运行验收。

可复用模板位于 `backend/platform_api/templates/*.json`，管理接口为 `GET /api/manage/problem-templates`，仅管理员与组织者可访问。添加类似题目可直接修改表单、材料与评分；接入新环境时再安装受信任的评测器 JSON 清单、固定摘要镜像和工作节点。新评测器仍需要实现实际环境与指标采集，选择模板不能替代运行端。

本地已有且没有提交记录的 SQLite 赛题草稿可用以下命令预览更新；加 `--apply` 才写入。工具保留 ID、赛道、编号和 URL，更新前自动备份 SQLite，拒绝已发布题目和已有提交的题目。

```bash
python backend/apply_problem_template.py --problem open-world-minecraft-agent --template minecraft-open-world
```

## 程序运行协议

配置程序评测的题目遵循“正式提交 → 自动运行 → 指标生成 → 人工评审”。评委可提前查看代码与报告，但评测成功完成前不能保存评分，API 同样拒绝提前评分；运行失败时需核查环境或重新提交。只收材料、没有程序评测配置的题目可直接评审。评委工作台提供最新评测结果刷新入口。

管理员可在题目中选择评测器、任务、CPU/内存/GPU、最长运行时间、运行次数、API 调用上限和公开展示的指标。配置写入 `problems.evaluation_config`；提交时冻结配置与附件引用，生成 `evaluation_runs` 任务。覆盖提交会废弃旧任务。工作进程按每次运行回传指标，平台校验指标名、范围和有限数值，再保存逐次结果与平均值。公开作品页和评委工作台展示指标，**不产生外部成绩，也不影响评委打分或榜单**。

内置 `minecraft-agent-v1`、`classification-v1` 与 `robot-arm-agent-v1` 是协议清单，不含游戏环境、数据集、仿真器或镜像。Minecraft 的 `open-world` 任务、观测指标计算及候选开源环境见 [Minecraft 运行端接入备忘](minecraft_benchmark.md)。新评测器可以通过 `EVALUATION_ADAPTER_MANIFEST_DIR` 安装受信任的 JSON 清单，字段为 `id`、`name`、`submission: {extension: "zip", label}`、`tasks: [id]`、`metrics: [{key, label, unit, direction: "min" | "max", min, max}]`。清单只声明协议，不接受可执行命令或镜像名。指标上限 64 个；已领取任务使用创建时冻结的指标定义。管理员配置的适配器只在 `EVALUATION_ENABLED_ADAPTERS` 包含对应 ID 且工作进程密钥存在时接受正式提交。

赛事与题目草稿仅对管理员、组织者开放；公开接口不返回草稿，未发布的题目不能被参赛者提交。私有题面可放在 Git 忽略的 `backend/data/challenge_drafts.json`，运行 `python backend/import_challenge_draft.py` 预检，再运行 `python backend/import_challenge_draft.py --apply` 导入；遇到同 slug 的赛事会拒绝覆盖，请在管理页继续编辑。导入前先备份数据库，不要将私有草稿文件加入版本控制。

工作进程需运行在独立主机（或隔离的专用节点），安装 Docker 及所需 GPU 驱动。将适配器 ID 对应到经审核的固定摘要镜像，例如 `registry.example/agent@sha256:<64 位摘要>`，设置 `EVALUATION_IMAGES_JSON` 为此映射，以及 `EVALUATION_API_BASE`（形如 `https://api.example/api`）与同一 `EVALUATION_WORKER_TOKEN`，运行 `python backend/evaluation_worker.py`。节点只领取镜像映射中支持的适配器；GPU 任务只分配给配置了 `EVALUATION_GPU_DEVICE`（专用 GPU UUID）的节点。普通适配器镜像读取只读挂载的 `/input/package.zip` 和 `/input/config.json`，在 `/output/result.json` 写出 `{"episodes": [{"metric_key": 0}]}`；数组长度须等于题目配置的运行次数，每项包含全部选定指标。Minecraft `open-world` 使用两个隔离容器，参赛容器无法挂载指标目录或隐藏场景，具体部署见 [Minecraft 运行端接入备忘](minecraft_benchmark.md)。工作进程不接受参赛者指定的镜像或 shell 命令。

API 调用由平台的 `/api/evaluation-worker/runs/<id>/api` 代理到固定 HTTPS 上游，按每次请求扣除限额；上游密钥仅留在 API 服务。启用 API 的适配器节点还需配置 `EVALUATION_API_PROXY_URL` 和具有限定出口规则的 `EVALUATION_NETWORK`；参赛容器只获得单任务 API 调用令牌，领取任务和提交指标的租约留在可信工作进程。未启用 API 的任务使用无网络容器。部署前必须限制该网络只能到达受控代理，不能把普通桥接网络当成出口限制。适配器镜像负责将参赛代码与指标采集、`/output/result.json` 隔离，不能允许参赛代码自行写指标文件。平台目前限制单个附件 20 MB；需要大模型权重时，应先接入对象存储与大文件上传。初始化或升级已有数据库前请备份，并执行 `python -m flask --app backend/app.py db upgrade`。
