"""Reusable challenge drafts. Applying a template never publishes a problem."""

from copy import deepcopy
import json
from pathlib import Path

from .evaluation import validate_evaluation_config
from .reviewing import validate_scoring_config
from .submission_schema import validate_submission_schema


def templates():
    result = []
    for path in sorted((Path(__file__).parent / "templates").glob("*.json")):
        template = json.loads(path.read_text(encoding="utf-8"))
        problem = template["problem"]
        problem["submission_schema"] = validate_submission_schema(problem.get("submission_schema"))
        problem["evaluation_config"] = validate_evaluation_config(problem.get("evaluation_config"))
        problem["scoring_config"] = validate_scoring_config(problem.get("scoring_config"))
        problem["status"] = "draft"
        result.append(template)
    return deepcopy(result)
