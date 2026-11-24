// Cross-check consent.decode_tcf against IAB Tech Lab's reference TCF library.
//
// Run outside the crawler, in a scratch folder:
//   npm install @iabtechlabtcf/core@1
//   python scripts/dump_tc_strings.py data/full_eu_20261122/crawl.sqlite > tc_strings.json
//   node scripts/check_tcf_official.mjs tc_strings.json
//
// tc_strings.json is private: it holds consent cookies from the crawl. Do not commit it.
import { TCString } from '@iabtechlabtcf/core';
import fs from 'fs';

const rows = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const sorted = (set) => [...set].sort((a, b) => a - b);
let agree = 0;
for (const r of rows) {
  const m = TCString.decode(r.tc);
  const official = {
    purposes: sorted(m.purposeConsents.values()),
    special_features: sorted(m.specialFeatureOptins.values()),
    vendors: m.vendorConsents.size,
  };
  const same = JSON.stringify(official) === JSON.stringify(r.ours);
  agree += same;
  if (!same) console.log('MISMATCH', r.run, r.site, JSON.stringify(official), JSON.stringify(r.ours));
}
console.log(`IAB reference decoder agrees on ${agree}/${rows.length} TC strings ` +
            '(purposes, special features, vendor-consent count)');
