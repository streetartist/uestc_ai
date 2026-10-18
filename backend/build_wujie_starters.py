"""Build participant-only packages; never include hidden scenes or credentials."""
from pathlib import Path
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/evaluation_adapters"


def write_source(archive, source, name):
    """Keep participant text identical on Windows and Linux."""
    archive.writestr(name, Path(source).read_text(encoding="utf-8"))


def build(output=None, kind=None):
    output = output or ROOT / "public/downloads"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    if kind in (None, "classification"):
        classification = output / "wujie-classification-starter.zip"
        with zipfile.ZipFile(classification, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as archive:
            write_source(archive, ROOT / "backend/build_classification_package.py", "build_classification_package.py")
            write_source(archive, ROOT / "backend/competitions/wujie-cup-2026/technical-route.md", "technical-route.md")
            for name in ("config.json", "inference.py", "network.py", "train.py", "predict.py"):
                write_source(archive, SOURCE / "classification_example" / name, name)
            archive.writestr("README.md", (ROOT / "backend/competitions/wujie-cup-2026/classification.md").read_text(encoding="utf-8")
                + "\n\n" + (ROOT / "deploy/linux/autodl-depth-participant.README.md").read_text(encoding="utf-8"))
        results.append(classification)
    from evaluation_adapters.wujie_scenes import minecraft_scenes, libero_scored_scenes
    entries = {"world": SOURCE / "minecraft/example_agent.py", "arm": SOURCE / "libero/example_agent.py"}
    if kind is not None:
        entries = {name: entry for name, entry in entries.items() if name == kind}
    for kind, entry in entries.items():
        with zipfile.ZipFile(output / f"wujie-{kind}-starter.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            write_source(archive, SOURCE / "libero/starter/REPORT.md" if kind == "arm" else ROOT / "backend/competitions/wujie-cup-2026/technical-route.md", "technical-route.md")
            write_source(archive, entry, "agent.py")
            write_source(archive, SOURCE / "starter_api.py", "model_api.py")
            write_source(archive, SOURCE / "practice.py", "practice.py")
            write_source(archive, SOURCE / "practice_review.py", "practice_review.py")
            if kind == "world":
                write_source(archive, SOURCE / "minecraft/actions.py", "minecraft_actions.py")
                write_source(archive, SOURCE / "minecraft/action_items.json", "action_items.json")
            for module in ("__init__.py", "minecraft_agent.py", "minecraft_protocol.py", "minecraft_metrics.py",
                           "minecraft_runner.py", "libero_runner.py", "wujie_scenes.py"):
                write_source(archive, SOURCE / module, f"evaluation_adapters/{module}")
            write_source(archive, SOURCE / "libero/scoring.py", "evaluation_adapters/libero/scoring.py")
            if kind == "arm":
                write_source(archive, SOURCE / "libero/robot_skills.py", "robot_skills.py")
                for name in ("pack.py", "run.ps1", "run.sh", "START.md"):
                    if name == "run.sh":
                        archive.writestr(name, (SOURCE / "libero/starter" / name).read_text(encoding="utf-8").replace("\r\n", "\n"))
                    else:
                        write_source(archive, SOURCE / "libero/starter" / name, name)
                archive.writestr("submission-files.json", json.dumps(
                    ["agent.py", "config.json", "model_api.py", "robot_skills.py"], indent=2))
                write_source(archive, SOURCE / "libero_scene_pack.py", "evaluation_adapters/libero_scene_pack.py")
                write_source(archive, SOURCE / "libero/replay.py", "evaluation_adapters/libero/replay.py")
                dockerfile = (SOURCE / "libero/Dockerfile.controller").read_text().replace("COPY backend/evaluation_adapters", "COPY evaluation_adapters")
                dockerfile = dockerfile.replace('CMD ["python", "-m", "evaluation_adapters.libero_runner"]', 'COPY practice.py /runner/practice.py\nWORKDIR /workspace\nENTRYPOINT ["python", "/workspace/practice.py"]')
                archive.writestr("Dockerfile.environment", dockerfile)
                for name in ("policy_features.py", "nearest_neighbor_agent.py", "train_policy.py"):
                    write_source(archive, SOURCE / "libero" / name, name)
                archive.writestr("BASELINE.md", "开发包包含接口示例agent.py和纯CPU最近邻模仿学习基线：\n\n1. 按LIBERO官方数据说明取得三个已公布任务的公开演示HDF5，可遍历公开初态、收集轨迹并训练或调试策略。最终评测采用独立场景。\n2. 训练侧安装h5py，运行 `python train_policy.py --demo task_demo.hdf5 --output task-policy.npz`。每任务单独训练，默认最多2048样本。\n3. 将nearest_neighbor_agent.py改名为agent.py；config.json的policy_files将三个task_name分别映射到对应NPZ。\n4. 将agent.py、config.json、policy_features.py和三个NPZ打包，运行practice.py，再按20MB限制上传。\n\n可改为BC网络、策略模型或自定义harness，也可复用固定脚本技能。verification、记忆和规划均使用题面规定的画面、机器人状态与执行历史；物体真值、接触状态和原生成功标志仅供评测器使用。训练与测试动作均为原生七维动作，图像上下方向须一致。\n")
                archive.writestr("INSTALL.md", "Python3.11虚拟环境中安装：\n\n```bash\npip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu\npip install robosuite==1.4.0 mujoco==2.3.7 numpy==1.26.4 Pillow==11.3.0 bddl==1.0.1 future==1.0.0 matplotlib==3.8.4 cloudpickle==3.1.1 gym==0.25.2 easydict==1.13 pyyaml==6.0.2 opencv-python-headless==4.10.0.84 termcolor==2.4.0 requests==2.32.5\ngit clone https://github.com/Lifelong-Robot-Learning/LIBERO.git\ncd LIBERO\ngit checkout 8f1084e3132a39270c3a13ebe37270a43ece2a01\npip install --no-deps -e .\ncd ..\n```\n\n保留完整克隆目录中的assets/bddl_files/init_files。首次导入按提示建立LIBERO路径配置，演示数据并非运行环境必须项。Linux软件渲染与视频录制需安装libosmesa6、ffmpeg，并设置MUJOCO_GL=osmesa、PYOPENGL_PLATFORM=osmesa。也可直接在解压目录构建Dockerfile.environment，它已包含资源和路径配置。\n")
            config = {"model": "填写 API 平台 → 本队本题额度中允许的模型名称"}
            if kind == "arm":
                config.update(max_output_tokens=4096, api_timeout=135, thinking=False)
            archive.writestr("config.json", json.dumps(config, ensure_ascii=False, indent=2))
            config_text = """# config.json 与模型接入

在网站顶部进入「API 平台」(`/api-platform`)，找到本队、当前题目的 API 额度，复制其中允许使用的模型名称，填写 `model`。必须使用模型名称，不是渠道名称或上游内部编号。看不到本题额度时，先确认队伍已报名，并联系组织方开通；不要猜测模型名。

`config.json` 由平台原样传入选手的 `Agent.configure(config)`。`model` 供你自己的策略读取；内置移动/空动作示例不调用模型，单独修改该字段不会自动启用大模型。使用模型时随包提交 `model_api.py`，调用 `ModelClient(model=config["model"]).chat(...)`。开发时的 API Key 在「API 平台」创建，通过环境变量 `UESTC_MODEL_API_KEY` 设置；测评时短期凭据由平台注入，均不要写进 ZIP。

旧版示例中的 `memory: "same-test-only"` 已从默认配置中移除。它只是给选手 harness 的可选元信息，不是平台支持的枚举、内存额度开关或自动记忆功能；平台不读取或校验它。若你自己的代码读取该字段，其可用取值和行为由你的代码定义。保留旧字段不影响提交。

实际存活范围：同一次测试的 Agent 进程状态和 `/tmp` 可在三个场景之间保留，场景开始时调用 `reset(goal)`，由选手自行清理或更新记忆；不同场景的环境背包不共享。测试结束后容器销毁，不能跨测试保存文件或记忆。程序 RAM 上限由题目的运行配置决定，不能通过 config.json 更改。
"""
            if kind == "arm":
                config_text = "# 模型配置\n\n" + "官网 [API 平台](/api-platform) 提供本队 C 题的模型名称与 Key。填写 config.json 的 model，并设置 UESTC_MODEL_API_KEY。默认 agent.py 会调用模型；max_output_tokens、thinking 和 api_timeout 控制单次请求。测评时平台自动注入凭据。\n\n每个场景开始调用 reset(goal)，默认示例清空该场景的记忆。开发和提交操作见 README.md。\n"
            archive.writestr("CONFIG.md", config_text)
            if kind == "arm":
                intro = (SOURCE / "libero/starter/START.md").read_text(encoding="utf-8")
                archive.writestr("README.md", intro)
                archive.writestr("TASK.md", (ROOT / "backend/competitions/wujie-cup-2026/arm.md").read_text(encoding="utf-8") + "\n\n" + (ROOT / "backend/competitions/wujie-cup-2026/libero-rules.md").read_text(encoding="utf-8"))
            else:
                archive.writestr("README.md", (ROOT / "backend/competitions/wujie-cup-2026/world.md").read_text(encoding="utf-8") + "\n\n## 开发包使用\n\n模型接入见 CONFIG.md。将 agent.py、config.json 和依赖模块打包为 agent.zip；practice.py 和练习配置留在本地。开发时设置 UESTC_MODEL_API_KEY，测评凭据自动注入。\n")
            scenes = minecraft_scenes() if kind == "world" else libero_scored_scenes()
            archive.writestr("practice-scenes.json", json.dumps(scenes, ensure_ascii=False))
    return [*results, *(output / f"wujie-{kind}-starter.zip" for kind in entries)]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("classification", "world", "arm"))
    args = parser.parse_args()
    for path in build(kind=args.kind): print(path)
