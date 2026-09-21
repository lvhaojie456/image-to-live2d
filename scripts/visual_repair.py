"""Bounded repairs of exported models. Model advice never becomes executable code."""
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw


ISSUE_ACTIONS = {
    'eye_residual': 'eyes', 'eye_seam': 'eyes',
    'mouth_artifact': 'mouth', 'mouth_shape': 'mouth',
    'identity_drift': 'identity', 'background_leak': 'layers',
    'layer_order': 'layers', 'joint_gap': 'rig', 'motion_weak': 'rig',
    'other': 'manual',
}
SEVERITIES = {'minor': 0, 'major': 2, 'critical': 4}
PARTS = {'eyes', 'mouth', 'head', 'arms', 'body', 'garment', 'background', 'other'}
MAX_REPAIR_ROUNDS = 3


def normalize_review(answer):
    """Fail closed on incomplete or contradictory judgments; scores alone never pass."""
    if not isinstance(answer, dict) or answer.get('schemaVersion') != 2:
        raise ValueError('Missing visual review schema')
    for key in ('identityPreserved', 'motionAdequate'):
        if type(answer.get(key)) is not bool:
            raise ValueError('Invalid visual review check: ' + key)
    score = answer.get('score')
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('Invalid visual score')
    issues = answer.get('issues')
    if not isinstance(issues, list) or len(issues) > 12:
        raise ValueError('Invalid visual issues')
    clean = []
    for issue in issues:
        if not isinstance(issue, dict) or issue.get('code') not in ISSUE_ACTIONS or issue.get('severity') not in SEVERITIES or issue.get('part') not in PARTS:
            raise ValueError('Unknown visual repair instruction')
        detail = issue.get('detail')
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError('Missing visual issue detail')
        clean.append({k: issue[k] for k in ('code', 'severity', 'part')} | {
            'detail': ''.join(c for c in detail if c >= ' ')[:200]})
    passed = answer['identityPreserved'] and answer['motionAdequate'] and not any(SEVERITIES[i['severity']] for i in clean)
    return {'schemaVersion': 2, 'passed': passed, 'identityPreserved': answer['identityPreserved'],
            'motionAdequate': answer['motionAdequate'], 'score': round(score, 3), 'issues': clean,
            'explanation': str(answer.get('explanation', ''))[:200]}


def rank(review):
    if review is None:
        return (2, 100, 0)
    severity = sum(SEVERITIES[i['severity']] for i in review['issues'])
    severity += 20 if not review['identityPreserved'] else 0
    severity += 4 if not review['motionAdequate'] else 0
    return (0 if review['passed'] else 1, severity, -review['score'])


def actions_for(review):
    actions = {ISSUE_ACTIONS[i['code']] for i in review['issues'] if SEVERITIES[i['severity']]}
    if not review['identityPreserved']:
        actions.add('identity')
    if not review['motionAdequate']:
        actions.add('rig')
    # Unsupported defects stay blocking and are described in the manual handoff.
    return sorted(actions - {'manual'})


def repair_loop(initial, review, repair, supervisor, report_path):
    """Callbacks operate on separate round directories. Always retain the best valid model."""
    best = initial
    best_review = review(initial, 0)
    history = [{'round': 0, 'candidate': initial['directory'].name, 'review': best_review, 'selected': True}]
    stagnant = 0
    reason = 'passed' if best_review and best_review['passed'] else 'review_unavailable'
    for number in range(1, MAX_REPAIR_ROUNDS + 1):
        if best_review is None or best_review['passed']:
            break
        actions = actions_for(best_review)
        if not actions:
            reason = 'manual_action_required'; break
        if supervisor.mode != 'act':
            reason = 'repairs_disabled'; break
        if not supervisor.consume('visual_repairs', ','.join(actions)):
            reason = 'repair_budget_exhausted'; break
        try:
            candidate = repair(best, best_review, actions, number)
            judgment = review(candidate, number)
            protected = judgment is not None and all(
                not best_review[key] or judgment[key] for key in ('identityPreserved', 'motionAdequate'))
            selected = protected and rank(judgment) < rank(best_review)
            record = {'round': number, 'candidate': candidate['directory'].name, 'actions': actions,
                      'review': judgment, 'selected': selected}
            if selected:
                best, best_review = candidate, judgment
                stagnant = 0
            else:
                stagnant += 1
        except Exception as error:
            # Keep the prior structure-checked model when an edit or rebind fails.
            record = {'round': number, 'actions': actions, 'error': type(error).__name__, 'selected': False}
            stagnant += 1
        history.append(record)
        reason = 'passed' if best_review and best_review['passed'] else 'repair_budget_exhausted'
        if best_review and best_review['passed']:
            break
        if stagnant >= 2:
            reason = 'no_improvement'; break
    result = {'schemaVersion': 2, 'passed': bool(best_review and best_review['passed']),
              'selectedRound': best['directory'].name, 'reason': reason,
              'rounds': history, 'review': best_review}
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return best, result


def evidence_images(candidate, original, regions):
    """Evidence comes from the exported MOC, including exact expressions and consecutive frames."""
    directory = candidate['directory']
    verification = directory / 'body-motion/verification'
    destination = directory / 'visual-evidence'
    destination.mkdir(exist_ok=True)
    manifest = json.loads((directory / 'cubism-ready/authoring-manifest.json').read_text())
    with Image.open(verification / 'neutral.png') as image:
        width, height = image.size
    head_boxes=[layer['bbox'] for layer in manifest['layers'] if layer['name'] in {'face','front hair','back hair','ears-l','ears-r'}]
    face=[min(b[0] for b in head_boxes),min(b[1] for b in head_boxes),max(b[2] for b in head_boxes),max(b[3] for b in head_boxes)]
    sx, sy = width / manifest['width'], height / manifest['height']
    x1, y1, x2, y2 = face
    pad = max(x2 - x1, y2 - y1) * .2
    face_box = (max(0, int((x1-pad)*sx)), max(0, int((y1-pad)*sy)),
                min(width, int((x2+pad)*sx)), min(height, int((y2+pad)*sy)))

    def sheet(names, filename, crop=None, columns=4):
        result = Image.new('RGB', (columns*320, math.ceil(len(names)/columns)*350), '#e7eef2')
        for index, name in enumerate(names):
            with Image.open(verification / (name + '.png')) as opened:
                im = opened.convert('RGBA')
            bounds = crop or im.getchannel('A').getbbox()
            if not bounds:
                raise ValueError('Exported model rendered empty')
            im = im.crop(bounds); im.thumbnail((308, 310), Image.Resampling.LANCZOS)
            x, y = (index % columns)*320, (index // columns)*350
            result.paste(im, (x+(320-im.width)//2, y), im)
            ImageDraw.Draw(result).text((x+5, y+320), name, fill='black')
        path = destination / filename; result.save(path); return path

    with Image.open(original) as image:
        x, y, w, h = regions['face']
        image.convert('RGB').crop((max(0, x-w*.15), max(0, y-h*.15),
                                  min(image.width, x+w*1.15), min(image.height, y+h*1.15))).save(destination/'reference.png')
    expressions = ['neutral', 'eyes_closed', 'left_closed', 'right_closed',
                   'mouth_half', 'mouth_open', 'mouth_smile', 'closed_open']
    poses = ['neutral', 'ParamBodyAngleZ_min', 'ParamBodyAngleZ_max', 'ParamBreath_max',
             'ParamArmLSwing_min', 'ParamArmLSwing_max', 'ParamArmRSwing_max', 'ParamSkirtSwing_max']
    # One fixed viewport per sequence. Individually recentering each silhouette
    # concealed the very translations and leaning that this gate should inspect.
    sequence=[f'frame_{i:03d}' for i in range(16,24)]
    boxes=[]
    for name in poses+sequence:
        with Image.open(verification/(name+'.png')) as image:
            bounds=image.convert('RGBA').getchannel('A').getbbox()
            if bounds:boxes.append(bounds)
    body_box=(max(0,min(b[0] for b in boxes)-10),max(0,min(b[1] for b in boxes)-10),
              min(width,max(b[2] for b in boxes)+10),min(height,max(b[3] for b in boxes)+10))
    evidence = [('reference_identity', destination/'reference.png'),
                ('exported_expression_states', sheet(expressions, 'expressions.png', face_box)),
                ('exported_body_extremes', sheet(poses, 'poses.png',body_box)),
                ('exported_consecutive_body_frames', sheet(sequence, 'sequence.png',body_box)),
                ('exported_consecutive_face_frames', sheet([f'frame_{i:03d}' for i in range(16, 24)], 'face-sequence.png', face_box))]
    if (verification/'eye_sweep_000.png').is_file():
        evidence += [('slow_eye_transition',sheet([f'eye_sweep_{i:03d}' for i in [0,10,15,20,23,26,28,30]],'eye-sweep.png',face_box)),
                     ('slow_mouth_transition',sheet([f'mouth_sweep_{i:03d}' for i in [0,5,10,15,20,25,28,30]],'mouth-sweep.png',face_box))]
    moc = directory / 'body-motion/model/MotionCharacter.moc3'
    hashes = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest() for _, p in evidence}
    hashes[str(moc.relative_to(directory))] = hashlib.sha256(moc.read_bytes()).hexdigest()
    (destination/'hashes.json').write_text(json.dumps(hashes, indent=2))
    return evidence
