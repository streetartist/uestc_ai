# 独立 Docker 测评节点

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

当前节点 8 CPU、约 8GB 内存，工作进程一次只执行一个任务；没有登记独立 GPU，因此 GPU 任务不会被领取。MC 验收使用 4 CPU、6144MB、900 秒、3 场景。更大的训练或并行任务需要增加节点资源，并重新做容量验收。磁盘日志启用 Docker local 驱动的大小轮转；私有仿真证据应按比赛要求保留和备份，不能无条件清除进行中的记录。

## 维护

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
