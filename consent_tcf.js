// TCF callback lifecycle. Evaluated inside each frame, not loaded from a CDN.
(operation) => {
  const key = '__tmConsentTCF';
  const api = window.__tcfapi;
  const snapshot = d => ({
    tcString: d.tcString, cmpId: d.cmpId, cmpVersion: d.cmpVersion,
    tcfPolicyVersion: d.tcfPolicyVersion, cmpStatus: d.cmpStatus,
    gdprApplies: d.gdprApplies, eventStatus: d.eventStatus,
    purposeConsents: d.purpose && d.purpose.consents,
    vendorConsents: d.vendor && d.vendor.consents,
    disclosedVendors: d.vendor && d.vendor.disclosedVendors,
    specialFeatureOptins: d.specialFeatureOptins,
    purposeLegitimateInterests: d.purpose && d.purpose.legitimateInterests,
    vendorLegitimateInterests: d.vendor && d.vendor.legitimateInterests,
    publisher: d.publisher,
    purposesConsented: Object.keys((d.purpose || {}).consents || {}).filter(k => d.purpose.consents[k]).map(Number),
    vendorsConsented: Object.values((d.vendor || {}).consents || {}).filter(Boolean).length
  });
  const remove = (fn, id) => {
    if (id != null) { try { fn('removeEventListener', 2, () => {}, id); } catch (_) {} }
  };
  if (operation === 'stop') {
    const old = window[key];
    if (old) { old.closed = true; remove(old.api, old.id); delete window[key]; }
    return true;
  }
  if (typeof api !== 'function') return null;
  if (operation === 'start' || operation === 'arm') {
    let watch = window[key];
    if (!watch || watch.api !== api) {
      if (watch) { watch.closed = true; remove(watch.api, watch.id); }
      watch = window[key] = {api, id: null, armed: false, closed: false, seen: false, actionTCString: null};
      try {
        api('addEventListener', 2, (d, ok) => {
          if (d && d.listenerId != null) watch.id = d.listenerId;
          if (watch.closed) { remove(api, watch.id); return; }
          // The immediate registration callback is not evidence of our action.
          if (ok && d && watch.seen && watch.armed && d.eventStatus === 'useractioncomplete') {
            watch.actionTCString = d.tcString;
          }
          watch.seen = true;
        });
      } catch (_) { watch.closed = true; delete window[key]; return false; }
    }
    if (operation === 'arm') { watch.armed = true; watch.actionTCString = null; }
    return true;
  }
  return new Promise(resolve => {
    let finished = false, id = null, latest = null;
    const finish = value => {
      if (finished) return;
      finished = true; clearTimeout(timer); remove(api, id); resolve(value);
    };
    const timer = setTimeout(() => finish(latest || 'timeout'), 1800);
    try {
      api('addEventListener', 2, (d, ok) => {
        if (d && d.listenerId != null) id = d.listenerId;
        if (finished) { remove(api, id); return; }
        if (!ok || !d) return finish('error');
        latest = snapshot(d);
        const watch = window[key];
        latest.actionObserved = !!(watch && watch.armed && watch.actionTCString &&
                                    watch.actionTCString === d.tcString);
        // Do not resolve on the immediate loading/stub callback.
        if (d.cmpStatus === 'loaded' && typeof d.gdprApplies === 'boolean') finish(latest);
      });
    } catch (_) { finish('error'); }
  });
}
