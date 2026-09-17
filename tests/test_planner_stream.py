import json
import os
from argparse import Namespace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
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


class BrokenStream:
    """The peer closes the chunked body after the first delta, as seen through a proxy."""
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def __iter__(self):
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content='{"charac'),finish_reason=None)])
        raise httpx.RemoteProtocolError('peer closed connection without sending complete message body (incomplete chunked read)')


class PlannerStreamRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.image=self.root/'input.png'; Image.new('RGB',(32,32)).save(self.image)
        self.addCleanup(patch.stopall)
        self.create=patch.object(live2d_pipeline,'client').start().return_value.chat.completions.create
        self.sleep=patch.object(live2d_pipeline.time,'sleep').start()

    def plan(self,name):
        live2d_pipeline.plan(Namespace(image=str(self.image),output=str(self.root/name)))

    def test_reissues_the_same_request_once_after_a_transport_drop(self):
        self.create.side_effect=[BrokenStream(),Stream('stop')]
        self.plan('ok')
        self.assertTrue((self.root/'ok/layer_plan.json').is_file())
        self.assertEqual(self.create.call_count,2)
        first,second=self.create.call_args_list
        self.assertEqual(first.kwargs['messages'],second.kwargs['messages'])
        self.sleep.assert_called_once_with(5)

    def test_gives_up_after_the_configured_retries(self):
        self.create.side_effect=[BrokenStream(),BrokenStream()]
        with self.assertRaises(httpx.RemoteProtocolError):
            self.plan('twice')
        self.assertEqual(self.create.call_count,2)
        self.assertFalse((self.root/'twice/layer_plan.json').exists())
        self.create.reset_mock(); self.create.side_effect=[BrokenStream()]
        with patch.dict(os.environ,{'ASTRA_STREAM_RETRIES':'0'}), self.assertRaises(httpx.RemoteProtocolError):
            self.plan('none')
        self.assertEqual(self.create.call_count,1)

    def test_does_not_retry_a_stream_that_ended_without_stop(self):
        self.create.return_value=Stream('length')
        with self.assertRaises(RuntimeError):
            self.plan('length')
        self.assertEqual(self.create.call_count,1)
        self.sleep.assert_not_called()


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
