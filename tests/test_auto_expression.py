import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from auto_expression import measure_mouth, eye_patch_rect
from face_assets import feathered_patch, polygon_mask
from build_refinement_package import repair_face


class AutoExpressionTests(unittest.TestCase):
    def test_mouth_uses_aperture_instead_of_skin_rectangle(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(237,206,181))
            draw=ImageDraw.Draw(image)
            draw.ellipse((55,75,125,115),fill=(45,20,20))
            draw.rectangle((69,79,112,85),fill=(235,230,218))
            draw.ellipse((70,98,109,111),fill=(190,105,100))
            image.save(path)
            spec=measure_mouth(path,[50,65,80,60])
            mask=polygon_mask(image.size,spec['outline'])
            self.assertEqual(mask.getpixel((50,65)),0)
            self.assertEqual(mask.getpixel((90,90)),255)
            self.assertTrue(55<=spec['cavity_sample'][0]<=125)
            teeth=polygon_mask(image.size,spec['teeth'])
            tongue=polygon_mask(image.size,spec['tongue'])
            self.assertGreater(teeth.getpixel((90,82)),240)
            self.assertGreater(tongue.getpixel((90,105)),240)

    def test_textured_face_and_neutral_mouth_preserve_source_pixels(self):
        pixels=np.random.default_rng(3).integers(30,230,(40,40,4),dtype='uint8')
        pixels[...,3]=255
        face=Image.fromarray(pixels)
        preserved=repair_face(face,{'mode':'preserve_texture'})
        self.assertTrue(np.array_equal(np.array(preserved),pixels))
        mouth=feathered_patch(face,(8,20,30,32),2)
        self.assertEqual(mouth.getpixel((15,25)),face.getpixel((15,25)))
        self.assertEqual(mouth.getpixel((0,0))[3],0)

    def test_eye_region_uses_psd_position_and_padded_crop(self):
        rect=eye_patch_rect((40,10,60,20),(80,120),(10,-10,40,40),
                            (400,400),(120,120))
        self.assertEqual(rect,[60,150,340,370])


if __name__=='__main__':
    unittest.main()
