"""Observational Chromium initiator capture. No request interception or payload changes."""
import json
import uuid


async def attach_initiators(context, page, state):
    session = await context.new_cdp_session(page)
    identity = str(uuid.uuid4())
    def record(event):
        if state.visit_id is None:
            return
        request = event.get('request', {})
        # Store only bounded source stack metadata, not headers, cookies or credentials.
        initiator = event.get('initiator', {})
        def stack(value, depth=0):
            if not value or depth >= 4:
                return None
            return dict(callFrames=[{k: f.get(k) for k in ('functionName', 'url', 'lineNumber', 'columnNumber')}
                                    for f in value.get('callFrames', [])[:30]],
                        parent=stack(value.get('parent'), depth + 1))
        safe = {k: initiator.get(k) for k in ('type', 'url', 'lineNumber', 'columnNumber')}
        safe['stack'] = stack(initiator.get('stack'))
        state.db.execute('INSERT INTO network_initiators(visit_id,session_id,request_id,frame_id,loader_id,url,method,wall_time,initiator) VALUES(?,?,?,?,?,?,?,?,?)',
            (state.visit_id, identity, event.get('requestId'), event.get('frameId'), event.get('loaderId'),
             request.get('url', '')[:8000], request.get('method'), event.get('wallTime'), json.dumps(safe)))
    session.on('Network.requestWillBeSent', record)
    await session.send('Network.enable')
    return session


def initiator_for(db, request):
    """Do not silently choose among repeated identical requests. Correlation is explicit."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='network_initiators'").fetchone():
        return dict(status='not_captured')
    rows = db.execute('SELECT * FROM network_initiators WHERE visit_id=? AND url=? AND method=? AND ABS(wall_time-?)<=0.1',
                      (request['visit_id'], request['url'], request['method'], request['ts'])).fetchall()
    if len(rows) != 1:
        return dict(status='ambiguous' if rows else 'unavailable', candidates=len(rows))
    row = rows[0]
    # Reverse uniqueness prevents the same CDP event being assigned to two Playwright requests.
    count = db.execute('SELECT COUNT(*) FROM requests WHERE visit_id=? AND url=? AND method=? AND ABS(ts-?)<=0.1',
                       (request['visit_id'], request['url'], request['method'], row['wall_time'])).fetchone()[0]
    if count != 1:
        return dict(status='ambiguous', candidates=count)
    return dict(status='unique_time_url_method_correspondence', cdp_frame_id=row['frame_id'],
                loader_id=row['loader_id'], event_id=row['id'], initiator=json.loads(row['initiator']),
                limitation='CDP/Playwright correlation, not an engine-shared request ID; page-session coverage may omit workers/OOPIFs.')
