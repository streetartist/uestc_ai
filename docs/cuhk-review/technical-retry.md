# 观行知意 v2 候选审查意见

## 结论

当前候选不能发布。有三处会直接中断学生流程：

- 推理入口导入失败
- training/ 下脚本导入失败
- 完整训练缺少 timm

另外，官方基线到底用不用 YOLO 裁剪，交付包和队列说法不一致。这会直接影响参考值该取哪一次实测。计分公式本身基本可用，问题在参考值的来源和延迟口径。

## 现场处置

把权重复制进任务根目录、核验 SHA 后恢复原队列，这样处理合理，也没有放宽路径限制。补两点：

- 基线任务的 `metadata.json` 中，`pretrained_sha256` 应等于 `download_pretrained.py` 里的 `ade133f1…e0e1`。要逐字核对，再拿它填参考值。
- BAAI 现在只有 GPU 0 和 7 空闲。三个 pending 任务里，只有与发布配置一致的那一个是发布必需的，见第 5 条。另外两个任务的结果不能替代基线。

## 主要问题与最小修复

**1. [阻断] 推理入口依赖 training/build_cache.py**

位置：`DELIVERY/inference.py` 第 6 行 `from build_cache import crop_window`。

- `build_cache.py` 在 `training/` 下。从 DELIVERY 根目录运行 `predict.py` 时，`import inference` 就会报 ModuleNotFoundError。
- `prepare_classification_submission.py` 只打包 `source.glob('*.py')`，也就是根目录文件，所以 ZIP 里同样没有 build_cache。
- 本地检查不执行模型（`model_executed: False`），能通过。要等平台测评才暴露问题，还会消耗一次测试机会。

修复：把 `crop_window` 移到共享模块，两边都从这里导入。

```python
# depth_ir_io.py 末尾追加（原样搬自 build_cache.py）
def crop_window(boxes, w, h):
    b = np.asarray(boxes); x0, y0 = b[:, :2].min(0); x1, y1 = b[:, 2:].max(0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    side = max(max(x1 - x0, y1 - y0) * 1.4, .35 * max(w, h))
    return [max(0, (cx - side/2) / w), max(0, (cy - side/2) / h),
            min(1, (cx + side/2) / w), min(1, (cy + side/2) / h)]
```

```python
# inference.py
from depth_ir_io import sample_frame_lists, read_frame_lists, uniform_indices, crop_window
# training/build_cache.py：删除本地定义，改为
from depth_ir_io import read_directories, frame_paths, uniform_indices, crop_window
```

**2. [阻断] training/ 下脚本导入不到根目录模块**

位置：`train_raw.py` 导入 `raw_model`，`build_cache.py` 导入 `depth_ir_io`。

`python training/train_raw.py` 只会把 `training/` 加进 sys.path，所以这两处导入都会失败。材料里的 README 只给了第一段，我无法核对它写的命令。

修复：README 统一从 DELIVERY 根目录用 `-m` 运行。Python 3 的命名空间包不需要 `__init__.py`。

```bash
.venv/bin/python -m training.prepare_training_manifest --data DATA --output work/manifest.json
.venv/bin/python -m training.build_cache --manifest work/manifest.json --output work/cache
.venv/bin/python -m training.train_raw --cache work/cache --pretrained weights/pretrained.pth --output runs/baseline
```

**3. [阻断] 依赖和文件清单不完整**

- `train_raw.py` 在非 smoke 分支里 `from timm.utils import ModelEmaV3`，但 `requirements.txt` 只有 numpy 和 Pillow。smoke 在导入 timm 之前就返回了，所以学生会在缓存建完、真正开训时才失败。
  - 修复：照 BAAI 基线环境 `pip freeze` 的结果，把 timm 的确切版本写进 requirements.txt，保证学生环境与基线一致。版本号以实际环境为准，这里不替你猜。
- 材料里没有出现 `ig65m_backbone.py` 和 `evaluation_adapters/classification_runner.py`。前者会被 `check_environment.py`、训练和推理导入，后者会被检查和打包脚本导入。
  - 要确认两者都在交付包内。ig65m_backbone 应位于根目录，这样 glob 才能把它打进 ZIP。它也不能依赖 torch.hub 联网。

**4. [高] 推理配置是手写的，和训练缓存没有绑定**

位置：`config.json` 和 `experiment-config.json`。两者内容完全相同，`weights_file` 都是 `runs/experiment-16/last_ema.pt`，但 `train_raw` 的输出目录由学生自定，不会产生这个路径。

- `build_cache.py` 的 `--include-ir` 默认关闭。漏写这个参数会得到 3 通道纯深度模型，和 `channels: 4` 不符。好在 strict load 会报错，不会悄悄出错。
- `--person-crop` 和 `config.person_crop` 之间没有任何联动。训练时裁剪、推理时不裁剪，不会报错，只是精度会悄悄下降。

修复：

- 由 `train_raw` 根据缓存 manifest 生成推理配置。`predict.py` 本来就按 config 所在目录解析 `weights_file`，可以直接用。
- `prepare_classification_submission.py` 增加 `--config` 参数，指向该文件。
- 删除 `experiment-config.json`。
- 把 IR 改为默认开启，提供 `--no-ir` 关闭。

```python
# train_raw.py，保存 last_ema.pt 之后
shape = tr.meta['shape_per_sample']
cfg = {'num_classes': 40, 'input_version': 2, 'channels': shape[0],
       'depth_mode': tr.meta['depth_mode'], 'person_crop': tr.meta['person_crop'],
       'frames': shape[1], 'size': shape[2],
       'weights_file': 'last_ema.pt', 'weights_format': 'pytorch'}
if tr.meta['person_crop']:
    cfg['detector_file'] = 'yolo11n.pt'  # 与打包进 ZIP 根目录的检测器文件名一致
write(a.output/'config.json', cfg)
```

```python
# build_cache.py
p.add_argument('--no-ir', dest='include_ir', action='store_false')  # 默认包含 IR
```

**5. [高] 官方基线是否裁剪，说法不一致**

- 交付包的 `config.json` 是 `person_crop: false`，也没有 `detector_file`。
- 队列里名为 baseline 的任务是 `compact-depth-ir-yolo-r34-baseline`。
- `train_raw.py` 的 recipe 字符串写的也是 YOLO11n。

发布前要二选一：

- 选择 YOLO 版：交付包要带上检测器权重（低于 19 MiB 的限制）和 `detector_file`，README 要加 `requirements-crop.txt` 的安装步骤，平台测评镜像要确认装有 ultralytics。检查脚本不会检查这些。
- 选择不裁剪版：对应的是 `full-r34-16frames` 这个任务，recipe 文案也要改。

参考值只能来自被选中的那一次运行。另外两个任务是否继续跑都可以，但它们的数字不进文档。

**6. [高] 计分参考值的来源必须是平台实测**

位置：`cuhk-depth-ir-v2-scoring.md` 的参考值表，以及 `performance_scoring.py` 的 `reference`。

不能用以下数字代替：

- `summary.json` 的 `accuracy`：这是验证集上 last EMA 的结果。
- `model_only_latency_ms_median`：这是在训练 GPU 上、fp16、只算模型的延迟。线上推理是 fp32，而且包含读图，选 YOLO 版还要加上 CPU 检测。
- 旧灰度 64.81%。

正确做法：用 `prepare` 打出基线 `inference.zip`，再在正式测评硬件上用同一 runner 跑完整测试集。`baseline_accuracy` 和 `latency_ms` 都取这一次的结果。

表中还要同时登记：

- 包 SHA、权重 SHA、预训练 SHA
- 数据版本、runner 版本、GPU 型号
- `gain_span_pp` 的取值理由

在实测之前，这一栏保持"待测"。

**7. [中] 公式细节**

位置：`performance_result` 中 v2 分支。

- 分段斜率大体连续。以基线 60% 为例：低于基线时每 pp 约 1.17 分，其中准确率项 1 分、效率项约 0.17 分；高于基线时每 pp 得 20/span 分。这个设计可以接受。
- v2 提前 return，没有像其他 preset 那样 `round(…, 6)`，`components` 也没有取整。榜单显示和平分判定会不一致。
- 效率项占 10 分，以基线延迟为满分线。在 100M 参数以内换成更重的模型，可能丢掉大部分这 10 分。这和赛题鼓励研究模型结构有张力。至少要在文档里写明。如果想放宽，可以给延迟设一个容差，例如 2 倍以内满分，但这属于规则改动，需要你们拍板。
- runner 报的是平均延迟（代码文案是"平均延迟"）。首个样本的冷启动会抬高均值，需要确认 runner 是否排除了预热。

最小修复：

```python
components = {k: round(v, 6) for k, v in components.items()}
return {**scoring, "score": round(sum(components.values()), 6), "components": components}
```

**8. [中] 本地检查不跑模型，"40-class config" 检查项名不副实**

位置：`check_classification_submission.py`。

- `checks` 列表里写了 `'40-class config'`，但代码并没有检查 `num_classes`。
- 第 1 条的问题只能靠真实执行才能发现。

修复：

- 增加 `config.get('num_classes') == 40` 的断言。
- 增加可选参数 `--sample`。在隔离的子进程里，从解压后的 ZIP 加载模型，配合本地权重推理一个验证样本，并断言输出 40 个有限值。

```python
if sample and weights:
    code = ("import sys,json,math,torch;r=sys.argv[1];sys.path.insert(0,r);from inference import Model;"
            "c=json.load(open(r+'/config.json'));c.update(weights_path=sys.argv[2],package_dir=r);"
            "s=Model(c,'cuda' if torch.cuda.is_available() else 'cpu').predict(sys.argv[3]);"
            "assert len(s)==40 and all(map(math.isfinite,s))")
    subprocess.run([sys.executable, '-I', '-c', code, str(root), str(Path(weights).resolve()),
                    str(Path(sample).resolve())], check=True, cwd=root)
```

`-I` 会排除 PYTHONPATH 和 DELIVERY 目录，这样能真实复现平台上只有 ZIP 内容的情况。

**9. [中] AutoDL 上的可用性**

- `setup.sh` 会从 download.pytorch.org 下载 cu118 版 torch，`download_pretrained.py` 会从 GitHub Releases 下载权重。在 AutoDL 国内节点上这两处常常很慢甚至失败，而下载时间也在消耗 100 元券。
  - 修复：README 写明先开启 AutoDL 学术加速，或者提供一个同 SHA 的平台镜像地址。脚本已经做了 SHA 校验，换源不影响安全性。
- torch 2.5.1+cu118 不支持 RTX 50 系（sm_120）。`check_environment.py` 会报出含义不清的 CUDA 错误。
  - 修复：README 写明推荐的 GPU 型号。
- 部分镜像里没有 `python3.10` 或 venv 模块。README 要写明可以用 `PYTHON=…` 覆盖。
- 存储估算：缓存为 2461 个样本 × 4×16×128×128 字节，即 1 MiB/个，约 2.4 GiB，加上数据 4.90 GB，磁盘够用。
- 训练耗时：A100 上的耗时不能直接换算成 AutoDL 花费。README 不要写没有实测过的时长或花费。

**10. [低] 赛题文案不够明确**

位置：`classification-depth-ir-v2.md`、quickstart。

需要补上：

- 只允许使用本届发布的 Depth_Color 和 IR。不得使用 CUHK-X 的 RGB、骨架等其他模态或其他划分，也不得使用在 CUHK-X 上训练过的模型。
- "公开通用预训练"的界定，例如 ImageNet、Kinetics、IG65M 这类公开发布的权重。
- 100M 参数指整条推理流程的参数，包含检测器。代码已经是这样统计的。按公开模型规模，R(2+1)D-34 约 6 千万参数，YOLO11n 约 2.6M，余量充足。这一点我没有实测。
- 深度数据是 8 位伪彩色图，加载器会拒绝 16 位数值深度。
- 测试集不公开。
- 计分公式见计分说明。

附带一处：`raw_model.py` 注释里的 "Caffe2-compatible BN momentum .9" 有误导。PyTorch 中 momentum=0.9 表示新 batch 的权重占 90%，和 Caffe2 的含义正好相反。如果这是在复现上游做法，可以保留数值，但注释要改成"沿用上游设置"。另外上游通常还会设 `eps=1e-3`，需要确认 `ig65m_backbone` 里有没有设。

## 未能核实

以下内容不在材料中，或者需要真实运行才能确认，我没有核实：

- README 的完整命令
- ig65m_backbone 和 evaluation_adapters 是否存在
- 平台 runner 怎样注入 `weights_path`，以及延迟的统计口径
- 测评镜像里有哪些依赖
- 发布数据是否带有 subject 或 provenance.json（`prepare_training_manifest` 缺了它会直接报错）
- `compact-depth-ir-20261018/progress.json` 的内容

文中所有准确率和延迟都没有给出具体数值，等平台实测后再填。