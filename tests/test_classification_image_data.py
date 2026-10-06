import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from evaluation_adapters.classification_runner import digest_file
from install_classification_image_data import install


class ImageDataTests(unittest.TestCase):
    def malicious_archive(self, path, name, link=False):
        with tarfile.open(path, "w:gz") as archive:
            entry = tarfile.TarInfo(name)
            if link:
                entry.type, entry.linkname = tarfile.SYMTYPE, "/etc/passwd"
                archive.addfile(entry)
            else:
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))

    def test_changed_archive_fails_before_creating_dataset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, output = root / "data.tar.gz", root / "dataset"
            archive.write_bytes(b"modified archive")
            with self.assertRaisesRegex(ValueError, "archive failed SHA256"):
                install(archive, output, archive_sha256="0" * 64)
            self.assertFalse(output.exists())

    def test_unknown_split_is_rejected_before_reading_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "select train, validation or test"):
                install(root / "missing.tar.gz", root / "installed", source_split="../test")
            self.assertFalse((root / "installed").exists())

    def test_archive_paths_and_symlinks_cannot_escape_private_split(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, output = root / "data.tar.gz", root / "dataset"
            for name, link in (("../escaped.npy", False), ("sample.npy", True)):
                self.malicious_archive(archive, "uestc-depth-prepared-v1/test/" + name, link)
                with self.assertRaisesRegex(ValueError, "unexpected entry"):
                    install(archive, output, archive_sha256=digest_file(archive))
                self.assertFalse(output.exists())
                self.assertFalse((root / "escaped.npy").exists())

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "getuid") and os.getuid() == 0,
                         "private dataset ownership requires Linux root")
    def test_only_selected_split_is_installed_and_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "uestc-depth-prepared-v1"
            for group in ("train", "validation", "test"):
                folder = source / group
                folder.mkdir(parents=True)
                samples = []
                for label in range(40):
                    path = folder / f"{group}-{label:02}.npy"
                    np.save(path, np.full((4, 1, 4, 4), label, dtype=np.float32), allow_pickle=False)
                    samples.append({"file": path.name, "label": label, "sha256": digest_file(path)})
                (folder / "manifest.json").write_text(json.dumps({"version":1,"modality":"depth",
                    "classes":[f"class-{i}" for i in range(40)],"samples":samples}))
            archive = root / "data.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                stream.add(source, arcname=source.name)
            for group in ("train", "validation", "test"):
                with self.subTest(group=group):
                    expected = digest_file(source / group / "manifest.json")
                    output = root / ("installed-" + group)
                    result = install(archive, output, archive_sha256=digest_file(archive),
                                     manifest_sha256=expected, source_split=group)
                    self.assertEqual(result["samples"], 40)
                    self.assertEqual({path.name for path in output.iterdir()},
                                     {"manifest.json", *(f"{group}-{label:02}.npy" for label in range(40))})
                    self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                    self.assertEqual((output / "manifest.json").stat().st_mode & 0o777, 0o600)
                    self.assertEqual(install(archive, output, archive_sha256=digest_file(archive),
                                             manifest_sha256=expected, source_split=group), result)


if __name__ == "__main__":
    unittest.main()
