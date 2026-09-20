"""Configuration names: new neutral names win, legacy names keep working, required ones fail loudly."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import live2d_pipeline
from live2d_pipeline import env, image_model, planner_model, required_env


class EnvNamesTests(unittest.TestCase):
    def test_new_name_wins_over_legacy_alias(self):
        with patch.dict(os.environ, {'PLANNER_MODEL': 'new', 'ASTRA_MODEL': 'old'}):
            self.assertEqual(planner_model(), 'new')
        with patch.dict(os.environ, {'ASTRA_MODEL': 'old'}, clear=True):
            self.assertEqual(planner_model(), 'old')

    def test_blank_values_are_ignored(self):
        with patch.dict(os.environ, {'PLANNER_MODEL': '  ', 'ASTRA_MODEL': 'old'}):
            self.assertEqual(planner_model(), 'old')
        self.assertEqual(env('DOES_NOT_EXIST_X', default='d'), 'd')

    def test_required_names_fail_with_the_canonical_name(self):
        with patch.dict(os.environ, {}, clear=True):
            for fn, name in ((planner_model, 'PLANNER_MODEL'), (image_model, 'IMAGE_MODEL')):
                with self.assertRaises(SystemExit) as raised:
                    fn()
                self.assertIn(name, str(raised.exception))
            with self.assertRaises(SystemExit) as raised:
                required_env('REMOTE_SSH_HOST')
            self.assertIn('REMOTE_SSH_HOST', str(raised.exception))

    def test_client_reads_new_and_legacy_credentials(self):
        with patch.dict(os.environ, {'APEXIN_API_KEY': 'legacy-key', 'APEXIN_BASE_URL': 'https://legacy.example/v1/'}, clear=True), \
                patch.object(live2d_pipeline, 'load_env'), patch.object(live2d_pipeline, 'OpenAI') as openai:
            live2d_pipeline.client()
            self.assertEqual(openai.call_args.kwargs['api_key'], 'legacy-key')
            self.assertEqual(openai.call_args.kwargs['base_url'], 'https://legacy.example/v1')
        with patch.dict(os.environ, {'LLM_API_KEY': 'new-key', 'APEXIN_API_KEY': 'legacy-key'}, clear=True), \
                patch.object(live2d_pipeline, 'load_env'), patch.object(live2d_pipeline, 'OpenAI') as openai:
            live2d_pipeline.client()
            self.assertEqual(openai.call_args.kwargs['api_key'], 'new-key')
            # No base URL configured: the SDK default applies, nothing vendor-specific is baked in.
            self.assertIsNone(openai.call_args.kwargs['base_url'])
        with patch.dict(os.environ, {}, clear=True), patch.object(live2d_pipeline, 'load_env'):
            with self.assertRaises(SystemExit) as raised:
                live2d_pipeline.client()
            self.assertIn('LLM_API_KEY', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
