# AutoDL 4080 分类测评

当前数据内置版本与验收状态见[镜像记录](../deploy/linux/autodl-depth-images.json)。选手训练版只含训练/验证数据，组织方测评版内置470段测试数据与标签，不能发给选手。组织方已确认取得覆盖参赛队伍的数据分发授权。所有镜像均不包含平台令牌或API密钥，编号属于组织方AutoDL账号，其它账号需按授权重新保存。

早期薄镜像 `uestc-4080-eval-v1-clean` / `image-ad5a8d1cf9` 曾完成还原验收，其记录保留在[旧版本记录](../deploy/linux/autodl-classification-image.json)；该记录不能代替新数据镜像的还原验收。

组织方使用专用 AutoDL Pro 实例，与选手 SSH 训练实例分开。AutoDL 容器实例不支持嵌套 Docker，须通过 `image/save` 保存原生私有镜像。
参考：[环境限制](https://www.autodl.com/docs/env/)、[Pro API](https://www.autodl.com/docs/instance_pro_api/)。

## 固定环境和接口

当前数据内置版本使用Python3.12.3、PyTorch2.8.0+cu128、torchvision0.23.0+cu128、NumPy2.3.2、Pillow10.4.0、psutil6.1.1、safetensors0.5.3。已在RTX4080SUPER32GB上运行470个真实数据样本；早期薄镜像的Python3.8/PyTorch2.0环境不作为当前版本环境。

解释器 `/opt/uestc-classification/runtime/bin/python`；工作端 `/opt/uestc-classification/backend/classification_worker.py`，使用现有测试机会、任务租约、指标回传和正式提交结果选择。

ZIP 根目录必须有 `inference.py`、`config.json`，不在线安装选手依赖。`Model(config, device)` 实现 `predict(sample_path)`，返回 **40 个有限 Python 数值**。深度输入为非 RGB NPY，布局 **[T,1,H,W]**，禁止 pickle；原始数据由组织方提前转换，这不是 CUHK-X 原文件格式。输出索引对应组织方发布的 40 类有序映射。

小权重放在 ZIP 中，配置 `num_classes:40`、`weights_file:"model.safetensors"`。附件限 20 MB、解压限 64 MB/256 文件。本仓库 `classification_example` 提供 3D-CNN 训练/推理示例，必须先训练；`build_classification_package.py` 打包训练后的 safetensors 权重。

大权重采用下列配置，可信下载器核验大小和 SHA256，最多 2 GiB，下载计入总时限。每次重定向都核验 `EVALUATION_MODEL_HOSTS` 白名单、公网 DNS 和 TLS，连接固定到已核验 IP。提交表中的模型链接供评委查阅；实际执行权重由冻结 ZIP 配置及摘要决定。

```json
{"num_classes":40,"weights":{"url":"https://huggingface.co/ORG/MODEL/resolve/COMMIT/model.safetensors","sha256":"64位小写摘要","size":123456789}}
```

## 可信指标

推理代码使用独立低权限 UID，不继承工作端凭据。seccomp 拒绝 IP、原始包、VSOCK 联网；Landlock 限制写入；保留 CUDA 所需 Unix 驱动通信和自身线程命名。无隔离能力时拒绝执行，组合已在 AutoDL 5.15 内核验证。

隐藏标签、原始清单和可信结果目录 root-only。选手代码只读自身程序、权重、类别映射和当前无标识 clip。一次一任务，限制 CPU 核、总时长、同 UID 进程 RSS、临时文件量；超时、终止、失去租约会终止推理进程组。

- `accuracy`：全部样本 Top-1 准确率，百分数。
- `macro_f1`：40 类 F1 的算术平均，无分母类别记 0，百分数。
- `latency_ms`：可信父进程测量请求到响应，含读取、预处理、推理、CUDA 同步、协议开销；不含环境/模型载入及组织方 clip 拷贝。第一样本作耗时预热，仍计准确率；单样本采用其耗时。
- `peak_vram_mb`：可信 `nvidia-smi` 外部采样的专用 GPU 显存占用峰值，MiB，含上下文/驱动开销。目标间隔 100 ms 加查询开销，**不是精确瞬时分配峰值**。要求 GPU 空闲独占，不接受代码自报值。

私有证据保存混淆矩阵、逐样本预测/耗时、GPU 型号、清单/ZIP 摘要。队伍/评委收到聚合指标，不暴露隐藏样本对应关系。自动指标供赛事既定 80% 表现部分评审使用，报告与答辩规则不变。

## 正式数据安装

当前组织方镜像的私有目录为 `/opt/uestc-classification/datasets/wujie-depth-v1`，root所有、0700，已内置于系统镜像。薄镜像可另用 `/root/autodl-tmp/uestc-evaluation/datasets/<版本化数据集ID>` 并覆盖环境变量。2026-10-06实测Pro数据目录位于系统盘overlay，不能假定是独立磁盘。更换系统镜像前必须备份；选手镜像必须移除测试数据与标签，所有镜像都不烘焙平台令牌。同账号共享文件存储不能隔离拥有root的不同队伍。

组织方提供授权的深度 NPY、隐藏 CSV（严格 `file,label` 两列，0～39）及 40 类有序 JSON 数组：

```bash
cd /opt/uestc-classification/backend
/opt/uestc-classification/runtime/bin/python prepare_classification_dataset.py \
  --clips /root/autodl-tmp/official-depth-clips \
  --labels /root/autodl-tmp/hidden-labels.csv \
  --classes /root/autodl-tmp/official-40-classes.json \
  --output /root/autodl-tmp/uestc-evaluation/datasets/wujie-depth-v1
```

将输出 JSON 导入题目私有场景。网页仅存 ID/清单摘要，工作端只领取已安装、摘要匹配的数据集；更新数据须新目录/新配置，不覆盖旧清单。

题目设置选“组织方 AutoDL 专用测评实例”，填写私有镜像编号，资源 `gpu:true`、`episodes:1`，时长和次数在题目统一设置。

```json
{"execution":"autodl-native","image":"image-实际保存编号","agent_image":"","scenarios":[{"dataset":"wujie-depth-v1","manifest_sha256":"清单SHA256"}]}
```

Docker/Native 工作端互不领取对方任务；Native 核对镜像和安装的数据摘要。禁止给队伍测评实例的 SSH 密码/工具入口，队伍训练资源另配。

镜像保存后，将 `deploy/linux/autodl-classification.env.example` 复制为 `/etc/uestc-classification.env`，填写工作端令牌/镜像编号，root 所有、0600。启动 `bash /opt/uestc-classification/autodl-classification-start.sh`，使用 supervisor 或 AutoDL 启动命令保持运行。关机中断沿用租约回收，同一机会不重复扣次数。

## 验收和限制

真实 4080 上的 40 类合成输入验证正确/错位预测、隐藏标签与联网隔离、NaN 拒绝、伪造写入拒绝、超时终止。真实 AutoDL 工作端与网站 API 的隔离库验证了队伍自测扣一次→回传指标→正式选择结果→队伍/评委可见，正式提交不重复扣次数。

**合成验收不是赛事成绩。** 当前已核对官方原始数据摘要、40类映射、12/3/3被试划分、全部2,931片段和跨集重复，并完成470段真实测试数据验收。弱基线一轮准确率10.2128%，仅证明接口可运行。数据镜像保存前的工作端验收使用本地资产服务，生产网站仍需接入新工作端、核对新镜像与数据摘要再开启自动测评；不能注册合成清单为正式赛题。
