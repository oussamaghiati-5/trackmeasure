// Controlled TCF fixture. A fresh default is not a completed choice.
window.__tcfapi = (command, version, callback, listenerId) => {
  if (command === 'removeEventListener') { callback(true); return; }
  if (command !== 'addEventListener') { callback(null, false); return; }
  const decision = (document.cookie.match(/(?:^|;\s*)consent=([^;]+)/) || [])[1];
  const accepted = decision === 'accepted';
  callback({ listenerId: 1, cmpId: 999, cmpStatus: 'loaded', gdprApplies: true,
    eventStatus: window.fixtureChoiceMade ? 'useractioncomplete' : (decision ? 'tcloaded' : 'cmpuishown'),
    // consent.assess_tcf requires an "accept all" fixture to grant at least
    // nine purposes 1..10.  Keep rejection explicit by setting each false.
    purpose: { consents: Object.fromEntries(Array.from({length: 10}, (_, i) => [i + 1, accepted])) },
    vendor: { consents: { 1: accepted } }, specialFeatureOptins: { 1: false, 2: false }
  }, true);
};
