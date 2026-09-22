"""Measure expression silhouettes from edited pixels instead of rectangular crops."""
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import binary_fill_holes

ROOT = Path(__file__).resolve().parents[1]


def contour(mask, label):
    candidates, _ = cv2.findContours(mask.astype('uint8'), cv2.RETR_EXTERNAL,
                                     cv2.CHAIN_APPROX_SIMPLE)
    if not candidates:
        raise ValueError('No visible expression pixels for '+label)
    curve = max(candidates, key=cv2.contourArea)
    curve = cv2.approxPolyDP(curve, max(0.6,cv2.arcLength(curve,True)*0.004), True)
    if len(curve) < 3:
        raise ValueError('Expression silhouette is too small for '+label)
    return curve[:,0,:].tolist()


def largest_near(mask, center):
    count, ids, stats, centroids = cv2.connectedComponentsWithStats(mask.astype('uint8'))
    if count < 2:
        raise ValueError('Could not find mouth pixels in the detected region')
    choices = [i for i in range(1,count) if stats[i,cv2.CC_STAT_AREA] >= 8]
    if not choices:
        raise ValueError('Mouth artwork is too small to extract')
    best = max(choices, key=lambda i:stats[i,cv2.CC_STAT_AREA] /
               (1+np.linalg.norm(centroids[i]-center)*0.02))
    return ids == best


# Darkness cut-offs tried in order for the mouth cavity. Illustrations pass at the
# first one; realistic renders put beard shadow and skin creases below it, which
# fuses them with the cavity until the blob spans the search box.
MOUTH_DARK_THRESHOLDS = (145, 120, 100)

# A dim photo breaks every absolute cut-off: the skin around the mouth is already
# below 100, so the whole search box is "dark" and each candidate blob spans it.
# The same cut-offs applied to brightness normalised against that skin do work
# (verified on the 2026-09-22 dark photo: ring median 82 -> x2.13 -> passes at 145),
# so each cut-off is retried once against the normalised frame.
MOUTH_NORMALISE_LEVEL = 175.0      # the grey a well-lit face's cheek sits at
MOUTH_SKIN_MIN = 24.0              # below this the frame is unusable and stays as-is
# Teeth and tongue are colour thresholds that also assume a lit face; the same
# factor that fixes the cavity is applied to them.
MOUTH_TEETH_GRAY = 155
MOUTH_TONGUE_GRAY = 65
MOUTH_TONGUE_RED_RATIO = 1.30
# Cheek brightness below this (0-255) with the cavity unmeasurable is reported as
# "the photo is too dark", not "no face": see PHOTO_TOO_DARK_GRAY.
PHOTO_TOO_DARK_GRAY = 120.0
# How much of the PLANNED mouth box the landmarked inner lip may cover. Measured
# against the box, never the expanded search box: on the 2026-09-22 production
# edits the inner lip fills 22-24% of the box but only ~7% of the search box, so a
# search-box floor silently rejected every correct loop.
MOUTH_LANDMARK_MIN_SHARE = 0.05
MOUTH_LANDMARK_MAX_SHARE = 1.5


def mouth_aperture(gray, region, center, threshold, limits):
    """Fill the largest dark blob near the mouth centre at one darkness threshold.

    Returns None when the blob is degenerate or spans the search box, so the
    caller can retry with a darker cut-off.
    """
    dark = (gray<threshold)&region
    dark = cv2.morphologyEx(dark.astype('uint8'),cv2.MORPH_CLOSE,np.ones((3,3),'uint8'))
    aperture = largest_near(dark,center)
    # The dark rim can contain gaps where teeth and tongue meet a lip. Fill its
    # convex outline so those bright details remain inside the extracted mouth.
    coords = cv2.findNonZero(aperture.astype('uint8'))
    hull = cv2.convexHull(coords)
    aperture = np.zeros_like(dark,dtype='uint8')
    cv2.fillConvexPoly(aperture,hull,1)
    aperture = binary_fill_holes(aperture)
    # One pixel of the lip edge keeps the color transition without including skin corners.
    aperture = cv2.dilate(aperture.astype('uint8'),np.ones((3,3),'uint8'))>0
    _,_,aw,ah = cv2.boundingRect(aperture.astype('uint8'))
    if aw < 5 or ah < 5 or aw > limits[0]*.98 or ah > limits[1]*.98:
        return None
    return aperture


def face_brightness(path, rectangle=None):
    """Median grey of the cheek ring around the mouth, 0-255.

    The picture is what it is; this only decides whether an unmeasurable cavity
    should be reported as "no face" or as "the photo is too dark".
    """
    with Image.open(path) as image:
        gray = cv2.cvtColor(np.array(image.convert('RGB')), cv2.COLOR_RGB2GRAY)
    if rectangle is None:
        return float(np.median(gray))
    height, width = gray.shape[:2]
    x, y, w, h = rectangle
    x1, x2 = max(0, round(x - w * .35)), min(width, round(x + w * 1.35))
    y1, y2 = max(0, round(y - h * .5)), min(height, round(y + h * 1.5))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return float(np.median(gray))
    window = gray[y1:y2, x1:x2].astype('float32').copy()
    mx1, my1 = max(0, x - x1), max(0, y - y1)
    window[my1:my1 + h, mx1:mx1 + w] = np.nan      # the mouth itself is not skin
    value = float(np.nanmedian(window)) if np.isfinite(window).any() else np.nan
    return value if np.isfinite(value) else float(np.median(gray))


def normalised_gray(gray, skin):
    """The frame rescaled so a well-lit face's cheek sits at MOUTH_NORMALISE_LEVEL."""
    if not np.isfinite(skin) or skin < MOUTH_SKIN_MIN:
        return None, 1.0
    factor = MOUTH_NORMALISE_LEVEL / skin
    if 0.8 <= factor <= 1.25:
        return None, 1.0                           # already a lit frame; keep the pixels
    return np.clip(gray.astype('float32') * factor, 0, 255).astype('uint8'), round(factor, 2)


def landmark_mouth(path, rectangle, points):
    """Aperture mask from the inner-lip loop, clipped to the search region.

    `points` is the pixel-space loop returned by `scripts/mouth_landmarks.py`.
    Returns None when the loop does not overlap the search box: a wrong face
    (cartoon art, a bystander) must never move the measurement.
    """
    with Image.open(path) as image:
        height, width = image.size[1], image.size[0]
    x, y, w, h = rectangle
    x1, x2 = max(0, round(x - w * .35)), min(width, round(x + w * 1.35))
    y1, y2 = max(0, round(y - h * .5)), min(height, round(y + h * 1.5))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    loop = np.array([[int(round(px)), int(round(py))] for px, py in points], dtype='int32')
    if len(loop) < 8:
        return None
    aperture = np.zeros((height, width), dtype='uint8')
    cv2.fillConvexPoly(aperture, loop, 1)
    # A wrong face (cartoon art, a bystander) either misses the box entirely or
    # covers far more of it than a mouth can; the share is read against the box.
    inside = int(aperture[y1:y2, x1:x2].sum())
    share = inside / max(1, w * h)
    if share < MOUTH_LANDMARK_MIN_SHARE or share > MOUTH_LANDMARK_MAX_SHARE:
        return None
    aperture = cv2.dilate(aperture, np.ones((3, 3), 'uint8')) > 0   # one pixel of lip edge
    aperture[:y1] = False; aperture[y2:] = False
    aperture[:, :x1] = False; aperture[:, x2:] = False
    if not aperture.any():
        return None
    return aperture


def locate_mouth_landmarks(path, rectangle):
    """Inner-lip loop from the helper environment, or None when it cannot run.

    The helper lives in its own venv (MediaPipe pins numpy 1.x, the pipeline runs
    numpy 2.x), so absence is normal and never an error: the caller keeps the
    darkness paths. Paths come from `MOUTH_LANDMARKS_PYTHON` /
    `MOUTH_LANDMARKS_MODEL`, both defaulting under `dependencies/` in this repository.
    """
    python = os.environ.get('MOUTH_LANDMARKS_PYTHON') or str(
        ROOT / 'dependencies/mouth-landmarks/venv/bin/python')
    model = os.environ.get('MOUTH_LANDMARKS_MODEL') or str(
        ROOT / 'dependencies/mouth-landmarks/model/face_landmarker.task')
    helper = ROOT / 'scripts/mouth_landmarks.py'
    if not (Path(python).is_file() and Path(model).is_file() and helper.is_file()):
        return None
    try:
        result = subprocess.run([python, str(helper), '--image', str(path), '--model', model],
                                capture_output=True, text=True, timeout=120, check=False)
        payload = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else {}
    except Exception:                              # absent runtime, crash, timeout: darkness paths remain
        return None
    found = payload.get('mouth')
    if not found or not found.get('polygon'):
        return None
    return found


def mouth_features(gray, rgb, aperture):
    """Teeth, tongue and cavity sample inside a cavity mask, at fixed thresholds."""
    yy, xx = np.indices(aperture.shape)
    ax, ay, aw, ah = cv2.boundingRect(aperture.astype('uint8'))
    teeth = aperture & (gray > MOUTH_TEETH_GRAY) & (yy < ay + ah * .48)
    tongue = aperture & (yy > ay + ah * .45) & (gray > MOUTH_TONGUE_GRAY) & (
        rgb[..., 0].astype(float) > rgb[..., 1] * MOUTH_TONGUE_RED_RATIO)
    teeth = largest_near(teeth, (ax + aw / 2, ay + ah * .2))
    tongue = largest_near(tongue, (ax + aw / 2, ay + ah * .75))
    dark_inside = aperture & ~teeth & ~tongue
    sample_scores = np.where(dark_inside, gray, 255)
    sy, sx = np.unravel_index(sample_scores.argmin(), gray.shape)
    return {'outline': contour(aperture, 'mouth'),
            'teeth': contour(teeth, 'teeth'), 'tongue': contour(tongue, 'tongue'),
            'cavity_sample': [int(sx), int(sy)], 'lip_split_y': round(ay + ah * .35)}


def measure_mouth(path, rectangle, use_landmarks=True, use_normalise=True):
    """Find the dark aperture, enclosed teeth and the lower red tongue.

    Three attempts, in order, each keeping the first result that fits the box:
    1. the absolute cut-offs on the frame as delivered;
    2. the same cut-offs on a frame normalised against the cheek ring;
    3. the inner-lip loop from face landmarks (realistic faces only).
    `measurement` records which one produced the outline. The two optional
    strategies are separate switches so a caller (and a test) can isolate them.
    """
    with Image.open(path) as image:
        rgb = np.array(image.convert('RGB'))
    height, width = rgb.shape[:2]
    x, y, w, h = rectangle
    x1, x2 = max(0, round(x - w * .35)), min(width, round(x + w * 1.35))
    y1, y2 = max(0, round(y - h * .5)), min(height, round(y + h * 1.5))
    region = np.zeros((height, width), dtype=bool)
    region[y1:y2, x1:x2] = True
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    centre, limits = (x + w / 2, y + h / 2), (x2 - x1, y2 - y1)

    aperture = None; threshold = None; factor = 1.0; measurement = ''
    for candidate in MOUTH_DARK_THRESHOLDS:
        aperture = mouth_aperture(gray, region, centre, candidate, limits)
        if aperture is not None:
            threshold, measurement = candidate, 'dark blob in the edit'
            break
    if aperture is None and use_normalise:
        skin = face_brightness(path, rectangle)
        frame, factor = normalised_gray(gray, skin)
        if frame is not None:
            for candidate in MOUTH_DARK_THRESHOLDS:
                aperture = mouth_aperture(frame, region, centre, candidate, limits)
                if aperture is not None:
                    gray = frame
                    threshold = candidate
                    measurement = 'dark blob after normalising the photo x%.2f' % factor
                    break
    if aperture is None and use_landmarks:
        found = locate_mouth_landmarks(path, rectangle)
        if found:
            aperture = landmark_mouth(path, rectangle, found['polygon'])
            if aperture is not None:
                measurement = 'inner-lip landmarks'
                # Landmarks are trained on realistic faces; on dark ones the
                # colour thresholds still need the normalised frame.
                frame, factor = normalised_gray(gray, face_brightness(path, rectangle))
                if frame is not None:
                    gray = frame
    if aperture is None:
        raise ValueError('Mouth measurement hit the search boundary; remeasure face regions')
    spec = mouth_features(gray, rgb, aperture)
    spec['measurement'] = measurement
    spec['dark_threshold'] = threshold
    spec['brightness'] = round(face_brightness(path, rectangle), 1)
    if factor != 1.0:
        spec['normalised'] = factor
    return spec


def eye_patch_rect(layer_box, reference_size, crop, edit_size, canvas):
    """Use See-through eye positions to correct coarse full-image planning boxes."""
    rw,rh = reference_size
    scale = max(rw,rh)/canvas[0]
    px,py = (max(rw,rh)-rw)//2,(max(rw,rh)-rh)//2
    x1,y1,x2,y2 = layer_box
    x1,x2 = x1*scale-px,x2*scale-px
    y1,y2 = y1*scale-py,y2*scale-py
    ew,eh = edit_size
    w,h = x2-x1,y2-y1
    return [max(0,round((x1-w*.2-crop[0])*ew/crop[2])),
            max(0,round((y1-h*.5-crop[1])*eh/crop[3])),
            min(ew,round((x2+w*.2-crop[0])*ew/crop[2])),
            min(eh,round((y2+h*.7-crop[1])*eh/crop[3]))]
