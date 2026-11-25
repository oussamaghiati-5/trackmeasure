// Injected into every frame before any page script runs (Playwright add_init_script).
// Hooks fingerprinting-relevant and storage APIs, attributes each call to the calling
// script via the stack trace, and ships batches to the Python side via window.__tmLog.
(() => {
  'use strict';
  const ATTRIBUTE_EVIDENCE = false; // Enabled only by the explicit crawler option.
  const attributeValueCounts = new Map();
  let attributeValuesSaved = 0;
  function attributeEvidence(api, value, metadata) {
    if (!ATTRIBUTE_EVIDENCE || !/^(Navigator\.|Screen\.|Window\.devicePixelRatio$)/.test(api)) return undefined;
    const key = (metadata && metadata.script || '(unknown)') + '\n' + api;
    const count = attributeValueCounts.get(key) || 0;
    if (count >= 20) return {attribute_value: {status: 'limit', scope: 'script_property_document'}};
    if (attributeValuesSaved >= 1000) return {attribute_value: {status: 'frame_limit', limit: 1000}};
    if (attributeValueCounts.size >= 256 && !attributeValueCounts.has(key))
      return {attribute_value: {status: 'key_limit', limit: 256}};
    // Never coerce, enumerate or serialize returned objects: this can invoke page code.
    const type = typeof value;
    if (!['string', 'number', 'boolean'].includes(type) || (type === 'number' && !Number.isFinite(value)))
      return {attribute_value: {status: 'unsupported', type}};
    if (type === 'string' && value.length > 512) return {attribute_value: {status: 'too_long', type}};
    attributeValueCounts.set(key, count + 1);
    attributeValuesSaved++;
    return {attribute_value: {status: 'captured', type, value}};
  }
  if (window.__tmInstalled) return;
  Object.defineProperty(window, '__tmInstalled', { value: true, enumerable: false });

  const CAP = 1500;               // max detailed records per API per frame
  const FLUSH_MS = 250;
  const NativeError = Error;
  const nativeSetTimeout = window.setTimeout.bind(window);
  const nativeToString = Function.prototype.toString;
  const counts = new Map();
  let detailedRecords = 0;
  const objects = new WeakMap();
  const roots = new WeakMap();
  let nextObject = 0, sequence = 0;
  const documentId = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  const installed = [], unavailable = [];
  const asyncAPIs = new Set(['Canvas.convertToBlob', 'Media.enumerateDevices',
    'Navigator.getBattery', 'Permissions.query']);
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
    try { st = new NativeError().stack || ''; } catch (e) { return {}; }
    const lines = st.split('\n');
    for (let i = 1; i < lines.length; i++) {
      const m = lines[i].match(/((?:https?|blob):\/\/?[^\s)]+?):(\d+):(\d+)\)?\s*$/);
      if (m) return {script: m[1], line: Number(m[2]), column: Number(m[3])};
    }
    return {};
  }

  function flush() {
    scheduled = false;
    if (!queue.length) return;
    const batch = queue; queue = [];
    try { return (send || (send = window.__tmLog))(batch); } catch (e) { /* binding unavailable */ }
  }

  function objectId(value) {
    if (!value || (typeof value !== 'object' && typeof value !== 'function')) return null;
    if (!objects.has(value)) objects.set(value, 'o' + (++nextObject));
    return objects.get(value);
  }

  function trace(receiver, api) {
    const location = callerScript();
    let target = receiver;
    // Context and canvas operations must share the canvas ID.
    if (api.startsWith('Canvas2D.') || api.startsWith('WebGL.')) {
      try { target = receiver.canvas; } catch (_) {}
    }
    let root = receiver;
    try { root = roots.get(receiver) || target; } catch (_) { root = target; }
    return {...location, version: 3, document_id: documentId, sequence: ++sequence,
      object_id: objectId(target), context_id: objectId(receiver), root_id: objectId(root)};
  }

  function log(api, args, metadata) {
    metadata = metadata || {version: 3, document_id: documentId, sequence: ++sequence,
      object_id: null, context_id: null, root_id: null, success: null, status: 'trace_error'};
    const key = ATTRIBUTE_EVIDENCE ? (metadata.script || '(unknown)') + '\n' + api : api;
    if (ATTRIBUTE_EVIDENCE && detailedRecords++ >= 15000) {
      if (detailedRecords !== 15001) return;
      args = {capped: true, limit: 15000, scope: 'document'};
      metadata = {...metadata, status: 'capped', success: null};
    }
    const c = (counts.get(key) || 0) + 1;
    if (ATTRIBUTE_EVIDENCE && counts.size >= 256 && !counts.has(key)) return;
    counts.set(key, c);
    if (c > CAP) {
      if (c !== CAP + 1) return;
      args = {capped: true, limit: CAP};
      metadata = {...metadata, status: 'capped', success: null};
    }
    queue.push({ api, args: {...(args || {}), _tm: metadata}, script: metadata.script || '',
                 frame_url: location.href, ts: Date.now() });
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
    if (!obj) { unavailable.push(label); return; }
    const [owner, d] = findDesc(obj, name);
    if (!d || typeof d.value !== 'function') { unavailable.push(label); return; }
    const orig = d.value;
    const w = { [name](...a) {
      let metadata, detail;
      try { metadata = trace(this, label); detail = argfn ? argfn(this, a) : undefined; } catch (_) {}
      let result;
      try { result = orig.apply(this, a); }
      catch (error) {
        try { log(label, detail, {...metadata, success: false, status: 'threw', error: error.name}); } catch (_) {}
        throw error;
      }
      // Do not attach Promise handlers or alter callbacks: that can change page
      // semantics. Asynchronous completion remains explicitly unobserved.
      try { log(label, detail, {...metadata,
        ...(label === 'Canvas.toDataURL' ? {result_valid: typeof result === 'string' && result.startsWith('data:image/')} : {}),
        success: asyncAPIs.has(label) ? null : true,
        status: asyncAPIs.has(label) ? 'async_requested' : 'returned'}); } catch (_) {}
      try {
        if (result && (typeof result === 'object' || typeof result === 'function')) {
          roots.set(result, roots.get(this) || this);
        }
      } catch (_) {}
      return result;
    } }[name];
    Object.defineProperty(w, 'length', { value: orig.length });
    fakes.set(w, nativeToString.call(orig));
    try { Object.defineProperty(owner, name, { ...d, value: w }); installed.push(label); }
    catch (_) { unavailable.push(label); }
  }

  // Like wrapMethod, but also ships the return value (first VALUE_CAP per API per frame),
  // so the analysis can look for it in outgoing requests. Python stores only match tokens.
  const VALUE_CAP = 10;
  const valueCounts = new Map();
  function wrapMethodValue(obj, name, label, argfn, valfn) {
    if (!obj) { unavailable.push(label); return; }
    const [owner, d] = findDesc(obj, name);
    if (!d || typeof d.value !== 'function') { unavailable.push(label); return; }
    const orig = d.value;
    const w = { [name](...a) {
      let metadata, args;
      try { metadata = trace(this, label); args = argfn ? argfn(this, a) : {}; } catch (_) { args = {}; }
      let ret;
      try { ret = orig.apply(this, a); }
      catch (err) {
        try { log(label, args, {...metadata, success: false, status: 'threw', error: err.name}); } catch (_) {}
        throw err;
      }
      try {
        const v = valfn(this, a, ret);
        if (v !== undefined) {
          const c = (valueCounts.get(label) || 0) + 1;
          valueCounts.set(label, c);
          if (c <= VALUE_CAP) args.value = v;
        }
        log(label, args, {...metadata, success: true, status: 'returned',
          ...(label === 'Canvas.toDataURL' ? {result_valid: typeof ret === 'string' && ret.startsWith('data:image/')} : {})});
      } catch (_) {}
      return ret;
    } }[name];
    Object.defineProperty(w, 'length', { value: orig.length });
    fakes.set(w, nativeToString.call(orig));
    try { Object.defineProperty(owner, name, { ...d, value: w }); installed.push(label); }
    catch (_) { unavailable.push(label); }
  }

  function wrapPromiseMethod(obj, name, label, argfn) {
    if (!obj) { unavailable.push(label); return; }
    const [owner, d] = findDesc(obj, name);
    if (!d || typeof d.value !== 'function') { unavailable.push(label); return; }
    const orig = d.value;
    const w = { [name](...a) {
      let metadata, detail;
      try { metadata = trace(this, label); detail = argfn ? argfn(this, a) : undefined; } catch (_) {}
      let result;
      try { result = orig.apply(this, a); }
      catch (error) {
        try { log(label, detail, {...metadata, success: false, status: 'threw', error: error.name}); } catch (_) {}
        throw error;
      }
      try { log(label, detail, {...metadata, success: null, status: 'async_requested'}); } catch (_) {}
      if (result && typeof result.then === 'function') {
        result.then(value => {
          try {
            if (value && (typeof value === 'object' || typeof value === 'function')) roots.set(value, roots.get(this) || this);
            log(label + '.complete', detail, {...metadata, sequence: ++sequence, success: true,
              status: 'fulfilled', result_object_id: objectId(value)});
          } catch (_) {}
        }, error => {
          try { log(label + '.complete', detail, {...metadata, sequence: ++sequence, success: false,
            status: 'rejected', error: error && error.name}); } catch (_) {}
        });
      }
      return result;
    } }[name];
    Object.defineProperty(w, 'length', { value: orig.length });
    fakes.set(w, nativeToString.call(orig));
    try { Object.defineProperty(owner, name, { ...d, value: w }); installed.push(label); }
    catch (_) { unavailable.push(label); }
  }

  function wrapCanvasBlob(obj, name, label, argfn) {
    if (!obj) { unavailable.push(label); return; }
    const [owner, d] = findDesc(obj, name);
    if (!d || typeof d.value !== 'function') { unavailable.push(label); return; }
    const orig = d.value;
    const w = { [name](...a) {
      let metadata, detail;
      try { metadata = trace(this, label); detail = argfn ? argfn(this, a) : undefined; } catch (_) {}
      const callback = a[0];
      if (typeof callback === 'function') {
        a[0] = function(blob) {
          try { log(label + '.complete', {...detail, size: blob && blob.size, blob_type: blob && blob.type},
            {...metadata, sequence: ++sequence, success: !!blob, status: blob ? 'fulfilled' : 'empty'}); } catch (_) {}
          return callback.apply(this, arguments);
        };
      }
      try {
        const result = orig.apply(this, a);
        try { log(label, detail, {...metadata, success: null, status: 'async_requested'}); } catch (_) {}
        return result;
      } catch (error) {
        try { log(label, detail, {...metadata, success: false, status: 'threw', error: error.name}); } catch (_) {}
        throw error;
      }
    } }[name];
    Object.defineProperty(w, 'length', { value: orig.length });
    fakes.set(w, nativeToString.call(orig));
    try { Object.defineProperty(owner, name, { ...d, value: w }); installed.push(label); }
    catch (_) { unavailable.push(label); }
  }

  function wrapAccessor(obj, name, getLabel, setLabel, setArgfn) {
    if (!obj) return;
    const [owner, d] = findDesc(obj, name);
    if (!d || (!d.get && !d.set)) return;
    const nd = { ...d };
    if (d.get && getLabel) {
      const g = d.get;
      const holder = { get [name]() {
        let metadata; try { metadata = trace(this, getLabel); } catch (_) {}
        try { const value = g.call(this); try { log(getLabel, attributeEvidence(getLabel, value, metadata), {...metadata, success: true, status: 'returned'}); } catch (_) {} return value; }
        catch (error) { try { log(getLabel, undefined, {...metadata, success: false, status: 'threw', error: error.name}); } catch (_) {} throw error; }
      } };
      nd.get = Object.getOwnPropertyDescriptor(holder, name).get;
      fakes.set(nd.get, nativeToString.call(g));
    }
    if (d.set && setLabel) {
      const s = d.set;
      const holder = { set [name](v) {
        let metadata, detail; try { metadata = trace(this, setLabel); detail = setArgfn ? setArgfn(v) : undefined; } catch (_) {}
        try { const value = s.call(this, v); try { log(setLabel, detail, {...metadata, success: true, status: 'returned'}); } catch (_) {} return value; }
        catch (error) { try { log(setLabel, detail, {...metadata, success: false, status: 'threw', error: error.name}); } catch (_) {} throw error; }
      } };
      nd.set = Object.getOwnPropertyDescriptor(holder, name).set;
      fakes.set(nd.set, nativeToString.call(s));
    }
    try { Object.defineProperty(owner, name, nd); installed.push(getLabel || setLabel); }
    catch (_) { unavailable.push(getLabel || setLabel); }
  }

  const str = (v, n) => { try { return String(v).slice(0, n); } catch (e) { return ''; } };
  const P = (ctor) => (typeof window[ctor] === 'function' ? window[ctor].prototype : null);

  // ---------------- Canvas ----------------
  wrapMethodValue(P('HTMLCanvasElement'), 'toDataURL', 'Canvas.toDataURL', (c, a) => ({ w: c.width, h: c.height, type: a[0] || 'image/png' }),
    (c, a, r) => (typeof r === 'string' && r.length <= 500000 ? r : undefined));
  wrapCanvasBlob(P('HTMLCanvasElement'), 'toBlob', 'Canvas.toBlob', (c, a) => ({ w: c.width, h: c.height, type: a[1] || 'image/png' }));
  wrapMethod(P('HTMLCanvasElement'), 'getContext', 'Canvas.getContext', (c, a) => ({ type: str(a[0], 20), w: c.width, h: c.height }));
  for (const ctor of ['CanvasRenderingContext2D', 'OffscreenCanvasRenderingContext2D']) {
    const pr = P(ctor);
    wrapMethod(pr, 'getImageData', 'Canvas2D.getImageData', (c, a) => ({ sx: a[0], sy: a[1], sw: a[2], sh: a[3] }));
    wrapMethod(pr, 'fillText', 'Canvas2D.fillText', (c, a) => ({ text: str(a[0], 100), font: c.font, x: a[1], y: a[2] }));
    wrapMethod(pr, 'strokeText', 'Canvas2D.strokeText', (c, a) => ({ text: str(a[0], 100), font: c.font, x: a[1], y: a[2] }));
    wrapMethod(pr, 'measureText', 'Canvas2D.measureText', (c, a) => ({ font: c.font, text: str(a[0], 30) }));
    wrapMethod(pr, 'isPointInPath', 'Canvas2D.isPointInPath');
    for (const method of ['fillRect', 'strokeRect', 'arc', 'ellipse', 'bezierCurveTo', 'quadraticCurveTo',
                          'drawImage', 'fill', 'stroke']) {
      wrapMethod(pr, method, 'Canvas2D.' + method);
    }
    wrapMethod(pr, 'clearRect', 'Canvas2D.clearRect', (c, a) => ({x: a[0], y: a[1], sw: a[2], sh: a[3], w: c.canvas.width, h: c.canvas.height}));
    wrapMethod(pr, 'reset', 'Canvas2D.reset');
    // Animation signals let analysis exclude common canvas rendering behavior.
    wrapMethod(pr, 'save', 'Canvas2D.save');
    wrapMethod(pr, 'restore', 'Canvas2D.restore');
  }
  for (const ctor of ['HTMLCanvasElement', 'OffscreenCanvas']) {
    wrapAccessor(P(ctor), 'width', null, 'Canvas.width.set', v => ({value: v}));
    wrapAccessor(P(ctor), 'height', null, 'Canvas.height.set', v => ({value: v}));
  }
  wrapPromiseMethod(P('OffscreenCanvas'), 'convertToBlob', 'Canvas.convertToBlob', (c) => ({ w: c.width, h: c.height, type: 'image/png' }));

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
  wrapPromiseMethod(P('OfflineAudioContext'), 'startRendering', 'Audio.startRendering');
  wrapMethod(P('AnalyserNode'), 'getFloatFrequencyData', 'Audio.getFloatFrequencyData');
  wrapMethod(P('AudioBuffer'), 'getChannelData', 'Audio.getChannelData');

  // ---------------- WebRTC / devices / misc ----------------
  wrapMethod(P('RTCPeerConnection'), 'createDataChannel', 'WebRTC.createDataChannel');
  wrapPromiseMethod(P('RTCPeerConnection'), 'createOffer', 'WebRTC.createOffer');
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
      let t = '', metadata, labels = [];
      try {
        t = String(type);
        if (BEHAVIOR.has(t)) labels.push('Event.' + t);
        if (typeof HTMLCanvasElement === 'function' && this instanceof HTMLCanvasElement) labels.push('Canvas.addEventListener');
        if (labels.length) metadata = trace(this, labels[0]);
      } catch (e) {}
      try {
        const result = orig.call(this, type, ...rest);
        for (const label of labels) try {
          log(label, label === 'Canvas.addEventListener' ? {type: str(t, 30)} : {target: targetName(this)},
              {...metadata, success: true, status: 'returned'});
        } catch (e) {}
        return result;
      } catch (error) {
        for (const label of labels) try {
          log(label, {target: targetName(this)}, {...metadata, success: false, status: 'threw', error: error.name});
        } catch (e) {}
        throw error;
      }
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

  Object.defineProperty(window, '__tmFlush', {value: flush, configurable: false});
  log('Instrumentation.status', {installed, unavailable}, {version: 3, document_id: documentId,
    sequence: ++sequence, object_id: null, context_id: null, root_id: null, success: true, status: 'metadata'});
  window.addEventListener('pagehide', flush, true);
})();
