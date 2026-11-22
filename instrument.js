// Injected into every frame before any page script runs (Playwright add_init_script).
// Hooks fingerprinting-relevant and storage APIs, attributes each call to the calling
// script via the stack trace, and ships batches to the Python side via window.__tmLog.
(() => {
  'use strict';
  if (window.__tmInstalled) return;
  Object.defineProperty(window, '__tmInstalled', { value: true, enumerable: false });

  const CAP = 1500;               // max detailed records per API per frame
  const FLUSH_MS = 250;
  const NativeError = Error;
  const nativeSetTimeout = window.setTimeout.bind(window);
  const nativeToString = Function.prototype.toString;
  const counts = new Map();
  let queue = [];
  let scheduled = false;
  let send = window.__tmLog;

  // Make wrapped functions stringify like the originals ("function x() { [native code] }"),
  // so trivial tamper checks in tracking scripts don't change their behaviour.
  const fakes = new WeakMap();
  const toStringShim = { toString() { return fakes.has(this) ? fakes.get(this) : nativeToString.call(this); } }.toString;
  fakes.set(toStringShim, nativeToString.call(nativeToString));
  Function.prototype.toString = toStringShim;

  function callerScript() {
    let st = '';
    try { st = new NativeError().stack || ''; } catch (e) { return ''; }
    const lines = st.split('\n');
    for (let i = 1; i < lines.length; i++) {
      const m = lines[i].match(/((?:https?|blob):\/\/?[^\s)]+?):\d+:\d+\)?\s*$/);
      if (m) return m[1];
    }
    return '';
  }

  function flush() {
    scheduled = false;
    if (!queue.length) return;
    const batch = queue; queue = [];
    try { (send || (send = window.__tmLog))(batch); } catch (e) { /* binding unavailable */ }
  }

  function log(api, args) {
    const c = (counts.get(api) || 0) + 1;
    counts.set(api, c);
    if (c > CAP) {
      if (c === CAP + 1) queue.push({ api, args: { capped: true }, script: callerScript(), ts: Date.now() });
      return;
    }
    queue.push({ api, args, script: callerScript(), ts: Date.now() });
    if (!scheduled) { scheduled = true; nativeSetTimeout(flush, FLUSH_MS); }
  }

  function findDesc(obj, name) {
    let o = obj;
    while (o) {
      const d = Object.getOwnPropertyDescriptor(o, name);
      if (d) return [o, d];
      o = Object.getPrototypeOf(o);
    }
    return [null, null];
  }

  function wrapMethod(obj, name, label, argfn) {
    if (!obj) return;
    const [owner, d] = findDesc(obj, name);
    if (!d || typeof d.value !== 'function') return;
    const orig = d.value;
    const w = { [name](...a) {
      try { log(label, argfn ? argfn(this, a) : undefined); } catch (e) {}
      return orig.apply(this, a);
    } }[name];
    Object.defineProperty(w, 'length', { value: orig.length });
    fakes.set(w, nativeToString.call(orig));
    Object.defineProperty(owner, name, { ...d, value: w });
  }

  // Like wrapMethod, but also ships the return value (first VALUE_CAP per API per frame),
  // so the analysis can look for it in outgoing requests. Python stores only match tokens.
  const VALUE_CAP = 10;
  const valueCounts = new Map();
  function wrapMethodValue(obj, name, label, argfn, valfn) {
    if (!obj) return;
    const [owner, d] = findDesc(obj, name);
    if (!d || typeof d.value !== 'function') return;
    const orig = d.value;
    const w = { [name](...a) {
      let ret;
      try { ret = orig.apply(this, a); }
      catch (err) { try { log(label, argfn ? argfn(this, a) : undefined); } catch (e) {} throw err; }
      try {
        const args = argfn ? argfn(this, a) : {};
        const v = valfn(this, a, ret);
        if (v !== undefined) {
          const c = (valueCounts.get(label) || 0) + 1;
          valueCounts.set(label, c);
          if (c <= VALUE_CAP) args.value = v;
        }
        log(label, args);
      } catch (e) {}
      return ret;
    } }[name];
    Object.defineProperty(w, 'length', { value: orig.length });
    fakes.set(w, nativeToString.call(orig));
    Object.defineProperty(owner, name, { ...d, value: w });
  }

  function wrapAccessor(obj, name, getLabel, setLabel, setArgfn) {
    if (!obj) return;
    const [owner, d] = findDesc(obj, name);
    if (!d || (!d.get && !d.set)) return;
    const nd = { ...d };
    if (d.get && getLabel) {
      const g = d.get;
      const holder = { get [name]() { try { log(getLabel); } catch (e) {} return g.call(this); } };
      nd.get = Object.getOwnPropertyDescriptor(holder, name).get;
      fakes.set(nd.get, nativeToString.call(g));
    }
    if (d.set && setLabel) {
      const s = d.set;
      const holder = { set [name](v) { try { log(setLabel, setArgfn ? setArgfn(v) : undefined); } catch (e) {} s.call(this, v); } };
      nd.set = Object.getOwnPropertyDescriptor(holder, name).set;
      fakes.set(nd.set, nativeToString.call(s));
    }
    Object.defineProperty(owner, name, nd);
  }

  const str = (v, n) => { try { return String(v).slice(0, n); } catch (e) { return ''; } };
  const P = (ctor) => (typeof window[ctor] === 'function' ? window[ctor].prototype : null);

  // ---------------- Canvas ----------------
  wrapMethodValue(P('HTMLCanvasElement'), 'toDataURL', 'Canvas.toDataURL', (c, a) => ({ w: c.width, h: c.height, type: a[0] || 'image/png' }),
    (c, a, r) => (typeof r === 'string' && r.length <= 500000 ? r : undefined));
  wrapMethod(P('HTMLCanvasElement'), 'toBlob', 'Canvas.toBlob', (c, a) => ({ w: c.width, h: c.height, type: a[1] || 'image/png' }));
  wrapMethod(P('HTMLCanvasElement'), 'getContext', 'Canvas.getContext', (c, a) => ({ type: str(a[0], 20), w: c.width, h: c.height }));
  for (const ctor of ['CanvasRenderingContext2D', 'OffscreenCanvasRenderingContext2D']) {
    const pr = P(ctor);
    wrapMethod(pr, 'getImageData', 'Canvas2D.getImageData', (c, a) => ({ sw: a[2], sh: a[3] }));
    wrapMethod(pr, 'fillText', 'Canvas2D.fillText', (c, a) => ({ text: str(a[0], 100), font: c.font }));
    wrapMethod(pr, 'strokeText', 'Canvas2D.strokeText', (c, a) => ({ text: str(a[0], 100), font: c.font }));
    wrapMethod(pr, 'measureText', 'Canvas2D.measureText', (c, a) => ({ font: c.font, text: str(a[0], 30) }));
    wrapMethod(pr, 'isPointInPath', 'Canvas2D.isPointInPath');
    // Englehardt & Narayanan exclude scripts that save/restore the context (animations).
    wrapMethod(pr, 'save', 'Canvas2D.save');
    wrapMethod(pr, 'restore', 'Canvas2D.restore');
  }
  wrapMethod(P('OffscreenCanvas'), 'convertToBlob', 'Canvas.convertToBlob', (c) => ({ w: c.width, h: c.height }));

  // ---------------- WebGL ----------------
  for (const ctor of ['WebGLRenderingContext', 'WebGL2RenderingContext']) {
    const pr = P(ctor);
    wrapMethodValue(pr, 'getParameter', 'WebGL.getParameter', (c, a) => ({ p: a[0] }),
      (c, a, r) => ((a[0] === 37445 || a[0] === 37446) && typeof r === 'string' ? r.slice(0, 500) : undefined));
    wrapMethod(pr, 'getExtension', 'WebGL.getExtension', (c, a) => ({ name: str(a[0], 60) }));
    wrapMethod(pr, 'getSupportedExtensions', 'WebGL.getSupportedExtensions');
    wrapMethod(pr, 'getShaderPrecisionFormat', 'WebGL.getShaderPrecisionFormat');
    wrapMethod(pr, 'readPixels', 'WebGL.readPixels');
  }

  // ---------------- Audio ----------------
  const BAC = P('BaseAudioContext') || P('AudioContext');
  wrapMethod(BAC, 'createOscillator', 'Audio.createOscillator');
  wrapMethod(BAC, 'createDynamicsCompressor', 'Audio.createDynamicsCompressor');
  wrapMethod(BAC, 'createAnalyser', 'Audio.createAnalyser');
  wrapMethod(P('OfflineAudioContext'), 'startRendering', 'Audio.startRendering');
  wrapMethod(P('AnalyserNode'), 'getFloatFrequencyData', 'Audio.getFloatFrequencyData');
  wrapMethod(P('AudioBuffer'), 'getChannelData', 'Audio.getChannelData');

  // ---------------- WebRTC / devices / misc ----------------
  wrapMethod(P('RTCPeerConnection'), 'createDataChannel', 'WebRTC.createDataChannel');
  wrapMethod(P('RTCPeerConnection'), 'createOffer', 'WebRTC.createOffer');
  wrapMethod(P('MediaDevices'), 'enumerateDevices', 'Media.enumerateDevices');
  wrapMethod(P('Navigator'), 'getBattery', 'Navigator.getBattery');
  wrapMethod(P('Navigator'), 'getGamepads', 'Navigator.getGamepads');
  wrapMethod(P('Navigator'), 'javaEnabled', 'Navigator.javaEnabled');
  wrapMethod(P('Navigator'), 'sendBeacon', 'Navigator.sendBeacon', (c, a) => ({ url: str(a[0], 300) }));
  wrapMethod(P('Permissions'), 'query', 'Permissions.query', (c, a) => ({ name: a[0] && str(a[0].name, 40) }));
  wrapMethod(P('SpeechSynthesis'), 'getVoices', 'Speech.getVoices');
  wrapMethod(P('FontFaceSet'), 'check', 'Fonts.check', (c, a) => ({ font: str(a[0], 80) }));
  wrapMethod(window, 'matchMedia', 'Window.matchMedia', (c, a) => ({ q: str(a[0], 80) }));
  wrapMethod(Intl.DateTimeFormat.prototype, 'resolvedOptions', 'Intl.resolvedOptions');
  wrapMethod(Date.prototype, 'getTimezoneOffset', 'Date.getTimezoneOffset');

  // ---------------- Attribute getters ----------------
  for (const n of ['userAgent', 'appVersion', 'platform', 'language', 'languages', 'hardwareConcurrency',
                   'deviceMemory', 'plugins', 'mimeTypes', 'doNotTrack', 'maxTouchPoints', 'vendor',
                   'cookieEnabled', 'webdriver', 'connection', 'userAgentData', 'pdfViewerEnabled',
                   'globalPrivacyControl', 'oscpu', 'product', 'productSub']) {
    wrapAccessor(P('Navigator'), n, 'Navigator.' + n);
  }
  for (const n of ['width', 'height', 'availWidth', 'availHeight', 'availLeft', 'availTop', 'colorDepth', 'pixelDepth']) {
    wrapAccessor(P('Screen'), n, 'Screen.' + n);
  }
  wrapAccessor(window, 'devicePixelRatio', 'Window.devicePixelRatio');

  // ---------------- Storage ----------------
  let ls = null;
  const storeKind = (s) => { try { if (ls === null) ls = window.localStorage; } catch (e) { ls = undefined; } return s === ls ? 'local' : 'session'; };
  wrapMethod(P('Storage'), 'getItem', 'Storage.getItem', (c, a) => ({ store: storeKind(c), key: str(a[0], 100) }));
  wrapMethod(P('Storage'), 'setItem', 'Storage.setItem', (c, a) => ({ store: storeKind(c), key: str(a[0], 100), value: str(a[1], 200) }));
  wrapMethod(P('Storage'), 'removeItem', 'Storage.removeItem', (c, a) => ({ store: storeKind(c), key: str(a[0], 100) }));
  wrapMethod(P('IDBFactory'), 'open', 'IndexedDB.open', (c, a) => ({ name: str(a[0], 100) }));
  wrapAccessor(document, 'cookie', 'Document.cookie.get', 'Document.cookie.set', (v) => ({ value: str(v, 300) }));

  // ---------------- Behavioural signals ----------------
  // Registration of input/motion listeners per script (not the events themselves).
  const BEHAVIOR = new Set(['mousemove', 'mousedown', 'mouseup', 'pointermove', 'pointerdown', 'keydown', 'keyup',
    'keypress', 'touchstart', 'touchmove', 'touchend', 'wheel', 'scroll', 'devicemotion', 'deviceorientation']);
  const targetName = (t) => (t === window ? 'window' : t === document ? 'document' : str(t && t.nodeName, 20));
  (() => {
    const [owner, d] = findDesc(P('EventTarget'), 'addEventListener');
    if (!d || typeof d.value !== 'function') return;
    const orig = d.value;
    const w = { addEventListener(type, ...rest) {
      try {
        const t = String(type);
        if (BEHAVIOR.has(t)) log('Event.' + t, { target: targetName(this) });
        if (typeof HTMLCanvasElement === 'function' && this instanceof HTMLCanvasElement) log('Canvas.addEventListener', { type: str(t, 30) });
      } catch (e) {}
      return orig.call(this, type, ...rest);
    } }.addEventListener;
    Object.defineProperty(w, 'length', { value: orig.length });
    fakes.set(w, nativeToString.call(orig));
    Object.defineProperty(owner, 'addEventListener', { ...d, value: w });
  })();
  for (const n of ['ondevicemotion', 'ondeviceorientation']) {
    wrapAccessor(window, n, null, 'Event.' + n.slice(2), () => ({ target: 'window', via: 'property' }));
  }
  for (const n of ['onmousemove', 'onkeydown', 'onkeyup', 'ontouchmove']) {
    wrapAccessor(document, n, null, 'Event.' + n.slice(2), () => ({ target: 'document', via: 'property' }));
  }
  for (const n of ['Accelerometer', 'Gyroscope', 'LinearAccelerationSensor', 'GravitySensor', 'Magnetometer',
                   'AbsoluteOrientationSensor', 'RelativeOrientationSensor', 'AmbientLightSensor']) {
    const O = window[n];
    if (typeof O !== 'function') continue;
    const W = new Proxy(O, { construct(t, a, nt) { try { log('Sensor.' + n); } catch (e) {} return Reflect.construct(t, a, nt); } });
    try { Object.defineProperty(window, n, { value: W, writable: true, configurable: true }); } catch (e) {}
  }

  window.addEventListener('pagehide', flush, true);
})();
