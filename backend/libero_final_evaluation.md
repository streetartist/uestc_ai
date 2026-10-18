# LIBERO 独立最终评测接口

本分支实现独立最终评测，复用现有可信工作进程与双容器隔离。`EvaluationRun.purpose` 增加 `final` 值，无需新增数据库列。

## 规则与提交

在 LIBERO 的 `scoring_config` 中设置 `final_evaluation_required: true`。A、B 赛题保持原流程。

学生仍提交一个 ZIP，并关联同 SHA256 的已完成平台自测。该结果作为开发记录保存。最终评分只取组织方独立执行的最终结果；学生不能调用最终调度、结果回传或发布接口。原有组织方外部分数导入保留用于现场答辩。

组织方在赛事 `ends_at` 截止之后调度最终测评。首次调度冻结该作品，后续不能通过提交接口覆盖代码、说明或附件。快照记录 ZIP SHA256、全部附件 SHA256、提交快照哈希、场景集版本、运行配置哈希、最终预算配置哈希和冻结时间。worker 下载 ZIP 时再次核对文件哈希。

作品提交截止使用 `Competition.ends_at`。部署时填写第一阶段提交截止日，答辩日期在赛事安排中另列。

## 调度

管理员或组织者登录后请求：

```text
POST /api/submission-versions/{version_id}/final-evaluation-runs
```

请求对象必须包含三个字段：

| 字段 | 内容 |
|---|---|
| `scene_set_version` | 组织方确定的场景集版本名，限字母、数字、点、下划线、短横线 |
| `runtime` | 完整独立运行配置：固定摘要控制器镜像、固定摘要 Agent 镜像、私有场景列表 |
| `evaluation_config` | 完整最终预算配置；沿用题目的 adapter、task、metrics，场数、时间、请求数由组织方填写 |

本版本固定五题、7200秒、500次API调用，参考步数300/300/600/700/1100。最终场景数量必须与 `resources.episodes` 一致。同一题目的所有最终调度使用同一场景集版本、配置与预算。首个队伍调度后禁止用不同配置启动其他队伍。

五题v2正式测评保留前三道公开任务；后两道组合题与当前和历史平台自测比较。仅修改 id、label、difficulty、seed 或 max_steps 的重复初态会被拒绝；恢复同一个 LIBERO 初态时，改 seed 不会被当作新布局。不同 `scene_asset` 的具体 BDDL/初态内容由可信镜像的场景包哈希绑定。程序判重不能证明某个官方初态从未公开，最终场景仍须由组织方检查并选择。

API 启用时，要求服务器设置组织方 `EVALUATION_API_URL`（HTTPS）、`EVALUATION_API_KEY` 和 `EVALUATION_FINAL_API_POLICY_JSON`。缺少通道返回 503，策略不完整返回 400，均不创建最终任务。最终调用走此通道，受本轮 `api.max_calls` 限制；不调用团队 grant，不增加团队自测次数。

`EVALUATION_FINAL_API_POLICY_JSON` 必填 `allowed_models`、`max_output_tokens`、`reasoning_efforts`，可填 `max_request_bytes`、`max_response_bytes`、`timeout_seconds`；后三项默认沿用全局 AI 网关上限，不能超出它们。模型、推理强度、输出上限要与公布的资源一致，服务端不设置比赛默认值。策略写入终评快照并纳入统一配置哈希，后续队伍不能换一套参数。

最终代理复用现有 AI 网关消息/内联图像/token 校验，保留 `thinking`、`reasoning_effort`、`response_format`。只允许完整响应、单候选和明确支持的请求字段；禁止越过输出上限、指定未授权模型或另设无界推理预算。请求未写输出上限时使用冻结策略的上限，不隐式截成 1024。验证测试使用 6 张内联图像、32768 输出上限、thinking low、120 秒超时，并模拟上游回传；这些测试值不是发布默认值。

最终任务由现有 `/api/evaluation-worker/claim` 领取。只向认证的可信 worker 返回 runtime/scenarios。现有 worker 将场景文件挂载到控制器，Agent 通过原观测与动作接口交互。私有场景包必须放在控制器镜像中，Agent 镜像不得包含它。

## 结果与发布

worker 结果必须包含与冻结场景列表顺序一致的 `scenario: {id, label, difficulty}`，场数与指标通过现有校验；只有有效租约可回传。最终结果存为 `purpose=final`，不接受参赛者自报分数替代。

组织者和有权限的评委可查最终结果。参赛队在发布前看不到最终指标、回放、轨迹和日志，作品页面只给运行状态；关联的开发记录仍可查看。待结果审核完成，由组织方请求：

```text
POST /api/evaluation-runs/{run_id}/publish
```

只能发布当前冻结作品的已完成最终结果。发布后队伍可查看结果和实验材料。公开作品仍遵守原有展示授权，排行榜须等待最终结果发布。

响应新增字段：`result_scope`、`scene_set_version`、`scene_set_sha256`、`final_config_sha256`、`version_sha256`、`frozen_at`。作品详情新增 `final_evaluation_required`、私有 `development_evaluation`、`final_evaluation_status`；后者取 pending/queued/running/failed/awaiting_publication/completed。

失败重跑只能由组织方发起，复用同一冻结作品、场景和预算；已完成结果不会被后续重跑覆盖。运行失败不会增加团队自测计数。实际服务故障的判定与重跑记录由组织方审核，不能把正常低分当作服务故障。

## 接入与验收

C 题配置生成、历史版本迁移、镜像安装与统一终评命令见 [五题版本接入](../docs/libero-v2-activation.md)。网页和真实 Docker 的完整验收记录见 [网页验收](../docs/libero-web-acceptance.md)。
