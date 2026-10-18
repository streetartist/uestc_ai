import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from prepare_depth_ir_dataset import compact_clip, prepare
from evaluation_adapters.depth_ir_dataset import validate_manifest


class CompactRecipeTests(unittest.TestCase):
    def test_original_frames_share_indices_and_published_png_recipe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'source' / 'sample'
            for modality, mode in [('Depth_Color', 'RGB'), ('IR', 'L')]:
                (source / modality).mkdir(parents=True)
                for index in range(45):
                    Image.new(mode, (640, 480), index).save(source / modality / f'frame_{index:04d}.png')
            labels = root / 'labels.csv'
            labels.write_text('file,label\nsample,3\n')
            classes = root / 'classes.json'
            classes.write_text(json.dumps([str(i) for i in range(40)]))
            output = root / 'compact'
            digest = prepare(source.parent, labels, classes, output, compact=True)
            self.assertEqual(len(digest), 64)
            manifest = json.loads((output / 'manifest.json').read_text())
            validate_manifest(output, manifest)
            indices = np.linspace(0, 44, 32).round().astype(int).tolist()
            sidecar = json.loads((output / 'provenance.json').read_text())
            self.assertEqual(sidecar['samples'][0]['selected_source_indices'], indices)
            for modality, mode in [('Depth_Color', 'RGB'), ('IR', 'L')]:
                self.assertEqual(len(list((output / 'sample' / modality).glob('*.png'))), 32)
                for index in indices:
                    name = f'frame_{index:04d}.png'
                    with Image.open(source / modality / name) as image:
                        expected = io.BytesIO()
                        image.convert(mode).resize((304, 228), Image.Resampling.BILINEAR).save(expected, format='PNG', compress_level=6)
                    self.assertEqual((output / 'sample' / modality / name).read_bytes(), expected.getvalue())
            with self.assertRaises(FileExistsError):
                prepare(source.parent, labels, classes, output, compact=True)

    def test_mismatched_source_frames_rejected_and_short_clip_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for modality, mode in [('Depth_Color', 'RGB'), ('IR', 'L')]:
                (root / 'source' / modality).mkdir(parents=True)
                Image.new(mode, (640, 480)).save(root / 'source' / modality / 'frame_1.png')
            self.assertEqual(compact_clip(root / 'source', root / 'short'), [0])
            Image.new('L', (640, 480)).save(root / 'source' / 'IR' / 'frame_2.png')
            with self.assertRaises(ValueError):
                compact_clip(root / 'source', root / 'mismatch')


if __name__ == '__main__':
    unittest.main()
