# 慧眼巧手开发包

## 任务与计分

公开练习包含黑碗放盘、字母汤罐入篮、双物品入篮。正式测评运行这三题及两道新布局组合题，完成分依次为10、10、10、15、15，效率分共40分，参考步数依次为300、300、600、700、1100。组合题两个目标等权，共用本题参考步数；达成步数从本题开局累计。五题控制步数上限依次为600、600、1200、1400、2200；本地公开三题合计2400步，最终五题合计6000步。

## 环境与模型

Windows 使用 Docker Desktop 的 WSL2 后端，Linux/WSL 使用 Docker Engine。仿真使用CPU，本地环境与Agent共用4核、4GB容器；官网分别限制Agent为2GiB、环境为3GiB。配套离线镜像已包含LIBERO、场景资源、模型请求依赖和视频编码器。

解压开发包，将 `wujie-libero-practice-20261018-final.tar.gz` 放到同一目录。官网「API平台」提供本队C题的允许模型名称和API Key。将模型名称填入 `config.json` 的 `model`，在终端设置Key并运行：

```powershell
$env:UESTC_MODEL_API_KEY = "你的 Key"
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1
```

Linux/WSL：

```bash
export UESTC_MODEL_API_KEY='你的 Key'
bash run.sh
```

首次运行自动导入离线镜像、生成 `agent.zip` 并测试三个公开任务。`UESTC_MODEL_API_URL` 可设置完整请求地址，默认 `https://uestcai.top/api/ai/v1/chat/completions`。官网测评由平台注入模型接口和临时凭据。

`config.json` 默认输出上限4096，API等待135秒。`thinking: true`、`reasoning_effort: "low"` 可启用额度支持的推理模式。模型名称、输出上限和请求额度以本队C题的API页面为准。

## Harness与接口

`agent.py` 调用模型选择动作技能；`robot_skills.py` 逐步执行移动、旋转、夹爪动作，并返回位移、位置误差和夹爪反馈。模型在下一次决策中接收前后画面、最近技能记录及自己保存的memory。提示词、规划、记忆、技能和验证逻辑均可修改。

平台按 `configure(config)`、`reset(goal)`、`act(observation)` 调用Agent。

| 接口 | 内容 |
| --- | --- |
| `configure(config)` | 接收config.json中的自定义配置 |
| `reset(goal)` | 每题开始调用；goal含instruction、suite、task_name、max_steps |
| `act(observation)` | 接收当前观察，返回一个七维动作 |

观察字段：

| 字段 | 含义 |
| --- | --- |
| `images_jpeg_base64` | agentview外部相机、robot0_eye_in_hand腕部相机，正向128×128 JPEG |
| `eef_position`、`eef_quaternion` | 末端位置与姿态，四元数顺序xyzw |
| `gripper_position`、`joint_position` | 夹爪与机械臂关节状态 |
| `step`、`last_action_valid` | 已执行控制步数、上一动作是否合法 |
| `control` | 动作格式、控制频率及夹爪方向 |

`act` 返回 `[dx,dy,dz,rx,ry,rz,g]`，每项为[-1,1]内有限数值。平移采用世界坐标相对增量，每轴缩放至最多0.05米；旋转为相对轴角，每轴最多0.5弧度；g=1关闭夹爪，g=-1打开。每次返回执行一个20Hz控制步。实际位移受动力学与碰撞影响。非法动作保留上一夹爪命令、执行零位移与零旋转，并计入控制步。

模型返回的技能JSON由默认harness解析；平台接收的始终是七维动作。物体真值、接触状态、奖励和原生成功标志由可信评测端保存。

## 运行与分析

结果保存在独立的 `runs/日期时间/` 目录，打开 `review.html` 查看逐题分数、双视角视频、动作轨迹和模型决策。

- `result.json`：整轮原始指标。
- `evidence/题号/trajectory.json`：逐控制步记录、目标有效达成步数及参考值。
- `agent/harness.jsonl`：模型回复、用量、技能与执行反馈。
- `partial-results.json`：中断时已完成题目的记录。

公开三题合计完成分30、效率分20，开发包显示这一部分的得分，满分50。正式测评加入后两题，满分100。每路回放以256×256录制，合成512×256视频，按仿真时间播放。模型等待时长单独保存在轨迹中。

复制 `practice-scenes.json` 并保留一题，可单独调试；修改 `init_state_id` 可尝试同一任务的其他公开初态。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\run.ps1 -Scenes bowl-scenes.json
```

```bash
bash run.sh bowl-scenes.json
```

本地默认整轮时限1800秒。在已安装环境中可用 `python practice.py --package agent.zip --scenes practice-scenes.json --output runs/experiment-01 --time-seconds 3600` 调整。官网自测采用题面公布的统一资源限制。

## 打包与官网提交

`submission-files.json` 列出作品文件，默认包含 `agent.py`、`config.json`、`model_api.py`、`robot_skills.py`。新增模块或权重时加入该列表，再运行脚本或 `python pack.py` 生成ZIP。ZIP根目录须含agent.py与config.json；上限20MiB、256文件、解压64MiB。API Key通过环境变量设置。

登录官网、创建或加入队伍并报名C题后，在C题提交页上传本地测试的 `agent.zip`，发起官网自测。自测完成后查看结果与回放，关联相同ZIP的测评记录，填写技术路线和运行说明并正式提交。代码仓库、演示链接和补充PDF按需附上。

修改作品后重新生成ZIP并自测，新记录用于新版本提交。提交截止后，组织方冻结同一份作品，统一运行五题，核对代码后发布成绩与回放。代码审阅为通过/不通过。最终成绩为自动测评50%＋技术报告10%＋答辩40%。报告评分为方案设计30%、实现与复现30%、结果与局限分析30%、引用与团队贡献10%；答辩评分为方案介绍30%、运行演示30%、现场问答40%。

报告模板见 `technical-route.md`，与提交 ZIP、运行配置和自测记录一同填写。

`nearest_neighbor_agent.py`、`train_policy.py` 提供公开演示数据的最近邻模仿学习示例，见 `BASELINE.md`。
