# 观行知意 · 开发包

包含深度图＋IR数据读取、R(2+1)D-34公开预训练微调、验证、样本预览和错例分析代码。

## 环境安装

Linux / WSL，Python 3.10。沿用实验环境：PyTorch 2.5.1、torchvision 0.20.1、CUDA 11.8。适用于本次使用的 A100、H800 及 AutoDL RTX 4090。

在开发包目录执行：

```bash
bash setup.sh
source .venv/bin/activate
```

安装脚本会创建独立环境、下载依赖并检查 GPU。依赖版本见 requirements.txt；完整实验环境记录见 environment-reference.json。Python路径可用 PYTHON=/path/to/python3.10 bash setup.sh 指定；缺少 ensurepip 时安装 python3.10-venv。AutoDL 上将开发包、data 和 runs 放在数据盘目录（如 /root/autodl-tmp）。

## 数据

将数据 ZIP 放在开发包旁边，解压到 data 目录：

```bash
python -m zipfile -e ../wujie-classification-depth-ir-data.zip data
python training/prepare_training_manifest.py --data data --output runs/raw-manifest.json
```

目录为 data/train 和 data/validation，各自包含 manifest.json、provenance.json 和样本文件夹。训练集 1,995 段，验证集 466 段；每段最多 32 对同步深度／IR帧，分辨率 304×228。

## 预训练权重

```bash
python download_pretrained.py
```

脚本下载并校验 IG65M→Kinetics-400 的 R(2+1)D-34 权重，保存到 weights/pretrained.pth。

## 微调

默认使用 16 帧、128×128、深度＋IR四通道输入。

```bash
python training/build_cache.py --manifest runs/raw-manifest.json --output runs/cache-16 --depth-mode color --include-ir --frames 16 --size 128
python training/train_raw.py --cache runs/cache-16 --pretrained weights/pretrained.pth --output runs/experiment-16 --epochs 30 --batch 16 --workers 2 --seed 42 --lr 0.00005
```

显存不足时降低 --batch。每次实验设置新的 --output 目录。history.jsonl 保存逐轮结果，last_ema.pt 为最后一轮权重，best_ema.pt 为验证集准确率最高一轮的权重。

## 验证与分析

根据训练缓存和选定权重生成 config.json，验证与提交共用这份配置。使用 best_ema.pt 时，把命令中的权重路径替换为该文件。

```bash
python prepare_classification_inference_config.py --cache runs/cache-16 --weights runs/experiment-16/last_ema.pt --output config.json
python predict.py --dataset data/validation --config config.json --output predictions.csv
python analyze_predictions.py --predictions predictions.csv --manifest data/validation/manifest.json --output analysis/experiment-16
python preview_sample.py --dataset data/validation --sample-id 样本ID --output analysis/preview.png
```

analysis/experiment-16/report.html 展示准确率、各类表现和错例。样本ID取自 predictions.csv 或错例表；preview.png 展示同一片段的8组同步深度／IR画面。

## 实验设置

32帧：缓存命令改为 --frames 32，设置新的缓存和实验目录，再用对应缓存与权重生成 config.json。

人物裁剪：安装 pip install -r requirements-crop.txt，缓存命令加入 --person-crop --yolo-weights yolo11n.pt --device cpu。生成配置时加入 --detector yolo11n.pt；脚本读取缓存中的裁剪设置，并核对检测器权重。

缓存、训练与推理使用相同的帧数、分辨率和裁剪设置。

## 官网提交

使用比赛平台账号，填写赛题网址末尾的题目标识。上传脚本在终端提示输入密码。

```bash
python upload_classification_weights.py --weights runs/experiment-16/last_ema.pt --problem PROBLEM_SLUG --email YOUR_EMAIL
python prepare_classification_submission.py --source . --reference weights-reference.json --output inference.zip
python check_classification_submission.py inference.zip --weights runs/experiment-16/last_ema.pt
```

上传脚本生成 weights-reference.json。打包脚本将该记录写入 ZIP 内 config.json 的 weights_asset，保留本地配置。网页附件区上传 inference.zip；分类器权重通过上传脚本单独提交。

可在提交前用一个验证片段试运行实际 ZIP：

```bash
python check_classification_submission.py inference.zip --weights runs/experiment-16/last_ema.pt --sample data/validation/样本目录
```

样本目录取自 validation/manifest.json 的 file 字段。检查会加载 ZIP 中的模型并执行预测，不占官网自测次数。更换实验后，用新缓存和权重重新生成 config.json。人物裁剪所用 yolo11n.pt 已在开发包中，设置 detector_file 后会一并打包。自定义依赖文件使用 --include 子目录/文件 加入。训练代码可通过技术说明中的代码仓库与版本提供。

官网发起自测后，查看指标并选择一条完成结果，再以相同代码ZIP和权重正式提交。验证集预测提交 predictions.csv，列名为 sample_id,predicted_class。技术说明填写作品 README，参数统计填写“推理模型与参数量”。

详细接口和续传命令见 classification-private-weights.md。
