"""Public bundle export (scripts/export_public_results.py) and the reject-without-GPC
consent view (scripts/purpose_results.py)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_public_results as export  # noqa: E402
import purpose_results  # noqa: E402


def write(path, text):
    path.write_text(text, encoding="utf-8")


class PublicExport(unittest.TestCase):
    def make_batch(self, root, provenance):
        batch = root / "results" / "demo_batch"
        batch.mkdir(parents=True)
        for name in export.COPY_FILES:
            write(batch / name, "{}\n" if name.endswith(".json") else "run,site\n")
        write(batch / "per_site.csv", "run,site,error\nbaseline,a.com,HTTP 403 at https://a.com/x?token=1\n")
        write(batch / "fp_scripts.csv", "script_domain,technique,script_url\nb.com,canvas,https://b.com/s.js?id=9\n")
        write(batch / "manifest.json", json.dumps({"exit": {"ip": "192.0.2.7", "country": "DE"}}))
        write(batch / "vpn_provenance.json", json.dumps(provenance))
        sites = root / "sites.csv"
        write(sites, "domain,category\na.com,news\n")
        return batch, sites

    def test_bundle_names_its_batch_and_keeps_only_public_incident_fields(self):
        provenance = {"provider": "P", "files_sha256": {"profile": "secret-hash"},
                      "incidents": [{"condition": "accept", "description": "uplink lost",
                                     "handling": "resumed", "at_utc": "2026-11-25T15:51Z"}, "not a dict"]}
        with tempfile.TemporaryDirectory() as tmp:
            batch, sites = self.make_batch(Path(tmp), provenance)
            out = Path(tmp) / "public"
            export.export(batch, sites, out)
            readme = (out / "README.md").read_text(encoding="utf-8")
            self.assertIn("# Public results bundle — demo_batch", readme)
            self.assertNotIn("full_eu_20261122", readme)
            public = json.loads((out / "vpn_provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(public["incidents"], [{"condition": "accept", "description": "uplink lost",
                                                    "handling": "resumed"}])
            self.assertNotIn("files_sha256", public)
            self.assertEqual(json.loads((out / "manifest.json").read_text())["exit"]["ip"], "redacted")
            self.assertNotIn("token=1", (out / "per_site.csv").read_text())
            self.assertNotIn("script_url", (out / "fp_scripts.csv").read_text())

    def test_no_incidents_key_without_incidents(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, sites = self.make_batch(Path(tmp), {"provider": "P"})
            out = Path(tmp) / "public"
            export.export(batch, sites, out)
            self.assertEqual(json.loads((out / "vpn_provenance.json").read_text()), {"provider": "P"})


class RejectWithoutGpc(unittest.TestCase):
    def frames(self, runs):
        ps = pd.DataFrame([dict(run=run, site=site, comparison_eligible=1, consent_outcome=outcome,
                                consent_reason="r", tp_domains=1, tp_cookies_final=0)
                           for run, site, outcome in runs])
        det = pd.DataFrame([dict(run="reject", site="a.com", phase_group="post", group="Ads/analytics",
                                 script_domain="ads.com", purpose_primary="Advertising", technique="canvas",
                                 vendor="Ads Inc")],
                           columns=["run", "site", "phase_group", "group", "script_domain", "purpose_primary",
                                    "technique", "vendor"])
        return ps, det

    def test_reported_only_when_the_condition_exists(self):
        ps, det = self.frames([("optout", "a.com", "confirmed"), ("accept", "a.com", "confirmed")])
        self.assertNotIn("reject_without_gpc", purpose_results.consent(ps, det)[1])

    def test_counts_verified_reject_sites_and_their_fingerprinting(self):
        ps, det = self.frames([("optout", "a.com", "confirmed"), ("accept", "a.com", "confirmed"),
                               ("reject", "a.com", "confirmed"), ("reject", "b.com", "confirmed"),
                               ("reject", "c.com", "failed")])
        out = purpose_results.consent(ps, det)[1]["reject_without_gpc"]
        self.assertEqual(out["verified_sites"], 2)
        self.assertEqual(out["also_verified_in_optout"], 1)
        self.assertEqual(out["after_reject_sites_with_Ads/analytics"], 1)
        self.assertEqual(out["after_reject_sites_with_any_fp"], 1)
        self.assertEqual(out["after_reject_ads_analytics_sites"], ["a.com"])


if __name__ == "__main__":
    unittest.main()
