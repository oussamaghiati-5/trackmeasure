"""Small access/consent preflight; exports candidates only when accept AND reject pass.

Does not run fingerprinting comparisons. Output is preliminary until challenge-page
screenshots are reviewed. No automatic bypass of 403/429, paywalls or bot challenges.
"""
import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from check_consent import run_check, consent_source_hash


def acceptable(row):
    return (row['navigation']=='completed' and row['consent_outcome']=='confirmed'
            and row['gpc']==('enabled' if row['action']=='reject' else 'disabled'))


def screen(sites_file, limit=None, seed=None, check=run_check, resume=None):
    with Path(sites_file).open(newline='') as stream:
        candidates=list(csv.DictReader(line for line in stream if not line.lstrip().startswith('#')))
    if not candidates:raise ValueError('Candidate list is empty')
    if limit is not None and limit < 1:raise ValueError('Limit must be positive')
    if any(not r.get('domain') or not r.get('category') for r in candidates):raise ValueError('Missing domain/category')
    if len({r['domain'] for r in candidates})!=len(candidates):raise ValueError('Duplicate candidates')
    input_hash=hashlib.sha256(Path(sites_file).read_bytes()).hexdigest()
    if resume:
        out=Path(resume).resolve(); metadata=json.loads((out/'screen.json').read_text())
        if metadata['input_sha256']!=input_hash:raise RuntimeError('Candidate list changed since this screen started')
        if metadata['consent_sha256']!=consent_source_hash():raise RuntimeError('Consent method changed since this screen started')
        lookup={r['domain']:r for r in candidates}
        if any(name not in lookup for name in metadata['selected']):raise RuntimeError('Saved candidate missing from input')
        selected=[lookup[name] for name in metadata['selected']]
        records=metadata.get('completed',[]); seed=metadata.get('seed'); metadata['status']='running'; metadata.pop('error',None)
    else:
        if seed is not None:random.Random(seed).shuffle(candidates)
        selected=candidates[:limit]
        stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        out=ROOT/'results/site-screen'/stamp;out.mkdir(parents=True)
        records=[]
        metadata=dict(status='running',seed=seed,selected=[r['domain'] for r in selected],
            input_sha256=input_hash,consent_sha256=consent_source_hash(),
            note='Selection uses access and confirmed saved choices, never fingerprinting outcomes. Eligibility preliminary pending visual access-wall review.')
    eligible=[];exit_info=None
    for record in records:
        if record.get('passed_both'):
            site=next(r for r in selected if r['domain']==record['site'])
            eligible.append(dict(domain=site['domain'],category=site['category'],consent_basis='Accept and reject+GPC confirmed in screen '+out.name+'; screenshots require review'))
        for row in record.get('actions',[]):exit_info=dict(ip=row['exit_ip'],country=row['exit_country'])
    def save():
        (out/'screen.json').write_text(json.dumps(dict(metadata,completed=records,eligible_candidates=len(eligible)),indent=2)+'\n')
        with (out/'eligible-candidates.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['domain','category','consent_basis']);writer.writeheader();writer.writerows(eligible)
    print('Screen directory:',out,flush=True);save()
    try:
        by_site={r['site']:r for r in records}
        for index,site in enumerate(selected,1):
            previous=by_site.get(site['domain'])
            if previous and previous.get('passed_both'):
                print(f"[{index}/{len(selected)}] SKIP {site['domain']} (already passed)",flush=True);continue
            print(f"[{index}/{len(selected)}] SCREEN {site['domain']}",flush=True)
            if previous:
                record=previous;actions=record['actions']
            else:
                actions=[];record=dict(site=site['domain'],passed_both=False,actions=actions);records.append(record)
            if actions and not all(acceptable(r) for r in actions):
                print('  retained prior ineligible result',flush=True);continue
            for action in ['accept','reject'][len(actions):]:
                if consent_source_hash()!=metadata['consent_sha256']:raise RuntimeError('Consent method changed during screening')
                row=check(site['domain'],action,required_exit=exit_info,screen_only=True)
                actions.append(row);exit_info=dict(ip=row['exit_ip'],country=row['exit_country'])
                save()
                print(f"  {action}: navigation={row['navigation']} consent={row['consent_outcome']} eligible={'yes' if acceptable(row) else 'no'} reason={row['reason_code']}",flush=True)
                if not acceptable(row):break  # Both arms required; no need to spend time after failure.
            passed=len(actions)==2 and all(acceptable(r) for r in actions)
            record['passed_both']=passed
            if passed:
                eligible.append(dict(domain=site['domain'],category=site['category'],consent_basis='Accept and reject+GPC confirmed in screen '+out.name+'; screenshots require review'))
            save()
        metadata['status']='complete';save()
    except BaseException as error:
        metadata.update(status='stopped',error=type(error).__name__+': '+str(error)[:300]);save();raise
    print(f"SCREEN RESULT: {len(eligible)}/{len(selected)} passed both consent arms. Review page screenshots before freezing this list.",flush=True)
    return out,eligible


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sites',type=Path,default=ROOT/'sites_verified.csv')
    parser.add_argument('--limit',type=int,help='optional explicit cap; by default check the whole fixed list')
    parser.add_argument('--seed',type=int,help='optional randomized ordering; default preserves fixed list order')
    parser.add_argument('--resume',type=Path,help='resume a stopped screen directory with the same input and consent method')
    args=parser.parse_args();out,eligible=screen(args.sites,args.limit,args.seed,resume=args.resume)
    if not eligible:sys.exit(2)
