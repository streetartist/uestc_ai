# A100计分参考值标定

2026年10月19日。使用已冻结的16帧全图R(2+1)D-34权重，按正式分类runner完成四次完整测评。

| 参考值 | 实测值 | 来源 |
|---|---:|---|
| B | **70.00%** | 470段组织方测试集，正确329段 |
| G | **15个百分点** | `min(15, 100−B)` |
| T | **96.42ms/片段** | 3次完整公开验证平均延迟的中位数 |

## 运行结果

| 运行 | 片段数 | 准确率 | Macro F1 | 平均推理延迟 | judge总耗时 |
|---|---:|---:|---:|---:|---:|
| test | 470 | 70.00% | 57.60% | 94.52ms | 77.98s |
| validation-1 | 466 | 63.95% | 57.54% | 96.54ms | 76.35s |
| validation-2 | 466 | 63.95% | 57.54% | 96.42ms | 76.03s |
| validation-3 | 466 | 63.95% | 57.54% | 95.87ms | 75.54s |

三次公开验证的466条逐样本预测完全一致，准确率均与导出权重的完整FP32验证结果一致。470段测试数据覆盖全部40类动作。计时包括选手进程中的图像读取、预处理、分类、CUDA同步与进程通信；首个预热片段计入准确率，排除在平均延迟外。

## 固定版本

- GPU：NVIDIA A100-SXM4-40GB，40960MiB，驱动580.126.20。
- 资源：4个CPU线程，6144MiB内存限制，每次1800秒。
- 权重SHA256：`a4e19dbb6d6cef9107d6cd69e80ea15c8593c36e1fa4544ecef033478ae024c5`。
- Python与依赖、runner各文件SHA、数据清单SHA和四轮结果见[标定JSON](cuhk-a100-score-reference.json)。
- 推理代码及配置在四轮中相同；权重由组织方本地预置，HTTPS权重传输另行验收。
- 私有预测、标签、输入片段和混淆矩阵保存在组织方目录；PR保留汇总与校验记录。

## 配置

[发布配置](cuhk-v2-release-config.template.json)的 `performance_scoring.reference` 已填写全部未舍入值。总成绩为客观测评50分、报告10分、答辩40分。

正式测评采用上述A100配置。更换GPU型号或影响推理耗时的环境时，重新标定T并记录计分版本。

## 复现命令

```bash
python backend/calibrate_classification_baseline.py \
  --baseline "$FROZEN_BASELINE" --validation "$PUBLIC_VALIDATION" \
  --test-archive "$PRIVATE_TEST_ZIP" --output "$NEW_OUTPUT" \
  --gpu "$A100_GPU_UUID" \
  --weights-sha256 a4e19dbb6d6cef9107d6cd69e80ea15c8593c36e1fa4544ecef033478ae024c5 \
  --test-archive-sha256 a2b0d01d0b896b2f8e0c499a03cce2ba2f8a7dcbe236bde93b3d1c2370ae5bf6 \
  --test-manifest-sha256 e882068de745d7faa6b411e91d230e41e1a84ebeb788d2d632a243a9c459e330
```

在组织方Linux root环境和已分配的空闲A100上执行。临时解包与验证数据位于本地临时目录，运行结束自动清理；输出目录保留四轮测评记录。
