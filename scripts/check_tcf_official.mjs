// Cross-check consent.decode_tcf against IAB Tech Lab's reference TCF library.
//
// Run outside the crawler, in a scratch folder:
//   npm install @iabtechlabtcf/core@1
//   python scripts/dump_tc_strings.py data/full_eu_20261122/crawl.sqlite > tc_strings.json
//   cp scripts/check_tcf_official.mjs . && node check_tcf_official.mjs tc_strings.json
//
// tc_strings.json is private: it holds consent cookies from the crawl. Do not commit it.
//
// The crawler stores cookie values cut at 1000 characters (crawler.py), so long TC
// strings lose their tail. The library's full decode then throws on the cut field.
// For those strings the core segment is decoded again field by field with the
// library's own encoders, stopping at the first field that does not decode. The
// comparison is made only when every field we compare decoded before that point.
import { Base64Url, BitLength, FieldEncoderMap, Fields, Segment, SegmentEncoder, TCModel,
         TCString, VendorVectorEncoder } from '@iabtechlabtcf/core';
import fs from 'fs';

const COMPARED = [Fields.specialFeatureOptins, Fields.purposeConsents, Fields.vendorConsents];

// SegmentEncoder.decode for the v2 core segment, keeping the fields decoded before an error.
function decodeCorePartial(core) {
  const bits = Base64Url.decode(core);
  const model = new TCModel();
  const encoders = FieldEncoderMap();
  let idx = 0;
  const done = [];
  for (const key of SegmentEncoder.fieldSequence['2'][Segment.CORE]) {
    const n = BitLength[key];
    try {
      const chunk = bits.substr(idx, n);
      model[key] = encoders[key] === VendorVectorEncoder ? VendorVectorEncoder.decode(chunk, 2)
                                                         : encoders[key].decode(chunk, n);
      idx += Number.isInteger(n) ? n : model[key].bitLength;
    } catch (e) {
      return { model, done, stoppedAt: key };
    }
    done.push(key);
  }
  return { model, done, stoppedAt: null };
}

const rows = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sorted = (set) => [...set].sort((a, b) => a - b);
let agree = 0, partial = 0, unchecked = 0;
for (const r of rows) {
  let m, note = '';
  try {
    m = TCString.decode(r.tc);
  } catch (e) {
    const p = decodeCorePartial(r.tc.split('.')[0]);
    if (!COMPARED.every((k) => p.done.includes(k))) {
      unchecked++;
      console.log('UNCHECKED', r.run, r.site, `len=${r.tc.length}`, `library stopped at ${p.stoppedAt}`);
      continue;
    }
    m = p.model;
    partial++;
    note = ` (core only, len=${r.tc.length}, library stopped at ${p.stoppedAt ?? 'a later segment'})`;
  }
  const official = {
    purposes: sorted(m.purposeConsents.values()),
    special_features: sorted(m.specialFeatureOptins.values()),
    vendors: m.vendorConsents.size,
  };
  const same = JSON.stringify(official) === JSON.stringify(r.ours);
  agree += same;
  console.log(same ? 'AGREE   ' : 'MISMATCH', r.run, r.site, JSON.stringify(official),
              same ? '' : JSON.stringify(r.ours), note);
}
console.log(`IAB reference decoder agrees on ${agree}/${rows.length - unchecked} TC strings ` +
            `(purposes, special features, vendor-consent count); ${partial} of them decoded from ` +
            `the core segment only because the stored value was cut; ${unchecked} not checkable`);
