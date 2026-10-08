# 独立 Docker 测评节点

## 开发入口与外部服务故障（2026-10-07）

如果在 Nginx 增加授权码或维护页面，必须让 `/api/evaluation-worker/` 到达后端。该前缀各接口仍校验工作端 Bearer Token 或运行租约；不能豁免鉴权，也不必开放其他网站页面。验收时，无凭据访问 runtime-catalog 应返回 JSON 401，不能返回 302/HTML；正常工作端应恢复心跳。线上开发入口曾把全部测评请求重定向到占位页，已修复路由。

AutoDL status 返回 `RecordNotFoundError` 表示已登记实例不存在，与短暂断网分开处理。组织方池显示实例已删除，未执行的排队测试退次且只退一次，不会自动创建或重新开机；管理员需重新登记实例。已运行的参赛程序不能通过此故障免除测试次数。

模型网关统一发送 `User-Agent: uestc-ai-platform/1.0`。Command Code 官方接口实际拒绝默认 Python 客户端标识；使用平台标识后，文本和内联图片调用均已验证。模型列表不代表当前订阅可调用所有模型，应使用队伍实际要用的模型进行真实请求验证。

GPU 分类测评采用组织方 AutoDL 原生私有镜像，安装与可信边界见 [分类测评](../../backend/classification_benchmark.md)。Docker 节点继续运行 MC 和机械臂，不能领取 AutoDL 原生任务。

组织方 GPU 池在题目“环境与资源”配置，受 `uestc-ai-compute` 自动管理；有排队测试才唤醒已登记的私有实例，无任务120秒后关机。`evaluation_judge_pools` 持久化开机/关机意图、状态、错误和租约；`evaluation_runs.judge_pool_id` 固定任务归属，`quota_refunded` 记录基础设施失败的一次性退回。调度与选手训练卡时独立，不创建新的组织方实例。库存不足或测评程序未启动的处理和HTTPS源站路由见 [开赛接入](wujie-launch.md)。

网站、PostgreSQL、Redis 部署在 `47.84.83.142`，所有测评容器与 Docker 镜像部署在 `103.79.186.75`。维护入口为 `root@103.79.186.75:32161`，采用 Ed25519 SSH 密钥并固定主机公钥；Docker 不开放公网 TCP 管理端口。

## 运行目录与服务

- `/opt/uestc-worker/app`：可信工作进程代码，含与网站版本匹配的 backend。
- `/opt/uestc-worker/venv`：Python 依赖。
- `/etc/uestc-worker/worker.env`：root 管理的生产配置，应用组只读。
- `/opt/uestc-worker/data/tmp`：临时输入、结果及 Unix Socket，必须对宿主 Docker daemon 可见。
- `/opt/uestc-worker/data/logs/<run-id>`：私有控制器日志、相机帧与轨迹，不能直接公开。
- `uestc-ai-evaluation`：串行领取任务、续约、执行、回传；systemd 自动启动与失败重启。
- `uestc-ai-evaluation-network`：恢复内部 Docker 网络及访问限制。

按 Docker 官方 Debian 安装说明安装 Engine、Buildx 和 Compose，使用本地 Unix Socket。工作进程账户属于 docker 组，因此整个节点属于组织方可信基础设施；参赛程序没有 Docker Socket、SSH 密钥、工作进程令牌、数据库或私有场景。

工作进程停止时会移除当前容器；异常退出后启动时只清理相同 `EVALUATION_WORKER_ID` 标签的残留容器。服务停机期间由网站租约超时恢复任务，不重复扣参赛队伍的测试次数。每个运行的日志带任务 ID，不记录密钥或租约。

Linux 上移除全部容器 capabilities 后，容器 root 不能绕过宿主绑定目录的访问权限。MC 控制器使用工作进程的 GID 写入私有结果目录；IPC 目录只对该组可写，选手通过权限受限的 Socket 连接。机械臂控制器及通用适配器使用非 root 工作进程的 UID，保证嵌套证据、临时结果能清理。不要恢复容器的 DAC override 权限来掩盖目录问题。Windows 开发环境不能代替这项 Linux 验收。

MC 的 Java 临时调试文件保存在控制器 `/tmp`，控制器标准输出保存到私有 `controller.log`，宿主 `/output` 只接收指标文件，避免 Java 创建的 root 嵌套目录阻止清理。断网控制器使用 `localhost` 主机名；Docker 的 `network=none` 下不会给随机主机名登记 IP，Java / Gradle 的本机地址查询仍须正常工作。

## 安全连接与模型访问

`EVALUATION_API_BASE=https://uestcai.top/api`，工作进程与网站使用同一个随机 `EVALUATION_WORKER_TOKEN`。当前节点通过 `/etc/hosts` 将主域名指向已验证证书的源站，避免机器接口遭到 CDN 浏览器验证；TLS 证书校验保持启用。

`EVALUATION_INSTALLED_ADAPTERS` 只填写已安装并验收的适配器，本次为 `minecraft-agent-v1,robot-arm-agent-v1`，使空站点在导入第一个题目前也能识别在线测评端。`EVALUATION_IMAGES_JSON={}`：正式题目需要导入固定镜像与私有场景，不处理缺少受信任 runtime 的旧任务。`EVALUATION_WORKER_ADAPTERS` 可用于限制专用节点。

`uestc-evaluation` 是内部网络，网段 `172.30.44.0/24`，桥为 `br-uestc-eval`。选手只能访问宿主 `172.30.44.1:8080` 上特定任务的模型 API POST 路径。Nginx 对网站使用 HTTPS 并验证证书链；其他路径拒绝访问，iptables 阻止容器连接主机 SSH、其他主机端口或转发流量，DNS 不向外网转发。可信控制器始终无网络；未启用模型 API 的选手容器也无网络。

不要将普通 Docker bridge 替代这个网络，也不要开放远程 Docker daemon。模型上游密钥留在网站上，选手只得到本次测评的短期 API 令牌。没有上游渠道时不能宣称已经完成真实模型调用验收。

## 镜像与题目导入

将已验收的 Minecraft / Panda 控制器和选手镜像导入该节点，并核对 `docker image inspect --format '{{.Id}}'`。本机 `sha256` 镜像 ID 随 `docker save/load` 保留；跨其他节点须预装同一镜像或使用组织者仓库的固定 digest。不要使用可变标签作为赛题 runtime。

题目 ZIP 包的 `runtime.json` 填控制器 `image`、选手 `agent_image`，`scenarios.json` 放组织者场景。网站保存任务与场景快照，工作进程领取后在此节点执行，并将可信结果写回网站数据库。后续自定义 Docker 适配器也通过相同工作进程处理，但组织者必须先安装与审核镜像、适配器清单及指标口径。

当前节点 8 CPU、约7.76GiB内存，工作进程一次只执行一个任务；没有登记独立 GPU，因此 GPU 任务不会被领取。MC与LIBERO选手程序的硬上限均为2GiB；环境控制器分别为4GiB、3GiB。机械臂空动作接口实测控制器约1.45GB、选手进程约8MB，这只能证明该简单策略的用量，不能据此开放两个任意参赛程序并发。当前并发容量固定为1。需要增加吞吐时，优先增加独立节点，预装对应固定镜像、配置唯一worker标识、验收后接入；每个节点各领取一项任务，可跨节点并行。单机并发扩展须先增加内存、实现各运行的资源预留，再以代表性MC/LIBERO策略做容量验收。磁盘日志启用 Docker local 驱动的大小轮转；私有仿真证据应按比赛要求保留和备份，不能无条件清除进行中的记录。

## 内存保护 · 2026-10-06

生产节点配置`EVALUATION_MEMORY_BUDGET_MB=6144`、`EVALUATION_CGROUP_PARENT=uestc-evaluation.slice`。安装并启用仓库中的`uestc-evaluation.slice`，聚合上限`MemoryMax=6G`、`MemorySwapMax=0`、`CPUQuota=600%`；所有正式测评容器都挂到同一父组。节点实测总内存7946MiB，聚合上限6144MiB，为宿主Docker、SSH和工作进程留下约1802MiB。工作进程本身不在该容器组，容器组超限不会直接终止调度进程。

`resources.memory_mb`明确表示选手程序的内存上限；环境由可信适配器单独限制，MC为4096MiB、LIBERO/Panda为3072MiB。两者均显式设置相等的`--memory`和`--memory-swap`，禁止借用宿主交换空间。[Docker内存与交换空间规则](https://docs.docker.com/engine/containers/resource_constraints/#--memory-swap-details)。不要开启`--oom-kill-disable`，不要把工作进程服务的MemoryMax误当作Docker容器的上限：Docker由独立daemon创建容器，必须设置容器父组。

工作进程广告聚合内存容量，领取和开赛检查均将环境开销计入；超过容量的任务保留排队、尚不执行，不把错误配置变成选手失败。执行前再次核对预算，拒绝绕过调度的超大配置。节点预算至少给宿主保留1536MiB；升级节点时需同时调整广告预算与父组上限，不能只改一项。已发布赛题只在无参赛提交和测评记录时调整本次内存规则，题面及开发包同步为`wujie-v4`；AutoDL原生GPU分类资源保持原值。

容器退出前检查Docker的`OOMKilled`标记，仅确证OOM时反馈“超过内存上限”；其他错误保留原失败原因。退出后移除该任务的选手和环境容器，按原规则扣一次测试机会，再领取下一项。真实超限验收以2GiB程序限额申请更多内存，验证只终止对应容器、交换空间禁止、父组上限有效、紧接着的新容器可正常运行。不得根据`free`在容器内报告的宿主内存或交换空间判断容器实际额度。

同轮真实正常运行验收：MC三个场景各8步、LIBERO两个排队任务各10步均在新程序/环境限制下完成；MC正式选择后本队与评委可读六份证据，LIBERO排队跨接口重启、每队只扣一次和容器清理均通过。测试使用独立接口库，无正式参赛记录、真实模型调用或全步数质量声明。生产服务及节点容量广告恢复在线，公开题面与两个开发ZIP逐字节核对一致。私有报告为开发机`outputs/docker-memory-oom-result.json`、`outputs/docker-memory-mc-verification.json`、`outputs/docker-memory-libero-queue-result.json`、`outputs/docker-memory-public-verification.json`，宿主日志在`/opt/uestc-worker/data/docker-memory-*`。

## Docker 排队与恢复 · 2026-10-06

MC、LIBERO及其他兼容Docker题目共用持久化任务队列，按`created_at`、任务ID领取最早可执行任务。数据库记录任务的`worker_id`，在同一事务中锁定工作端、检查已有有效租约、领取任务并更新工作端状态；同节点重复领取返回204，其他独立节点仍可领取下一项。节点仅领取自己支持的运行后端、适配器、GPU和模型代理配置，AutoDL原生任务继续由GPU池管理。

Docker工作进程启动时先持有宿主文件锁，再清理自身残留容器，避免重复进程误删正在执行的容器。Linux默认锁为`/run/uestc-ai-evaluation/docker-worker.lock`，systemd通过`RuntimeDirectory`创建并保留目录；手动运行也必须遵守同一宿主锁，不要用不同锁路径绕开单节点容量限制。工作进程按任务续约；网站或节点短暂离线时，队列保留在数据库中，租约到期后恢复同一记录，不新扣队伍次数。

选手可看到排队状态及前方兼容待处理任务数，不能看到其他队伍身份。该数字不承诺开始时间，多节点可能同时处理前方任务。没有在线兼容节点时显示等待节点恢复；排队期间不启动题目运行时钟，测试机会在首次入队时预占一次，重复领取不额外扣减。任务完成、超时或失败后释放位置；执行失败仍按原题目次数规则计次。

本次真实Docker验收使用独立SQLite接口库和两个临时队伍，各运行同一个真实LIBERO任务10步，约272.5秒完成两次运行与环境准备；验证同节点第二次领取被拒绝、第二项等待、接口重启后仍领取原排队记录、每队仅扣一次、每次上传两份私有证据、全部容器清理。验收后生产服务恢复在线，没有修改线上赛事、赛程或模型配置。另通过共享节点MC/分类FIFO、并发领取、独立节点并行、离线租约恢复、时限、失败释放与心跳在线状态等自动检查；本次短任务验收不代表任意选手策略的容量或求解质量。

## 维护

2026-10-07 补充：Minecraft 控制器仍按镜像要求使用容器 root 和宿主工作组，Java 日志保留在容器 tmpfs。宿主 worker 必须在启动前创建 `/output/evidence` 及每个场景的子目录，并设置工作组可写的0770权限；外层临时目录保持0700，选手容器仍不挂载输出。否则控制器新建的 root 所有、0755目录会使非root宿主进程无法递归删除，已上传的正常结果也可能因收尾异常被标为失败。当前修复通过宿主预建目录，保留固定镜像摘要和控制器判分代码。

网站服务器的维护私钥：`/etc/uestc-ai/worker_ssh_ed25519`；固定公钥文件：`/etc/uestc-ai/worker_known_hosts`，均仅 root 读取。

开发机已配置 `ssh uestc-eval-worker`，默认 Docker context 已切换为 `uestc-evaluation`，普通 Docker 命令指向该远程节点。也可使用 `docker --context uestc-evaluation ps` 明确查看远程容器；运行测评脚本应在节点内进行，不能给远程 Docker daemon 传 Windows 本地绑定路径。开发机的本地测评工作进程已停止，旧镜像保留作备份。

更新 worker 时先停止领取新任务并等待正在执行的任务结束，更新代码后重启服务，检查站点 heartbeat、容器清理和指标回写。网站服务不需要安装 Docker，网站数据库也不在测评节点保存。

## 本次验收（2026-10-05）

正式工作进程 `docker-103-79-186-75` 已启动并设置开机自启；网站通过 HTTPS 收到持续 heartbeat，识别 MC、机械臂、受信任 runtime 和受限模型代理能力。网站 `EVALUATION_ENABLED_ADAPTERS` 已启用上述两个适配器。正式库仍只有管理员、没有赛事；验收使用网站节点的独立 PostgreSQL 数据库，完成后已删除。

- 实际提交 ZIP、领取任务、远程 Docker 仿真、回传、评委接口读取均通过。MC 三场景完成率为 `100 / 0 / 0`，探索进度为 `100 / 50 / 0`，失败场景有 8 次无效动作；机械臂抓取、放置、堆叠均为 `100`，稳定保持至少 0.5 秒。
- 两项跨节点验收均使用 4 CPU、6144MB、900 秒、3 场景；从领取到回传分别约 689 秒、717 秒，含环境准备。此 CPU 节点冷启动与软件渲染耗时明显，组织者配置时长应考虑这一开销，不能把决策耗时当作完整运行耗时。
- 机械臂伪造成功、无效动作和空动作案例均为 `0 / 0 / 0`，无效动作分别为 `30 / 0 / 30`。30 秒慢程序测试在约 31.1 秒内完成终止和清理，再次超额提交返回 429。
- 真实 Docker 验证了普通工作账户的嵌套结果读写及清理；启动恢复只删除本工作进程的残留容器。验收后无运行中测评容器、无临时输入目录残留。
- 内部容器可以到达指定模型代理路径，其他管理路径、宿主 SSH、外网和外部 DNS 均被阻止；生产工作进程认证和 Nginx 上游 TLS 校验通过。未配置真实模型渠道，未调用收费模型或新建收费算力实例。
- 后端 136 项检查中 135 项通过、1 项跳过；没有改动前端界面。

原始私有控制器日志与机械臂证据保存在节点 `/opt/uestc-worker/data/acceptance`。开发机验收报告位于忽略提交的 `outputs/cross-node-verification.json`、`outputs/worker-robot-negative.json`、`outputs/worker-robot-timeout.json`；访问凭据、私钥和运行数据不能加入 Git 或构建上下文。

2026-10-06 更新：选手从提交页主动发起测试，正式提交绑定已完成结果，不再触发新的 Docker 任务或扣减机会。工作节点继续使用原有领取、心跳、下载和回传协议；下载端点为自主测试读取独立程序副本。线上单场景 MC 验收约 249 秒完成，队伍和评委看到一致的正式指标，重复提交仍只扣一次机会，验收后无运行中容器。详细权限和绑定规则见 [自主测评与正式结果](community-accounts.md#队伍自主测评与正式结果)。历史的“再次超额提交”验收现在对应“再次超额开始测试”。
