# LIBERO 五题版本接入

参考步数固定为300、300、600、700、1100；自动测评中，完成分10/10/10/15/15，效率分40按相同比例分配。五题使用同一冻结作品。总成绩为自动测评50分、技术报告10分、现场答辩40分，代码审阅为通过/不通过。

开发包、题目构建器已采用v2计分、公开初态0/1/2及对应参考步数。开发1800秒/300次API，正式7200秒/500次API；五题步数上限600/600/1200/1400/2200，均为对应参考步数的两倍。镜像ID和运行配置见组织方交付的images.json、site-config.json及final-request.json。

现有C题使用backend/activate_libero_release.py保存旧配置并更新，队伍、提交和历史记录保留。新题可导入组织方题目ZIP。backend/libero_final_batch.py提供登录、调度和发布命令。正式测评启动后配置按同一场景版本与预算冻结。

学生下载公开开发包和practice镜像；evaluator镜像及私有场景由组织方保管。观察字段与七维动作协议见backend/competitions/wujie-cup-2026/arm.md；计分公式见backend/competitions/wujie-cup-2026/libero-rules.md。

## 发布文件

公开环境及 SHA256SUMS 位于 [LIBERO 开发环境发布页](https://github.com/Takamatsu-Hikaru/uestc_ai/releases/tag/libero-20261018-final)。题面直接链接这份离线镜像，开发包随网站静态资源发布。公开文件的地址、大小和镜像摘要记录在 `public/downloads/libero-release.json`。组织方终评镜像、两道场景的 BDDL 和初态文件单独交付，保存在网站公开目录之外。

## 网站配置

在仓库根目录生成当前代码对应的 C 题配置：

```bash
python backend/build_libero_release.py --output outputs/libero-release
```

现有网站在当前虚拟环境及生产环境变量下执行：

```bash
python backend/activate_libero_release.py --config outputs/libero-release/site-config.json
python backend/activate_libero_release.py --config outputs/libero-release/site-config.json --snapshot /srv/uestc-private/libero-before.json
```

第一条预览题目；第二条保存旧配置后应用。新建赛题可在管理页导入生成的 `wujie-robot-arm-agent.zip`。本次只更新 C 题，无需重新导入整场比赛。该变更不新增数据库列。

网页重新构建、重启，静态开发包同步到 `dist/client/downloads/`。访问 C 题，核对开发包下载、离线环境链接、五题分值和报名入口。开放自测前，在 API 平台为 C 题配置统一允许模型和每队额度；新队伍会取得对应资源，学生从该页面获取密钥。模型名与最终评测策略使用同一名称。

## 评分录入

自动测评、报告和答辩均按百分制记录，最终按 `总成绩 = 0.5 × 自动测评 + 0.1 × 报告 + 0.4 × 答辩` 合成。

评审页的自动表现分由测评结果写入，评委填写技术报告分并选择代码审阅通过或不通过。报告按方案设计30%、实现与复现30%、结果与局限分析30%、引用与团队贡献10%评定。代码审阅不通过的作品不生成最终成绩。

现场答辩按方案介绍30%、运行演示30%、现场问答40%评定，合成百分制分数后导入网站，对应总成绩中的12、12、16分。网站将自动测评与报告合为60分的线上部分，其中权重为5/6与1/6；答辩占其余40分。例如自动测评80、报告90、答辩70，最终成绩为77分。

## 测评节点

```bash
docker load -i wujie-libero-practice-20261018-final.tar.gz
docker load -i /srv/uestc-private/wujie-libero-evaluator-20261018-final.tar.gz
```

评测镜像及 Agent 镜像 ID 由私有交付清单核对；开发镜像 ID 可用公开发布清单核对。两份镜像加载后，按 `deploy/linux/worker.md` 启动当前代码的 worker，启用 `libero-agent-v1`，配置受限网络与模型代理。终评使用 `deploy/libero/final-request.json`，其中两个 `scene_asset` 由组织方镜像提供。

API 服务配置 `EVALUATION_API_URL`、`EVALUATION_API_KEY` 与 `EVALUATION_FINAL_API_POLICY_JSON`；后者格式见 `deploy/libero/final-api-policy.example.json`。填写当前统一模型名称，单次输出上限须与对外资源一致。密钥保留在服务环境变量中。

## 提交与最终评测

学生上传 ZIP、发起三题自测，关联完成的记录并正式提交。截止后管理员使用同一份最终请求调度五题：

```bash
python backend/libero_final_batch.py --email ADMIN_EMAIL --version-id VERSION_ID --request deploy/libero/final-request.json
python backend/libero_final_batch.py --email ADMIN_EMAIL --publish-run RUN_ID
```

登录密码通过 `UESTC_ORGANIZER_PASSWORD` 环境变量提供。评委在评审页填写报告分，代码审阅选择通过或不通过；答辩分以百分制导入。发布后，作品页展示五题自动测评分、逐题结果、双视角视频与轨迹；报告与答辩成绩齐全后生成最终成绩。原开发记录保留。
