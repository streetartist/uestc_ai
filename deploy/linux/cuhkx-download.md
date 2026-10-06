# CUHK-X 数据准备与无卡工作流

下载、安装依赖和镜像构建使用控制台无卡模式；GPU 仅用于训练与推理验收，不同时开启临时构建机和数据实例。AutoDL Pro 官方 API 当前只支持 `payload: gpu` 开机，不能通过 API 冒充无卡模式。

官方数据仓库为 `Kevin-Pal/CUHK-X_Small_Model_Track`，读取账号须先在浏览器获准访问。2026-10-06 已验证的真实文件路径与数据卡示例不同：

- 类别映射：`Small-Model-Track/class_mapping.csv`，40 类。
- 带标签训练压缩包：`Small-Model-Track/Training/data/HAR.z01` 至 `HAR.z08`，以及 `HAR.zip`，共 44,622,809,265 字节。
- 原挑战赛匿名测试没有公开标签，本届采用公开带标签数据按被试重新划分，并结合评委代码审查。此方案不宣称严格独立盲测。

本次用户下载完成后已通过最新 API 列表定位实例，并重新读取其 SSH 信息；数据检查结果见文末。下载和构建优先在控制台使用无卡模式（建议至少100GB磁盘）。用户已明确要求继续使用当前有GPU的实例，因此本次沿用同一实例进行构建与GPU验收，不另开机器。镜像构建的隔离权限修复已完成Linux/GPU验收。

在控制台无卡开机后运行：

```bash
python -m venv /root/cuhkx-download-env
/root/cuhkx-download-env/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -U huggingface-hub
source /etc/network_turbo
read -rsp 'HF Read Token: ' HF_TOKEN
echo
export HF_TOKEN
export HF_HUB_DOWNLOAD_TIMEOUT=120 HF_HUB_ETAG_TIMEOUT=60
mkdir -p /root/autodl-tmp/cuhkx-source
HF_XET_HIGH_PERFORMANCE=1 /root/cuhkx-download-env/bin/python - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download('Kevin-Pal/CUHK-X_Small_Model_Track', repo_type='dataset',
    allow_patterns=['Small-Model-Track/Training/data/HAR.*', 'Small-Model-Track/class_mapping.csv'],
    local_dir='/root/autodl-tmp/cuhkx-source', max_workers=8)
PY
```

可重复运行续传。分卷 ZIP 包含全部模态，下载后只提取 `Depth_Color`；压缩包不得直接作为本届私有标签数据或成绩。先核对文件大小与官方 LFS SHA256，再合并或使用支持分卷的解压工具，转换前核对磁盘余量。

`prepare_cuhkx_depth.py` 只处理官方 `Depth_Color`：16 帧均匀抽样、112×112 双线性缩放、Pillow L 亮度转换、float32 [0,1]、`[T,1,H,W]`。输入为伪彩色深度，不宣称恢复物理距离。工具记录库版本、源文件摘要、划分、类别覆盖，并拒绝跨集合被试重叠或相同转换片段。

跨被试划分位于 `backend/competitions/wujie-cup-2026/depth-subject-split.json`：12人训练、3人验证、3人测试。原14/2/2候选在真实数据上缺少类别；分卷目录中只有user6+user7这一个二人组合覆盖全部40类，无法用两个互不相交的二人集合同时覆盖验证集和测试集。调整后的目录检查计数为训练1,995、验证466、测试470片段，三组均覆盖40类；正式冻结仍须解压、转换与跨集重复检查通过，不以目录检查启用测评。选手训练与组织方测评须采用相同转换和类别顺序；转换后的默认 `depth_scale=1`。

官网：https://huggingface.co/datasets/Kevin-Pal/CUHK-X_Small_Model_Track

无卡 API 限制：https://www.autodl.com/docs/instance_pro_api/

## 用户指定的国内镜像与并行下载

用户指定 `https://hf-mirror.com` 为下载入口。可使用该站 hfd/aria2 工具，`-x 8 -j 4` 表示每文件最多8连接、同时4文件。当前 hfd 参数校验上限10，不能提供 `-x 16`。该仓库 gated，需已获准账号的 HF 用户名与 Read Token；用户名不是邮箱。凭据通过终端隐藏输入，目录使用 umask077；hfd 的 `.hfd` 元数据会保存认证信息，不可收入镜像或公开包。国内镜像直连时清除当前 shell 的 network_turbo 代理变量，避免经过国外代理。

```bash
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
umask 077
apt-get update
apt-get install -y aria2 jq curl
curl -fL https://hf-mirror.com/hfd/hfd.sh -o /root/hfd.sh
export HF_ENDPOINT=https://hf-mirror.com
read -rp 'HF用户名（不是邮箱）: ' HF_USERNAME
read -rsp 'HF Read Token: ' HF_TOKEN
echo
mkdir -p /root/autodl-tmp/cuhkx-source
bash /root/hfd.sh Kevin-Pal/CUHK-X_Small_Model_Track --dataset \
  --hf_username "$HF_USERNAME" --hf_token "$HF_TOKEN" \
  --tool aria2c -x 8 -j 4 \
  --include 'Small-Model-Track/Training/data/HAR.*' 'Small-Model-Track/class_mapping.csv' \
  --local-dir /root/autodl-tmp/cuhkx-source
```

工具参数来源：https://gist.github.com/padeoe/697678ab8e528b85a2a7bddafea1fa4f

## 2026-10-06 下载完成后的构建检查

通过官方 Pro API 定位用户当前实例后，已在 `/root/cuhkx-source` 验证9个训练分卷：文件大小与官方记录一致，全部 LFS SHA256 验证通过。分卷总计44,622,809,265字节。不要因示例下载路径是 `/root/autodl-tmp/cuhkx-source` 而重复下载。

分卷中央目录中，`HAR/data/Depth_Color` 有85,879张图像、2,931个片段，原图解压量14,188,792,524字节。Python自带 `zipfile` 不支持这套多盘 ZIP；使用支持分卷的解压工具，不把 `HAR.zip` 单卷视为完整数据。上述数量来自目录检查，尚未代替解压、类别覆盖和图像可读性检查。

当前实例基础环境为Python3.12.3、PyTorch2.8.0+cu128，不能执行旧脚本中固定PyTorch2.0版本的断言。`autodl-depth-competition-install.sh` 接受当前基础PyTorch版本，记录运行环境并做CPU基线检查；GPU验收另行执行，不能以CPU检查宣称测评上线。

原始下载已从系统盘备份到组织方文件存储，并核对全部摘要；原始44.6GB多模态压缩包不收入比赛镜像。用户于2026-10-06明确确认已有覆盖参赛队伍的数据分发授权，随后要求镜像内置数据：选手镜像只放训练/验证预处理数据，测试数据和标签只放组织方镜像。HF认证缓存、平台工作端令牌和API密钥均不收入镜像。`/root/autodl-fs`是独立共享文件存储，不能假定其它账号/实例挂载了它，内置数据镜像也不依赖它。

本次转换已完成全部2,931片段：12/3/3被试划分、各组40类覆盖、跨集重复检查均通过。原版分卷中973个文件采用 `Depth_00000205_Color.png` 这类无时间戳名称，转换器同时支持两种命名格式并按数字帧号排序。

当前RTX4080SUPER32GB上完成六项GPU隔离/异常验收；真实数据上训练1995片段一轮耗时8.9515秒，验证集准确率11.8026%。全部470测试片段的可信测评耗时10.9124秒，准确率10.2128%、Macro F1为0.4633%、平均延迟6.3579毫秒、显存采样峰值390MiB。这是弱基线的一轮接口验收，不能作为赛事提交或泛化到其它模型的训练耗时。

转换库版本为Pillow10.4.0、NumPy2.3.2。清单摘要：训练 `b1da519efcaffea98e2b153f39a3195bfa902617826135e866bbca289380ed28`，验证 `6e5c15ff25b726d4925c4e58820204746db83551e10156a303571c0d84b6baa5`，测试 `32b5e9e0f09b578757dcbc3ba763020d558bfdbce7fbaad6151d936a6a10b303`。

镜像打包只包含显式列出的代码和公开类别/被试配置，不依赖outputs中的临时文件。CSV保留官方原始字节，包括换行，以保证映射摘要一致；代码/脚本规范为LF。打包工具接受1980年前的源文件时间，避免从镜像解包的示例生成提交ZIP时报错。

AutoDL文件存储在同一账号的实例之间共享，root-only目录权限不能充当各队root之间的隔离边界。共享盘可以保存已授权的公开源数据；组织方正式测评实例上的测试映射、工作端令牌仍应独立保管，不向队伍开放组织方SSH。

不带数据的v3薄镜像已保存，UUID为 `image-dd6e1b4b40`，增量大小277,288,960字节。内置数据版本的发布记录见 `autodl-depth-images.json`。组织方版在保存前通过工作端完整执行路径的470个真实样本验收：下载冻结提交包、SHA校验、GPU隔离推理和指标计算均通过。该验收使用本地资产服务，尚未连接生产网站工作端；不将镜像保存等同于新实例恢复验收。后续下载/构建仍优先控制台无卡模式，不通过GPU-only API自行创建额外构建实例。
