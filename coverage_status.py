"""Navigation completion and consent eligibility are different outcomes."""
from consent import consent_record


def site_status(db, run, site, requested='none', navigation_ok=True):
    row=db.execute("SELECT consent FROM visits WHERE run=? AND site=? AND phase='after_consent' ORDER BY id DESC LIMIT 1",(run,site)).fetchone()
    record=consent_record(row[0]) if row and row[0] else {}
    outcome=record.get('outcome','not_reached')
    confirmed=record.get('action')==requested and outcome=='confirmed'
    eligible=bool(navigation_ok and (requested=='none' or confirmed))
    if requested=='reject' and run=='reject_gpc' and record.get('gpc') is not True:
        eligible=False
    if requested=='accept' and record.get('gpc') is True:
        eligible=False
    reason_code=record.get('reason_code','consent_not_reached' if requested!='none' else 'not_requested')
    if not navigation_ok:
        reason_code='navigation_failure'
    elif confirmed and not eligible:
        reason_code='gpc_mismatch'
    return dict(navigation='completed' if navigation_ok else 'failed',consent=outcome,
        eligible=eligible,scope=record.get('scope','not_observed'),
        reason_code=reason_code,reason=record.get('reason',''),
        obstacle=record.get('obstacle','not_observed'))


def progress_line(status):
    reason=str(status['reason_code']).replace('\n',' ')[:100]
    return (f"navigation={status['navigation']} consent={status['consent']} "
            f"eligible={'yes' if status['eligible'] else 'no'} reason={reason}")
