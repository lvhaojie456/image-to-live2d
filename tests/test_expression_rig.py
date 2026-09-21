from pathlib import Path
import sys
import unittest

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from expression_rig import prepare_expression_rig


class ExpressionRigTests(unittest.TestCase):
    def test_clean_base_preserves_alpha_and_pixels_away_from_features(self):
        face=Image.new('RGBA',(96,96),(205,170,145,255))
        draw=ImageDraw.Draw(face)
        draw.ellipse((24,32,37,39),fill=(245,241,233,255))
        draw.ellipse((55,32,68,39),fill=(245,241,233,255))
        draw.line((40,60,56,60),fill=(80,40,35,255),width=2)
        layers={'face':face}
        for side,x in [('r',24),('l',55)]:
            for name,color in [('eyewhite',(245,241,233,255)),('eyelash',(40,25,20,255)),('irides',(60,35,20,255))]:
                im=Image.new('RGBA',face.size);ImageDraw.Draw(im).ellipse((x,32,x+13,39),fill=color);layers[name+'-'+side]=im
            im=Image.new('RGBA',face.size);ImageDraw.Draw(im).line((x,38,x+13,38),fill=(45,25,20,255));layers['eye_close-'+side]=im
        for name in ['mouth_close','mouth_open','lip_upper','lip_lower']:
            im=Image.new('RGBA',face.size);ImageDraw.Draw(im).rectangle((40,58,56,63),fill=(70,35,30,255));layers[name]=im
        before=np.array(face)
        neutral_lips=np.array(layers['mouth_close'])
        rig=prepare_expression_rig(layers);after=np.array(layers['face'])
        self.assertTrue(np.array_equal(before[:,:,3],after[:,:,3]))
        self.assertTrue(np.array_equal(before[:20],after[:20]))
        self.assertTrue(np.array_equal(before[72:],after[72:]))
        self.assertLess(float(after[35,30,:3].mean()),float(before[35,30,:3].mean()))
        self.assertGreater(float(after[60,45,:3].mean()),float(before[60,45,:3].mean()))
        self.assertEqual(set(rig['eyes']),{'l','r'})
        self.assertTrue(all(35<y<40 for _,y in rig['eyes']['l']['seam']))
        upper=np.array(layers['lip_upper']);lower=np.array(layers['lip_lower'])
        self.assertTrue(np.array_equal(upper[:,:,3].astype(int)+lower[:,:,3],neutral_lips[:,:,3]))
        self.assertFalse(np.any((upper[:,:,3]>0)&(lower[:,:,3]>0)))
        self.assertTrue(rig['mouth']['neutral_lips'])


if __name__=='__main__':unittest.main()
