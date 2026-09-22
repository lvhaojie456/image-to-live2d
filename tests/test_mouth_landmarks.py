"""The landmark helper must degrade, never break: the pipeline venv has no MediaPipe."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts' / 'mouth_landmarks.py'


class MouthLandmarkHelperTests(unittest.TestCase):
    def run_helper(self, image, model):
        return subprocess.run([sys.executable, str(HELPER), '--image', str(image), '--model', str(model)],
                              capture_output=True, text=True, check=False)

    def test_missing_input_reports_the_reason_and_exits_two(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_helper(Path(temporary) / 'absent.png', Path(temporary) / 'absent.task')
            self.assertEqual(result.returncode, 2)
            self.assertIn('missing input', json.loads(result.stdout)['error'])

    def test_absent_runtime_reports_no_mouth_instead_of_crashing(self):
        # This suite runs in the pipeline venv, which deliberately has no MediaPipe
        # (it pins numpy 1.x). The helper must answer, not raise: measure_mouth then
        # keeps its darkness strategies.
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'edit.png'
            Image.new('RGB', (64, 64), 'white').save(image)
            model = Path(temporary) / 'model.task'
            model.write_bytes(b'not-a-model')
            result = self.run_helper(image, model)
            self.assertEqual(result.returncode, 0)
            payload = json.loads(result.stdout.strip().splitlines()[-1])
            self.assertIsNone(payload.get('mouth'))
            self.assertNotIn('polygon', json.dumps(payload))

    def test_installer_check_reports_both_artifacts(self):
        installer = ROOT / 'scripts' / 'install_mouth_landmarks.py'
        result = subprocess.run([sys.executable, str(installer), '--check', '--root', tempfile.mkdtemp()],
                                capture_output=True, text=True, check=False)
        payload = json.loads(result.stdout)
        self.assertFalse(payload['model_ready'])
        self.assertFalse(payload['venv_ready'])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload['model_sha256'], '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff')


if __name__ == '__main__':
    unittest.main()
