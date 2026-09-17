import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from psd_tools import PSDImage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from face_assets import register_edit, ink_layer
from build_refinement_package import partition, write_psd


class RefinementTests(unittest.TestCase):
    def test_crop_registration_preserves_feature_position(self):
        image=Image.new('RGBA',(20,20))
        ImageDraw.Draw(image).rectangle((4,6,9,11),fill=(0,0,0,255))
        result=register_edit(image,(80,120),(20,10,20,20),(120,120))
        self.assertEqual(result.getchannel('A').getbbox(),(44,16,50,22))
        padded=register_edit(image,(80,120),(20,-10,20,20),(120,120))
        self.assertEqual(padded.getchannel('A').getbbox(),(44,0,50,2))
        with self.assertRaises(ValueError):
            register_edit(image,(80,120),(80,10,20,20),(120,120))
        with self.assertRaises(ValueError):
            register_edit(image,(80,120),(float('nan'),10,20,20),(120,120))

    def test_ink_extraction_removes_skin_and_other_features(self):
        image=Image.new('RGBA',(20,20),(250,230,220,255))
        d=ImageDraw.Draw(image)
        d.line((5,8,14,8),fill=(70,40,40,255),width=2)
        d.line((2,2,17,2),fill=(30,30,30,255),width=1)
        result=ink_layer(image,(4,6,16,11),dark=100,light=170)
        self.assertEqual(result.getchannel('A').getbbox(),(5,8,15,10))
        self.assertEqual(result.getpixel((1,1))[3],0)
        self.assertEqual(result.getpixel((7,8))[3],255)

    def test_body_partition_recombines_without_gaps(self):
        rng=np.random.default_rng(2)
        pixels=rng.integers(0,256,(24,24,4),dtype='uint8')
        first,second=partition(Image.fromarray(pixels),np.indices((24,24))[1]>=12)
        first.alpha_composite(second)
        self.assertTrue(np.array_equal(np.array(first)[...,3],pixels[...,3]))
        self.assertTrue(np.array_equal(np.array(first)[...,:3][pixels[...,3]>0],pixels[...,:3][pixels[...,3]>0]))

    def test_psd_keeps_visibility_and_does_not_square_alpha(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'layers.psd'
            mouth=Image.new('RGBA',(32,32))
            ImageDraw.Draw(mouth).rectangle((8,8,14,10),fill=(180,80,90,128))
            eye=Image.new('RGBA',(32,32))
            ImageDraw.Draw(eye).line((5,5,15,5),fill=(20,20,20,255),width=2)
            write_psd({'mouth_close':mouth,'eye_close-l':eye},(32,32),path,{'eye_close-l'})
            doc=PSDImage.open(path)
            layers={x.name:x for x in doc.descendants() if not x.is_group()}
            self.assertFalse(layers['eye_close-l'].visible)
            self.assertEqual(layers['mouth_close'].topil().getchannel('A').getextrema(),(128,128))
            self.assertFalse(layers['mouth_close'].has_mask())
            self.assertEqual(doc.composite(force=True).getpixel((10,9))[3],128)


if __name__ == '__main__':
    unittest.main()
