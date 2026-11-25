#!/usr/bin/env python3
"""Prepare/resume repeated, randomized four-condition validation. No live crawl on prepare."""
import argparse
import csv
import datetime
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from run_fingerprint_validation import CONDITIONS, load_sites


def schedule(sites, repeats, seed):
    if repeats < 1:
        raise ValueError('Use at least one pass')
    rng = random.Random(seed)
    result = []
    for repeat in range(repeats):
        order, site_order = list(CONDITIONS), list(sites)
        rng.shuffle(order); rng.shuffle(site_order)
        result.append(dict(repeat=repeat+1, conditions=order, sites=site_order))
    return result


def cluster_estimates(rows, repeats, seed=0, samples=2000):
    """Resample websites, retaining all their repetitions together. Complete pairs only."""
    groups=defaultdict(list)
    for row in rows:
        if row['eligible']:
            groups[row['site']].append(row)
    groups={site:items for site,items in groups.items() if len(items)==repeats}
    sites=sorted(groups)
    if not sites:
        return dict(complete_sites=0, raw_ci=None, reviewed_ci=None, reviewed_minus_raw_ci=None)
    def measure(chosen, reviewed=False):
        baseline=sum(r['supported_baseline' if reviewed else 'raw_baseline'] for s in chosen for r in groups[s])
        treatment=sum(r['supported_run' if reviewed else 'raw_run'] for s in chosen for r in groups[s])
        return 1-treatment/baseline if baseline else None
    decisive=all(r['unresolved_baseline']==r['unresolved_run']==0 for items in groups.values() for r in items)
    rng=random.Random(seed);raws=[];reviews=[];deltas=[]
    for _ in range(samples):
        chosen=[rng.choice(sites) for _ in sites]
        raw=measure(chosen); reviewed=measure(chosen,True) if decisive else None
        if raw is not None:raws.append(raw)
        if reviewed is not None:reviews.append(reviewed)
        if raw is not None and reviewed is not None:deltas.append(reviewed-raw)
    def ci(values):
        # Undefined resamples (e.g. zero denominator) are never silently discarded for a CI.
        if len(sites)<2 or len(values)!=samples:return None
        values=sorted(values)
        return [values[int(.025*(samples-1))],values[int(.975*(samples-1))]]
    return dict(complete_sites=len(sites), excluded_sites=sorted(set(r['site'] for r in rows)-set(sites)),
                raw_reduction=measure(sites), reviewed_reduction=measure(sites,True) if decisive else None,
                raw_ci=ci(raws),reviewed_ci=ci(reviews),reviewed_minus_raw_ci=ci(deltas),
                bootstrap_seed=seed,bootstrap_samples=samples,
                caution='Website-cluster percentile bootstrap for complete paired sites; selected-site inference only. Few clusters produce unstable intervals. Reviewed intervals require all decisive labels.')


def summary(study, review_dir='review'):
    study = Path(study)
    manifest = json.loads((study / 'study.json').read_text())
    rounds, rows, paired = [], [], []
    for block in manifest['blocks']:
        batch = ROOT / block['batch']
        path = batch / review_dir / 'summary.json'
        if not path.exists():
            rounds.append(dict(repeat=block['repeat'], status='not_analyzed'))
            continue
        data = json.loads(path.read_text())
        rounds.append(dict(repeat=block['repeat'], status=data['status'], pair_labels=data['pair_labels']))
        if data['status'] == 'complete':
            rows += [dict(row, repeat=block['repeat']) for row in data['aggregates']]
            paired += [dict(row, repeat=block['repeat']) for row in data['comparisons']]
    conditions = {}
    for condition in ('reject_gpc','accept','ubol'):
        selected = [r for r in rows if r['run'] == condition]
        baseline = sum(r['raw_baseline'] for r in selected)
        treatment = sum(r['raw_run'] for r in selected)
        support_b = sum(r['supported_baseline'] for r in selected)
        support_t = sum(r['supported_run'] for r in selected)
        uncertain_b = sum(r['uncertain_baseline'] for r in selected)
        uncertain_t = sum(r['uncertain_run'] for r in selected)
        reviewed = bool(selected) and all(r['unreviewed_baseline']==r['unreviewed_run']==0 for r in selected)
        conditions[condition] = dict(completed_repeats=len(selected), eligible_site_visits=sum(r['eligible_sites'] for r in selected),
            raw_baseline=baseline, raw_treatment=treatment,
            raw_reduction=1-treatment/baseline if baseline else None,
            supported_baseline=support_b, supported_treatment=support_t,
            supported_count_reduction_descriptive=1-support_t/support_b if support_b and reviewed else None,
            reviewed_reduction_low=1-(support_t+uncertain_t)/support_b if support_b and reviewed else None,
            reviewed_reduction_high=1-support_t/(support_b+uncertain_b) if support_b+uncertain_b and reviewed else None,
            repeat_raw_reductions=[r['raw_reduction'] for r in selected],
            repeat_paired_raw_baselines=[r['raw_baseline'] for r in selected],
            repeat_paired_raw_treatments=[r['raw_run'] for r in selected])
        conditions[condition]['complete_site_cluster_analysis']=cluster_estimates(
            [r for r in paired if r['run']==condition],manifest['repeats'],manifest['seed'])
    result = dict(rounds=rounds, conditions=conditions,
        limitation='Repeated visits are not independent websites. Descriptive reductions and identification bounds, not significance tests or confidence intervals. Eligibility sets can differ by repeat. Review labels and exclusions before interpretation.')
    (study / ('repeat_summary.json' if review_dir=='review' else 'repeat_summary-'+review_dir+'.json')).write_text(json.dumps(result,indent=2)+'\n')
    return result


def prepare(sites_file, repeats=1, seed=0, final_study=False):
    sites_file = Path(sites_file).resolve()
    rows = load_sites(sites_file, final_study)
    plans = schedule([r['domain'] for r in rows], repeats, seed)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    study = ROOT / 'results/repeated-validation' / stamp
    study.mkdir(parents=True)
    frozen_sites = study / 'candidate_sites.csv'
    frozen_sites.write_bytes(sites_file.read_bytes())
    sites_sha256 = hashlib.sha256(frozen_sites.read_bytes()).hexdigest()
    lookup = {r['domain']:r for r in rows}
    frozen = None
    for plan in plans:
        site_path = study / ('sites-' + str(plan['repeat']) + '.csv')
        with site_path.open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader()
            writer.writerows(lookup[name] for name in plan['sites'])
        output = subprocess.check_output([sys.executable,'scripts/run_fingerprint_validation.py',
            '--prepare-only','--attribute-evidence','--sites',str(site_path),
            '--condition-order',','.join(plan['conditions']), *(['--final-study'] if final_study else [])],cwd=ROOT,text=True)
        prefix = 'Validation directory: '
        batch = Path(next(line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)))
        child = json.loads((batch / 'experiment.json').read_text())
        if frozen is not None and frozen != child['source_hashes']:
            raise RuntimeError('Method changed during preparation')
        frozen = child['source_hashes']
        plan['batch'] = batch.relative_to(ROOT).as_posix()
        plan['manifest_sha256'] = hashlib.sha256((batch/'experiment.json').read_bytes()).hexdigest()
    protocol_path = ROOT / 'study_protocol.json'
    manifest=dict(status='prepared',kind='final candidate study' if final_study else 'validation',repeats=repeats,seed=seed,
        sites_input=sites_file.name,sites_count=len(rows),sites_sha256=sites_sha256,
        frozen_sites='candidate_sites.csv',scheduled_site_visits=len(rows)*len(CONDITIONS)*repeats,
        protocol_sha256=hashlib.sha256(protocol_path.read_bytes()).hexdigest() if protocol_path.exists() else None,
        blocks=plans,source_hashes=frozen,
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        protocol='Same randomized site order within each round; condition order randomized per round. One fresh profile per site/condition. Fixed exit required across rounds. Study is frozen at preparation.')
    (study / 'study.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return study


def resume(study):
    study=Path(study).resolve()
    path=study/'study.json'
    manifest=json.loads(path.read_text())
    if manifest['runner_sha256'] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise RuntimeError('Repeated runner changed; prepare a new study')
    frozen_sites = study / manifest['frozen_sites']
    if (not frozen_sites.is_file() or
            hashlib.sha256(frozen_sites.read_bytes()).hexdigest() != manifest['sites_sha256']):
        raise RuntimeError('Frozen original candidate list changed')
    protocol_path = ROOT / 'study_protocol.json'
    if manifest.get('protocol_sha256') and (not protocol_path.is_file() or
            hashlib.sha256(protocol_path.read_bytes()).hexdigest() != manifest['protocol_sha256']):
        raise RuntimeError('Study protocol changed since preparation; prepare a new study')
    fixed_exit=manifest.get('exit')
    try:
        manifest['status']='running'
        for block in manifest['blocks']:
            batch=ROOT/block['batch']
            child=json.loads((batch/'experiment.json').read_text())
            if child['source_hashes'] != manifest['source_hashes']:
                raise RuntimeError('Child method differs from frozen study')
            if child['status']=='prepared' and hashlib.sha256((batch/'experiment.json').read_bytes()).hexdigest()!=block['manifest_sha256']:
                raise RuntimeError('Prepared child plan changed')
            if child.get('exit') and fixed_exit and child['exit'] != fixed_exit:
                raise RuntimeError('Exit differs across repetitions')
            if fixed_exit and not child.get('exit'):
                child['exit']=fixed_exit
                (batch/'experiment.json').write_text(json.dumps(child,indent=2)+'\n')
                block['manifest_sha256']=hashlib.sha256((batch/'experiment.json').read_bytes()).hexdigest()
                path.write_text(json.dumps(manifest,indent=2)+'\n')
            if child['status']!='complete' or not (batch/'review/summary.json').exists():
                subprocess.run([sys.executable,'scripts/run_fingerprint_validation.py','--resume',str(batch)],cwd=ROOT,check=True)
            child=json.loads((batch/'experiment.json').read_text())
            fixed_exit=child.get('exit',fixed_exit)
            manifest['exit']=fixed_exit
            path.write_text(json.dumps(manifest,indent=2)+'\n')
        manifest['status']='complete'
    except BaseException as error:
        manifest.update(status='interrupted',error=type(error).__name__+': '+str(error)[:500])
        raise
    finally:
        path.write_text(json.dumps(manifest,indent=2)+'\n')
        summary(study)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sites',type=Path,default=ROOT/'fingerprint_validation_sites.csv')
    ap.add_argument('--repeats',type=int,default=1); ap.add_argument('--seed',type=int,default=0)
    ap.add_argument('--resume',type=Path); ap.add_argument('--summarize',type=Path)
    ap.add_argument('--final-study',action='store_true')
    ap.add_argument('--review-dir',default='review')
    args=ap.parse_args()
    if args.summarize:
        print(json.dumps(summary(args.summarize,args.review_dir),indent=2))
    elif args.resume:
        resume(args.resume)
    else:
        print('Prepared study:',prepare(args.sites,args.repeats,args.seed,args.final_study))
        print('No live visits started. Run this script with --resume STUDY_DIRECTORY to execute.')

if __name__=='__main__':
    main()
