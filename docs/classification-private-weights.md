# 分类题权重上传与提交检查

学生训练得到的权重直接上传比赛平台，代码 ZIP 引用该队伍的权重 ID。权重保持私有，评测 worker 使用本次自测的租约读取。现有 HTTPS 权重地址仍可用。

## 学生操作

开发包提供以下三个脚本。在本地运行，账号密码由终端提示输入，密码不写进命令或配置文件。

```bash
python upload_classification_weights.py --weights baseline.pt --problem PROBLEM_SLUG --email YOUR_EMAIL
python prepare_classification_submission.py --source . --reference weights-reference.json --output inference.zip
python check_classification_submission.py inference.zip --weights baseline.pt
```

`--problem` 填赛题页面地址末尾的标识，也支持题目 ID。脚本自动匹配当前赛事中自己的队伍，必要时可用 `--team 队伍名` 指定。上传完成生成 `weights-reference.json`。如网络中断，用相同命令加 `--resume 上传ID` 继续；已上传的分块会复用。单文件上限 2 GiB，分块大小 16 MiB。默认每队每题可存放 8 GiB、最多 32 份权重（未完成上传也按声明大小占用配额）；未用于自测的文件可以删除。

打包脚本收集当前目录的 Python 文件，以及配置中 `detector_file` 指定的小型检测器权重；额外依赖可用 `--include 子目录/文件` 加入。分类器的大权重单独上传，代码 ZIP 保持在 20 MiB 内。脚本会保留本地 `config.json`，只在生成的 ZIP 中替换权重引用：

```json
{"weights_asset": {"id": "上传返回的ID", "sha256": "上传返回的SHA256", "size": 123456}}
```

检查器检查 ZIP 结构与大小、40 类配置、`Model.predict` 声明，以及本地权重与提交引用的 SHA256。它不执行模型、不消耗平台自测次数。完整预测与准确率仍使用开发包的本地验证命令。

将生成的 `inference.zip` 上传原有提交页，发起自测。每队三次自测、正式提交选择已完成的自测结果，流程保持不变。权重上传不占用自测次数；已被自测引用的权重保留，避免历史成绩对应的模型被替换。

## API

所有学生接口要求当前登录用户是该队伍成员，并已报名该题所属赛道。

| 操作 | 接口 |
|---|---|
| 创建上传 | `POST /api/problems/{problem}/model-weights`，JSON：`team_id/name/size/sha256` |
| 查看分块 | `GET /api/model-weights/{id}` |
| 上传分块 | `PUT /api/model-weights/{id}/chunks/{index}`，请求体为对应原始字节 |
| 完成上传 | `POST /api/model-weights/{id}/complete` |
| 删除未引用权重 | `DELETE /api/model-weights/{id}` |

完整文件通过 SHA256 后才进入 `ready` 状态。自测创建时校验队伍、题目、大小和哈希，并冻结引用。worker 在 lease 认证下下载同一份文件，复核大小与 SHA256，再以只读文件交给隔离推理程序。普通学生和其他队伍没有权重下载接口。

## 发布配置与验收

1. 备份数据库，运行迁移至 `f18c20261018`；该迁移只新增 `model_weight_assets` 表。
2. 更新 API 与分类 worker／推理镜像，配置 `CLASSIFICATION_WEIGHT_UPLOAD_ENABLED=1`。磁盘容量按实际队伍数配置，`CLASSIFICATION_WEIGHT_TEAM_BYTES` 可调整每队配额。
3. 确认反向代理允许至少 17 MiB 的请求，分块上传保留现有请求上限。完整权重下载需覆盖文件实际传输时间；上传、worker 下载都使用 HTTPS。
4. 发布包含三个脚本的新开发包，并在下载说明给出实际题目、队伍 ID 查询入口。当前上传入口为开发包命令行，网站新增拖放权重控件尚未实现。
5. 使用学生测试账号上传约 250 MiB 权重，模拟断线续传，运行一次真实 GPU 自测并正式选择结果，核对引用和成绩。测试完检查其他队伍无法访问。

本地集成测试使用真实 Flask 路由和临时数据库，覆盖分块、冻结、权限、快照、worker 下载与正式提交。模型计算在该集成测试中使用固定结果；生产 HTTPS、完整大文件传输、GPU 推理和浏览器操作仍需部署验收。
