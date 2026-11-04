"""Explicit human-delivered reports, independent of machine-generated article marts."""
import copy
import hashlib
import json
import re
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

from .reviews import parse_json, _timestamp, _revision
from .pipeline import write_json

ID = re.compile(r"report-[a-f0-9]{32}\Z")
ISSUE = re.compile(r"issue-[a-f0-9]{20}\Z")
MAX_BYTES = 60000

def empty():
    return {"schema_version": "1.0", "revision": 0, "updated_at": None, "reports": [], "requests": {}}

def text(value, limit, label):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 and c not in '\n\t' for c in value):
        raise ValueError(f"Invalid {label}")
    return value.strip()

def valid_day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Invalid report date")
    date.fromisoformat(value)
    return value

def validate(store):
    if not isinstance(store, dict) or set(store) != {"schema_version", "revision", "updated_at", "reports", "requests"} or store['schema_version'] != '1.0':
        raise ValueError("Invalid history store")
    _revision(store['revision'])
    if not isinstance(store['reports'], list) or not isinstance(store['requests'], dict):
        raise ValueError("Invalid history records")
    if store['revision'] == 0:
        if store != empty(): raise ValueError("Invalid empty history")
    else: _timestamp(store['updated_at'])
    seen = set()
    for record in store['reports']:
        if set(record) != {'id','versions'} or not ID.fullmatch(record['id']) or record['id'] in seen or not record['versions']:
            raise ValueError("Invalid report identifier")
        seen.add(record['id'])
        for v in record['versions']:
            if set(v) != {'title','key_text','reported_on','status','sources','updated_at','reason'}:
                raise ValueError("Invalid report version")
            text(v['title'], 200, 'title'); text(v['key_text'], 4000, 'key text'); valid_day(v['reported_on'])
            text(v['reason'], 500, 'reason'); _timestamp(v['updated_at'])
            if v['status'] not in {'reported','cancelled'} or not isinstance(v['sources'],list) or not 1 <= len(v['sources']) <= 20:
                raise ValueError("Invalid report state/sources")
            for s in v['sources']:
                if set(s) != {'issue_id','title','company','topic','source_name','published_date','url'} or not ISSUE.fullmatch(s['issue_id']):
                    raise ValueError("Invalid source snapshot")
                for field in ['title','company','topic','source_name','published_date','url']:
                    if not isinstance(s[field],str) or len(s[field]) > 3000: raise ValueError("Invalid source field")
                from urllib.parse import urlsplit
                u=urlsplit(s['url'])
                if u.scheme != 'https' or not u.hostname or u.username or u.password: raise ValueError('Invalid source URL')
    for key, val in store['requests'].items():
        if not re.fullmatch(r'[a-f0-9]{32}',key) or not re.fullmatch(r'[a-f0-9]{64}',val): raise ValueError('Invalid request receipt')
    return store

def load(root):
    p=Path(root)/'reports/delivered.json'
    return validate(parse_json(p.read_text())) if p.exists() else empty()

def verify(root):
    root=Path(root); store=load(root); p=root/'site/data/delivered-reports.json'
    if p.exists() and validate(parse_json(p.read_text())) != store: raise ValueError('History mirror mismatch')
    if (root/'reports/delivered.json').exists() and not p.exists(): raise ValueError('Missing history mirror')
    return store

def apply(root, patch):
    root=Path(root); current=verify(root)
    if not isinstance(patch,dict) or set(patch) != {'schema_version','base_revision','request_id','public_acknowledged','confirmed_reported','report'} or patch['schema_version'] != '1.0':
        raise ValueError('Invalid history patch')
    _revision(patch['base_revision'])
    request=patch['request_id']
    if not isinstance(request,str) or not re.fullmatch(r'[a-f0-9]{32}',request): raise ValueError('Invalid request id')
    digest=hashlib.sha256(json.dumps(patch,sort_keys=True,ensure_ascii=True).encode()).hexdigest()
    if request in current['requests']:
        if current['requests'][request] != digest: raise ValueError('Request id reused with different contents')
        return {'ok':True,'revision':current['revision'],'duplicate_request':True}
    if patch['base_revision'] != current['revision']: raise ValueError('History changed. Reload before saving.')
    if patch['public_acknowledged'] is not True or patch['confirmed_reported'] is not True:
        raise ValueError('Explicit report and public-storage confirmations required')
    r=patch['report']
    if not isinstance(r,dict) or set(r) != {'id','title','key_text','reported_on','status','issue_ids','reason'} or not isinstance(r['id'],str) or not ID.fullmatch(r['id']):
        raise ValueError('Invalid report fields')
    if r['status'] not in {'reported','cancelled'}: raise ValueError('Invalid status')
    title=text(r['title'],200,'title'); key=text(r['key_text'],4000,'key text'); day=valid_day(r['reported_on'])
    if day > datetime.now(timezone(timedelta(hours=9))).date().isoformat(): raise ValueError('A future report cannot be confirmed')
    reason=text(r['reason'],500,'reason')
    ids=r['issue_ids']
    if not isinstance(ids,list) or not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids) or any(not isinstance(x,str) or not ISSUE.fullmatch(x) for x in ids): raise ValueError('Invalid article selection')
    record=next((x for x in current['reports'] if x['id']==r['id']),None)
    if record is None and r['status']=='cancelled': raise ValueError('Cannot cancel an unknown report')
    prior={s['issue_id']:s for s in record['versions'][-1]['sources']} if record else {}
    sources=[]
    for issue in ids:
        # Never rewrite historical source snapshots during report correction.
        if issue in prior: sources.append(prior[issue]); continue
        p=root/'site/data/issues'/f'{issue}.json'
        if not p.exists(): raise ValueError('Selected article unavailable; reload')
        detail=json.loads(p.read_text())
        if detail.get('is_demo') is not False or detail.get('issue_id') != issue or not detail.get('evidence'): raise ValueError('Invalid source article')
        e=detail['evidence'][0]
        sources.append({'issue_id':issue,'title':detail['title'],'company':detail.get('company',''),'topic':detail.get('topic',''),'source_name':e.get('source_name',''),'published_date':e.get('published_date') or e.get('published_at') or '', 'url':e['source_url']})
    result=copy.deepcopy(current); stamp=datetime.now(timezone.utc).isoformat()
    version={'title':title,'key_text':key,'reported_on':day,'status':r['status'],'sources':sources,'updated_at':stamp,'reason':reason}
    target=next((x for x in result['reports'] if x['id']==r['id']),None)
    if target is None: result['reports'].append({'id':r['id'],'versions':[version]})
    else: target['versions'].append(version)
    result['revision']+=1; result['updated_at']=stamp; result['requests'][request]=digest
    validate(result)
    # Pair writes are recoverable locally; workflow commits only after both verify.
    paths=[root/'reports/delivered.json',root/'site/data/delivered-reports.json']
    backups=[p.read_bytes() if p.exists() else None for p in paths]
    try:
        for p in paths: write_json(p,result)
    except OSError:
        for p,old in zip(paths,backups):
            if old is None: p.unlink(missing_ok=True)
            else: p.write_bytes(old)
        raise
    return {'ok':True,'revision':result['revision'],'report_count':sum(x['versions'][-1]['status']=='reported' for x in result['reports'])}
