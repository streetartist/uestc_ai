from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from platform_api.problem_templates import templates
from platform_api.submission_schema import validate_submission_materials, validate_submission_schema
from evaluation_adapters.minecraft_agent import load_agent


class SubmissionRequirementsTests(unittest.TestCase):
    def setUp(self):
        self.schema = next(item["problem"]["submission_schema"] for item in templates() if item["id"] == "minecraft-open-world")
        self.data = {"readme_md": "# System", "fields": {"runtime_notes": "agent.py and config.json"}}

    def test_draft_can_be_incomplete_but_formal_requires_materials(self):
        validate_submission_materials(self.schema, {"readme_md": "", "fields": {}}, [], False)
        with self.assertRaisesRegex(ValueError, "运行与复现"):
            validate_submission_materials(self.schema, {"readme_md": "README", "fields": {}}, [], True)
        with self.assertRaisesRegex(ValueError, "智能体代码"):
            validate_submission_materials(self.schema, self.data, [], True)

    def test_zip_contents_report_and_url_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = SimpleNamespace(original_name="agent.zip", storage_name="package.zip")
            report = SimpleNamespace(original_name="report.pdf", storage_name="report.pdf")
            with zipfile.ZipFile(root / "package.zip", "w") as archive:
                archive.writestr("agent.py", "# agent")
            with self.assertRaisesRegex(ValueError, "config.json"):
                validate_submission_materials(self.schema, self.data, [package, report], True, root)
            with zipfile.ZipFile(root / "package.zip", "a") as archive:
                archive.writestr("config.json", "{}")
            with self.assertRaisesRegex(ValueError, "研究报告"):
                validate_submission_materials(self.schema, self.data, [package], True, root)
            validate_submission_materials(self.schema, self.data, [package, report], True, root)
            with self.assertRaisesRegex(ValueError, "HTTP/HTTPS"):
                validate_submission_materials(self.schema, {**self.data, "fields": {**self.data["fields"], "repository": "javascript:alert(1)"}}, [package, report], True, root)
            with self.assertRaisesRegex(ValueError, "智能体代码"):
                validate_submission_materials(self.schema, self.data, [package, package, report], True, root)

    def test_invalid_schema_rejected_and_legacy_fields_supported(self):
        self.assertEqual(validate_submission_schema({"fields": ["repository"]}), {"fields": ["repository"]})
        for invalid in ({"fields": ["a", "a"]}, {"fields": ["a"], "field_definitions": {"b": {}}}, {"fields": [], "field_definitions": {"x": {"type": "shell"}}}, {"attachments": [{"key": "report", "label": "Report", "extensions": ["exe"], "min_count": 1, "max_count": 1}]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_submission_schema(invalid)
        validate_submission_materials({"fields": ["repository"]}, {"readme_md": "", "fields": {}}, [], True)

    def test_configuration_hook_and_bad_config(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "agent.zip"
            source = "class Agent:\n def configure(self, config): self.config = config\n def act(self, observation): return observation['noop_action']\n"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("agent.py", source)
                archive.writestr("config.json", json.dumps({"memory": "episodic"}))
            agent = load_agent(package, root / "loaded")
            self.assertEqual(agent.config, {"memory": "episodic"})
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("agent.py", source)
                archive.writestr("config.json", "[]")
            with self.assertRaisesRegex(ValueError, "object"):
                load_agent(package, root / "bad")


if __name__ == "__main__":
    unittest.main()
