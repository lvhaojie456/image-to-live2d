"""Measure local expression geometry and remove baked-in features from the face base."""
import cv2
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import gaussian_filter1d


def _bounds(image):
    box=image.getchannel('A').getbbox()
    if box is None: raise ValueError('Empty expression layer')
    return box


def prepare_expression_rig(layers):
    face=np.array(layers['face']).copy()
    mask=np.zeros(face.shape[:2],np.uint8)
    eyes={}
    for side in ('l','r'):
        parts=[layers[n] for n in ('eyewhite-'+side,'irides-'+side,'eyelash-'+side) if n in layers]
        if not parts: continue
        boxes=[_bounds(im) for im in parts]
        x1,y1,x2,y2=min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)
        white=layers.get('eyewhite-'+side,parts[0]);wx1,wy1,wx2,wy2=_bounds(white)
        # Local masks remove the pale eye shapes painted into the original face;
        # alpha and all unrelated pixels remain untouched.
        pad=max(2,round((x2-x1)*.10))
        mask[max(0,y1-pad):min(mask.shape[0],y2+pad),max(0,x1-pad):min(mask.shape[1],x2+pad)]=255
        closed=np.array(layers['eye_close-'+side])
        gray=cv2.cvtColor(closed[:,:,:3],cv2.COLOR_RGB2GRAY)
        points=[]
        for x in range(wx1,wx2):
            ys=np.arange(max(0,wy1-1),min(mask.shape[0],wy2+4))
            weights=np.maximum(0,130-gray[ys,x].astype(float))*(closed[ys,x,3]/255)
            if weights.sum()>1: points.append((x,float(np.average(ys,weights=weights))))
        if len(points)<3:
            points=[(wx1,(wy1+wy2)/2),(wx2-1,(wy1+wy2)/2)]
        xp,yp=np.array(points).T;xs=np.linspace(x1,x2,17)
        def fitted_curve(x,y,sample):
            center=(x1+x2)/2;scale=max(1,(x2-x1)/2)
            coefficients=np.polyfit((x-center)/scale,y,min(2,len(x)-1))
            return np.polyval(coefficients,(sample-center)/scale)
        ys=fitted_curve(xp,yp,xs)
        # Closed artwork includes a skin rectangle. Keep only its painted lid,
        # which can follow the moving upper lid without fading a second skin/eye.
        yy,xx=np.indices(mask.shape)
        center=np.interp(xx[0],xs,ys)[None,:]
        region=((xx>=wx1)&(xx<wx2)&(abs(yy-center)<=3)&(closed[:,:,3]>100)&(gray<120))
        colors=closed[:,:,:3][region]
        color=tuple(int(v) for v in np.median(colors,axis=0)) if len(colors) else (55,35,30)
        # Supersampled traced stroke removes staircase artifacts amplified by
        # the 1280px model canvas; curvature and ink colour come from the edit.
        raster=Image.new('RGBA',(face.shape[1]*4,face.shape[0]*4))
        traced_x=np.linspace(wx1-1,wx2+1,65);traced_y=fitted_curve(xp,yp,traced_x)
        ImageDraw.Draw(raster).line([(round(x*4),round(y*4)) for x,y in zip(traced_x,traced_y)],
                                  fill=(*color,255),width=max(4,round((wx2-wx1)*.047*4)),joint='curve')
        layers['eye_close-'+side]=raster.resize((face.shape[1],face.shape[0]),Image.Resampling.LANCZOS)
        lash=np.array(layers.get('eyelash-'+side,white).getchannel('A'))
        upper=[]
        for x in range(x1,x2):
            hits=np.flatnonzero(lash[:,x]>80)
            if len(hits):upper.append((x,float(hits[0]+.5)))
        if len(upper)<3:upper=[(x1,wy1),(x2,wy1)]
        upper_x,upper_y=np.array(upper).T
        tops=fitted_curve(upper_x,upper_y,xs)
        eyes[side]={'seam':[[round(float(x),3),round(float(y),3)] for x,y in zip(xs,ys)],
                    'upper':[[round(float(x),3),round(float(y),3)] for x,y in zip(xs,tops)],
                    'bounds':[x1,y1,x2,y2]}
    mouth_boxes=[_bounds(layers[n]) for n in ('mouth_close','mouth_open','lip_upper','lip_lower')]
    x1,y1,x2,y2=min(b[0] for b in mouth_boxes),min(b[1] for b in mouth_boxes),max(b[2] for b in mouth_boxes),max(b[3] for b in mouth_boxes)
    mask[max(0,y1-2):min(mask.shape[0],y2+3),max(0,x1-2):min(mask.shape[1],x2+3)]=255
    mask[face[:,:,3]<200]=0
    repaired=cv2.inpaint(face[:,:,:3],mask,5,cv2.INPAINT_TELEA)
    face[:,:,:3][mask>0]=repaired[mask>0]
    layers['face']=Image.fromarray(face)
    closed=np.array(layers['mouth_close']);cx1,cy1,cx2,cy2=_bounds(layers['mouth_close'])
    gray=cv2.cvtColor(closed[:,:,:3],cv2.COLOR_RGB2GRAY)
    points=[]
    for x in range(cx1,cx2):
        ys=np.arange(cy1,cy2);weights=np.maximum(0,140-gray[ys,x].astype(float))*(closed[ys,x,3]/255)
        if weights.sum()>1:points.append((x,float(np.average(ys,weights=weights))))
    if len(points)<3:points=[(cx1,(cy1+cy2)/2),(cx2-1,(cy1+cy2)/2)]
    xp,yp=np.array(points).T;xs=np.linspace(x1,x2,17);ys=gaussian_filter1d(np.interp(xs,xp,yp),1)
    # Keep the real neutral lips throughout speech. Split their artwork at the
    # measured seam; the rig moves each lip to the cavity boundary without
    # flattening its texture or fading away the person's painted lip shape.
    yy,xx=np.indices(mask.shape);seam=np.interp(xx[0],xs,ys)[None,:]
    top=closed.copy();bottom=closed.copy()
    top[:,:,3][yy>seam]=0;bottom[:,:,3][yy<=seam]=0
    top[top[:,:,3]==0,:3]=0;bottom[bottom[:,:,3]==0,:3]=0
    layers['lip_upper']=Image.fromarray(top);layers['lip_lower']=Image.fromarray(bottom)
    aperture=np.array(layers['mouth_open'].getchannel('A'))
    upper=[];lower=[]
    for x in range(x1,x2):
        hits=np.flatnonzero(aperture[:,x]>100)
        if len(hits):upper.append((x,float(hits[0])));lower.append((x,float(hits[-1])))
    if len(upper)<2:raise ValueError('Cannot measure continuous mouth boundary')
    ax,ay=np.array(upper).T;bx,by=np.array(lower).T
    tops=np.interp(xs,ax,ay);bottoms=np.interp(xs,bx,by)
    return {'version':1,'eyes':eyes,'mouth':{'bounds':[x1,y1,x2,y2],
            'seam':[[round(float(x),3),round(float(y),3)] for x,y in zip(xs,ys)],
            'upper':[[round(float(x),3),round(float(y),3)] for x,y in zip(xs,tops)],
            'lower':[[round(float(x),3),round(float(y),3)] for x,y in zip(xs,bottoms)],'neutral_lips':True},
            'face_pixels_repaired':int((mask>0).sum())}
