"""Build participant-only packages; never include hidden scenes or credentials."""
from pathlib import Path
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/evaluation_adapters"


def build(output=None):
    output = output or ROOT / "public/downloads"
    output.mkdir(parents=True, exist_ok=True)
    classification = output / "wujie-classification-starter.zip"
    with zipfile.ZipFile(classification, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
        archive.write(ROOT / "backend/competitions/wujie-cup-2026/technical-route.md", "technical-route.md")
        for name in ("config.json", "inference.py", "network.py", "train.py", "predict.py"):
            archive.write(SOURCE / "classification_example" / name, name)
        archive.writestr("README.md", (ROOT / "backend/competitions/wujie-cup-2026/classification.md").read_text(encoding="utf-8")
            + "\n\n" + (ROOT / "deploy/linux/autodl-depth-participant.README.md").read_text(encoding="utf-8"))
    from evaluation_adapters.wujie_scenes import minecraft_scenes, libero_scenes
    entries = {"world": SOURCE / "minecraft/example_agent.py", "arm": SOURCE / "libero/example_agent.py"}
    for kind, entry in entries.items():
        with zipfile.ZipFile(output / f"wujie-{kind}-starter.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(ROOT / "backend/competitions/wujie-cup-2026/technical-route.md", "technical-route.md")
            archive.write(entry, "agent.py")
            archive.write(SOURCE / "starter_api.py", "model_api.py")
            archive.write(SOURCE / "practice.py", "practice.py")
            if kind == "world":
                archive.write(SOURCE / "minecraft/actions.py", "minecraft_actions.py")
                archive.write(SOURCE / "minecraft/action_items.json", "action_items.json")
            for module in ("__init__.py", "minecraft_agent.py", "minecraft_protocol.py", "minecraft_metrics.py",
                           "minecraft_runner.py", "libero_runner.py", "wujie_scenes.py"):
                archive.write(SOURCE / module, f"evaluation_adapters/{module}")
            if kind == "arm":
                dockerfile = (SOURCE / "libero/Dockerfile.controller").read_text().replace("COPY backend/evaluation_adapters", "COPY evaluation_adapters")
                dockerfile = dockerfile.replace('CMD ["python", "-m", "evaluation_adapters.libero_runner"]', 'COPY practice.py /runner/practice.py\nWORKDIR /workspace\nENTRYPOINT ["python", "/runner/practice.py"]')
                archive.writestr("Dockerfile.environment", dockerfile)
                for name in ("policy_features.py", "nearest_neighbor_agent.py", "train_policy.py"):
                    archive.write(SOURCE / "libero" / name, name)
                archive.writestr("BASELINE.md", "接口示例agent.py可立即练习，不代表任务求解。另提供纯CPU最近邻模仿学习基线：\n\n1. 按LIBERO官方数据说明取得三个已公布任务的公开演示HDF5，不使用测试初始状态生成训练数据。\n2. 训练侧安装h5py，运行 `python train_policy.py --demo task_demo.hdf5 --output task-policy.npz`。每任务单独训练，默认最多2048样本。\n3. 将nearest_neighbor_agent.py改名为agent.py；config.json的policy_files将三个task_name分别映射到对应NPZ。\n4. 将agent.py、config.json、policy_features.py和三个NPZ打包，运行practice.py，再按20MB限制上传。\n\n该基线仅是可优化的轻量策略入口，未提供预训练权重和成功率承诺。可改为BC网络、策略模型或自定义harness。训练与测试动作均为原生七维动作，图像上下方向须一致。\n")
                archive.writestr("INSTALL.md", "Python3.11虚拟环境中安装：\n\n```bash\npip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu\npip install robosuite==1.4.0 mujoco==2.3.7 numpy==1.26.4 Pillow==11.3.0 bddl==1.0.1 future==1.0.0 matplotlib==3.8.4 cloudpickle==3.1.1 gym==0.25.2 easydict==1.13 pyyaml==6.0.2 opencv-python-headless==4.10.0.84 termcolor==2.4.0 requests==2.32.5\ngit clone https://github.com/Lifelong-Robot-Learning/LIBERO.git\ncd LIBERO\ngit checkout 8f1084e3132a39270c3a13ebe37270a43ece2a01\npip install --no-deps -e .\ncd ..\n```\n\n保留完整克隆目录中的assets/bddl_files/init_files。首次导入按提示建立LIBERO路径配置，演示数据并非运行环境必须项。Linux软件渲染需安装libosmesa6并设置MUJOCO_GL=osmesa、PYOPENGL_PLATFORM=osmesa。也可直接在解压目录构建Dockerfile.environment，它已包含资源和路径配置。\n")
            archive.writestr("config.json", json.dumps({"model": "填写题目资源面板的允许模型名称", "memory": "same-test-only"}, ensure_ascii=False))
            archive.writestr("README.md", (ROOT / f"backend/competitions/wujie-cup-2026/{'world' if kind == 'world' else 'arm'}.md").read_text(encoding="utf-8") + "\n\n## 开发包使用\n\n将agent.py和config.json打包为agent.zip；practice.py和练习配置留在本地。LIBERO可在解压目录 `docker build -f Dockerfile.environment -t wujie-libero-practice .`，然后 `docker run --rm -v \"$PWD:/workspace\" wujie-libero-practice --package agent.zip --scenes practice-scenes.json --output practice-output`。需要模型时，从model_api导入ModelClient，调用chat(text, images=[画面base64])；开发时配置UESTC_MODEL_API_KEY，测评凭据自动注入。429或其他错误不自动重试，请在harness中处理。练习文件不改变私有测试场景。\n")
            scenes = minecraft_scenes() if kind == "world" else libero_scenes()
            archive.writestr("practice-scenes.json", json.dumps(scenes, ensure_ascii=False))
    return [classification, *(output / f"wujie-{kind}-starter.zip" for kind in entries)]


if __name__ == "__main__":
    for path in build(): print(path)
