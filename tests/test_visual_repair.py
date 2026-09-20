import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from supervisor import Supervisor
from visual_repair import normalize_review, repair_loop, actions_for


def judgment(code=None, severity='major', score=.7, identity=True, motion=True):
    return normalize_review({'schemaVersion': 2, 'identityPreserved': identity, 'motionAdequate': motion,
        'score': score, 'issues': [] if code is None else [
            {'code': code, 'severity': severity, 'part': 'eyes', 'detail': '闭眼仍露眼球'}]})


class VisualRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sup = Supervisor(self.root, state_path=self.root/'state.json', mode='act', model='test-model')
        self.initial = {'directory': self.root/'round-00'}

    def run_loop(self, reviews, failure=False):
        seen = []; iterator = iter(reviews)
        def repair(best, review, actions, number):
            seen.append((best['directory'].name, actions))
            if failure: raise ValueError('bad edit')
            return {'directory': self.root/f'round-{number:02d}'}
        selected, result = repair_loop(self.initial, lambda *_: next(iterator), repair, self.sup, self.root/'review.json')
        return selected, result, seen

    def test_serious_defects_block_despite_high_score_and_minor_notes_can_pass(self):
        self.assertFalse(judgment('eye_residual', score=.99)['passed'])
        self.assertFalse(judgment(identity=False)['passed'])
        self.assertFalse(judgment(motion=False)['passed'])
        self.assertTrue(judgment('eye_seam', 'minor')['passed'])
        self.assertEqual(actions_for(judgment('mouth_artifact')), ['mouth'])
        for payload in [{}, {'schemaVersion': 2}, {'schemaVersion': 2, 'identityPreserved': 'true'}]:
            with self.assertRaises(ValueError): normalize_review(payload)
        payload=judgment(); payload['issues']=[{'code':'run_shell','severity':'major','part':'eyes','detail':'x'}]
        with self.assertRaises(ValueError): normalize_review(payload)

    def test_repairs_only_affected_part_then_rechecks_and_publishes(self):
        selected, result, seen = self.run_loop([judgment('eye_residual'), judgment('eye_seam','minor',.82)])
        self.assertEqual(seen, [('round-00', ['eyes'])])
        self.assertEqual(selected['directory'].name, 'round-01')
        self.assertTrue(result['passed']); self.assertEqual(result['reason'], 'passed')

    def test_identity_regression_rolls_back_even_when_score_increases(self):
        selected, result, seen = self.run_loop([judgment('eye_residual'), judgment(score=.99,identity=False), judgment(score=.98,identity=False)])
        self.assertEqual(selected, self.initial)
        self.assertFalse(result['passed']); self.assertEqual(result['reason'], 'no_improvement')
        self.assertEqual([p[0] for p in seen], ['round-00','round-00'])

    def test_identity_and_motion_are_protected_even_if_many_other_issues_disappear(self):
        original=judgment('eye_residual');original['issues']*=12
        for replacement in [judgment(identity=False,score=.99),judgment(motion=False,score=.99)]:
            self.sup.state={'used':{},'history':[]}
            selected,result,_=self.run_loop([original,replacement,replacement])
            self.assertEqual(selected,self.initial);self.assertFalse(result['passed'])

    def test_corrupt_budget_does_not_reset_the_repair_allowance(self):
        (self.root/'state.json').write_text('{broken')
        with self.assertRaises(ValueError): Supervisor(self.root,state_path=self.root/'state.json',mode='act')

    def test_three_round_cap_is_shared_across_attempts(self):
        selected, result, seen = self.run_loop([judgment('eye_residual',score=s) for s in [.5,.6,.7,.8]])
        self.assertEqual(len(seen),3); self.assertFalse(result['passed'])
        again = Supervisor(self.root/'next', state_path=self.root/'state.json', mode='act', model='test-model')
        self.assertFalse(again.can('visual_repairs'))
        self.assertEqual(selected['directory'].name,'round-03')

    def test_missing_review_or_disabled_repairs_never_pass(self):
        _,result,seen=self.run_loop([None]); self.assertFalse(result['passed']); self.assertEqual(seen,[])
        self.sup.mode='shadow'
        _,result,seen=self.run_loop([judgment('eye_residual')]); self.assertFalse(result['passed']); self.assertEqual(seen,[])
        self.assertEqual(result['reason'],'repairs_disabled')

    def test_failed_rebuild_keeps_downloadable_previous_candidate(self):
        selected,result,seen=self.run_loop([judgment('mouth_artifact')],failure=True)
        self.assertEqual(selected,self.initial); self.assertFalse(result['passed']); self.assertEqual(len(seen),2)
        self.assertEqual(json.loads((self.root/'review.json').read_text())['rounds'][1]['error'],'ValueError')


if __name__ == '__main__': unittest.main()
