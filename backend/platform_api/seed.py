from __future__ import annotations

from datetime import datetime, timezone

from flask import current_app
from werkzeug.security import generate_password_hash

from .extensions import db
from .models import (
    AuditLog, Competition, CompetitionReviewer, Content, EvaluationRun, MarkdownAsset, Problem,
    Registration, Review, Score, ScoreBatch, StagedSubmissionAsset, Submission,
    SubmissionAsset, SubmissionVersion, Team, Track, User,
)


def validate_demo_data(
    expected_competition_slug: str, expected_content_slugs: set[str],
    purge_catalog_history: bool = False,
) -> tuple[Competition, list[Content], list[AuditLog]]:
    competitions = Competition.query.all()
    contents = Content.query.all()
    if len(competitions) != 1 or competitions[0].slug != expected_competition_slug:
        raise RuntimeError("Database competition does not match the expected demo")
    if {item.slug for item in contents} != expected_content_slugs or len(contents) != len(expected_content_slugs):
        raise RuntimeError("Database content does not match the expected demo")
    if len(competitions[0].tracks) != 4 or sum(len(track.problems) for track in competitions[0].tracks) != 10:
        raise RuntimeError("Database tracks or problems do not match the expected demo")
    protected_models = (
        Team, Registration, Submission, SubmissionVersion, SubmissionAsset, StagedSubmissionAsset,
        Review, ScoreBatch, Score, MarkdownAsset, EvaluationRun,
    )
    if any(db.session.query(model.id).first() is not None for model in protected_models):
        raise RuntimeError("Database has activity records; demo replacement was cancelled")
    catalog_history = AuditLog.query.filter(AuditLog.entity_type.in_(("competition", "content", "team"))).all()
    if catalog_history and not purge_catalog_history:
        raise RuntimeError("Database has catalog history; pass --purge-catalog-history to remove it")
    admin_email = current_app.config["INITIAL_ADMIN_EMAIL"].strip().lower()
    reviewer_email = current_app.config["INITIAL_REVIEWER_EMAIL"].strip().lower()
    if not User.query.filter_by(email=admin_email).first() or not User.query.filter_by(email=reviewer_email).first():
        raise RuntimeError("Existing demo accounts do not match the configured emails")
    return competitions[0], contents, catalog_history


def replace_demo_data(
    expected_competition_slug: str, expected_content_slugs: set[str],
    purge_catalog_history: bool = False,
) -> None:
    competition, contents, catalog_history = validate_demo_data(
        expected_competition_slug, expected_content_slugs, purge_catalog_history,
    )
    for item in catalog_history:
        db.session.delete(item)
    for item in contents:
        db.session.delete(item)
    db.session.delete(competition)
    db.session.flush()
    seed_database()


def seed_database() -> None:
    if Competition.query.first():
        return
    now = datetime.now(timezone.utc)
    admin_email = current_app.config["INITIAL_ADMIN_EMAIL"].strip().lower()
    admin_password = current_app.config["INITIAL_ADMIN_PASSWORD"]
    reviewer_email = current_app.config["INITIAL_REVIEWER_EMAIL"].strip().lower()
    reviewer_password = current_app.config["INITIAL_REVIEWER_PASSWORD"]
    if not admin_password or not reviewer_password:
        raise RuntimeError(
            "INITIAL_ADMIN_PASSWORD and INITIAL_REVIEWER_PASSWORD are required "
            "when SEED_DATABASE=1"
        )
    admin = User.query.filter_by(email=admin_email).first()
    if admin is None:
        admin = User(email=admin_email, name="样例管理员", password_hash=generate_password_hash(admin_password), role="admin", email_verified_at=now)
    reviewer = User.query.filter_by(email=reviewer_email).first()
    if reviewer is None:
        reviewer = User(email=reviewer_email, name="样例评委", password_hash=generate_password_hash(reviewer_password), role="reviewer", email_verified_at=now)
    competition = Competition(
        slug="paper-city-2027", name="纸城创作节（虚构样例）",
        summary="一座不存在的城市，四种记录方式。所有名称、规则和内容均为演示用虚构数据。",
        status="published", registration_opens_at=datetime(2027, 3, 1, tzinfo=timezone.utc),
        registration_closes_at=datetime(2027, 4, 12, tzinfo=timezone.utc),
        starts_at=datetime(2027, 4, 18, tzinfo=timezone.utc), ends_at=datetime(2027, 5, 24, tzinfo=timezone.utc),
        config={"team_size": {"min": 1, "max": 4}, "submission_limit": 10, "leaderboard": {"visible": True}},
    )
    db.session.add_all([admin, reviewer, competition])
    db.session.flush()
    db.session.add(CompetitionReviewer(competition=competition, reviewer=reviewer, weight_percent=None))
    track_defs = [
        ("paper-machines", "纸艺机关", "用纸板、折痕和简单机械结构构造可操作的城市装置。", {"accent": "coral", "short": "PM"}),
        ("sound-walks", "声音漫游", "为虚构街区采集或创作声音，组织可以独立聆听的漫游路线。", {"accent": "ink", "short": "SW"}),
        ("night-signs", "夜间招牌", "设计一组可读、可辨认的街道标识与夜间导视。", {"accent": "sage", "short": "NS"}),
        ("story-maps", "故事地图", "通过地点、人物与短篇叙事呈现纸城的一天。", {"accent": "sand", "short": "SM"}),
    ]
    tracks = {}
    for position, (slug, name, description, config) in enumerate(track_defs, 1):
        track = Track(competition=competition, slug=slug, name=name, description=description, config=config, position=position)
        tracks[slug] = track
        db.session.add(track)
    db.session.flush()
    problem_defs = [
        ("paper-machines", "PM-001", "folding-bridge", "折叠桥模型", "制作能展开并承载纸质车辆的桥梁模型。", 2, "纸板、尺、剪刀与胶带即可", "candidate", ["repository", "demo_video", "report"], {"stability": 0.6, "craft": 0.4}),
        ("paper-machines", "PM-002", "clockwork-window", "会移动的橱窗", "用手摇结构驱动至少两组橱窗陈设。", 3, "手工材料，不需要电子设备", "candidate", ["repository", "demo_video", "report"], {"motion": 0.5, "craft": 0.5}),
        ("paper-machines", "PM-003", "portable-kiosk", "可折叠街角小亭", "设计展开后可站立、收纳后便于携带的小亭模型。", 3, "A3 纸板与常见连接件", "candidate", ["repository", "demo_video", "report"], {"portability": 0.5, "stability": 0.5}),
        ("sound-walks", "SW-001", "rainy-market", "雨后集市声音路线", "创作五段连续的街区声音片段，形成完整的步行体验。", 2, "录音笔或手机录音即可", "candidate", ["repository", "demo_url", "report"], {"continuity": 0.5, "clarity": 0.5}),
        ("sound-walks", "SW-002", "station-chimes", "车站提示音设计", "为一座虚构车站设计到站、换乘和闭站提示音。", 3, "常见音频编辑软件即可", "candidate", ["repository", "demo_url", "report"], {"recognition": 0.6, "comfort": 0.4}),
        ("sound-walks", "SW-003", "quiet-alley", "安静小巷声景", "使用有限的声音元素营造清晨小巷的空间感。", 3, "耳机与常见音频编辑软件", "candidate", ["repository", "demo_url", "report"], {"space": 0.5, "balance": 0.5}),
        ("night-signs", "NS-001", "lantern-wayfinding", "灯笼街区导视", "为三处岔路设计形状一致、信息清晰的夜间导视牌。", 3, "矢量绘图或手绘扫描均可", "candidate", ["repository", "demo_url", "report"], {"legibility": 0.6, "consistency": 0.4}),
        ("night-signs", "NS-002", "shopfront-letters", "街角店铺字牌", "创作一套适用于五家虚构店铺的招牌字样。", 2, "纸笔或排版软件即可", "published", ["repository", "demo_url", "report"], {"legibility": 0.5, "character": 0.5}),
        ("story-maps", "SM-001", "postcard-route", "明信片投递路线", "以六张明信片串联纸城的不同地点与人物。", 2, "文字与静态图片即可", "published", ["repository", "demo_url", "report"], {"story": 0.6, "coherence": 0.4}),
        ("story-maps", "SM-002", "midnight-library", "午夜图书馆地图", "通过地图和短篇文字说明一间只在夜间开放的虚构图书馆。", 3, "文字与静态图片即可", "published", ["repository", "demo_url", "report"], {"story": 0.5, "navigation": 0.5}),
    ]
    for track_slug, code, slug, title, summary, difficulty, compute, status, fields, rubric in problem_defs:
        statement = f"# {title}\n\n{summary}\n\n## 提交要求\n\n请提供作品说明、制作过程和展示材料。本题仅供平台功能演示，不代表实际赛事安排。"
        db.session.add(Problem(track=tracks[track_slug], code=code, slug=slug, title=title, summary=summary, difficulty=difficulty, compute_note=compute, status=status, statement_md=statement, submission_schema={"fields": fields, "readme_required": True}, judging_schema={"rubric": rubric}, scoring_config={"external_weight_percent": 0}))
    content_items = [
        ("announcement", "paper-city-demo-open", "纸城创作节样例页面开放", "这是虚构的演示活动，展示报名、赛道和题目页面的使用方式。"),
        ("blog", "paper-city-postcards", "从一张明信片开始", "用地点和人物线索为一座虚构城市写下短篇故事。"),
        ("event", "paper-city-night-walk", "纸城夜间漫游记录", "记录灯光、招牌与街道声音如何组成一条虚构路线。"),
    ]
    for kind, slug, title, excerpt in content_items:
        db.session.add(Content(kind=kind, slug=slug, title=title, excerpt=excerpt, body_md=f"# {title}\n\n{excerpt}\n\n以上内容均为虚构样例，仅用于展示站点功能。", status="published", published_at=now))
    db.session.commit()
