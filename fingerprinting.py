"""Explainable API-pattern candidates, not verdicts about tracking or intent.

No browser/pandas dependency: this module is shared by analysis, review and tests.
Each input record has api, args, call_id, visit_id, phase, script_url and database_id.
"""
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def dataset_identity(db, path):
    table = db.execute("SELECT 1 FROM sqlite_master WHERE name='measurement_meta'").fetchone()
    row = db.execute("SELECT value FROM measurement_meta WHERE key='dataset_id'").fetchone() if table else None
    return row[0] if row else "legacy-path-" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:24]

RULE_VERSION = "evidence-v3"
TECHNIQUES = ["canvas", "canvas_font", "webrtc", "audio", "webgl", "attr_enum"]
CANVAS_ANIMATION = {"Canvas2D.save", "Canvas2D.restore", "Canvas.addEventListener"}
LIMITS = ["API patterns do not prove identification, transmission or tracking purpose.",
          "Workers, unhooked APIs, stack attribution and observation time limit coverage."]
REVIEW_GUIDANCE = {
    "canvas": {
        "profile_collection": "Rendered output is combined with other measurements, hashed, stored, or sent as a browser identifier.",
        "ordinary_usage": "Readback is used for a visible export, editor, chart, game, image transform, or other page feature.",
    },
    "canvas_font": {
        "profile_collection": "Measured widths or font availability form a vector used in an identifier or profile.",
        "ordinary_usage": "Measurements select layout, fit text, or render a visible typography feature.",
    },
    "webrtc": {
        "profile_collection": "Offer, codec, device, or network properties are extracted for a profile outside a user communication flow.",
        "ordinary_usage": "The offer belongs to a call, streaming, file-transfer, or connection setup feature.",
    },
    "audio": {
        "profile_collection": "Generated audio is rendered and numerical samples are summarized into an identifier or profile.",
        "ordinary_usage": "Audio output or samples drive playback, visualization, editing, or another visible feature.",
    },
    "webgl": {
        "profile_collection": "GPU values are combined with other measurements, hashed, stored, or transmitted as a profile.",
        "ordinary_usage": "GPU values choose rendering features, diagnose compatibility, or are displayed to the user.",
    },
    "attr_enum": {
        "profile_collection": "Multiple browser/device attributes are assembled into an identifier or profile.",
        "ordinary_usage": "Individual attributes support compatibility, responsive layout, localization, or diagnostics.",
    },
}


def arguments(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def meta(call):
    value = call["args"].get("_tm")
    return value if isinstance(value, dict) else {}


def legacy_techniques(calls):
    """Old rules for old databases; lack object/order evidence and remain labelled."""
    by = defaultdict(list)
    for api, args in calls:
        by[api].append(args or {})
    chars = set("".join(str(a.get("text") or "") for k in ("Canvas2D.fillText", "Canvas2D.strokeText") for a in by[k]))
    read = any(read_size(k, a) for k in ("Canvas.toDataURL", "Canvas.toBlob", "Canvas2D.getImageData") for a in by[k])
    found = {"canvas"} if len(chars) >= 10 and read and not (CANVAS_ANIMATION & set(by)) else set()
    if len({a.get("font") for a in by["Canvas2D.measureText"] if a.get("font")}) >= 50:
        found.add("canvas_font")
    if by["WebRTC.createDataChannel"] and by["WebRTC.createOffer"]:
        found.add("webrtc")
    if by["Audio.createOscillator"] and by["Audio.createDynamicsCompressor"] and any(
            by[k] for k in ("Audio.startRendering", "Audio.getFloatFrequencyData", "Audio.getChannelData")):
        found.add("audio")
    if any(a.get("p") in (37445, 37446) for a in by["WebGL.getParameter"]):
        found.add("webgl")
    methods = {"Navigator.sendBeacon", "Navigator.getBattery", "Navigator.getGamepads", "Navigator.javaEnabled"}
    if len({k for k in by if k.startswith(("Navigator.", "Screen.")) and k not in methods}) >= 12:
        found.add("attr_enum")
    return found


def read_size(api, args):
    try:
        if api == "Canvas2D.getImageData":
            return abs(float(args.get("sw") or 0)) >= 16 and abs(float(args.get("sh") or 0)) >= 16
        return (float(args.get("w") or 0) >= 16 and float(args.get("h") or 0) >= 16
                and args.get("type", "image/png") not in ("image/jpeg", "image/webp"))
    except (TypeError, ValueError):
        return False


def find_candidates(records):
    """Return findings with exact supporting call rows and explicit limitations.

    New Canvas rules link within document+canvas+script, across visit phases;
    resets from ANY script invalidate earlier drawings on that canvas.
    Other techniques keep conservative, explicitly heuristic script-level rules.
    """
    groups, legacy, doc_limits = defaultdict(list), defaultdict(list), defaultdict(set)
    for index, original in enumerate(records):
        c = dict(original, args=arguments(original.get("args")))
        c.setdefault("call_id", index + 1)
        c.setdefault("database_id", "legacy")
        m = meta(c)
        base = (c["database_id"], c.get("run"), c.get("site"))
        if m.get("document_id"):
            key = (*base, m["document_id"])
            groups[key].append(c)
            if c["args"].get("capped"):
                doc_limits[key].add("Trace capped: additional calls were omitted.")
            if c.get("api") == "Instrumentation.status" and c["args"].get("unavailable"):
                doc_limits[key].add("Some instrumentation hooks were unavailable: " + ", ".join(c["args"]["unavailable"]))
        elif c.get("api") != "Instrumentation.status":
            legacy[(*base, c.get("visit_id"), c.get("frame_url"), c.get("script_url"))].append(c)

    findings = []
    emitted = set()

    def emit(technique, evidence, reason, calls, limits=(), candidate=True, object_id=None, observed=()):
        anchor = calls[-1]
        bucket = (anchor["database_id"], anchor.get("visit_id"), meta(anchor).get("document_id"),
                  anchor.get("script_url"), technique, object_id, evidence)
        if bucket in emitted:
            return
        emitted.add(bucket)
        identity = [RULE_VERSION, anchor["database_id"], anchor.get("run"), anchor.get("site"),
                    technique, evidence, meta(anchor).get("document_id"), anchor.get("script_url"),
                    object_id, anchor["call_id"]]
        findings.append(dict(
            finding_id=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24],
            rule_version=RULE_VERSION, technique=technique, evidence_level=evidence,
            reason=reason, counts_as_candidate=candidate, object_id=object_id,
            document_id=meta(anchor).get("document_id"),
            **{k: anchor.get(k) for k in ("database_id", "run", "site", "visit_id", "phase", "frame_url",
                                          "script_url", "script_domain", "third_party")},
            phase_group="pre" if anchor.get("phase") == "landing" else "post",
            support_call_ids=[c["call_id"] for c in calls], supporting_calls=calls,
            observed_facts=list(observed), profile_assessment="requires_review" if candidate else "incomplete",
            review_guidance=REVIEW_GUIDANCE.get(technique, {}),
            limitations=sorted(set(LIMITS + list(limits)))))

    for key, calls in groups.items():
        calls.sort(key=lambda c: (meta(c).get("sequence", 0), c["call_id"]))
        limits = list(doc_limits[key])
        drawings, by_script = defaultdict(list), defaultdict(list)
        fonts, webrtc, audio, webgl = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
        for c in calls:
            api, a, m = c.get("api", ""), c["args"], meta(c)
            if a.get("capped") or api == "Instrumentation.status":
                continue
            script = c.get("script_url") or ""
            by_script[script, c.get("visit_id")].append(c)
            obj = m.get("object_id")
            root = m.get("root_id") or obj
            success = m.get("success") is True
            visit = c.get("visit_id")
            if api == "Canvas2D.measureText" and success and obj:
                fonts[obj, script, visit].append(c)
            if api.startswith("WebRTC.") and obj:
                webrtc[obj, script, visit].append(c)
            if api.startswith("Audio.") and root:
                audio[root, script, visit].append(c)
            if api.startswith("WebGL.") and obj:
                webgl[obj, script, visit].append(c)
            if not obj:
                continue
            # Canvas dimension assignment/reset clears the bitmap. clearRect is
            # treated conservatively as a reset even if a transform/clip applies.
            if success and api in (
                    "Canvas.width.set", "Canvas.height.set", "Canvas2D.reset", "Canvas2D.clearRect"):
                for drawing_key in list(drawings):
                    if drawing_key[0] == obj:
                        drawings.pop(drawing_key)
            if success and api.startswith("Canvas2D.") and api.split(".", 1)[1] in {
                    "fillText", "strokeText", "fillRect", "strokeRect", "arc", "ellipse",
                    "bezierCurveTo", "quadraticCurveTo", "drawImage", "fill", "stroke"}:
                drawings[obj, script].append(c)
            if api not in ("Canvas.toDataURL", "Canvas.toBlob.complete", "Canvas.convertToBlob.complete",
                           "Canvas2D.getImageData") or not read_size(api.replace(".complete", ""), a):
                continue
            draws = drawings[obj, script]
            chars = set("".join(str(d["args"].get("text") or "") for d in draws))
            shape_calls = [d for d in draws if d["api"] not in ("Canvas2D.fillText", "Canvas2D.strokeText")]
            if len(chars) < 10 and len(shape_calls) < 2:
                # A read alone is evidence of API use, insufficient for this candidate rule.
                continue
            own_limits = limits + ["Ordinary text drawing/export can match this rule; review source and page purpose.",
                                   "Text is truncated to 100 characters per call; cross-script draws are not linked.",
                                   "Spatial overlap, transforms, clipping and returned pixel content are not verified.",
                                   "Any observed clearRect conservatively resets accumulated text."]
            if success and m.get("result_valid") is not False:
                basis = ">=10 distinct text characters" if len(chars) >= 10 else ">=2 shape/render operations"
                emit("canvas", "linked_readback", "Successful draw then image read on the same canvas (" + basis + "; read >=16x16).",
                     draws + [c], own_limits, object_id=obj,
                     observed=["draw and readback linked to one canvas and script", basis, "readback completed successfully"])
            else:
                emit("canvas", "incomplete_operation", "Canvas pattern attempted; read failed, returned no image, or asynchronous completion was not observed.",
                     draws + [c], own_limits, candidate=False, object_id=obj)

        for (obj, script, visit), support in fonts.items():
            distinct = {c["args"].get("font") for c in support if c["args"].get("font")}
            if len(distinct) >= 50:
                emit("canvas_font", "linked_measurement", "At least 50 distinct font strings measured on one canvas by one script.",
                     support, limits + ["Font measurement can support layout or typography; inspect value use and page purpose."],
                     object_id=obj, observed=[f"{len(distinct)} distinct font strings", "measurements linked to one canvas and script"])

        for (obj, script, visit), support in webrtc.items():
            channel = [c for c in support if c["api"] == "WebRTC.createDataChannel" and meta(c).get("success") is True]
            offers = [c for c in support if c["api"] == "WebRTC.createOffer.complete" and meta(c).get("success") is True]
            if channel and offers:
                emit("webrtc", "linked_operation", "Data channel creation and completed offer observed on one peer connection.",
                     channel + offers, limits + ["A normal communication feature creates the same sequence; inspect surrounding source and UI."],
                     object_id=obj, observed=["data channel created", "offer promise fulfilled", "same peer connection and script"])

        for (root, script, visit), support in audio.items():
            wanted = {
                "oscillator": [c for c in support if c["api"] == "Audio.createOscillator" and meta(c).get("success") is True],
                "compressor": [c for c in support if c["api"] == "Audio.createDynamicsCompressor" and meta(c).get("success") is True],
                "render": [c for c in support if c["api"] == "Audio.startRendering.complete" and meta(c).get("success") is True],
                "read": [c for c in support if c["api"] in ("Audio.getFloatFrequencyData", "Audio.getChannelData") and meta(c).get("success") is True],
            }
            if all(wanted.values()):
                chosen = [wanted[k][0] for k in ("oscillator", "compressor", "render", "read")]
                emit("audio", "linked_operation", "Oscillator, compressor, completed offline render and numerical readback share one audio context.",
                     chosen, limits + ["Audio production and visualization can match this sequence; inspect how samples are used."],
                     object_id=root, observed=["offline render fulfilled", "numerical audio data read", "operations linked to one audio context and script"])

        for (obj, script, visit), support in webgl.items():
            params = {c["args"].get("p") for c in support if c["api"] == "WebGL.getParameter" and meta(c).get("success") is True}
            if {37445, 37446} <= params:
                selected = [c for c in support if c["api"] == "WebGL.getParameter" and c["args"].get("p") in (37445, 37446)]
                emit("webgl", "linked_measurement", "Unmasked GPU vendor and renderer queried on one WebGL context.",
                     selected, limits + ["Renderer selection and diagnostics query these values; inspect combination, storage and transmission."],
                     object_id=obj, observed=["unmasked vendor queried", "unmasked renderer queried", "same WebGL context and script"])

        for script_calls in by_script.values():
            usable = [c for c in script_calls if meta(c).get("success") is not False]
            attributes = [c for c in usable if c["api"].startswith(("Navigator.", "Screen."))
                          and c["api"] not in {"Navigator.sendBeacon", "Navigator.getBattery", "Navigator.getGamepads", "Navigator.javaEnabled"}
                          and meta(c).get("success") is True]
            names = {c["api"] for c in attributes}
            if len(names) >= 4:
                emit("attr_enum", "attribute_bundle", f"{len(names)} distinct navigator/screen attributes read by one script.",
                     attributes, limits + ["A four-attribute threshold favors review recall and will include responsive or diagnostic code."],
                     observed=[f"{len(names)} distinct browser/device attributes", "reads attributed to one script"])

    for calls in legacy.values():
        found = legacy_techniques([(c.get("api", ""), c["args"]) for c in calls])
        for technique in sorted(found):
            emit(technique, "legacy_unlinked", "Legacy rule match without document, object, order or operation-success evidence.",
                 calls, ["Legacy trace: recollect before comparing with evidence-v2 measurements."])
    return findings
