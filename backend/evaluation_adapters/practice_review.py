"""Write an offline review page for local experiments."""
import html
import json
from pathlib import Path


def render(root):
    root = Path(root)
    full = root / "result.json"
    partial = root / "partial-results.json"
    data = json.loads((full if full.exists() else partial).read_text(encoding="utf-8")) if full.exists() or partial.exists() else {}
    episodes = data.get("episodes", [])
    title = "本地测评完成" if full.exists() else "本地测评中断"
    rows = []
    public_weights = {"libero-1": 10, "libero-2": 10, "libero-3": 10, "sort-groceries": 15, "table-setting": 15}
    scored = bool(episodes) and all("automatic_score" in e.get("metrics", {}) for e in episodes)
    score_rows = []
    total_points = total_cap = 0.0
    for episode in episodes:
        scene = episode.get("scenario", {})
        metrics = episode.get("metrics", {})
        rows.append(f'<tr><td>{html.escape(str(scene.get("label", scene.get("id", ""))))}</td>'
                    f'<td>{metrics.get("task_success", "—")}</td><td>{metrics.get("action_count", "—")}</td>'
                    f'<td>{metrics.get("execution_seconds", "—")}</td></tr>')
        weight = public_weights.get(scene.get("id"))
        if scored and weight is not None:
            completion = metrics["completion_score"] * weight / 60
            efficiency = metrics["efficiency_score"] * weight / 60
            total_points += completion + efficiency
            total_cap += weight * 5 / 3
            score_rows.append(f'<tr><td>{html.escape(str(scene.get("label", scene.get("id"))))}</td>'
                              f'<td>{completion:.2f} / {weight}</td><td>{efficiency:.2f} / {weight*2/3:.2f}</td>'
                              f'<td>{completion+efficiency:.2f}</td></tr>')
    score_table = ('<h2>本轮得分</h2><p>本轮已结束任务：'
                   f'{total_points:.2f} / {total_cap:.2f}。全部三道公开题满分50，正式五题满分100。</p>'
                   '<table><tr><th>任务</th><th>完成分</th><th>效率分</th><th>得分</th></tr>'
                   + ''.join(score_rows) + '</table>') if score_rows else ''
    media = []
    groups = []
    log = root / "agent/harness.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("event") == "reset" or not groups:
                groups.append([])
            groups[-1].append(record)
    for directory in sorted((root / "evidence").glob("*")):
        videos = list(directory.glob("*.mp4"))
        gifs = list(directory.glob("*.gif"))
        links = ' · '.join(f'<a href="{html.escape(p.relative_to(root).as_posix(), quote=True)}">{html.escape(p.name)}</a>' for p in sorted(directory.iterdir()) if p.is_file())
        player = ''
        video_id = 'video-' + directory.name
        if videos:
            player = f'<video id="{html.escape(video_id, quote=True)}" controls preload="metadata" src="{videos[0].relative_to(root).as_posix()}"></video>'
        elif gifs:
            player = f'<img src="{gifs[0].relative_to(root).as_posix()}" alt="场景回放">'
        decisions = []
        index = int(directory.name) - 1 if directory.name.isdigit() else -1
        for event in groups[index] if 0 <= index < len(groups) else []:
            if event.get('event') not in ('decision', 'skill_result', 'format_error', 'vision', 'geometry', 'template_localization', 'grasp_rejected', 'placement_check', 'verification_disagreement', 'placement_retry'):
                continue
            step = event.get('step', 0)
            if type(step) is not int:
                continue
            if event['event'] == 'decision':
                reply = event.get('reply', {})
                text = reply.get('observation', '')
                detail = json.dumps(reply.get('skill', {}), ensure_ascii=False)
                label = '模型决策'
            elif event['event'] == 'skill_result':
                label = '执行反馈'
                text = event.get('reason', '')
                detail = json.dumps({k: event.get(k) for k in ('actual_delta', 'position_error_metres', 'gripper_position', 'gripper_width_metres', 'gripper_command')}, ensure_ascii=False)
            elif event['event'] != 'format_error':
                labels = {'vision':'视觉核验', 'geometry':'定位位置', 'template_localization':'图像匹配', 'grasp_rejected':'抓取反馈', 'placement_check':'放置核验', 'verification_disagreement':'判断差异', 'placement_retry':'重试'}
                label = labels[event['event']]
                text = event.get('reply', {}).get('evidence', event.get('reason', event.get('phase', '')))
                detail = json.dumps(event, ensure_ascii=False)
            else:
                label, text, detail = '格式修正', event.get('error', ''), event.get('response', '')
            jump = f'<button data-video="{html.escape(video_id, quote=True)}" data-time="{max(0,step)/20}">{step}</button>'
            decisions.append(f'<tr><td>{jump}</td><td>{label}</td><td>{html.escape(str(text))}<details><summary>记录</summary><pre>{html.escape(detail)}</pre></details></td></tr>')
        table = '<table><tr><th>步数</th><th>记录</th><th>观察与结果</th></tr>' + ''.join(decisions) + '</table>' if decisions else ''
        target_table = ''
        trajectory = directory / 'trajectory.json'
        if trajectory.exists():
            trajectory_data = json.loads(trajectory.read_text(encoding='utf8'))
            targets = trajectory_data.get('targets', []) if isinstance(trajectory_data, dict) else []
            target_rows = ''.join(f"<tr><td>{html.escape(str(t['target']))}</td><td>{'完成' if t['completed'] else '未完成'}</td><td>{t.get('attainment_step') if t.get('attainment_step') is not None else '—'}</td><td>{t.get('reference_steps', '—')}</td></tr>" for t in targets)
            if target_rows:
                target_table = '<table><tr><th>目标</th><th>结束状态</th><th>有效达成步数</th><th>参考步数</th></tr>' + target_rows + '</table>'
        media.append(f'<section><h2>场景 {html.escape(directory.name)}</h2>{player}<p>{links}</p>{target_table}{table}</section>')
    error = root / "error.txt"
    message = f'<pre>{html.escape(error.read_text(encoding="utf-8"))}</pre>' if error.exists() else ''
    records = [p for p in (full, partial, root / "agent/harness.jsonl", root / "scenes.json", root / "practice-config.json") if p.exists()]
    links = ' · '.join(f'<a href="{p.relative_to(root).as_posix()}">{html.escape(p.name)}</a>' for p in records)
    document = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>LIBERO 自测结果</title>
<style>body{{max-width:1000px;margin:40px auto;padding:0 20px;font:16px/1.7 system-ui;color:#182436}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:10px;text-align:left}}section{{margin:32px 0}}video,img{{width:100%;max-width:900px;background:#111}}pre{{white-space:pre-wrap}}a{{color:#155ba6}}</style>
<h1>{title}</h1><p>{links}</p>{message}{score_table}<h2>运行指标</h2><table><tr><th>场景</th><th>成功指标</th><th>控制步数</th><th>耗时（秒）</th></tr>{''.join(rows)}</table>{''.join(media)}
<script>document.querySelectorAll('button[data-video]').forEach(b=>b.onclick=()=>{{const v=document.getElementById(b.dataset.video);if(v){{v.currentTime=Number(b.dataset.time);v.scrollIntoView({{block:'center',behavior:'smooth'}});}}}});</script></html>'''
    (root / "review.html").write_text(document, encoding="utf-8")
