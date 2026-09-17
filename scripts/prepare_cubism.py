#!/usr/bin/env python3
"""Rebuild a measured Cubism materials package without repeating paid image requests."""
import argparse
from pathlib import Path
import shutil
import tempfile

from face_assets import build_face_assets
from build_refinement_package import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['base','source-psd','mouth','eyes','recipe','output']:
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--body-motion',action='store_true',help='Export editable body keyforms and verify the idle loop')
    parser.add_argument('--motion-recipe',type=Path)
    parser.add_argument('--java-home',type=Path)
    args=parser.parse_args()
    if args.body_motion and (args.motion_recipe is None or args.java_home is None):
        parser.error('--body-motion requires --motion-recipe and --java-home')
    output=args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise SystemExit('Choose a new, empty output directory')
    with tempfile.TemporaryDirectory(prefix='live2d-face-') as temporary:
        face=Path(temporary)/'face_assets'
        build_face_assets(args.base,args.mouth,args.eyes,args.recipe,face)
        build(args.source_psd,face,args.recipe,output)
    for label,source in [('reference',args.base),('mouth_edit',args.mouth),('eyes_edit',args.eyes)]:
        shutil.copy2(source,output/'sources'/(label+'.png'))
    if args.body_motion:
        from body_motion import ROOT,build_body_motion
        from verify_body_motion import run
        kit=ROOT/'work/third_party/live2d-agent-kit'
        engine=ROOT/'work/third_party/psd2live'
        core=Path('/Applications/Live2D Cubism 5.3/res')
        binding=output.with_name(output.name+'-motion')
        build_body_motion(output,binding,args.motion_recipe,kit,engine,args.java_home,core)
        run(binding,kit,args.java_home,core)


if __name__=='__main__':
    main()
