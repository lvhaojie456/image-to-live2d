"""Use the scheduler shipped with See-through's model instead of a hidden online fetch."""
from pathlib import Path
import sys

path = Path(sys.argv[1]) / 'common/utils/inference_utils.py'
source = path.read_text()
old = '            scheduler=None\n'
new = "            scheduler=DPMSolverMultistepScheduler.from_pretrained(pretrained, subfolder='scheduler')\n"
if new not in source:
    if source.count(old) != 1:
        raise SystemExit('Unexpected upstream source; patch was not applied')
    source = 'from diffusers import DPMSolverMultistepScheduler\n' + source.replace(old, new)
    path.write_text(source)
print('Local scheduler patch ready:', path)
