# 观行知意 v2 最终复核

按本次完整文件清单和服务器执行记录，上轮的三项"阻断"属于材料不全造成的误判，或者已经修复，予以撤回。配置、检查、预热、文档几项也一并撤回。

PR 层面没有剩余阻断项，可以合并。发布开赛仍取决于下文的部署验收。目前 B/T 还没有合格的实测来源。

## 逐条更正

1. 导入问题：撤回上轮第 1、2 条。根目录和 training/ 各有一份 build_cache.py、depth_ir_io.py、raw_model.py、ig65m_backbone.py。`python training/...` 运行时会把 training/ 加入导入路径，所以能解析导入。根目录的 build_cache.py 会被打包 glob 带进 ZIP。-m 方案不需要。剩下的只是两份副本的一致性，见下节。

2. 依赖：确认修复。requirements.txt 和 training-live 记录的版本一致，smoke 结果的 timm 也是 1.0.27。上轮说"缺 ig65m_backbone 与 evaluation_adapters"是因为材料缺失，撤回。

3. 推理配置：确认修复。channels、frames、size、depth_mode、person_crop 全部取自缓存 manifest。漏写 --include-ir 只会让配置和模型同为 3 通道，不再静默错配。裁剪时会校验检测器 SHA，并写入 detector_file。上轮"IR 默认开启"和"由 train_raw 写 config"两项建议撤回。

4. 本地检查：撤回第 8 条。runner 第 121 行的 extract_package 已经断言 `num_classes == 40`，检查项名副其实。--sample 的 smoke 结果为 `model_executed: true`。上轮说 -I"能真实复现平台"需要更正：它只排除了 PYTHONPATH 和开发包目录，venv 的 site-packages 仍然可见，依赖环境和平台镜像并不等价。你们把它定位为"执行学生自有程序的试运行"，这个表述准确。

5. 基线配置：上轮"说法不一致"的前提不成立，撤回。training-live 里的两个任务都是裁剪对照，缓存分别是 color-ir-crop 和 color-ir-crop32。pretrained_sha256 与 ade133f1…e0e1 逐字一致。inference_pipeline_parameters 为 66,138,780，已经计入检测器。另外，和 README 默认一致的 16 帧无裁剪任务不在这份输出里。如果官方基线选它，这次运行还需要完成并留下 metadata。

6. 参考值口径：撤回"B/T 同取一次测试集结果"。现行规则是：B 取冻结模型在组织方测试集上的准确率；T 取同一 runner 在公开验证集上 3 次平均延迟的中位数。两者都不依赖训练端数字，口径清楚。

7. 计分：撤回预热意见。runner 第 321 行的 `if index:` 排除了首样本计时，evidence.json 也记录了 `warmup_samples: 1`。不舍入、同分按原精度比较的规则保持不变。

8. AutoDL 文档：撤回。README 已写明 GPU 型号、PYTHON= 覆盖、python3.10-venv 和数据盘位置，也没有写未实测的时长或花费。

9. 文案与 BN：撤回。ig65m_backbone 对全部 BatchNorm3d 设了 `eps=1e-3`，注释已改。模态和外部权重规则按现文保留。

## 合并前可选的小修改（不阻断）

- 副本一致性：缓存和推理能否完全一致，取决于这四对重复文件。建议在现有打包测试里加一条，逐字节比较根目录和 training/ 的副本，或者比对 code-manifest.json 中的 SHA，防止以后只改一边。
- 权重与缓存绑定：生成配置时，如果权重同目录有 metadata.json，就比对其中的 cache_manifest_sha256 和 --cache 的 manifest SHA。通道数不符时 strict load 会报错，但帧数或裁剪设置配错不会报错。这一改动大约五行。
- 旧配置文件：experiment-config.json 和 experiment-32-config.json 仍在包内，README 已经不再引用。建议删除，以免学生照抄旧的手写配置。
- 分项显示：metricValue 用的是 `maximumFractionDigits: 2`，分项 9.996 会显示为"10 / 10"，和"显示两位小数"的规则不完全一致。如果要严格两位，加上 `minimumFractionDigits: 2`。

## 发布开赛前的部署验收

以下步骤都不能用本地 API 测试或 CPU 真样本检查替代：

1. 选定官方基线配置，跑完 30 轮训练，核对 metadata 中的 pretrained_sha256、cache_manifest_sha256 和 parameters，然后导出权重。
2. 通过正式 HTTPS 接口执行 upload_classification_weights.py。当前 smoke 中 `https_upload_tested: false`，asset id 是测试夹具。真实上传、续传，以及平台按 weights_asset 拉取权重，都还没有验证过。
3. 组织方把 470 段测试集装进评测环境，核对 manifest_sha256。用基线 ZIP 在正式 GPU 上跑一次完整 runner，取准确率作为 B，并确认能在 1,800 秒内完成。
4. 用同一 ZIP、同一 GPU 对公开验证集跑 3 次，取平均延迟的中位数作为 T。登记以下信息，再写入计分说明和 performance_scoring 的 reference：
   - evidence.json 中的 gpu 和 package_sha256
   - runner 版本
   - 三次的延迟数值
   - G 的取值理由
5. README 提供了裁剪路线，所以要让一个裁剪 ZIP 走一遍正式离线 runner。确认评测镜像装有与 requirements-crop.txt 同版本的 ultralytics，并确认在无网络、沙箱用户下导入时不会尝试联网，也不会写不可写的目录。
6. 发布数据 ZIP 和开发包的大文件链接，并公布 SHA。在一台实际的 AutoDL 节点上从零执行一遍：setup.sh、download_pretrained.py、解压数据、prepare_training_manifest，确认 PyTorch 源和 GitHub 下载都可达。
7. 参考值填定后，用组织方账号发起一次自测。确认前端显示的 B/G/T 和登记值一致，分项和总分与按公式手算的结果一致。

第 1 到第 4 步完成之前，计分说明中的 B/T 保持"待测"。第 5、6 步没完成时，对应的路线不要在开赛公告里宣称可用。