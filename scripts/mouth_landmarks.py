"""Inner-lip outline for an open-mouth edit, from face landmarks.

Runs in its own environment because MediaPipe pins numpy 1.x and OpenCV-contrib,
which would drag the shared pipeline venv away from its numpy 2.x /
opencv-python-headless set. A missing helper is not an error: the caller falls
back to the darkness thresholds, then to the supervisor's located box.

    mouth-landmarks/venv/bin/python scripts/mouth_landmarks.py \
        --image <open-mouth edit> --model <face_landmarker.task>

Prints one JSON object on stdout. Exit 0 with {"mouth": null} when no face or no
inner lip is found; exit 2 for a missing model or image (the caller treats every
failure the same way).
"""
import argparse
import json
from pathlib import Path
import sys

# MediaPipe Face Mesh: the inner-lip loop, connected and ordered. Sixteen of the
# twenty points lie on the lip border; four (78/308 corners, 13/14 centre) are
# shared with the outer loop.
INNER_LIP_LOOP = (78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191)
# Closed-mouth expressions still measure the aperture from the edit, so the
# landmarks are only used to place the search region.
MIN_FACE_CONFIDENCE = 0.3
MIN_AREA_SHARE = 0.00002   # of the edit's pixels; below this the loop is noise


def inner_lip_polygon(image_path, model_path):
    """Normalised (x, y) inner-lip loop, or None when no face/lip was found."""
    # Inputs are validated before the runtime is imported: a missing file must be
    # reported as such (exit 2) even where MediaPipe is not installed at all.
    import numpy as np
    from PIL import Image

    if not Path(image_path).is_file():
        raise FileNotFoundError(str(image_path))
    if not Path(model_path).is_file():
        raise FileNotFoundError(str(model_path))
    with Image.open(image_path) as opened:
        rgb = np.asarray(opened.convert("RGB"))
    height, width = rgb.shape[:2]

    import mediapipe as mp
    from mediapipe.tasks import python as mpp
    from mediapipe.tasks.python import vision

    options = vision.FaceLandmarkerOptions(
        base_options=mpp.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=1,
        min_face_detection_confidence=MIN_FACE_CONFIDENCE)
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if not result.face_landmarks:
        return None
    points = result.face_landmarks[0]
    loop = [(round(points[i].x * width, 1), round(points[i].y * height, 1)) for i in INNER_LIP_LOOP]
    xs = [p[0] for p in loop]
    ys = [p[1] for p in loop]
    box = (min(xs), min(ys), max(xs), max(ys))
    if (box[2] - box[0]) * (box[3] - box[1]) < MIN_AREA_SHARE * width * height:
        return None
    if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
        return None
    return {"width": width, "height": height, "box": [round(v) for v in box],
            "polygon": [[round(x), round(y)] for x, y in loop]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    try:
        found = inner_lip_polygon(args.image, args.model)
    except FileNotFoundError as error:
        print(json.dumps({"error": f"missing input: {error.filename}"}, ensure_ascii=False))
        return 2
    except Exception as error:                      # provider/runtime failure: caller degrades
        print(json.dumps({"error": type(error).__name__}, ensure_ascii=False))
        return 0
    print(json.dumps({"mouth": found}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
