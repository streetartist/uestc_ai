"""The downloadable package must work independently of the repository checkout."""
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from build_wujie_starters import build


class StarterImportTest(unittest.TestCase):
    def test_extracted_libero_starter_imports_its_controller_and_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build(root / 'packages')
            target = root / 'extracted'
            with zipfile.ZipFile(root / 'packages/wujie-arm-starter.zip') as archive:
                archive.extractall(target)
            code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                    'from evaluation_adapters.libero_runner import validate_scenarios; '
                    'from evaluation_adapters.libero.replay import ReplayWriter; '
                    'from evaluation_adapters.wujie_scenes import libero_scenes; '
                    'validate_scenarios(libero_scenes(), 3)')
            subprocess.run([sys.executable, '-I', '-c', code, str(target)],
                           cwd=root, check=True, capture_output=True, timeout=20)


if __name__ == '__main__':
    unittest.main()
