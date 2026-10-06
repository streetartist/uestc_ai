## 任务目标

在 MineDojo Minecraft 开放世界中，从空背包开始完成资源获取、工具制作与科技推进。智能体观察画面与自身状态，自主决定行动。简单策略、规划器、记忆模块、技能库和代码执行 harness 都可以接入，技术路线由队伍选择。

一次测试包含三个独立场景，每个档位一个场景，环境独立重置、物品不跨场景携带。

| 档位 | 目标物品（接口名称） | 最大动作步数 |
| --- | --- | --- |
| 入门：木制工具链 | 原木 `log`、木板 `planks`、工作台 `crafting_table` | 600 |
| 进阶：石器工具链 | 木镐 `wooden_pickaxe`、圆石 `cobblestone`、石镐 `stone_pickaxe`、熔炉 `furnace` | 1500 |
| 挑战：铁器工具链 | 铁矿 `iron_ore`、铁锭 `iron_ingot`、铁镐 `iron_pickaxe` | 3000 |

目标物品曾进入背包或可信制作事件即记为达成，后续消耗不撤销记录。全部完成、死亡或步数用尽结束当前场景。正式测试保留目标类型与难度口径，更换世界种子；不临时增加建造、战斗任务。

## 练习材料

[下载 Minecraft 开发包](/downloads/wujie-world-starter.zip?v=wujie-v4)：含 Agent 示例、模型代理示例、三档公开练习配置和本地入口。练习世界种子为42、43、44，正式种子固定且保密。本地练习可反复运行，不消耗平台自测次数。

安装匹配的 MineDojo 环境后，运行 `python practice.py --package agent.zip --scenes practice-scenes.json --output practice-output`；安装和动作语义见 [MineDojo 官方项目](https://github.com/MineDojo/MineDojo)。示例用于接口接通，不保证完成任务。

## 开发接口与 harness

ZIP 根目录含 `agent.py`、`config.json`，定义 `Agent`。必需 `act(observation)`，可选 `reset(goal)`、`configure(config)`。每场景 reset 接收目标物品列表。

观察保持 `image_jpeg_base64`、`inventory`、`position`、`health`、`noop_action` 五项；act 返回原生八维整数动作。规划、模型、代码执行、记忆与技能可放在 Agent 内部；控制器只提供观察和执行合法动作，不接受选手自报成绩。

动作索引遵循 [MineDojo 官方动作说明](https://docs.minedojo.org/sections/core_api/action_space.html)。先复制 `observation["noop_action"]`，只修改需要的维度，避免把摄像机静止值12误写成0。

| 索引 | 操作与取值 |
| --- | --- |
| 0／1 | 前后／左右：0静止，1前／左，2后／右 |
| 2 | 0静止、1跳跃、2潜行、3冲刺 |
| 3／4 | 俯仰／偏航：0至24，对应−180°至180°，12为不转动 |
| 5 | 0无操作、1使用、2丢弃、3攻击、4制作／冶炼、5装备、6放置、7销毁 |
| 6 | 制作／冶炼的物品编号，使用开发包244项固定目录 |
| 7 | 装备／放置／销毁的背包槽位，0至35 |

开发包提供 `minecraft_actions.py` 和 `action_items.json`。例如 `craft_action(observation, "planks")` 生成制作木板的动作，不需要选手容器安装MineDojo。制作仍须具备材料并满足工作台／熔炉条件；合法动作不等于制作成功，应根据后续观察调整策略。使用这些助手时，将两个文件一并放入代码ZIP。

选手程序使用 Python3.11、NumPy、Pillow、requests 和标准库。纯 Python 模块可随 ZIP 提交，额外依赖须提前登记运行镜像。`/tmp` 和同一进程状态可在本次测试内保留，reset 时自行清理或更新；测试结束后删除，不跨测试保存。

模型调用使用 `model_api.py`：`ModelClient(model="资源面板中的允许模型").chat(text, images=[jpeg_base64])`。测评注入 `EVALUATION_API_URL`、`EVALUATION_API_TOKEN`，请求头为 `X-Evaluation-API-Token`；允许模型见题目资源面板。300次调用由全部场景共享，同时受本队本题总额度约束。禁止包内放密钥或绕过代理联网；渠道未配置时不放行正式自测。

## 提交与运行限制

提交智能体代码与配置 ZIP、README 技术路线说明、运行命令、依赖与引用。补充 PDF、视频和研究实验可选，不强制完整论文或消融实验。

每队每题3次平台自测，整次3600秒（含环境启动和全部场景）。选手程序4CPU、2GiB内存，独立MineDojo环境4GiB内存，无GPU；内存为硬上限，选手程序超限会结束该次测试并计次。整组测评容器合计最多6GiB，不使用交换空间；排队不计运行时限。ZIP20MB、最多256个文件、解压64MB。独立选手容器不能读取私有场景和结果目录。

上传 ZIP → 发起自测 → 查看分场景指标 → 选择完成结果 → 用相同 ZIP 正式提交并补齐技术路线。正式选择不再扣次数，队伍与评委查看同一份正式结果；新自测不会自动替换正式选择。

## 指标与评分口径

展示任务达成率、探索进度、已完成目标数、不同物品数、移动距离、科技里程碑、死亡、无效动作和平均动作耗时。API调用次数按整次测试展示。探索进度是目标完成比例，移动距离仅作观察，不等同于探索能力。

每场景全部目标完成时 `S=100`，否则 `S=0`；`P=100×已完成目标数/目标总数`。**自动表现分 = 三个场景的 `0.70×P＋0.30×S` 的平均值**，版本 `wujie-world-v1`。各档权重相同，部分完成也有进度分。
