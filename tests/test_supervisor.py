import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from supervisor import BUDGET, DIAGNOSIS_CODES, MESSAGES, Supervisor, rectangle, sanitize_text


class FakeClient:
    def __init__(self, answers):
        self.answers = list(answers); self.calls = []
        completions = self
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=completions.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=answer), finish_reason='stop')])


class SupervisorTests(unittest.TestCase):
    def make(self, answers, mode='shadow', state=None):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.client = FakeClient(answers)
        return Supervisor(self.root / 'attempt', state_path=state, mode=mode, client_factory=lambda: self.client, model='test-model')

    def test_mode_off_never_calls_the_model_and_logs_the_skip(self):
        sup = self.make(['{"ok":true}'], mode='off')
        image = Image.new('RGB', (64, 96), 'white')
        self.assertIsNone(sup.review_regions(self.save(image), {'face': [10, 10, 20, 20], 'left_eye': [12, 14, 4, 3], 'right_eye': [22, 14, 4, 3], 'mouth': [16, 24, 8, 4]}))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(json.loads((self.root / 'attempt/supervisor/gate-planning.json').read_text())['skipped'], 'mode_off')

    def save(self, image):
        path = self.root / 'image.png'; path.parent.mkdir(parents=True, exist_ok=True); image.save(path); return path

    def test_region_review_scales_thumbnail_boxes_back_and_rejects_invalid_ones(self):
        sup = self.make([json.dumps({'ok': False, 'issues': ['嘴框偏高'], 'regions': {'face': None, 'left_eye': None, 'right_eye': None, 'mouth': [100, 300, 60, 30]}, 'explanation': '嘴框应下移。'})])
        image = Image.new('RGB', (1024, 1536), 'white')
        gate = sup.review_regions(self.save(image), {'face': [400, 100, 200, 200], 'left_eye': [500, 150, 40, 20], 'right_eye': [430, 150, 40, 20], 'mouth': [470, 250, 60, 30]})
        self.assertFalse(gate['ok'])
        self.assertEqual(gate['regions'], {'mouth': [300, 900, 180, 90]})  # thumbnail is 341x512, scale 3
        call = self.client.calls[0]
        self.assertFalse(call.get('stream', False)); self.assertEqual(call['temperature'], 0)
        self.assertEqual(call['response_format'], {'type': 'json_object'})
        self.assertTrue(call['messages'][1]['content'][2]['image_url']['url'].startswith('data:image/png;base64,'))
        sup2 = self.make([json.dumps({'ok': True, 'regions': {'mouth': [-5, 10, 2000, 30]}})])
        gate = sup2.review_regions(self.save(image), {'face': [400, 100, 200, 200], 'left_eye': [500, 150, 40, 20], 'right_eye': [430, 150, 40, 20], 'mouth': [470, 250, 60, 30]})
        self.assertEqual(gate['regions'], {})

    def test_budget_is_shared_through_the_state_file_and_stops_model_calls(self):
        state = Path(tempfile.mkdtemp()) / 'state.json'
        sup = self.make(['{"score":0.8,"issues":[]}'] * 20, state=state)
        for _ in range(BUDGET['model_calls']):
            self.assertIsNotNone(sup.visual_review([('sheet', Image.new('RGB', (32, 32)))]))
        self.assertIsNone(sup.visual_review([('sheet', Image.new('RGB', (32, 32)))]))
        self.assertEqual(len(self.client.calls), BUDGET['model_calls'])
        again = Supervisor(self.root / 'attempt2', state_path=state, mode='shadow', client_factory=lambda: self.client, model='m')
        self.assertFalse(again.can('model_calls'))
        self.assertTrue(again.consume('tune_motion')); self.assertTrue(again.consume('tune_motion')); self.assertFalse(again.consume('tune_motion'))
        self.assertEqual(json.loads(state.read_text())['used']['tune_motion'], 2)

    def test_diagnosis_is_whitelisted_and_failures_degrade_to_none(self):
        sup = self.make([json.dumps({'diagnosis_code': 'rig_unstable', 'action': 'tune_motion', 'explanation': '动作幅度过大。', 'confidence': 0.9}),
                         json.dumps({'diagnosis_code': 'made_up', 'action': 'tune_motion'}),
                         'not json at all',
                         RuntimeError('boom')])
        packet = sup.failure_packet('verifying', RuntimeError(f'failed at {sup.workspace}/body-motion with key sk-secret'), {'feet': 0.3})
        self.assertNotIn(str(sup.workspace), packet['error_message'])
        self.assertEqual(packet['stage'], 'verifying'); self.assertEqual(packet['metrics'], {'feet': 0.3})
        decision = sup.diagnose(packet)
        self.assertEqual(decision, {'diagnosis_code': 'rig_unstable', 'action': 'tune_motion', 'explanation': '动作幅度过大。', 'confidence': 0.9})
        self.assertIsNone(sup.diagnose(packet)); self.assertIsNone(sup.diagnose(packet)); self.assertIsNone(sup.diagnose(packet))
        logs = sorted(p.name for p in (sup.workspace / 'supervisor').iterdir())
        self.assertIn('diagnosis-verifying-rejected.json', logs)
        payload = sup.write_diagnosis('background_leak', 'bogus', 'x' * 500, 'give_up', packet)
        self.assertEqual(payload['suggestion'], 'regenerate_image'); self.assertEqual(len(payload['summary']), 200)
        payload = sup.write_diagnosis('unknown_code', None, '', None)
        self.assertEqual(payload['diagnosisCode'], 'provider_unavailable'); self.assertEqual(payload['summary'], MESSAGES['provider_unavailable'])
        self.assertEqual(json.loads((sup.workspace / 'supervisor/diagnosis.json').read_text())['diagnosisCode'], 'provider_unavailable')

    def test_mouth_locator_and_helpers(self):
        sup = self.make([json.dumps({'mouth': [100, 200, 60, 30]}), json.dumps({'mouth': None})])
        crop = Image.new('RGB', (1024, 1024), 'white')
        self.assertEqual(sup.locate_mouth(crop), [200, 400, 120, 60])
        self.assertIsNone(sup.locate_mouth(crop))
        self.assertIsNone(rectangle([1, 2, 3], 10, 10)); self.assertIsNone(rectangle([0, 0, 11, 1], 10, 10)); self.assertEqual(rectangle(['1', 2, 3, 4], 10, 10), [1, 2, 3, 4])
        self.assertEqual(sanitize_text('a\x00b\n c ', 3), 'ab')
        self.assertEqual(set(MESSAGES), set(DIAGNOSIS_CODES))
        with self.assertRaises(ValueError):
            Supervisor(self.root, mode='loud')


if __name__ == '__main__':
    unittest.main()
