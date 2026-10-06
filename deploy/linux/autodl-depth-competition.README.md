# 无界杯人体行为识别比赛镜像

预装环境：Python3.12.3、PyTorch2.8.0+cu128、torchvision0.23.0+cu128、NumPy2.3.2、Pillow10.4.0、safetensors0.5.3。解释器为 `/opt/uestc-classification/runtime/bin/python`，训练示例为 `/opt/uestc-classification/starter`。

本届提供两种内置数据镜像：选手镜像内含1,995段训练数据和466段验证数据；组织方镜像内含470段测试数据和标签，只能由组织方使用。另保留不带数据的v3薄镜像。用户已确认取得覆盖本届参赛队伍的数据分发授权，不能把本届授权理解为向公众任意发布原始数据的许可。所有镜像均不包含平台/API密钥或工作端令牌。

选手使用说明见 `autodl-depth-participant.README.md`，组织方说明见 `autodl-depth-organizer.README.md`。镜像内的 `/opt/uestc-classification/README.md` 与 `image-role.json` 标明当前用途。创建内置数据镜像的实例后无需重新下载或预处理；下文数据准备步骤仅用于重建数据或薄镜像。

## 数据准备

使用获得官方访问权限的账号下载 `Kevin-Pal/CUHK-X_Small_Model_Track`。使用支持分卷ZIP的工具提取 `HAR/data/Depth_Color`，不是只解压最后一个 `HAR.zip`。不得使用RGB模态。

当前划分按被试隔离，12人训练、3人验证、3人测试；源目录检查分别为1,995、466、470片段，三组覆盖40类。官方来源为公开带标签数据，测试源数据可从公开资料获得，本赛事结合评委代码审查，不能宣称严格独立盲测。正式数据版本须由组织方在转换与完整性检查通过后统一固定。

将自己的已解压深度目录替换到 `--source` 后运行：

```bash
/opt/uestc-classification/runtime/bin/python \
  /opt/uestc-classification/backend/prepare_cuhkx_depth.py \
  --source /root/cuhkx-extracted/HAR/data/Depth_Color \
  --mapping /opt/uestc-classification/depth-class-mapping.csv \
  --split /opt/uestc-classification/depth-subject-split.json \
  --output /root/uestc-depth-v1 --progress
```

输入为官方伪彩色深度图，按帧序号排序、均匀取16帧、Pillow亮度转换及双线性缩放至112×112，输出float32 `[16,1,112,112]` NPY，范围[0,1]。这不是物理深度距离。目录同时支持官方带时间戳与不带时间戳的深度PNG文件名。

## 重建内置数据镜像

本届转换归档SHA256为 `3a022b03a06078e2695cc5fcbf39acfc47691fd1c03d0034d97bba04d4281521`。由组织方将有权使用的归档保管在构建机外部，再按用途安装，安装器核对整个归档、所选清单和每个NPY摘要，并只提取指定集合。

```bash
# 选手版本：只执行train和validation，不执行test。
for split in train validation; do
  /opt/uestc-classification/runtime/bin/python \
    /opt/uestc-classification/backend/install_classification_image_data.py \
    --archive /path/to/uestc-depth-prepared-v1.tar.gz --split "$split"
done

# 组织方版本：在另一个干净构建状态下只安装test。
/opt/uestc-classification/runtime/bin/python \
  /opt/uestc-classification/backend/install_classification_image_data.py \
  --archive /path/to/uestc-depth-prepared-v1.tar.gz --split test
```

安装器默认将训练/验证放入 `/opt/uestc-classification/training-data`，测试放入 `/opt/uestc-classification/datasets/wujie-depth-v1`。制作选手版时必须删除任何先前安装的测试目录、构建归档、私有验收文件、下载认证缓存和组织方共享盘引用，再检查数据集合与用途。根据用途把对应README设置为镜像入口说明。归档与原始数据不进入Git仓库，镜像通过AutoDL原生 `image/save` 保存。

## 训练与打包

```bash
/opt/uestc-classification/runtime/bin/python \
  /opt/uestc-classification/starter/train.py \
  --training-data /opt/uestc-classification/training-data/train \
  --validation-data /opt/uestc-classification/training-data/validation \
  --epochs 10 --batch-size 8 --output /root/model.safetensors

/opt/uestc-classification/runtime/bin/python \
  /opt/uestc-classification/backend/build_classification_package.py \
  --weights /root/model.safetensors --output /root/submission.zip
```

上述命令适用于内置数据的选手镜像；薄镜像自行转换后改用实际输出路径。训练示例会逐轮显示耗时、损失和验证集准确率，有GPU时使用CUDA。初始示例不附带训练权重。正式提交的 `Model(config, device).predict(sample_path)` 返回40个有限数值，索引顺序与镜像内类别映射一致；不得在预测代码中读取测试标签或自报比赛分数。

## 组织方测评

组织方内置数据镜像默认发现 `/opt/uestc-classification/datasets/wujie-depth-v1`。平台令牌和实际保存的镜像编号在运行实例外部配置，参照 `autodl-classification.env.example`；薄镜像另需覆盖数据目录。测评端读取 `/etc/uestc-classification.env`，必须root所有且权限0600：

```bash
bash /opt/uestc-classification/autodl-classification-start.sh
```

镜像封存校验与GPU、真实数据验收分别进行。镜像编号和最终验收状态记录在发布时生成的镜像记录中，不能以合成输入的100%准确率作为赛事成绩。
