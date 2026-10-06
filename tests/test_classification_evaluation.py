import io
import math
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from evaluation_adapters.classification_assets import public_target
from evaluation_adapters.classification_runner import classification_metrics, predicted_class, extract_package
from platform_api.problem_setup import validate_runtime
import test_problem_setup as setup


class ClassificationProtocolTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Windows has no Unix umask permissions")
    def test_nested_package_remains_readable_under_private_organizer_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package, program = root / "test.zip", root / "program"
            program.mkdir()
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("inference.py", "pass")
                archive.writestr("config.json", '{"num_classes":40}')
                archive.writestr("model/nested/weights.txt", "weights")
            old = os.umask(0o077)
            try:
                extract_package(package, program)
            finally:
                os.umask(old)
            for folder in (program / "model", program / "model/nested"):
                self.assertEqual(folder.stat().st_mode & 0o777, 0o755)
            self.assertEqual((program / "model/nested/weights.txt").stat().st_mode & 0o777, 0o644)

    def test_full_mapping_accuracy_and_macro_f1(self):
        metrics, matrix = classification_metrics(list(range(40)), list(range(40)))
        self.assertEqual(metrics, {"accuracy": 100, "macro_f1": 100})
        metrics, _ = classification_metrics([0, 0, 1, 1], [0, 1, 1, 1])
        self.assertEqual(metrics["accuracy"], 75)
        self.assertAlmostEqual(metrics["macro_f1"], 100 * (2/3 + 4/5) / 40)
        self.assertEqual(len(matrix), 40)

    def test_no_self_reported_metric_or_invalid_score_is_accepted(self):
        good = {"type": "prediction", "id": "current", "scores": [0]*39+[1]}
        self.assertEqual(predicted_class(good, "current"), 39)
        for scores in ([1]*39, [True]*40, [math.nan]*40, [math.inf]*40):
            with self.assertRaises(ValueError):
                predicted_class({**good, "scores": scores}, "current")
        with self.assertRaises(ValueError): predicted_class({**good, "accuracy": 100}, "current")
        with self.assertRaises(ValueError): predicted_class(good, "different-request")

    def test_model_download_denies_private_and_unapproved_targets(self):
        for url in ("http://huggingface.co/a", "https://evil.example/a", "https://user:secret@huggingface.co/a"):
            with self.assertRaises(ValueError): public_target(url, ["huggingface.co"])
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"):
            with patch("socket.getaddrinfo", return_value=[(0,0,0,"",(address,443))]):
                with self.assertRaises(ValueError): public_target("https://huggingface.co/model", ["huggingface.co"])
        with patch("socket.getaddrinfo", return_value=[(0,0,0,"",("8.8.8.8",443))]):
            parsed, address = public_target("https://huggingface.co/model", ["huggingface.co"])
            self.assertEqual(address, "8.8.8.8")
            self.assertEqual(parsed.hostname, "huggingface.co")

    def test_submission_zip_cannot_escape_or_smuggle_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("../escape.py", "/escape.py", "bad\\file.py"):
                package = root / "test.zip"
                with zipfile.ZipFile(package, "w") as archive:
                    archive.writestr(name, "x")
                with self.assertRaises(ValueError): extract_package(package, root)
            package = root / "test.zip"
            with zipfile.ZipFile(package, "w") as archive:
                link = zipfile.ZipInfo("inference.py")
                link.external_attr = 0o120777 << 16
                archive.writestr(link, "/etc/passwd")
            with self.assertRaises(ValueError): extract_package(package, root)

    def test_native_runtime_requires_gpu_and_immutable_private_dataset(self):
        evaluation = {"adapter":"classification-v1", "resources":{"gpu":True,"episodes":1},"api":{"enabled":False}}
        runtime = {"execution":"autodl-native","image":"image-test","scenarios":[{"dataset":"test-depth","manifest_sha256":"a"*64}]}
        self.assertEqual(validate_runtime(runtime, evaluation)["execution"], "autodl-native")
        evaluation["resources"]["gpu"] = False
        with self.assertRaises(ValueError): validate_runtime(runtime, evaluation)
        evaluation["resources"]["gpu"] = True
        runtime["scenarios"][0]["dataset"] = "../../labels"
        with self.assertRaises(ValueError): validate_runtime(runtime, evaluation)


class NativeWorkerRoutingTests(unittest.TestCase):
    setUp = setup.ProblemSetupTests.setUp
    tearDown = setup.ProblemSetupTests.tearDown
    login = setup.ProblemSetupTests.login
    register = setup.ProblemSetupTests.register
    fixture = setup.ProblemSetupTests.fixture
    submit = setup.ProblemSetupTests.submit
    prepare = setup.ProblemSetupTests.prepare
    document = setup.ProblemSetupTests.document
    def test_native_job_is_never_claimed_by_docker_or_other_image(self):
        self.prepare()
        self.config["resources"].update(gpu=True, episodes=1)
        self.runtime_config = {"execution":"autodl-native", "image":"image-4080-test", "agent_image":"",
            "scenarios":[{"dataset":"private-depth","manifest_sha256":"b"*64}]}
        response = self.admin.put(self.url, json=self.document())
        self.assertEqual(response.status_code, 200, response.get_json())
        response = self.submit()
        self.assertEqual(response.status_code, 201, response.get_json())
        headers = {"Authorization":"Bearer test-worker"}
        capabilities = {"adapters":["classification-v1"],"gpu":True,"managed_runtime":True}
        self.assertEqual(self.worker.post("/api/evaluation-worker/claim",headers=headers,json=capabilities).status_code,204)
        capabilities.update(execution_backends=["autodl-native"], runtime_images=["image-wrong"])
        self.assertEqual(self.worker.post("/api/evaluation-worker/claim",headers=headers,json=capabilities).status_code,204)
        capabilities["runtime_images"] = ["image-4080-test"]
        self.assertEqual(self.worker.post("/api/evaluation-worker/claim",headers=headers,json=capabilities).status_code,204)
        capabilities["dataset_manifests"] = {"private-depth":"b"*64}
        job = self.worker.post("/api/evaluation-worker/claim",headers=headers,json=capabilities)
        self.assertEqual(job.status_code,200,job.get_json())
        self.assertEqual(job.get_json()["runtime"]["execution"], "autodl-native")


if __name__ == "__main__": unittest.main()
