# 无界杯：选手训练镜像（内置数据）

镜像内置本届固定版本的训练集1,995段、验证集466段，两组均覆盖40类。无需再次下载或解压44.6GB原始多模态数据。训练数据位于系统镜像内，创建实例后直接使用。

解释器：`/opt/uestc-classification/runtime/bin/python`。环境为Python3.12.3、PyTorch2.8.0+cu128、torchvision0.23.0+cu128、NumPy2.3.2、Pillow10.4.0、safetensors0.5.3。

## 开始训练

```bash
/opt/uestc-classification/runtime/bin/python \
  /opt/uestc-classification/starter/train.py \
  --training-data /opt/uestc-classification/training-data/train \
  --validation-data /opt/uestc-classification/training-data/validation \
  --epochs 10 --batch-size 8 --output /root/autodl-tmp/model.safetensors

/opt/uestc-classification/runtime/bin/python \
  /opt/uestc-classification/backend/build_classification_package.py \
  --weights /root/autodl-tmp/model.safetensors \
  --output /root/autodl-tmp/submission.zip
```

训练时逐轮显示耗时、损失和验证准确率。示例是轻量基线，选手可替换模型。`submission.zip`包含推理代码、配置和权重；训练代码与README技术路线按题目要求提交，研究PDF可选。

批量预测请下载最新开发包，在包含config.json、inference.py、network.py、predict.py与model.safetensors的目录运行：

```bash
/opt/uestc-classification/runtime/bin/python predict.py \
  --dataset /opt/uestc-classification/training-data/validation \
  --output predictions.csv
```

生成CSV两列 `sample_id,predicted_class`，类别编号0–39。predict.py在最新开发包中提供；已保存的v4镜像不需要重建或重新下载数据。

## 数据与预测接口

数据来自[CUHK-X官方仓库](https://huggingface.co/datasets/Kevin-Pal/CUHK-X_Small_Model_Track)，组织方已确认取得覆盖本届参赛队伍的数据分发授权，仅供本届授权参赛用途。请遵守数据授权并在报告中注明来源。

使用非RGB的伪彩色深度图，按帧序号排序后均匀取16帧，经Pillow亮度转换和112×112缩放，输出float32 `[16,1,112,112]` NPY，范围[0,1]，不代表物理距离。类别索引遵循 `/opt/uestc-classification/depth-class-mapping.csv`；训练、验证与测试被试互不重叠。

`Model(config, device).predict(sample_path)`必须返回40个有限数值。不得使用RGB、读取测试标签或自行填报比赛分数。组织方统一运行推理并生成指标；本届采用官方公开带标签数据重新划分，结合评委代码检查，不宣称严格独立盲测。

本镜像不包含组织方测试数据、测试标签、训练好的权重或平台/API密钥。不要向队伍挂载组织方共享文件存储；同一AutoDL账号的共享盘不能靠root权限隔离各队。
