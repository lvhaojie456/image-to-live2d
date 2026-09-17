import copy
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from body_motion import PARAMETERS,BODY_PARAMETERS,EXPRESSION_PARAMETERS,motion_document,validate_recipe
from verify_body_motion import sample_motion


class BodyMotionTests(unittest.TestCase):
    def test_motion3_round_trip_loop_and_bounds(self):
        doc=motion_document(8,30,PARAMETERS)
        self.assertEqual(doc['Meta']['CurveCount'],9)
        self.assertEqual(doc['Meta']['TotalSegmentCount'],9*240)
        for c in doc['Curves']:
            self.assertAlmostEqual(c['Segments'][1],c['Segments'][-1],places=7)
            self.assertEqual(c['Segments'][-2],8)
        for i in range(481):
            values=sample_motion(doc,i/60)
            self.assertTrue(0<=values['ParamBreath']<=1)
            self.assertTrue(0<=values['ParamEyeLOpen']<=1)
            self.assertTrue(0<=values['ParamEyeROpen']<=1)
            self.assertTrue(0<=values['ParamMouthOpenY']<=1)
            self.assertTrue(-10<=values['ParamBodyAngleZ']<=10)
            for p in PARAMETERS[2:]:self.assertTrue(-1<=values[p]<=1)
        self.assertEqual(sample_motion(doc,0),sample_motion(doc,8))

    def test_isolated_motion_does_not_drive_other_parameters(self):
        doc=motion_document(8,30,['ParamSkirtSwing'])
        self.assertEqual(set(sample_motion(doc,1)),{'ParamSkirtSwing'})

    def test_bad_falloff_and_nonfinite_recipe_fail(self):
        data=json.loads((ROOT/'recipes/e2e-body-motion.json').read_text())
        validate_recipe(data)
        invalid=copy.deepcopy(data);invalid['arms']['l']['free_y']=invalid['arms']['l']['pin_y']
        with self.assertRaises(ValueError):validate_recipe(invalid)
        invalid=copy.deepcopy(data);invalid['body']['lean_degrees']=float('nan')
        with self.assertRaises(ValueError):validate_recipe(invalid)
        invalid=copy.deepcopy(data);invalid['skirt']['hem_y']=invalid['skirt']['pin_y']-1
        with self.assertRaises(ValueError):validate_recipe(invalid)


if __name__=='__main__':unittest.main()
