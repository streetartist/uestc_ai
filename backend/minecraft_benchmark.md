# Minecraft 运行端接入备忘

## 题目环境与赛题包（2026-10-05）

题目编辑页“环境与资源”可设置固定控制器/智能体镜像和私有场景，并与 API、AutoDL 额度一起原子保存；支持[完整赛题 ZIP 导入/导出](problem_packages.md)。每次正式提交冻结环境快照，只由受认证 worker 领取，参赛者与公开作品不接触镜像/场景定义。采用此模式后，无须逐题修改运行端镜像映射或全局场景文件。平台的测评端认证、允许适配器及受限 API 网络仍由管理员一次部署。

2026-10-05 实际 MineDojo 双容器整包验收通过：900 秒预算下三个公开短场景共约 264 秒，探索进度 100% / 50% / 0%、成功率 100% / 0% / 0%，无效动作计数和评委端指标核对通过。正式隐藏场景文件仍需组织方提供；本次验收不自动启用主平台的正式测评端。

## 题目测试限额（2026-10-05）

题目配置用 `resources.time_seconds` 限制一次测试的环境启动与全部场景总耗时，用可选 `max_team_runs` 限制各队累计测试次数。自用 AutoDL 预算不控制这套 Docker 工作进程。正式提交扣一次，失败或超时仍计入，草稿不扣；覆盖作品不重置，运行端故障重领任务不额外扣次数。调整预算只影响新任务；适配器、场景数量与指标等规则在已有提交后仍锁定。

真实容器超时验收：`python backend/docker_minecraft_test.py --time-limit 30 --expect-timeout`。脚本使用独立数据库和慢智能体，验证 MC 控制器/参赛容器按总预算终止、清理带本次任务标签的容器、保存超时失败、额度扣至零并拒绝下一次正式提交。2026-10-05 本机通过；30 秒运行限制加容器清理共约 32 秒。该验收验证超时与次数控制，不代替完整任务指标验收。完整指标链路继续使用不带 `--expect-timeout` 的既有验收命令。

题目草稿在本机 `backend/data/challenge_drafts.json`，不进入 Git。当前实现使用 MineDojo 0.1 的 `open-ended` 环境；场景文件 `backend/data/minecraft-scenarios.json` 同样不进入 Git。没有安装 Docker 并通过真实 MineDojo 无头运行验收前，不要启用评测器。

## 可借鉴项目

| 项目 | 可借鉴内容 | 接入判断 |
| --- | --- | --- |
| [MineDojo](https://github.com/MineDojo/MineDojo) | `minedojo.make()` 的 Gym 风格观察/动作循环，以及生存、采集、科技树等可由环境状态判定的任务 | 优先做受控任务和可信事件采集；官方文档提供 JDK 8、无头运行说明，仍需在专用 Linux 节点验证 |
| [MineStudio](https://github.com/CraftJarvis/MineStudio) | Minecraft 模拟器、批量基准和自动评测流程 | 评估其环境适配和批量测试；官方说明要求 JDK 8 及 Xvfb/VirtualGL |
| [Voyager](https://github.com/MineDojo/Voyager) | 自动课程、可执行技能库、环境反馈与失败重试 | 适合作为参赛思路和开发参考；官方启动流程依赖 Minecraft 1.19 游戏实例与登录，不直接作为无人值守的评测器 |

三者的代码仓库均声明 MIT 许可。运行真实 Minecraft 软件、下载资源与分发环境镜像时，还需分别核对相关组件的授权和运行条件。

## 固定协议

“无界探索”赛题模板位于 `platform_api/templates/minecraft-open-world.json`，按题目要求收取一个代码与运行配置 ZIP、一个完整研究报告 PDF 和必填复现说明。程序包根目录须有 `agent.py`、`config.json`。参赛运行配置为 JSON 对象，最多 64 KB；加载器在 `reset` 前调用可选的 `Agent.configure(config)`。旧包没有配置文件时仍可由加载器运行，但新模板会在正式提交时检查文件存在。配置只供参赛程序使用，不改变组织方资源配额、场景或模型令牌。示例见 `minecraft/example_agent.py`、`minecraft/example_config.json`。

平台提供观察与动作协议；参赛智能体需要自行实现大模型规划、代码生成执行、技能复用、记忆与恢复循环。当前 MineDojo 接入只判定背包物品目标，不提供任务动态变化或自动“适应能力”评分；这些能力应通过未知场景实验与报告评审考察，增加任务类型时必须同步扩展可信环境事件采集。

- 当前平台适配器：`minecraft-agent-v1`，任务 `open-world`。题目配置 CPU、内存、运行时限、场景次数和受控 API 调用总额；场景规则和环境版本由**固定摘要的受信任镜像**掌握，不能从参赛包读取。
- 每次正式提交只接收一个 `.zip` 程序包。压缩包根目录必须有 `agent.py`，提供 `Agent.reset(goal)`（可选）与 `Agent.act(observation)`。`observation` 含 `image_jpeg_base64`、`inventory`、`position`、`health` 与 `noop_action`；`act` 返回 MineDojo 8 维整数动作向量。示例见 `backend/evaluation_adapters/minecraft/example_agent.py`。参赛 ZIP 最多 20 MB、256 项，解压后最多 64 MB；仅参赛容器可读。
- 参赛容器预装 Python 3.11、Pillow、NumPy 和 requests。需要模型推理时，从环境变量 `EVALUATION_API_URL` 读取固定代理地址，向其 POST JSON，并在 `X-Evaluation-API-Token` 请求头传入 `EVALUATION_API_TOKEN`。令牌仅在该次运行租约内有效；主平台记录整次运行的使用量与上限。
- 运行端为每个场景重置独立世界和智能体状态，复用同一参赛程序；公开练习场景与保密测试场景分离。固定环境版本、场景定义、时间上限和种子策略后再开放正式提交。
- 环境控制器从真实模拟器观察值生成 `required_objectives`、`completed_objectives`、`collected_items`、`positions`、`tech_milestones`、`deaths`、`invalid_actions` 与 `step_latencies_ms`。`evaluation_adapters.minecraft_metrics.summarize_episode()` 从这些可信事件计算指标，按题目选中的键投影到每场景结果。当前阶段目标是指定物品进入背包；更复杂的里程碑需要增加可信事件采集和场景定义。
- `task_success` 只在场景的所有目标完成时为 100，否则为 0；`exploration_progress` 是已完成目标的百分比；距离累加三维位置相邻采样的欧氏距离。重复物品与里程碑只计算一次；平均动作耗时按场景采样。模型 API 调用数按整次运行统计并显示。平台分别存储逐场景指标与跨场景均值，仅展示给公众及评委，不生成外部成绩或总分。

## 构建与接入

不安装 Docker 或 MineDojo 时，可先从仓库根目录执行协议冒烟测试：

    python backend/local_minecraft_test.py
    python backend/local_minecraft_test.py --package path\to\submission.zip

该脚本使用确定性的模拟环境验证 ZIP 安全加载、`Agent.reset/act`、三级场景重置、指标计算与难度元数据，不代表真实 Minecraft 任务成绩。

### 真实 Minecraft Docker 验收

以下命令构建真实 MineDojo 环境，再用正式提交接口上传程序包，由工作进程启动两个隔离容器，回传指标并检查评委队列。测试使用独立 SQLite 数据库和公开短场景，不修改网站现有数据，不接入模型 API。首次构建会下载 Java、Gradle 和游戏资源，请先确保 Docker 数据盘有足够空间。

```bash
docker build -f backend/evaluation_adapters/minecraft/Dockerfile.controller -t local/uestc-minecraft-controller:dev .
docker build -f backend/evaluation_adapters/minecraft/Dockerfile.agent -t local/uestc-minecraft-agent:dev .
python backend/docker_minecraft_test.py
```

默认验收使用 4 CPU、6 GB 内存和 900 秒总时限。智能体检查环境画面与材料隔离。三个场景分别检查已有背包目标的成功识别、部分完成目标与动作耗时、无效动作计数；这是指标采集与评审链路验收，不代表大模型智能体已能完成正式赛题。组织者控制的 `initial_inventory` 和世界条件只从可信场景文件读取，参赛配置不能改变它们。可用 `--package path/to/agent.zip` 运行其他智能体，但不再断言默认验收智能体的固定结果。

结果保存到 `outputs/minecraft-<UTC时间>/`，包含容器日志、逐场景结果、评委指标验证记录及测试数据库。失败时命令返回非零，保留日志并清理本次容器和 IPC 卷；只有生成 `verification.json` 且 `status=passed` 才代表真实环境验收通过。

Windows Docker Desktop 的 Unix socket 使用 Docker 管理的临时卷，避免 Windows 目录挂载无法传递 Linux socket。部署镜像仍使用固定仓库摘要；本地验收也允许不可变的 `sha256:` 镜像 ID，不接受可变标签作为工作进程执行配置。

可信游戏控制器允许最多 512 个进程/线程；真实世界生成会超过原先的 256 上限。参赛容器仍限制为 256，CPU、内存和时限继续按题目配置限制。

智能体等待场景初始化的连接超时跟随题目总时限，避免固定 300 秒等待提前结束仍在预算内的评测；工作进程仍按总时限终止整个运行。

2026-10-05 在 Windows Docker Desktop 上通过真实验收：正式上传 ZIP、领取任务、断网双容器运行、结果回传及评委队列读取全部成功，运行约 295 秒。三个公开场景的目标完成比例为 100% / 50% / 0%，任务成功为 100% / 0% / 0%；第二场景移动 1.4693 格，第三场景记录 8 次无效动作。验收程序同时核对目标数、物品种类、决策耗时和跨场景均值，并确认本次容器与 IPC 卷已清理。这是测评链路验收结果，不是大模型智能体正式比赛成绩，也不覆盖模型 token 或 GPU 使用计量。

MineDojo 0.1 引用的 JitPack `MixinGradle:dcfaf61` 已出现无法下载的问题。控制器构建脚本改从原发布方 SpongePowered 仓库解析兼容的 0.6 插件。旧 ForgeGradle 还使用 HTTP 下载游戏资源，构建脚本通过官方 HTTPS 地址预填其缓存，并按游戏索引校验长度和 SHA-1；缓存完整时断网运行无需下载。镜像补齐 Java 图形库，并保留真实游戏启动检查。

在支持 Docker 的 Linux 工作节点，从仓库根目录运行：

```bash
docker build -f backend/evaluation_adapters/minecraft/Dockerfile.controller -t local/minecraft-controller:1 .
docker build -f backend/evaluation_adapters/minecraft/Dockerfile.agent -t local/minecraft-agent:1 .
docker image inspect local/minecraft-controller:1 --format '{{.Id}}'
docker image inspect local/minecraft-agent:1 --format '{{.Id}}'
```

构建时控制器镜像会启动一次无头 MineDojo 环境以准备资源；失败即停止接入。将通过验收的镜像推送到受控仓库，获取真实 `repo@sha256:...` 摘要，再配置：

```text
EVALUATION_IMAGES_JSON={"minecraft-agent-v1":"registry.example/controller@sha256:..."}
EVALUATION_AGENT_IMAGE=registry.example/agent@sha256:...
EVALUATION_MINECRAFT_SCENARIOS=/path/to/private/minecraft-scenarios.json
EVALUATION_API_BASE=https://api.example/api
EVALUATION_WORKER_TOKEN=<same-secret-as-platform>
EVALUATION_API_PROXY_URL=https://api.example/api
EVALUATION_NETWORK=<network-restricted-to-the-model-api-proxy>
```

在平台 API 配置同一个 `EVALUATION_WORKER_TOKEN`，通过管理员接口确认场景与镜像跑通后再设置 `EVALUATION_ENABLED_ADAPTERS=minecraft-agent-v1`。工作进程使用 `python backend/evaluation_worker.py`；启动时缺少场景文件或镜像摘要，不会领取 Minecraft 任务。带模型 API 的容器网络必须用主机防火墙或专用网络策略限制出口到代理，不能使用无限制桥接网络。

## 上线前验收

### AutoDL 文档核对（2026-10-05）

- [产品对比](https://www.autodl.com/docs/instance_pro/)明确普通容器实例不提供 API，容器实例 Pro 提供 API。
- [Pro API](https://www.autodl.com/docs/instance_pro_api/)支持创建、列表、状态、详情、开机、关机、释放和保存镜像。基址为 `https://api.autodl.com`，开发者 Token 直接放入 `Authorization`，文档未使用 `Bearer` 前缀；需个人实名认证或企业认证。开机目前只支持 `payload=gpu`。
- Pro 的 `GET /api/v1/dev/instance/pro/snapshot` 返回 SSH 命令、主机、端口和密码；真正远程执行使用 SSH 协议，或创建／开机时配置 `start_command`。[SSH 文档](https://www.autodl.com/docs/ssh/)说明公钥登录配置。
- [弹性部署 API](https://www.autodl.com/docs/esd_api_doc/)提供 Job、Container、ReplicaSet、任务状态和副本调度，要求企业认证；[公共 API](https://www.autodl.com/docs/common_api/)可查询账户余额。
- [容器环境文档](https://www.autodl.com/docs/env/)明确容器实例内不支持 Docker，需要 Docker 时联系其裸金属服务。当前 MC 执行端依赖 Docker 双容器隔离，不能据开关机 API 的存在就认定可直接部署到 AutoDL；Pro 是否支持所需嵌套 Docker 权限仍须确认和实测。
- 以上仅为官方文档核对，未使用账户 Token、未调用创建／开机接口，也未验证远程 MC 运行。

1. 在专用 Linux 运行节点完成上述镜像构建，验证 MineDojo 无头渲染、游戏资源、三个场景的重置，以及 Java/Minecraft 版本与所需资源的授权。
2. 工作进程为 Minecraft 同时启动两个容器：可信控制器独占结果目录与隐藏场景；参赛容器只读 ZIP 和共享 Unix socket。检查 ZIP 无法访问 `/output`、场景文件、Docker 套接字或任意外网；可信控制器不读取 ZIP。
3. 联通模型代理，验证每任务调用限额、受限出口、超时、断线重试及失败恢复。平台代理记录整次运行的 API 调用量，且不得把工作进程租约传给参赛代码；token 与 GPU 使用计量需在具体资源接口接入时另行验收。
4. 用公开场景验证一次成功、一次部分完成、一次失败，复跑固定种子确认目标事件和指标口径；再验证隐藏场景无法被参赛程序读取。
5. 上述验收完成后才配置 `EVALUATION_IMAGES_JSON`、`EVALUATION_ENABLED_ADAPTERS=minecraft-agent-v1` 及独立工作进程。当前草稿仅可由管理者预览，不能作为已上线的 Minecraft 在线评测宣告。
