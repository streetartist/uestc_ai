# Panda 机械臂测评

## 范围与信任边界

`robot-arm-agent-v1` 已实现 robosuite 1.5.2 / MuJoCo 3.3.7 / Panda 真实物理仿真：红色积木抓取抬升、指定 XY 放置、红绿积木堆叠。20 Hz 控制、双相机 RGB、OSMesa CPU 渲染，无需 NVIDIA GPU。大模型通过现有受控网关调用。`pick-place`、`multi-step` 保留原 API 标识，目前代表基础操作/混合场景组，每场景独立 reset。任意物体、分类、多步语言组合与主动扰动尚未实现。

复用 MC 双容器：可信控制器只挂载配置、私有场景、IPC 和结果目录，绝不执行参赛 ZIP。选手以非 root、只读文件系统运行，只挂载 ZIP 和 IPC。API 需要短期测评令牌及受限网络；未启用 API 时两个容器无网络。场景、镜像、指标定义随提交冻结。

观察仅含双相机 JPEG、相机内外参、末端位置/姿态、夹爪状态、步数及动作有效性。相机标定属于可用传感器信息，不含物体位置、布局种子、奖励或判分状态。选手代码只在自己的容器中执行，仿真器只接受有界动作。

## 构建与导入

仓库根目录执行（Docker 需可用）：

```powershell
docker build -f backend/evaluation_adapters/robot_arm/Dockerfile.controller -t local/uestc-robot-arm-controller:dev .
docker build -f backend/evaluation_adapters/robot_arm/Dockerfile.agent -t local/uestc-robot-arm-agent:dev .
python backend/build_robot_arm_package.py --output outputs/robot-arm-problem.zip
```

管理员进入「发布赛题 → 从完整赛题包导入」，选择 ZIP、预览、创建草稿。包含题面、附件要求、7 项指标、3 个随机场景和两个本机固定镜像 ID，不含密钥。组织者在「环境与资源」复用镜像并编辑任务、种子、步数、稳定步数和放置位置，可设置统一 API 额度、时间与次数。

本机 `sha256:...` ID 仅在已构建镜像的 Docker 主机可用；跨机器需上传组织方镜像仓库并改用固定 digest。禁止可变标签。无需数据库迁移。

正式运行需一次性部署：后端 `EVALUATION_ENABLED_ADAPTERS` 包含 `robot-arm-agent-v1`，后端与 worker 配置同一 `EVALUATION_WORKER_TOKEN`，设置 worker `EVALUATION_API_BASE` 并启动 `backend/evaluation_worker.py`。Worker 自动获取题目环境，无需逐题配置环境变量。验收通过不代表正式题目已发布或生产 worker 已启动。

## 选手协议

ZIP 根目录有 `agent.py` 和 `config.json`；`Agent.act(observation)` 必需，`reset(goal)`、`configure(config)` 可选。沿用有界 JSON 和 MC ZIP 加载器。预装 numpy、Pillow、requests；额外依赖提前登记镜像，不能任意联网安装。

```python
{"target": [0.1, 0.1, 0.95], "gripper": 1, "repeat": 20}
{"delta": [0, 0, 0.5, 0, 0, 0, 1], "repeat": 10}
```

target 为世界坐标（米），X/Y ±0.35、Z 0.8—1.3；统一位置伺服不替选手定位物体。delta 为 7 维 OSC_POSE 控制，各项 [-1,1]；夹爪 +1 关闭、-1 打开。repeat 为 1—20，计入实际物理步数。无效消息转换成打开夹爪的零移动一次，计入步数和无效动作；没有传送物体、自报指标或仿真代码执行接口。

`example_agent.py` 是公开固定坐标验收示例，不能用于未知布局。`vision_agent.py` 是 RGB 入门基线：颜色定位、标定射线与桌面平面求交、分阶段动作。不读取物体真值、不使用模型 API；复杂遮挡、姿态、失败恢复需自行改进。

## 场景与判分

场景必填 id、label、difficulty、task（lift/place/stack）、整数 seed、max_steps（10—3000）、hold_steps（5—100）。放置须有 target: [x,y]（±0.25 m）。可选 placements 是组织方公开验收布局：红绿 XY 在 ±0.15 m 且相隔至少 8 cm；仍不发给选手。普通包省略它，使用种子随机布局。

| 指标 | 定义 |
| --- | --- |
| task_success | 连续 hold_steps 满足物理目标，场景 0/100。抬升需真实双指抓取；放置/堆叠需先抓取抬升、再松手；堆叠还检查两积木接触、中心对齐和高度差。 |
| stable_grasps | 连续真实抓取至少 hold_steps 的事件 / 所有抓取事件；无事件为 0。 |
| recovery_success | 夹爪仍命令关闭却在空中失去抓取后，再次连续抓取 hold_steps 的次数 / 掉落次数；无掉落为 0，主动松手不算掉落。表示恢复抓取，不等同于整个任务成功。 |
| action_count | 有效和无效动作指令总数，不是物理步数。 |
| execution_seconds | 场景 reset 完成且选手确认后的实际决策与执行时间。Worker 总时限另含启动、下载、所有场景和收尾。 |
| invalid_actions | 不符合有界动作协议的消息数。 |
| stable_seconds | 最长连续满足目标步数 / 20；达到 hold_steps 即结束场景。 |

数值沿用平台的逐场景结果、汇总和评委展示。超时移除两个容器及 IPC 卷，失败计入本次额度，租约恢复不重复扣次数。

## 证据与验收

配置 worker `EVALUATION_LOG_DIRECTORY` 为私有持久目录后，每个 run ID 保存容器日志、相机帧和轨迹。轨迹含物体真值，仅供组织方审计，不能直接发布。未配置时证据随临时目录回收，数值仍入平台数据库。当前未提供评委网页录像播放器。

```powershell
python backend/docker_robot_arm_test.py --output outputs/robot-acceptance
python backend/docker_robot_arm_test.py --negative --output outputs/robot-negative
python backend/docker_robot_arm_test.py --expect-timeout --time-limit 30 --output outputs/robot-timeout
python backend/docker_robot_arm_test.py --vision --output outputs/robot-vision
```

验收使用隔离 SQLite、公有测试场景，完整走题目包预览/导入、作品提交、双 Docker、回写、评委指标核对。结果在 verification.json。不连接上游模型、不创建收费实例、不更改主库发布状态。

### 2026-10-05 本地验证

- `outputs/robot-full-final/verification.json`：公开固定布局的抓取、放置、堆叠均成功，双 Docker 的结果已回写并与评委视图逐项核对。
- `outputs/robot-negative-01/verification.json`：越界动作、无操作、自报成功均未得分；越界动作和自报成功各记录 30 次无效动作。
- `outputs/robot-timeout-01/verification.json`：30 秒超时后失败，清理容器，扣一次测试额度；额度耗尽后的下一次提交返回 429。
- `outputs/robot-vision-01/verification.json`：RGB 基线在随机种子 42/43/44 下分别得到 100/0/0；判分链路验收成功不表示视觉基线已解决三个任务。

本地题目包已导入为草稿，900 秒 / 每队 3 次；后端与专用机械臂 worker 在线。专用 worker 设置 `EVALUATION_WORKER_ADAPTERS=robot-arm-agent-v1`，避免领取其他适配器任务。Worker 可自行读取根目录 `.env`；环境变量仍优先于文件配置。重启开发后端时须停止重载子进程，避免旧配置继续占用 5000 端口。保留证据的目录仅供组织者读取。

后端回归：128 项，1 项跳过，其余通过（`outputs/robot-all-tests-final.log`）。前端构建和 lint 通过；现有前端测试 72 项通过、1 项失败，失败是未改动首页的旧文案断言（`tests/rendered-html.test.mjs:76`）。Docker 测试后未残留测评容器。
