import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from anyi_worker import Api, collect_delivery


class AnyiWorkerTests(unittest.TestCase):
    def test_delivery_preserves_atlas_and_separates_chat_mouth_from_idle(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'source'
            model=root/'body-motion/model'
            verification=root/'body-motion/verification'
            material=root/'cubism-ready'
            for p in [model,verification,material]: p.mkdir(parents=True,exist_ok=True)
            (root/'build.json').write_text(json.dumps({'status':'complete'}))
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
            self.assertNotIn('runtime/character.cmo3',result)
            self.assertNotIn('runtime/do-not-publish.txt',result)
            with zipfile.ZipFile(result['project.zip']) as archive:
                self.assertIn('body-motion/model/character.cmo3',archive.namelist())
                self.assertFalse(any(n.endswith('.txt') for n in archive.namelist()))
            refs['Textures']=['../../build.json']
            manifest.write_text(json.dumps({'Version':3,'FileReferences':refs}))
            with self.assertRaises(ValueError): collect_delivery(root,Path(directory)/'unsafe')

    def test_worker_requires_https_and_keeps_token_out_of_url(self):
        for url in ['http://public.example','https://user:password@example.org','https://example.org/?token=x']:
            with self.assertRaises(ValueError): Api(url,'x'*40)
        Api('http://127.0.0.1:9000','x'*40)


if __name__=='__main__': unittest.main()
