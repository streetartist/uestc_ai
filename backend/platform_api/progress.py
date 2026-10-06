"""Progress checkpoints are independent of submissions, judging and trial quotas."""
from datetime import datetime, timezone
import re


def validate_checkpoints(value):
    if not isinstance(value, list) or len(value) > 3:
        raise ValueError("最多设置三个进度点。")
    seen, result, previous = set(), [], None
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"id", "label", "due_at"}
                or not isinstance(item["id"], str) or not re.fullmatch(r"checkpoint-[1-3]", item["id"])
                or item["id"] in seen or not isinstance(item["label"], str) or not 1 <= len(item["label"].strip()) <= 80):
            raise ValueError("进度点需要唯一编号、名称和截止时间。")
        due = item["due_at"]
        if due is not None:
            if not isinstance(due, str):
                raise ValueError("进度点时间格式错误。")
            try:
                parsed = datetime.fromisoformat(due.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError("进度点时间格式错误。") from error
            if parsed.tzinfo is None:
                raise ValueError("进度点时间须指定时区。")
            parsed = parsed.astimezone(timezone.utc)
            if previous and parsed <= previous:
                raise ValueError("进度点截止时间须按顺序递增。")
            previous, due = parsed, parsed.isoformat()
        seen.add(item["id"])
        result.append({"id": item["id"], "label": item["label"].strip(), "due_at": due})
    return result
