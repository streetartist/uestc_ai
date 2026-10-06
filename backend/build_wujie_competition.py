"""Build organizer-only ZIPs with versioned competition rules."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import secrets
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/competitions/wujie-cup-2026"
SCORING = (SOURCE / "common-rules.md").read_text(encoding="utf-8")


def build_catalogue(images: dict | None = None):
    overview = (SOURCE / "launch.md").read_text(encoding="utf-8")
    competition = {
        "slug": "wujie-cup-2026", "name": "2026“无界杯”UESTC AI挑战赛",
        "summary": "计算机学院主办、经管学院协办，AI社与计算机学院学生会承办。面向全校同学的人工智能挑战赛，设人体行为识别、开放世界探索与机械臂操作三条赛道；奖金总额30,000元，赛事时间待定。",
        "status": "draft", "config": {"overview_md": overview, "leaderboard": {"visible": False, "rank_scope": "track"},
            "proposal": {"author": "黄锐", "modified_at": "2026-09-10", "recipient": "学生科"},
            "prize_pool_yuan": 30000, "reviewing": {"locked_at": None},
            "launch": {"require_ready": True, "rules_version": "wujie-2026-v2"},
            "checkpoints": [{"id": f"checkpoint-{i}", "label": label, "due_at": None}
                            for i, label in enumerate(["首次跑通", "方案迭代", "提交预演"], 1)],
            "scoring_stages": {"performance_percent": 80, "report_percent": 20,
                               "first_stage_percent": 50, "defense_percent": 50}},
    }
    readme = (SOURCE / "technical-route.md").read_text(encoding="utf-8")
    classification = {
        "code": "WJ-DL-001", "slug": "wujie-human-activity-recognition",
        "title": "观行知意：人体日常行为识别挑战赛",
        "summary": "利用CUHK-X的深度图像等非RGB数据识别40类日常动作，兼顾准确率、计算量和推理速度。",
        "status": "draft", "difficulty": 3,
        "statement_md": (SOURCE / "classification.md").read_text(encoding="utf-8"),
        "compute_note": "RTX4080SUPER32GB；训练镜像内置1995段训练和466段验证数据，每队10GPU卡时、参考运行费用上限16.8元。独立测评镜像内置470段测试；每队3次自测、每次1800秒，按赛程开放。",
        "source_url": "https://github.com/openaiotlab/CUHK-X",
        "submission_schema": {"fields": ["repository", "model_link", "runtime_notes"],
            "field_definitions": {
                "repository": {"label": "代码仓库", "type": "url", "required": False},
                "model_link": {"label": "固定版本模型权重下载地址", "type": "url", "required": False,
                               "help": "包内权重可不填写；大权重须在ZIP的config.json中声明下载地址、大小和SHA-256，这里填写同一地址供评委查阅。"},
                "runtime_notes": {"label": "训练与推理复现说明", "type": "textarea", "required": True}},
            "readme_required": True, "readme_template": readme,
            "attachments": [
                {"key": "code_package", "label": "训练、推理代码与配置", "extensions": ["zip"], "min_count": 1, "max_count": 1, "required_files": ["inference.py", "config.json"]},
                {"key": "predictions", "label": "公开验证集预测结果", "extensions": ["csv"], "min_count": 1, "max_count": 1},
                {"key": "research_report", "label": "补充技术说明或研究报告（可选）", "extensions": ["pdf"], "min_count": 0, "max_count": 1}]},
        "evaluation_config": {"adapter": "classification-v1", "task": "classification", "max_team_runs": 3,
            "resources": {"cpus": 4, "memory_mb": 6144, "gpu": True, "time_seconds": 1800, "episodes": 1},
            "api": {"enabled": False, "max_calls": 0}, "metrics": ["accuracy", "macro_f1", "latency_ms", "peak_vram_mb"]},
    }
    definitions = [
        ("deep-learning", "深度学习赛道", "非RGB人体行为识别：理解数据处理、特征提取、模型训练与效果验证的完整流程。", "DL", "coral", classification, None),
        ("world-exploration", "世界探索智能体赛道", "开放世界自主决策：研究环境理解、目标分解、代码执行、技能复用、记忆与失败恢复。", "OW", "sage", "minecraft-open-world", "minecraft"),
        ("embodied-agent", "具身智能体赛道", "视觉理解与代码生成：在仿真机械臂环境中开展抓取、放置、堆叠与反馈规划实验。", "RA", "sand", "robot-arm-manipulation", "robot-arm"),
    ]
    tracks = []
    for position, (slug, name, description, short, accent, problem, runtime_key) in enumerate(definitions, 1):
        if isinstance(problem, str):
            problem = json.loads((ROOT / "backend/platform_api/templates" / (problem + ".json")).read_text(encoding="utf-8"))["problem"]
            problem = deepcopy(problem)
            problem["code"] = "WJ-OW-001" if runtime_key == "minecraft" else "WJ-RA-001"
            problem["slug"] = "wujie-open-world-agent" if runtime_key == "minecraft" else "wujie-robot-arm-agent"
            problem["statement_md"] = (SOURCE / ("world.md" if runtime_key == "minecraft" else "arm.md")).read_text(encoding="utf-8")
            problem["compute_note"] = f"4 CPU、6144 MiB 内存；每队3次自测，单次{'3600' if runtime_key == 'minecraft' else '1800'}秒总时限；三个场景，最多300次模型调用。"
            problem["submission_schema"]["readme_template"] = readme
            for attachment in problem["submission_schema"]["attachments"]:
                if attachment["key"] == "research_report":
                    attachment.update(label="补充技术说明或研究报告（可选）", min_count=0)
            problem["evaluation_config"]["resources"].update(cpus=4, memory_mb=2048,
                time_seconds=3600 if runtime_key == "minecraft" else 1800)
            if runtime_key == "robot-arm":
                problem["source_url"] = "https://github.com/Lifelong-Robot-Learning/LIBERO"
                problem["summary"] = "在 LIBERO 公布的抓取放置、物体操作与组合任务中，优化自定义策略或智能体 harness。"
                problem["evaluation_config"].update(adapter="libero-agent-v1", task="manipulation",
                    metrics=["task_success", "action_count", "execution_seconds", "invalid_actions"])
                problem["compute_note"] = "LIBERO / Panda，选手程序4CPU、2GiB内存，独立仿真环境3GiB；无GPU，每队3次自测、单次1800秒；三个任务600/600/1000步，最多300次模型调用。"
            else:
                problem["compute_note"] = "MineDojo，选手程序4CPU、2GiB内存，独立环境4GiB；无GPU，每队3次自测、整次3600秒，三档600/1500/3000步，最多300次模型调用。"
            # A model channel is required for these LLM tracks; do not silently fall back to unrestricted networking.
            problem["evaluation_config"]["api"] = {"enabled": True, "max_calls": 300}
        problem["status"] = "draft"
        problem["statement_md"] += "\n\n" + SCORING
        problem["judging_schema"] = {"rubric": {"模型或智能体实际表现": .8, "技术路线说明": .2}}
        problem["scoring_config"] = {"external_weight_percent": 50, "review_score_mode": "weighted", "external_score_label": "现场答辩",
            "online_score_label": "第一阶段", "stages": competition["config"]["scoring_stages"],
            "performance_scoring": {"preset": "wujie-world-v1" if runtime_key == "minecraft" else "wujie-libero-v1" if runtime_key else "wujie-depth-v1", "criterion": "模型或智能体实际表现"}}
        setup = {"evaluation_config": deepcopy(problem["evaluation_config"])}
        if runtime_key and images:
            from evaluation_adapters.minecraft_runner import validate_scenarios as validate_mc
            from evaluation_adapters.libero_runner import validate_scenarios as validate_robot
            from evaluation_adapters.wujie_scenes import minecraft_scenes, libero_scenes
            if runtime_key == "minecraft":
                scenes = minecraft_scenes([secrets.randbelow(2**31) for _ in range(3)])
                validate_mc(scenes, 3)
            else:
                scenes = libero_scenes([20, 21, 22], [secrets.randbelow(2**31) for _ in range(3)])
                validate_robot(scenes, 3)
            pair = images[runtime_key]
            setup["runtime"] = {"image": pair["controller"], "agent_image": pair["agent"], "scenarios": scenes}
        tracks.append({"slug": slug, "name": name, "description": description, "position": position,
            "config": {"short": short, "accent": accent}, "package": f"{problem['slug']}.zip",
            "problem": problem, "setup": setup})
    return {"competition": competition, "tracks": tracks}


def write_bundle(output: Path, images=None):
    from platform_api.problem_setup import read_package
    catalogue = build_catalogue(images)
    output.mkdir(parents=True, exist_ok=True)
    for track in catalogue["tracks"]:
        path = output / track["package"]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("problem.json", json.dumps({"version": 1, "problem": track["problem"],
                "setup": track["setup"]}, ensure_ascii=False))
        read_package(path.read_bytes())
    (output / "competition.json").write_text(json.dumps(catalogue, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalogue


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, help="Trusted image digest JSON; omitting it produces drafts without runtimes")
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/wujie-cup-2026")
    args = parser.parse_args()
    images = json.loads(args.images.read_text(encoding="utf-8")) if args.images else None
    write_bundle(args.output, images)
    print(f"Created 1 competition, 3 tracks and 3 validated draft ZIPs: {args.output.resolve()}")


if __name__ == "__main__":
    main()
