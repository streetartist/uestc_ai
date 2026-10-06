# 无界杯：组织方测评镜像（内置测试数据）

仅供组织方使用，不向参赛队伍提供镜像或实例SSH。镜像内置固定版本的470段测试数据和40类标签，路径为 `/opt/uestc-classification/datasets/wujie-depth-v1`，清单SHA256为 `32b5e9e0f09b578757dcbc3ba763020d558bfdbce7fbaad6151d936a6a10b303`。

解释器：`/opt/uestc-classification/runtime/bin/python`。Python3.12.3、PyTorch2.8.0+cu128、torchvision0.23.0+cu128、NumPy2.3.2、Pillow10.4.0、safetensors0.5.3。镜像包含可信指标计算和隔离的选手推理进程。测试数据root所有，目录0700、文件0600；选手推理进程使用独立非root身份、禁止联网，并受运行时间、内存和CPU限制。

## 接入测评工作端

在部署实例外部生成 `/etc/uestc-classification.env`，权限0600且root所有。参考镜像内 `autodl-classification.env.example`，配置生产网站地址、组织方工作端令牌和本镜像的真实UUID。密钥不能保存进镜像。

```bash
bash /opt/uestc-classification/autodl-classification-start.sh
```

默认自动发现内置测试目录和单卡GPU；可用环境变量覆盖。网站题目运行方式应为 `autodl-native`，镜像UUID与实例相符，场景数据名为 `wujie-depth-v1`，清单摘要与上文一致。

保存前已在当前RTX4080SUPER32GB实例上完成470个真实样本的工作端执行验收，验证冻结提交包下载、摘要核对、隔离推理与可信指标计算。本地验收服务通过不代表生产网站已经连接工作端，保存镜像也不等于新实例恢复验收。

数据来自[CUHK-X官方仓库](https://huggingface.co/datasets/Kevin-Pal/CUHK-X_Small_Model_Track)。组织方已确认有覆盖参赛队伍的分发授权；选手训练与组织方测试分别封装。本届采用公开带标签数据重新划分并结合代码审查，不宣称严格独立盲测。不得给选手开放组织方共享盘，root权限无法隔离同一账号的共享存储。
