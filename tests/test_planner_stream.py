import json
from argparse import Namespace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import live2d_pipeline


class Stream:
    def __init__(self, finish): self.finish=finish
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def __iter__(self):
        text=json.dumps({'character_summary':'test','layers':[],'repair_tasks':[],
                         'parameters':[],'physics':[],'acceptance_poses':[],'risks':[]})
        for value in [text[:20],text[20:]]:
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=value),finish_reason=None)])
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None),finish_reason=self.finish)])


class PlannerStreamTests(unittest.TestCase):
    def test_accepts_only_a_completed_stream(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            image=root/'input.png';Image.new('RGB',(32,32)).save(image)
            with patch.object(live2d_pipeline,'client') as client:
                client.return_value.chat.completions.create.return_value=Stream('stop')
                live2d_pipeline.plan(Namespace(image=str(image),output=str(root/'ok')))
                self.assertTrue((root/'ok/layer_plan.json').is_file())
                self.assertTrue(client.return_value.chat.completions.create.call_args.kwargs['stream'])
                for finish in ['length',None,'content_filter']:
                    client.return_value.chat.completions.create.return_value=Stream(finish)
                    output=root/str(finish)
                    with self.assertRaises(RuntimeError):
                        live2d_pipeline.plan(Namespace(image=str(image),output=str(output)))
                    self.assertFalse((output/'layer_plan.json').exists())


if __name__=='__main__': unittest.main()
