import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import auto_expression
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
            self.assertEqual(spec['dark_threshold'],145)
            mask=polygon_mask(image.size,spec['outline'])
            self.assertEqual(mask.getpixel((50,65)),0)
            self.assertEqual(mask.getpixel((90,90)),255)
            self.assertTrue(55<=spec['cavity_sample'][0]<=125)
            teeth=polygon_mask(image.size,spec['teeth'])
            tongue=polygon_mask(image.size,spec['tongue'])
            self.assertGreater(teeth.getpixel((90,82)),240)
            self.assertGreater(tongue.getpixel((90,105)),240)

    def test_mouth_falls_back_to_a_darker_threshold_when_shadow_spans_the_search_box(self):
        # Realistic renders: beard shadow (gray 130) fills the whole search box below the
        # lenient 145 cut-off, so only the darker cut-off isolates the cavity.
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(237,206,181))
            draw=ImageDraw.Draw(image)
            draw.rectangle((10,25,170,165),fill=(130,130,130))
            draw.ellipse((55,75,125,115),fill=(45,20,20))
            draw.rectangle((69,79,112,85),fill=(235,230,218))
            draw.ellipse((70,98,109,111),fill=(190,105,100))
            image.save(path)
            spec=measure_mouth(path,[50,65,80,60])
            self.assertEqual(spec['dark_threshold'],120)
            mask=polygon_mask(image.size,spec['outline'])
            self.assertEqual(mask.getpixel((30,40)),0)
            self.assertEqual(mask.getpixel((90,90)),255)
            self.assertGreater(polygon_mask(image.size,spec['teeth']).getpixel((90,82)),240)
            self.assertGreater(polygon_mask(image.size,spec['tongue']).getpixel((90,105)),240)

    def test_mouth_reports_the_boundary_when_no_threshold_isolates_a_cavity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(237,206,181))
            ImageDraw.Draw(image).rectangle((10,25,170,165),fill=(20,10,10))
            image.save(path)
            with self.assertRaisesRegex(ValueError,'search boundary'):
                measure_mouth(path,[50,65,80,60])

    def test_dim_photo_is_measured_after_normalising_against_the_cheek(self):
        # The 2026-09-22 production failure: a dim photo where the skin around the
        # mouth already sits below every cut-off, so each candidate blob spans the
        # search box. Normalising the frame against that skin recovers the cavity.
        with tempfile.TemporaryDirectory() as temporary:
            bright=Image.new('RGB',(180,180),(237,206,181))
            draw=ImageDraw.Draw(bright)
            draw.ellipse((55,75,125,115),fill=(45,20,20))
            draw.rectangle((69,79,112,85),fill=(235,230,218))
            draw.ellipse((70,98,109,111),fill=(190,105,100))
            dim=bright.point(lambda value:int(value*0.42))
            path=Path(temporary)/'dim.png';dim.save(path)
            spec=measure_mouth(path,[50,65,80,60],use_landmarks=False)
            self.assertIn('normalis',spec['measurement'])
            self.assertLess(spec['brightness'],120)
            mask=polygon_mask(dim.size,spec['outline'])
            self.assertEqual(mask.getpixel((90,90)),255)
            self.assertGreater(polygon_mask(dim.size,spec['teeth']).getpixel((90,82)),240)

    def test_a_realistically_sized_landmark_loop_is_accepted(self):
        # Regression: the guard used to measure the loop against the expanded search
        # box, where a correct inner lip is only ~7% — every real loop was rejected.
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(20,10,10))
            draw=ImageDraw.Draw(image)
            draw.rectangle((70,78,112,84),fill=(235,230,218))
            draw.ellipse((72,96,110,112),fill=(150,80,70))
            image.save(path)
            rectangle=[50,65,80,60]                     # 4800 px; the loop below is ~1400 px (29%)
            loop=[(62,78),(90,72),(118,78),(120,96),(118,114),(90,120),(62,114),(60,96)]
            with patch.object(auto_expression,'locate_mouth_landmarks',return_value={'polygon':loop}):
                spec=measure_mouth(path,rectangle)
            self.assertEqual(spec['measurement'],'inner-lip landmarks')
            self.assertEqual(polygon_mask(image.size,spec['outline']).getpixel((90,100)),255)

    def test_a_landmark_loop_spanning_the_whole_box_is_rejected(self):
        # A loop that covers the search box is a region, not a cavity: fall through.
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(237,206,181))
            ImageDraw.Draw(image).rectangle((10,25,170,165),fill=(20,10,10))
            image.save(path)
            huge=[(0,0),(179,0),(179,179),(0,179)]
            with patch.object(auto_expression,'locate_mouth_landmarks',return_value={'polygon':huge}):
                with self.assertRaisesRegex(ValueError,'search boundary'):
                    measure_mouth(path,[50,65,80,60])

    def test_a_landmark_loop_inside_the_search_box_supplies_the_cavity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(20,10,10))
            draw=ImageDraw.Draw(image)
            draw.rectangle((70,78,112,84),fill=(235,230,218))
            draw.ellipse((72,96,110,112),fill=(150,80,70))
            image.save(path)
            loop=[(58,76),(90,70),(122,76),(126,96),(122,116),(90,124),(58,116),(54,96)]
            with patch.object(auto_expression,'locate_mouth_landmarks',return_value={'polygon':loop}):
                spec=measure_mouth(path,[50,65,80,60])
            self.assertEqual(spec['measurement'],'inner-lip landmarks')
            mask=polygon_mask(image.size,spec['outline'])
            self.assertEqual(mask.getpixel((90,100)),255)
            self.assertEqual(mask.getpixel((5,5)),0)

    def test_a_landmark_loop_outside_the_search_box_is_ignored(self):
        # A cartoon face (or a bystander) yields a loop somewhere else on the canvas; the
        # measurement must not follow it.
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(237,206,181))
            ImageDraw.Draw(image).rectangle((10,25,170,165),fill=(20,10,10))
            image.save(path)
            far=[(600,600),(660,600),(660,650),(600,650)]
            with patch.object(auto_expression,'locate_mouth_landmarks',return_value={'polygon':far}):
                with self.assertRaisesRegex(ValueError,'search boundary'):
                    measure_mouth(path,[50,65,80,60])

    def test_face_brightness_reads_the_skin_not_the_mouth(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'mouth.png'
            image=Image.new('RGB',(180,180),(200,200,200))       # bright skin
            ImageDraw.Draw(image).ellipse((55,75,125,115),fill=(10,10,10))
            image.save(path)
            self.assertGreater(auto_expression.face_brightness(path,[50,65,80,60]),150)
            self.assertEqual(auto_expression.face_brightness(path),float(np.median(np.array(image.convert('L')))))

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
