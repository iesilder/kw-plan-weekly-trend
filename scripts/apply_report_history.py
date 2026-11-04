"""Save an explicitly confirmed public report patch. No collection or AI calls."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from weekly_trend.report_history import apply, verify, MAX_BYTES
from weekly_trend.reviews import parse_json
p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,default=Path('.')); p.add_argument('--check',action='store_true'); args=p.parse_args()
try:
    if args.check:
        result={'ok':True,'revision':verify(args.root)['revision']}
    else:
        raw=sys.stdin.buffer.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES: raise ValueError('Patch too large')
        result=apply(args.root,parse_json(raw.decode('utf-8')))
except (ValueError,OSError,KeyError,TypeError):
    p.exit(1,'Report save failed: invalid input, conflicting revision, or unavailable data. No report content is logged.\n')
print(json.dumps(result))
