"""Measure expression silhouettes from edited pixels instead of rectangular crops."""
import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import binary_fill_holes


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


def measure_mouth(path, rectangle):
    """Find the dark aperture, enclosed teeth and the lower red tongue."""
    with Image.open(path) as image:
        rgb = np.array(image.convert('RGB'))
    height,width = rgb.shape[:2]
    x,y,w,h = rectangle
    x1,x2 = max(0,round(x-w*.35)),min(width,round(x+w*1.35))
    y1,y2 = max(0,round(y-h*.5)),min(height,round(y+h*1.5))
    region = np.zeros((height,width),dtype=bool)
    region[y1:y2,x1:x2] = True
    gray = cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    for threshold in MOUTH_DARK_THRESHOLDS:
        aperture = mouth_aperture(gray,region,(x+w/2,y+h/2),threshold,(x2-x1,y2-y1))
        if aperture is not None:
            break
    else:
        raise ValueError('Mouth measurement hit the search boundary; remeasure face regions')
    yy,xx = np.indices(aperture.shape)
    ax,ay,aw,ah = cv2.boundingRect(aperture.astype('uint8'))
    teeth = aperture & (gray>155) & (yy<ay+ah*.48)
    tongue = aperture & (yy>ay+ah*.45) & (gray>65) & (
        rgb[...,0].astype(float)>rgb[...,1]*1.30)
    teeth = largest_near(teeth,(ax+aw/2,ay+ah*.2))
    tongue = largest_near(tongue,(ax+aw/2,ay+ah*.75))
    dark_inside = aperture & ~teeth & ~tongue
    sample_scores = np.where(dark_inside,gray,255)
    sy,sx = np.unravel_index(sample_scores.argmin(),gray.shape)
    return {'outline':contour(aperture,'mouth'),
            'teeth':contour(teeth,'teeth'),'tongue':contour(tongue,'tongue'),
            'cavity_sample':[int(sx),int(sy)],'lip_split_y':round(ay+ah*.35),
            'measurement':'connected aperture and color components in the actual AI edit',
            'dark_threshold':threshold}


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
