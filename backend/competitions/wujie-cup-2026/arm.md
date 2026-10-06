## 任务目标

使用 LIBERO Panda 仿真机械臂，根据自然语言任务与相机画面完成抓取、放置和组合操作。队伍可直接实现策略，也可在策略外加 agent harness，组织任务分解、模型调用、记忆、技能和反馈重试。所有队伍使用相同任务与原生成功判定。

## 任务范围与场景变化

一次测试包含三个独立任务，编号使用官方 `task_order_index=0` 顺序。

| 任务套件 / ID | 操作目标 | 最大控制步数 |
| --- | --- | --- |
| `libero_spatial / 2` | 从桌面中央抓取黑碗，放到盘子上 | 600 |
| `libero_object / 0` | 将字母汤罐放入篮子 | 600 |
| `libero_10 / 0` | 将字母汤罐和番茄酱两件物品放入篮子 | 1000 |

练习公布所选任务和初始状态0、1、2。正式测试沿用相同任务，换另一组官方有效初始状态与种子。每场景独立重置；不临时改任务指令、不增加未公布任务或生成无效布局。后续扩展须赛前统一公告。

## 练习材料与环境

[下载 LIBERO 开发包](/downloads/wujie-arm-starter.zip?v=wujie-v4)：含公开练习配置、接口基线、自定义 Policy 示例、模型代理、本地入口与安装说明。接口基线输出合法空动作，用于首次接通，不宣称具有抓取成功率。

安装匹配环境后运行 `python practice.py --package agent.zip --scenes practice-scenes.json --output practice-output`。代码、物体资源和初始状态来自 [LIBERO 官方项目](https://github.com/Lifelong-Robot-Learning/LIBERO)，固定提交 `8f1084e3132a39270c3a13ebe37270a43ece2a01`。环境为 Python3.11、robosuite1.4.0、MuJoCo2.3.7、NumPy1.26.4、128×128外部与腕部相机、20Hz控制、CPU无头渲染。练习与测试统一使用128×128绘制缓冲区，关闭多重采样、实时阴影和反射；保留物理状态、纹理、相机和原生成功判定，避免软件渲染开销占用策略时间。

训练策略可参考官方模仿学习代码与所选任务演示数据，下载方法见官方文档。默认选手容器含 NumPy、Pillow、requests，不要求训练模型；需要额外模型依赖、GPU或大权重时须赛前登记，当前 CPU 测评不临时安装依赖。

## 策略与 harness 接口

ZIP 根目录含 `agent.py`、`config.json`。定义 `Agent.act(observation)`，可选 configure 和 reset。`reset(goal)` 接收 `instruction`、`suite`、`task_name`、`max_steps`，不提供测试种子或初始状态编号。

观察提供 `images_jpeg_base64`（agentview、robot0_eye_in_hand）、`eef_position`、`eef_quaternion`、`gripper_position`、`joint_position`、`step`、`last_action_valid`、`control`。画面为正向JPEG，策略自行预处理；不提供物体真值、奖励或成功标志。

每次 act 返回原生七维浮点列表 `[dx,dy,dz,rx,ry,rz,g]`，各项有限且在[-1,1]，g=1关闭夹爪，g=-1打开。一次对应一个控制步；非法动作执行安全空动作并计数。开发包的 Policy 可替换为学习策略、视觉策略或 harness，不沿用旧积木环境的 `{target,gripper,repeat}` 动作。

模型使用 `model_api.py`，测评注入 `EVALUATION_API_URL`、`EVALUATION_API_TOKEN`，请求头 `X-Evaluation-API-Token`；允许模型见资源面板。最多300次调用由全部任务共享。环境与选手程序处于独立容器，私有场景和结果目录不挂载给选手。

## 提交与运行限制

提交策略或 Agent 代码、配置 ZIP、README 技术路线说明及复现命令。描述模型、权重、依赖和资源；补充 PDF、视频、研究与对照实验可选。

每队每题3次自测，整次1800秒。选手程序4CPU、2GiB内存，独立LIBERO环境3GiB内存，无GPU；内存为硬上限，选手程序超限会结束该次测试并计次。整组测评容器合计最多6GiB，不使用交换空间；排队不计运行时限。ZIP20MB、最多256文件、解压64MB。三个任务总控制步数上限2200；时限包含初始化、策略和仿真。

本地练习不限次数；平台自测与 checkpoint 分开。完成后选择一次结果，用相同 ZIP 正式提交，不多扣次数。队伍与评委查看同一份正式结果。

## 结果与评分口径

可信环境使用 LIBERO 原生 `check_success()`，选手自报成功无效。返回总成功率、各任务成功率、控制步数、执行耗时、无效动作，保存回放和动作轨迹。

**自动表现分 = 三个任务成功率的平均值**，版本 `wujie-libero-v1`；成功记100，否则0。执行效率和轨迹用于调试及技术路线说明，不因空动作运行快而加分。任务库、初始状态和预算在首个正式测试前冻结。
