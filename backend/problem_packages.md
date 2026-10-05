# 赛题环境配置与整包导入

## 管理入口

赛事管理 → 赛题 → **环境与资源**，统一保存以下配置：

- 指标测试：评测器、CPU/内存/GPU、整次测试时长、场景数量、每队累计次数、受控 API 调用上限及展示指标。
- Docker：管理员登记固定镜像摘要，组织者可复用已登记的可信镜像；MC 开放世界另需独立智能体镜像，避免智能体接触环境控制器和结果目录。
- 私有测试场景：MC 可直接编辑种子、收集目标、难度与行动步数，或上传 JSON；其他可信评测器可从 `/input/scenarios.json` 读取场景数据。
- 模型 API：选择已有渠道和模型，统一各队 Token、调用次数、费用及并发额度。
- AutoDL Pro：选择已有 GPU/镜像渠道，统一各队卡时和参考费用额度。队伍在 API 平台开机后获取私有 SSH、JupyterLab 等工具。

保存为单个数据库事务；任何部分无效或额度低于已用/预占量时，全部回滚。所有队伍额度相同，各队独立计量，新队伍继承策略。暂停资源应取消启用，保留授权与用量记录，不删除配额。

状态检查显示已保存配置与最近 75 秒的测评端心跳，不执行容器、开机或模型请求。渠道已配置不代表 GPU 有库存或第三方服务一定正常；渠道的实际连接测试仍在 API 平台完成。没有在线匹配运行端时明确显示待配置。

## ZIP 格式 v1

新建题目页支持上传 ZIP、检查预览、选择目标赛道并导入为草稿；现有题目可导出赛题包。跨平台导入时，可在预览前重新绑定模型渠道和算力渠道。

| 文件 | 内容 |
| --- | --- |
| `problem.json` | 必填。`version: 1`、`problem` 题目信息与可选 `setup` |
| `statement.md` | 可选。覆盖题面 Markdown |
| `runtime.json` | 可选。固定评测器镜像 `image`、MC 的 `agent_image` |
| `scenarios.json` | 可选。私有场景 JSON 列表，需配套 runtime |
| `connections.json` | 可选。按名称匹配渠道；不包含凭据 |
| `README.md` | 可选。组织方使用说明 |

`problem.json` 示例：

```json
{
  "version": 1,
  "problem": {
    "code": "MC-001",
    "slug": "open-world-agent",
    "title": "无界探索：开放世界智能体挑战赛",
    "difficulty": 4,
    "submission_schema": {"fields": ["repository", "report"], "readme_required": true},
    "judging_schema": {"rubric": {"system": 0.5, "report": 0.5}},
    "scoring_config": {"external_weight_percent": 0}
  },
  "setup": {
    "evaluation_config": {
      "adapter": "minecraft-agent-v1",
      "task": "open-world",
      "resources": {"cpus": 4, "memory_mb": 6144, "gpu": false, "time_seconds": 900, "episodes": 1},
      "api": {"enabled": false, "max_calls": 0},
      "metrics": ["task_success", "exploration_progress", "objectives", "unique_items", "distance_blocks"],
      "max_team_runs": 3
    }
  }
}
```

`runtime.json` 的镜像必须使用实际构建的 `镜像名@sha256:摘要` 或本机 `sha256:镜像ID`；不能使用可变版本标签。私有场景数量须等于 `episodes`。例如：

```json
[
  {"id": "beginner-1", "label": "基础探索", "difficulty": "beginner", "task_id": "open-ended", "world_seed": 1042, "max_steps": 600, "goals": ["log", "crafting_table"]}
]
```

API 可选配置 `setup.ai` 与既有统一额度接口同形：`enabled`、`allowed_models`、`allowed_channels`、`max_tokens`、`max_calls`、`max_output_tokens`、`requests_per_minute`、`max_concurrent`、`max_cost_micros`。AutoDL `setup.compute` 为 `enabled`、`provider_id`、`max_gpu_seconds`、`max_cost_millis`。导出会去掉渠道 ID，写入 `connections.json` 的 `channels: ["名称"]`、`provider: "名称"`，导入时绑定目标平台已有渠道，不创建新凭据。

ZIP 只接收这些约定文件，不解压到项目目录、不自动构建或执行上传的 Dockerfile/Python。限制为压缩包 20 MB、总展开内容 4 MB、单文件 2 MB；拒绝重复路径、目录、路径穿越、链接和加密文件。大依赖、模型权重与数据集应打入固定可信镜像，或由可信评测器以固定版本管理。

导出仅限管理员和组织者，包含私有场景，应妥善保存；不包含模型/AutoDL 密钥、SSH 密码、实例工具 Token 或队伍用量。参赛者的公开题目、作品快照和评测结果不返回镜像或场景定义。

## 运行端一次接入

管理员仍需一次部署可信 Docker 测评端：设置平台的 `EVALUATION_WORKER_TOKEN`、`EVALUATION_ENABLED_ADAPTERS`，为 worker 设置相同认证、`EVALUATION_API_BASE`，启动 `python backend/evaluation_worker.py`。具体安全部署见 [MC 接入备忘](minecraft_benchmark.md)。

采用题目环境配置后，不必再逐题修改 worker 的镜像映射和 MC 场景路径；worker 每次轮询从受认证目录发现题目适配器，并领取提交时冻结的私有运行快照。旧的 `EVALUATION_IMAGES_JSON` / `EVALUATION_AGENT_IMAGE` / `EVALUATION_MINECRAFT_SCENARIOS` 仍兼容。旧 worker 不会领取题目管理的新环境任务；缺少 GPU 或受限 API 网络的 worker 不会领取对应任务。

正式 API 测试还需配置受限制的 `EVALUATION_API_PROXY_URL` / `EVALUATION_NETWORK`；仅发放本次运行的 API Token，代理按本题每队额度和整次调用上限计量。队伍自用 AutoDL 实例不能因为能 SSH 就被当作可信测评端；测评端应由组织方独立部署管理，正式指标由可信环境收集。

新类型的复杂题目需要先安装可信评测器及指标声明，再打包题目。通用容器协议并不自动保证任意第三方评测代码的可信性：可信评测器必须独立隔离参赛程序、隐藏私有测试资料并掌管结果输出。MC 当前已实现独立控制器与智能体容器。

已有作品提交后，镜像、场景与固定评分规则不能更改，时长与次数可调整且只影响后续运行；已排队任务使用冻结快照。MC 开放世界当前仅支持九项可信探索/任务/动作指标，API 用量按整次运行展示，生存时长与 API 费用不冒充已实现的场景指标。

## 验证

`tests/test_problem_setup.py` 覆盖原子回滚、保留用量、私有场景不可读、运行快照、worker 兼容性、导出/导入回环、非法 ZIP 与角色限制。

`python backend/docker_minecraft_test.py --time-limit 900` 默认验证赛题包预览/导入 → 草稿发布 → 参赛 ZIP → 题目运行快照 → 实际 MineDojo Docker → 评委指标。`--legacy-runtime` 可验证旧环境映射；`--time-limit 30 --expect-timeout` 检查时限、容器清理与次数扣减，全部使用独立数据库，不触发收费算力。

2026-10-05 本机真实 MC 整包验收通过，运行约 264 秒：三个公开短场景的探索进度为 100% / 50% / 0%，任务成功率为 100% / 0% / 0%，第三场景的 8 次无效动作全部记入，评委读取指标与测评记录一致。180 秒的冷启动验收触发总时限并清理容器；启动成本必须计入题目预算。这些是公开验收场景，正式隐藏场景仍由组织方设置，不能将短场景通过当成完整比赛难度验收。
