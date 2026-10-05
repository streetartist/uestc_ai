"""Build organizer-only ZIPs and the complete 2026 Wujie Cup draft catalogue."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import secrets
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "backend/competitions/wujie-cup-2026"
SCORING = """## 本届评分规则

第一阶段采用百分制：模型或智能体实际表现 ×80% + 研究报告 ×20%。最终成绩 = 第一阶段 ×50% + 现场答辩 ×50%。各赛道分别评分与排名。实际表现按统一测评记录评审；研究报告考察方法设计、实验分析与结果论证。现场答辩包括方案介绍、运行演示与问答。

平台在线评委表单用于第一阶段；现场答辩按百分制导入并确认，权重50%。没有完成第一阶段评审或尚未确认答辩成绩时，不生成最终成绩。模型或智能体指标到表现分的换算细则另行公布，不采用参赛者自报分数。

## 本届筹备状态

赛事时间待定。本题的测试次数、时长、场景和资源额度是筹备配置，正式限制开赛前统一公布。模型接口和队伍自用GPU渠道、额度尚待配置。代码、运行配置和PDF研究报告必交；README须包含复现步骤、依赖、随机种子、实验、失败案例和引用。明显抄袭、作弊或数据造假取消获奖资格。
"""


def build_catalogue(images: dict | None = None):
    overview = (SOURCE / "overview.md").read_text(encoding="utf-8")
    competition = {
        "slug": "wujie-cup-2026", "name": "2026“无界杯”UESTC AI挑战赛",
        "summary": "计算机学院主办、经管学院协办，AI社与计算机学院学生会承办。面向全校同学的人工智能挑战赛，设人体行为识别、开放世界探索与机械臂操作三条赛道；奖金总额30,000元，赛事时间待定。",
        "status": "draft", "config": {"overview_md": overview, "leaderboard": {"visible": False, "rank_scope": "track"},
            "proposal": {"author": "黄锐", "modified_at": "2026-09-10", "recipient": "学生科"},
            "prize_pool_yuan": 30000, "reviewing": {"locked_at": None},
            "scoring_stages": {"performance_percent": 80, "report_percent": 20,
                               "first_stage_percent": 50, "defense_percent": 50}},
    }
    readme = "# 作品介绍\n\n## 方法与系统设计\n\n## 环境与依赖\n\n## 训练或运行命令\n\n## 实验结果与对比\n\n## 消融实验与失败案例\n\n## 复现步骤与文件校验\n\n## 引用与团队分工\n"
    classification = {
        "code": "WJ-DL-001", "slug": "wujie-human-activity-recognition",
        "title": "观行知意：人体日常行为识别挑战赛",
        "summary": "利用CUHK-X的深度图像等非RGB数据识别40类日常动作，兼顾准确率、计算量和推理速度。",
        "status": "draft", "difficulty": 3,
        "statement_md": (SOURCE / "classification.md").read_text(encoding="utf-8"),
        "compute_note": "模型训练建议使用GPU；正式测评硬件、数据与可信推理镜像待配置。",
        "source_url": "https://github.com/openaiotlab/CUHK-X",
        "submission_schema": {"fields": ["repository", "model_link", "runtime_notes"],
            "field_definitions": {
                "repository": {"label": "代码仓库", "type": "url", "required": False},
                "model_link": {"label": "固定版本模型权重下载地址", "type": "url", "required": True,
                               "help": "权重超过20MB时通过地址交付；在运行说明中填写大小与SHA-256，保持组织方可下载。"},
                "runtime_notes": {"label": "训练与推理复现说明", "type": "textarea", "required": True}},
            "readme_required": True, "readme_template": readme,
            "attachments": [
                {"key": "code_package", "label": "训练、推理代码与配置", "extensions": ["zip"], "min_count": 1, "max_count": 1},
                {"key": "predictions", "label": "预测结果", "extensions": ["csv"], "min_count": 1, "max_count": 1},
                {"key": "research_report", "label": "完整研究报告", "extensions": ["pdf"], "min_count": 1, "max_count": 1}]},
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
            problem["statement_md"] += "\n\n" + SCORING
            problem["submission_schema"]["readme_template"] = readme
            problem["evaluation_config"]["resources"].update(cpus=4, memory_mb=6144,
                time_seconds=3600 if runtime_key == "minecraft" else 1800)
            # A model channel is required for these LLM tracks; do not silently fall back to unrestricted networking.
            problem["evaluation_config"]["api"] = {"enabled": True, "max_calls": 300}
        problem["status"] = "draft"
        problem["judging_schema"] = {"rubric": {"模型或智能体实际表现": .8, "研究报告": .2}}
        problem["scoring_config"] = {"external_weight_percent": 50, "review_score_mode": "weighted", "external_score_label": "现场答辩",
            "online_score_label": "第一阶段", "stages": competition["config"]["scoring_stages"]}
        setup = {"evaluation_config": deepcopy(problem["evaluation_config"])}
        if runtime_key and images:
            from evaluation_adapters.minecraft_runner import validate_scenarios as validate_mc
            from evaluation_adapters.robot_arm_runner import validate_scenarios as validate_robot
            if runtime_key == "minecraft":
                scenes = [{"id": f"wujie-world-{i}", "label": label, "difficulty": difficulty,
                    "task_id": "open-ended", "world_seed": secrets.randbelow(2**31), "max_steps": 600, "goals": goals}
                    for i, (label, difficulty, goals) in enumerate([
                        ("资源采集", "beginner", ["log"]), ("基础制作", "intermediate", ["crafting_table"]),
                        ("工具发展", "challenge", ["wooden_pickaxe"])], 1)]
                validate_mc(scenes, 3)
            else:
                from build_robot_arm_package import default_scenes
                scenes = default_scenes()
                for scene in scenes:
                    scene["seed"] = secrets.randbelow(2**31)
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
