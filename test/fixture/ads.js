// Consent-respecting tracker: only fingerprints when consent=accepted, or no decision yet... no: only if accepted.
if (document.cookie.includes('consent=accepted')) {
  const c = document.createElement('canvas'); c.width = 200; c.height = 50;
  const x = c.getContext('2d'); x.font = '14px Arial'; x.fillText('Ads canvas probe 0123456789', 2, 20); c.toDataURL();
  new Image().src = 'http://ads.test/px.gif?c=1';
}
