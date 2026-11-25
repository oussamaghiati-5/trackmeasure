"""Conservative value correspondence, not dynamic taint tracking or purpose inference."""
import json
import re
import sqlite3
import base64
from email.parser import BytesParser
from email.policy import default as email_policy
from network_evidence import initiator_for
from collections import defaultdict
from urllib.parse import parse_qsl, urlsplit

VERSION = 'attribute-correspondence-v3'
ALIASES = {
    'userAgent': {'useragent', 'ua'}, 'appVersion': {'appversion'},
    'platform': {'platform'}, 'language': {'language', 'lang'},
    'hardwareConcurrency': {'hardwareconcurrency', 'cpucount'},
    'deviceMemory': {'devicememory'}, 'width': {'width', 'screenwidth'},
    'height': {'height', 'screenheight'}, 'availWidth': {'availwidth'},
    'availHeight': {'availheight'}, 'colorDepth': {'colordepth'},
    'pixelDepth': {'pixeldepth'}, 'devicePixelRatio': {'devicepixelratio', 'dpr'},
    'maxTouchPoints': {'maxtouchpoints'}, 'vendor': {'vendor'},
}


def fields(url, body=None, content_type='', status='no_body'):
    """Bound parsing to captured complete text; no arbitrary decoding or execution."""
    result = []
    def add(path, value, depth=0):
        if len(result) >= 256 or depth > 8:
            return
        if isinstance(value, dict):
            for key, item in list(value.items())[:256]:
                add(path + '.' + key, item, depth + 1)
        elif isinstance(value, (str, int, float, bool)):
            result.append((path, value))
            key = path.split('.')[-1]
            if key == 'sr' and isinstance(value, str) and re.fullmatch(r'\d{1,5}x\d{1,5}', value):
                width, height = value.split('x')
                # sr does not say whether it means screen, available screen, or viewport.
                # Preserve components for inspection without manufacturing named API evidence.
                for name, v in [('component1', width), ('component2', height)]:
                    if len(result) < 256:
                        result.append((path + '.ambiguous_resolution.' + name, v))
            # Explicit known bundle format; no arbitrary code or recursive decoding.
            if key == 'bd' and isinstance(value, str) and len(value) <= 4096:
                parts = value.split('::')
                if len(parts) == 6 and all(re.fullmatch(r'\d{1,5}', p) for p in parts[2:]):
                    for name, v in zip(['userAgent','language','width','height','deviceMemory','hardwareConcurrency'], parts):
                        add(path + '.bundle_split.' + name, v, 8)
    try:
        query = parse_qsl(urlsplit(url).query, max_num_fields=256)
    except ValueError:
        query = []
    for k, v in query:
        add('query.' + k, v)
        if k == 'cache' and len(v) <= 8192:
            try:
                decoded = json.loads(base64.b64decode(v, validate=True).decode('utf-8'))
                add('query.cache.base64json', decoded)
            except (ValueError, UnicodeError, RecursionError):
                pass
    if status != 'saved' or body is None or len(body) > 65536:
        return result
    try:
        text = body.decode('utf-8') if isinstance(body, bytes) else body
        if 'application/json' in content_type:
            add('json', json.loads(text))
        elif 'application/x-www-form-urlencoded' in content_type:
            for k, v in parse_qsl(text, max_num_fields=256):
                add('form.' + k, v)
        elif 'multipart/form-data' in content_type and '\r' not in content_type and '\n' not in content_type:
            raw = body if isinstance(body, bytes) else body.encode('utf-8')
            msg = BytesParser(policy=email_policy).parsebytes(('Content-Type: ' + content_type + '\r\n\r\n').encode() + raw)
            for index, part in enumerate(msg.iter_parts()):
                if index >= 256:
                    break
                name = part.get_param('name', header='content-disposition')
                if name and not part.get_filename() and not part.is_multipart():
                    add('multipart.' + name, part.get_payload(decode=True).decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError):
        pass
    return result[:256]


def matches(reads, request_fields):
    found = []
    for read in reads:
        value = read['value']
        if isinstance(value, bool) or value == '' or value is None:
            continue
        name = read['api'].split('.')[-1]
        for path, actual in request_fields:
            key = re.sub('[^a-z0-9]', '', path.split('.')[-1].lower())
            if key not in ALIASES.get(name, set()) or isinstance(actual, bool):
                continue
            # No substring matching, no arbitrary coercion and no numeric rounding.
            equal = type(actual) is type(value) and actual == value
            equal = equal or (isinstance(actual, str) and actual == str(value))
            if equal:
                found.append(dict(api=read['api'], value=value, field=path, call_id=read.get('call_id')))
    return found


def export_cards(db, out, pairs, findings):
    destination = out / 'attribute-cards'
    destination.mkdir(exist_ok=True)
    lookup = {f['finding_id']: f for f in findings}
    has_payloads = db.execute("SELECT 1 FROM sqlite_master WHERE name='request_payloads'").fetchone()
    cards = {}
    cursor = db.cursor()
    cursor.row_factory = sqlite3.Row
    for pair in pairs:
        if pair['technique'] != 'attr_enum':
            continue
        reads, statuses, hits = [], defaultdict(int), []
        for fid in pair['finding_ids']:
            for call in lookup[fid]['supporting_calls']:
                args = call.get('args') or {}
                record = args.get('attribute_value') or {'status': 'not_captured'}
                statuses[record['status']] += 1
                if record['status'] == 'captured':
                    reads.append(dict(api=call['api'], value=record['value'], call_id=call['call_id'],
                                      visit_id=call['visit_id'], frame_url=call.get('frame_url'),
                                      script_url=call.get('script_url'), ts=call['ts'],
                                      location=args.get('_tm', {})))
        for vid in sorted({r['visit_id'] for r in reads}):
            rows = cursor.execute('SELECT r.*,p.status AS payload_status,p.content_type,p.body FROM requests r '
                              'LEFT JOIN request_payloads p ON p.request_id=r.id WHERE r.visit_id=?', (vid,)) if has_payloads else []
            for request in rows:
                context = None
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='request_context'").fetchone():
                    context = db.execute('SELECT frame_id FROM request_context WHERE request_id=?', (request['id'],)).fetchone()
                frame_id = context[0] if context else None
                relevant = [r for r in reads if r['visit_id'] == vid and r['ts'] <= request['ts']
                            and (r['location'].get('frame_id') == frame_id if frame_id else r['frame_url'] == request['frame_url'])]
                try:
                    found = matches(relevant, fields(request['url'], request['body'],
                                                    request['content_type'] or '', request['payload_status']))
                except ValueError:
                    continue
                unique = {m['api'] for m in found}
                if len(unique) >= 2:
                    origin = initiator_for(db, request)
                    hits.append(dict(request_id=request['id'], destination=urlsplit(request['url']).netloc,
                                     failure=request['failure'], payload_status=request['payload_status'], matches=found,
                                     frame_association='frame_id' if frame_id else 'legacy_frame_url',
                                     network_initiator=origin,
                                     association='read-before-request value correspondence; inspect initiator separately, data flow unproven'))
        card = dict(version=VERSION, pair_id=pair['pair_id'], reads=reads,
                    capture_statuses=dict(statuses), structured_correspondences=hits,
                    source_snippet='../pair-snippets/' + pair['pair_id'] + '.txt',
                    limitations=['Value correspondence is not proven data flow or tracking.',
                                 'Named fields and two distinct attributes are required; common values can coincide.',
                                 'New traces use frame IDs; legacy URL associations cannot distinguish equal-URL frames. CDP initiators use explicit bounded correlation and limited page-session coverage.',
                                 'Hashed/encrypted/renamed values, objects, workers and omitted/truncated payloads may be missed.',
                                 'No automatic purpose label; independent review required.'])
        relative = 'attribute-cards/' + pair['pair_id'] + '.json'
        (out / relative).write_text(json.dumps(card, indent=2, ensure_ascii=True) + '\n')
        cards[pair['pair_id']] = relative
    return cards
