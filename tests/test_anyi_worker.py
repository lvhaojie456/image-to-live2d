import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from adapters.anyi.worker import Api, collect_delivery, read_diagnosis


class AnyiWorkerTests(unittest.TestCase):
    def test_upload_uses_300_seconds_while_control_requests_keep_90(self):
        api=Api('https://queue.example','x'*40)
        with patch.object(api.opener,'open') as opened:
            opened.return_value.__enter__.return_value.read.return_value=b'{"job":null}'
            api.call('/internal/live2d/jobs/claim',{})
            self.assertEqual(opened.call_args.kwargs['timeout'],90)
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'project.zip';path.write_bytes(b'PKfixture')
                api.upload({'id':'test-job','leaseToken':'test-lease'},'project.zip',path)
            self.assertEqual(opened.call_args.kwargs['timeout'],300)

    def test_delivery_preserves_atlas_and_separates_chat_mouth_from_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            model=root/'body-motion/model'
            verification=root/'body-motion/verification'
            material=root/'cubism-ready'
            for p in [model,verification,material]: p.mkdir(parents=True,exist_ok=True)
            (root/'build.json').write_text(json.dumps({'status':'complete','stages':{'body_motion':{'motion_scale':0.66},'foreground':{'leaking':['topwear']},'visual_review':{'schemaVersion':2,'passed':True,'identityPreserved':True,'motionAdequate':True,'score':0.8,'issues':[]}}}))
            (root/'body-motion/core-report.json').write_text(json.dumps({'passed':True}))
            (verification/'sequence-checks.json').write_text(json.dumps({'passed':True,'poseCount':363,'feetMaxDisplacementPixels':0}))
            (material/'validation.json').write_text(json.dumps({'psd_roundtrip_passed':True}))
            image=Image.new('RGBA',(32,32),(150,80,80,255))
            image.save(material/'neutral.png');image.save(model/'atlas.png');image.save(verification/'body-idle.gif')
            (model/'character.moc3').write_bytes(b'MOC3fixture')
            (model/'character.cmo3').write_bytes(b'CAFFfixture')
            (model/'do-not-publish.txt').write_text('private diagnostic')
            (model/'idle.motion3.json').write_text(json.dumps({'Meta':{},'Curves':[
                {'Id':'ParamMouthOpenY','Segments':[0,0,0,1,1]},
                {'Id':'ParamBreath','Segments':[0,0,0,1,1]}]}))
            refs={'Moc':'character.moc3','Textures':['atlas.png'],
                  'Motions':{'Idle':[{'File':'idle.motion3.json'}],'BodyIdle':[{'File':'idle.motion3.json'}]}}
            manifest=model/'MotionCharacter.model3.json'
            manifest.write_text(json.dumps({'Version':3,'FileReferences':refs}))
            result=collect_delivery(root,Path(directory)/'delivery')
            self.assertEqual(result['runtime/atlas.png'].read_bytes(),(model/'atlas.png').read_bytes())
            motion=json.loads(result['runtime/idle.motion3.json'].read_text())
            self.assertEqual([c['Id'] for c in motion['Curves']],['ParamBreath'])
            self.assertEqual(motion['Meta']['TotalPointCount'],2)
            validation=json.loads(result['validation.json'].read_text())
            self.assertEqual(validation['motionScale'],0.66); self.assertEqual(validation['backgroundClipped'],['topwear'])
            self.assertEqual(validation['visualReview']['score'],0.8); self.assertTrue(validation['corePassed'])
            self.assertNotIn('runtime/character.cmo3',result)
            self.assertNotIn('runtime/do-not-publish.txt',result)
            with zipfile.ZipFile(result['project.zip']) as archive:
                self.assertIn('body-motion/model/character.cmo3',archive.namelist())
                self.assertFalse(any(n.endswith('.txt') for n in archive.namelist()))
            build=json.loads((root/'build.json').read_text())
            build['stages']['visual_review']['identityPreserved']=False
            (root/'build.json').write_text(json.dumps(build))
            with self.assertRaises(ValueError): collect_delivery(root,Path(directory)/'bad-success')
            build['status']='needs_review'
            (root/'build.json').write_text(json.dumps(build))
            handoff=collect_delivery(root,Path(directory)/'handoff')
            self.assertFalse(json.loads(handoff['validation.json'].read_text())['visualPassed'])
            refs['Textures']=['../../build.json']
            manifest.write_text(json.dumps({'Version':3,'FileReferences':refs}))
            with self.assertRaises(ValueError): collect_delivery(root,Path(directory)/'unsafe')

    def test_diagnosis_payload_is_whitelisted(self):
        with tempfile.TemporaryDirectory() as directory:
            attempt=Path(directory); (attempt/'supervisor').mkdir()
            self.assertEqual(read_diagnosis(attempt),{})
            (attempt/'supervisor/diagnosis.json').write_text(json.dumps({'diagnosisCode':'background_leak','suggestion':'regenerate_image','summary':'背景\x07并入\n人物'+'长'*300,'packet':{'paths':'/private/secret'}}))
            payload=read_diagnosis(attempt)
            self.assertEqual(set(payload),{'diagnosisCode','suggestion','summary'})
            self.assertEqual(payload['summary'][:5],'背景并入人'); self.assertEqual(len(payload['summary']),200)
            (attempt/'supervisor/diagnosis.json').write_text(json.dumps({'diagnosisCode':'made_up','suggestion':'retry'}))
            self.assertEqual(read_diagnosis(attempt),{})
            (attempt/'supervisor/diagnosis.json').write_text(json.dumps({'diagnosisCode':'rig_unstable','suggestion':'bogus','summary':7}))
            self.assertEqual(read_diagnosis(attempt),{'diagnosisCode':'rig_unstable'})

    def test_worker_requires_https_and_keeps_token_out_of_url(self):
        for url in ['http://public.example','https://user:password@example.org','https://example.org/?token=x']:
            with self.assertRaises(ValueError): Api(url,'x'*40)
        Api('http://127.0.0.1:9000','x'*40)


if __name__=='__main__': unittest.main()
