// Aggressive tracker: fingerprints regardless of consent, respawns its ID from localStorage,
// and also sets an ID derived purely from the fingerprint.
(function () {
  const c = document.createElement('canvas'); c.width = 240; c.height = 60;
  const x = c.getContext('2d'); x.textBaseline = 'top'; x.font = '14px Arial'; x.fillStyle = '#f60';
  x.fillRect(100, 1, 62, 20); x.fillStyle = '#069'; x.fillText('Cwm fjordbank glyphs vext quiz, 😃', 2, 15);
  const canvasData = c.toDataURL();
  const fonts = []; for (let i = 0; i < 60; i++) fonts.push('Font' + i);
  for (const f of fonts) { x.font = '72px ' + f + ', monospace'; x.measureText('mmmmmmmmmmlli'); }
  try { const gl = document.createElement('canvas').getContext('webgl'); const e = gl.getExtension('WEBGL_debug_renderer_info');
        gl.getParameter(37446); gl.getParameter(37445); } catch (e) {}
  try { const ac = new OfflineAudioContext(1, 44100, 44100); const o = ac.createOscillator(); const k = ac.createDynamicsCompressor();
        o.connect(k); k.connect(ac.destination); o.start(0); ac.startRendering(); } catch (e) {}
  try { const pc = new RTCPeerConnection(); pc.createDataChannel(''); pc.createOffer().then(() => pc.close()); } catch (e) {}
  const n = navigator, s = screen;
  const attrs = [n.userAgent, n.platform, n.language, n.languages, n.hardwareConcurrency, n.deviceMemory, n.plugins,
    n.mimeTypes, n.doNotTrack, n.maxTouchPoints, n.vendor, n.cookieEnabled, n.webdriver, s.width, s.height, s.colorDepth,
    s.availWidth, devicePixelRatio, Intl.DateTimeFormat().resolvedOptions().timeZone, new Date().getTimezoneOffset()].join('|');
  let h = 0; const src = attrs + canvasData; for (let i = 0; i < src.length; i++) h = (h * 31 + src.charCodeAt(i)) >>> 0;
  document.cookie = '_fpid=fp' + h.toString(36) + 'x9; path=/; max-age=31536000';
  // stateful respawn
  let id = (document.cookie.match(/_trk=([^;]+)/) || [])[1];
  if (!id) { id = localStorage.getItem('_trk_backup') || ('u' + Math.random().toString(36).slice(2) + '7'); }
  document.cookie = '_trk=' + id + '; path=/; max-age=31536000'; localStorage.setItem('_trk_backup', id);
  navigator.sendBeacon('http://cdn.tracker.test/collect?id=' + id);
  new Image().src = 'http://cdn.tracker.test/p.gif?id=' + id;
})();
