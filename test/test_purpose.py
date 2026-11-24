"""Purpose labelling rules (purpose.py) and agreement statistics."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import purpose  # noqa: E402
from purpose_agreement import cohen_kappa  # noqa: E402


def script(url, third_party=1, techniques=("canvas",), page_context="Home only", reads_webdriver=0):
    return dict(url=url, third_party=third_party, techniques=list(techniques),
                page_context=page_context, reads_webdriver=reads_webdriver)


class Templates(unittest.TestCase):
    def test_random_segments_collapse_to_one_template(self):
        self.assertEqual(purpose.path_template("https://a.com/akam/13/46af0006"), "/akam/{n}/{hex}")
        self.assertEqual(purpose.path_template(
            "https://www.nike.com/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/ips.js"),
            "/{uuid}/{uuid}/ips.js")
        self.assertEqual(purpose.path_template("https://x.de/_fs-ch-1T1wmsGaOgGaSxcX/assets/script.js"),
                         "/{rand}/assets/script.js")
        self.assertEqual(purpose.path_template("https://x.de/tags.js"), "/tags.js")

    def test_page_types(self):
        self.assertEqual(purpose.page_type("https://shop.com/"), "Home")
        self.assertEqual(purpose.page_type("https://shop.com/account/login"), "Login")
        self.assertEqual(purpose.page_type("https://elpais.com/suscripciones/digital/"), "Checkout/payment")
        self.assertEqual(purpose.page_type("https://www.amazon.de/-/en/x/dp/B0"), "Product")
        self.assertEqual(purpose.page_type("https://news.com/2026/11/22/some-story/"), "Article")


class Labels(unittest.TestCase):
    def test_third_party_vendor_signature_with_agreeing_tracker_radar_is_high(self):
        lab = purpose.label(script("https://client.px-cloud.net/PXabc/main.min.js"),
                            radar={"categories": ["Fraud Prevention"]})
        self.assertEqual((lab["purpose_primary"], lab["confidence"], lab["party"]),
                         (purpose.SECURITY, "High", "Third-party"))
        self.assertIn("Tracker Radar", lab["evidence"])

    def test_tracker_radar_alone_gives_medium_and_keeps_second_purpose(self):
        lab = purpose.label(script("https://cdn.example-ads.com/x.js"),
                            radar={"categories": ["Advertising", "Analytics", "Ad Fraud"]})
        self.assertEqual((lab["purpose_primary"], lab["purpose_secondary"], lab["confidence"]),
                         (purpose.ADS, purpose.ANALYTICS, "Medium"))

    def test_cdn_category_alone_is_low_confidence(self):
        lab = purpose.label(script("https://static.cdn.net/app.js"), radar={"categories": ["CDN"]})
        self.assertEqual((lab["purpose_primary"], lab["confidence"]), (purpose.CDN, "Low"))

    def test_tracker_radar_is_never_applied_to_first_party_scripts(self):
        lab = purpose.label(script("https://www.walmart.com/si/snr.js", third_party=0),
                            radar={"categories": ["Advertising"]})
        self.assertEqual(lab["purpose_primary"], purpose.UNKNOWN)

    def test_hidden_first_party_vendor_needs_path_signature_and_cookie_for_high(self):
        url = "https://www.costco.com/149e9513-01fa-4fb0-aad4-566afd725d1b/2d206a39-8ed7-437e-a3be-862e0f06eea3/ips.js"
        lab = purpose.label(script(url, third_party=0))
        self.assertEqual((lab["party"], lab["purpose_primary"], lab["confidence"]),
                         ("Hidden first-party vendor", purpose.SECURITY, "Medium"))
        lab = purpose.label(script(url, third_party=0), site_cookie_vendors={"Kasada"})
        self.assertEqual(lab["confidence"], "High")

    def test_akamai_random_path_only_with_corroboration(self):
        url = "https://www.bestbuy.com/L3Z0d7/M/x/d7XTYjToBA/tcJuQkf3h9/ElMgbwQ2RwI/CRB1M0/tjDDabc"
        self.assertEqual(purpose.label(script(url, third_party=0))["purpose_primary"], purpose.UNKNOWN)
        lab = purpose.label(script(url, third_party=0), site_cookie_vendors={"Akamai"})
        self.assertEqual((lab["purpose_primary"], lab["vendor"]),
                         (purpose.SECURITY, "Akamai Bot Manager (sensor, random path)"))

    def test_same_owner_third_party_is_first_party(self):
        lab = purpose.label(script("https://static.xx.fbcdn.net/rsrc.php/a.js"), same_owner=True,
                            radar={"categories": ["Social Network", "CDN"]})
        self.assertEqual((lab["party"], lab["purpose_primary"]), ("First-party", purpose.UNKNOWN))

    def test_context_rules_are_low_confidence_and_unknown_is_default(self):
        lab = purpose.label(script("https://shop.com/app.js", third_party=0, page_context="Checkout/payment only"))
        self.assertEqual((lab["purpose_primary"], lab["confidence"]), (purpose.SECURITY, "Low"))
        lab = purpose.label(script("https://shop.com/app.js", third_party=0, page_context="All pages",
                                   techniques=("canvas", "webgl"), reads_webdriver=1))
        self.assertEqual((lab["purpose_primary"], lab["evidence"]), (purpose.SECURITY, "page context; behaviour"))
        lab = purpose.label(script("https://shop.com/app.js", third_party=0, page_context="All pages",
                                   techniques=("attr_enum",), reads_webdriver=1))
        self.assertEqual((lab["purpose_primary"], lab["confidence"]), (purpose.UNKNOWN, "Low"))

    def test_ad_verification_is_dual_use(self):
        lab = purpose.label(script("https://static.adsafeprotected.com/sca.17.6.4.js", techniques=("attr_enum",)))
        self.assertEqual((lab["purpose_primary"], lab["purpose_secondary"]), (purpose.ADS, purpose.SECURITY))

    def test_ad_recovery_fallback_needs_same_site_evidence(self):
        url = "https://www.bild.de/43a5bfe8-b83f-4799-a2e6-2b2311c09ebd"
        self.assertEqual(purpose.label(script(url, third_party=0))["purpose_primary"], purpose.UNKNOWN)
        lab = purpose.label(script(url, third_party=0), site_vendors={purpose.AD_RECOVERY},
                            cluster={"n_sites": 4, "vendor": ""})
        self.assertEqual((lab["purpose_primary"], lab["party"]), (purpose.ADS, "Hidden first-party vendor"))

    def test_cookie_vendor_detection(self):
        self.assertEqual(purpose.cookie_vendors(["_abck", "datadome", "KP_UIDz-ssn", "x"]),
                         {"Akamai", "DataDome", "Kasada"})


class Agreement(unittest.TestCase):
    def test_cohen_kappa(self):
        self.assertEqual(cohen_kappa(list("aabb"), list("aabb"))[0], 1.0)
        k, n = cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])
        self.assertEqual((k, n), (0.0, 4))
        self.assertEqual(cohen_kappa(["a", ""], ["a", "b"])[1], 1)


if __name__ == "__main__":
    unittest.main()
