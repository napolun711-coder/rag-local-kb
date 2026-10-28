/* ============================================================
   聊天页语音朗读（TTS）
   ------------------------------------------------------------
   聊天输入已经统一为 index.html 中的原生 textarea。本文件不再注入
   麦克风、按住说话或语音/文字模式切换，也不注册语音输入快捷键。

   功能：
   1) 聊天文本输入（index.html 负责）
      - 原生 textarea、自动增高、Enter 发送、Shift+Enter 换行；
      - 中文输入法组合输入期间不会误发送；
      - 空输入禁用发送，生成中同一按钮切换为停止生成。
   2) 🔊 语音朗读：**云端 TTS（可选）→ 本机 Matcha（可选）→ 本设备语音 → 浏览器语音**
      - 第 1 级：云端 TTS（在设置页「朗读（TTS）」里填），由服务端 /api/tts 转发；没配置就自动跳过；
      - 第 2 级：本机 Matcha（服务端进程内合成，离线；模型缺失时自动跳过）——
        半个字都不会发出去；
      - 第 2/3 级：**设备自己的语音**（iPadOS 系统语音 / 安卓系统语音 / 电脑浏览器音色），
        全在浏览器里完成，不经服务端；设备没有可用音色时才退到浏览器默认语音；
      - 云端失败时会**先弹一条明确的中文原因**再落到设备语音（不是静默切换）；
      - 每条回答底部左下角「朗读 / 停止朗读」按钮（index.html 渲染，本文件提供 window.voiceSpeakAnswer）：
        播放中图标变成「喇叭 + 斜杠」，再点一次即停止；停止时会中断在途请求并释放音频 URL
      - 顶栏「自动朗读」为按钮式开关：开＝绿字 + 浅绿底，关＝灰字 + 浅灰底；
        开启时回答显示后延迟若干秒整段朗读
      - **刷新后默认不自动朗读**；点任意回答左下角「朗读」后，本次页面内后续回答自动朗读，
        顶栏开关也可手动开启 / 关闭
      - 朗读按进度高亮当前句并平滑跟随；长回答分段读到结尾，不再只读前段
      - 朗读中**点一下空白处或回答文字也立即停止**（按钮 / 链接 / 输入框等有自己含义的控件除外，
        点它们照样做那件事，不被打断）
   3) 朗读不可用时明确提示并降级，不影响文本聊天。
   ============================================================ */
(function () {
  "use strict";

  var CONFIG = {
    autoSend: true,               // 识别结束后自动发送（复用现有 #send 按钮）
    // 刷新后默认不自动朗读；用户手动点某条「朗读」后，本次页面内的后续回答自动朗读。
    // 顶栏开关仍可随时手动开启 / 关闭；该状态不写盘，刷新后重新回到关闭。
    autoSpeak: false,
    speakDelayMs: 3000,           // 回答显示后等待多久开始朗读（约 3 秒）
    lang: "zh-CN",                // 识别与朗读语言
    browserChunkChars: 180,        // 设备语音分段长度；长文本逐段读完，不再截断
    allowBareSpaceShortcut: true, // 输入框未聚焦时，空格/回车也可开始识别
    // ---- 服务端朗读（云端 TTS / 本机 Matcha）----
    serverTts: true,              // 允许走服务端朗读（云端 / 本机 Matcha；是否可用由 /api/tts/status 决定）
    ttsUrl: "/api/tts",           // 合成接口（返回 audio/wav；云端转发或本机 Matcha 合成）
    ttsStatusUrl: "/api/tts/status",
    ttsCharsPerRequest: 800,      // 每次请求的字符上限（服务端默认上限 1000，留点余量）
    ttsTimeoutMs: 30000,          // 单次请求超时（超时按失败处理，不会一直转圈）
    localAsrUrl: "/api/asr/transcribe",
    asrTimeoutMs: 45000,          // 手机短语音上传 + 本机 Whisper 识别
    minRecordMs: 350,             // 过短通常是误触，不上传空音频
  };

  var SR = window.SpeechRecognition || window.webkitSpeechRecognition || null;
  var TTS = window.speechSynthesis || null;

  var micBtn = null;
  var holdBtn = null;
  var voiceModeBtn = null;
  var textModeBtn = null;
  var speakBtn = null;
  var recog = null;
  var listening = false;
  var holdActive = false;
  var holdBusy = false;
  var mediaRecorder = null;
  var mediaStream = null;
  var recordChunks = [];
  var holdStartedAt = 0;
  var finalText = "";
  var prefixText = "";
  var speaking = false;
  var pendingTimer = null;
  var voices = [];

  // ---- 服务端朗读状态（云端是否配好 / 本机 Matcha 模型是否就绪）----
  // ready: null = 还没探测完；true = 服务端有一级可用；false = 都没有（reason 里是中文原因）
  var SRV = { ready: null, reason: "", hint: "", engine: "", maxChars: 800, latinWarned: false };
  var serverToken = 0;        // 每次朗读递增：旧请求 / 旧音频一律作废，避免盖住新朗读
  var serverSession = null;   // { token, audio, urls, controller, stopped }
  var browserToken = 0;       // 设备语音逐段播放的会话令牌
  var reading = {
    view: null,
    root: null,
    text: "",
    map: [],
    lastRatio: -1,
    lastScrollAt: 0,
    scrollTarget: -1,
    scrollGuardUntil: 0,
    followPaused: false,
    overlay: null,
    pulseLayer: null,
    pulses: [],
    pulseRaf: 0,
    unitStart: -1,
    unitEnd: -1,
    unitKind: "",
    syncMode: "",
    lastIndex: -1,
    paused: false,
    workPercent: -1
  };

  /* ---------------- 基础工具 ---------------- */

  function $(id) { return document.getElementById(id); }

  function toast(text, kind) {
    if (typeof window.setToast === "function") {
      window.setToast(text, kind);
      return;
    }
    var node = $("toast");
    if (node) { node.textContent = text || ""; }
  }

  function currentInput() { return $("input"); }

  function syncAutoGrow() {
    if (typeof window.autoGrow === "function") {
      try { window.autoGrow(); } catch (err) { /* 忽略 */ }
    }
  }

  /* ---------------- 语音合成（朗读） ----------------

     链路（固定，无开关）：
       1) 云端 TTS —— 在「设置」页配置（留空则跳过）；它由服务端 /api/tts 转发
       1.5) 本机 Matcha —— 服务端进程内合成，离线；模型缺失时自动跳过
       2) 本设备语音 —— 设备自己的语音系统（iPad＝iPadOS 语音、安卓＝系统语音、电脑＝浏览器音色）
       3) 浏览器语音 —— 第 2 级不可用时的同一条 Web Speech 通道兜底

     Piper 仍在退场状态；本机合成改用服务端的 Matcha，前端不需要请求它的音色/模型。 */

  function loadVoices() {
    if (!TTS) { return; }
    try { voices = TTS.getVoices() || []; } catch (err) { voices = []; }
  }

  // 这台设备是什么（只为在面板/顶栏里如实说明"用谁的嗓子"）
  function deviceNameHint() {
    var ua = String((window.navigator && window.navigator.userAgent) || "");
    if (/iPad/i.test(ua) || (/Macintosh/i.test(ua) && Number(window.navigator.maxTouchPoints) > 1)) { return "iPad"; }
    if (/iPhone|iPod/i.test(ua)) { return "iPhone"; }
    if (/Android/i.test(ua)) { return "Android"; }
    return "浏览器";
  }

  // 浏览器兜底音的挑选：**优先系统提供的本机音色**。
  // 注意 localService 的语义（MDN）：true＝由本地合成器提供，false＝由远端合成器提供。
  // 但 iOS/iPadOS 上，iPadOS 自己的系统语音（含 Siri 音色）**有可能被标成 false** ——
  // 那仍然是设备本机的系统语音、不经过本项目，所以旧文案把它写成"可能走云端"是**误报**，
  // 这里改成如实说明（"系统语音"）；真正的云端音色（如 "Google …"）才保留"可能走云端"。
  function browserVoiceInfo() {
    if (!voices.length) { loadVoices(); }
    var local = [], remote = [], systemish = [], cloudish = [], i, v;
    for (i = 0; i < voices.length; i++) {
      v = voices[i];
      if (v && v.localService === true) { local.push(v); continue; }
      // localService !== true：区分"设备系统语音"与"真的云端音色"
      if (v && !/^Google\b/i.test(String(v.name || "")) && !/^Microsoft\b/i.test(String(v.name || ""))) {
        systemish.push(v);
      } else {
        cloudish.push(v);
      }
      remote.push(v);
    }
    var zhPick = function (list) {
      var j;
      for (j = 0; j < list.length; j++) {
        if (String(list[j].lang || "").toLowerCase() === "zh-cn") { return list[j]; }
      }
      for (j = 0; j < list.length; j++) {
        if (String(list[j].lang || "").toLowerCase().indexOf("zh") === 0) { return list[j]; }
      }
      return null;
    };
    // 优先顺序：本机音色 → 设备系统语音 →（真的没别的了才）云端音色
    var picked = zhPick(local) || zhPick(systemish) || zhPick(cloudish) || local[0] || systemish[0] || cloudish[0] || null;
    var isSystemish = !!(picked && picked.localService !== true
      && !/^Google\b/i.test(String(picked.name || "")) && !/^Microsoft\b/i.test(String(picked.name || "")));
    return {
      voice: picked,
      name: picked ? String(picked.name || "") : "",
      isLocal: !!(picked && picked.localService === true),
      isSystemish: isSystemish,               // 设备系统语音（localService 报 false 但确实在本机）
      hasLocal: local.length > 0,
      localCount: local.length,
      systemCount: systemish.length,
      total: voices.length
    };
  }

  function pickVoice() {
    return browserVoiceInfo().voice;
  }

  /* ---------------- 朗读进度：高亮 + 平滑跟随 ---------------- */

  var READING_HIGHLIGHT = "chat-reading";
  var READING_WORK_ID = "voice-reading";
  var readingWorkActive = false;

  function readingWorkBegin() {
    if (!window.WorkCompanion || readingWorkActive) { return; }
    readingWorkActive = true;
    try {
      window.WorkCompanion.begin(READING_WORK_ID, {
        category: "chat",
        priority: 60,
        motion: "reading",
        label: "朗读中…",
        detail: "朗读进度 0%",
        active: true
      });
    } catch (err) { /* 状态显示失败不影响朗读 */ }
  }

  function readingWorkUpdate(ratio) {
    if (!window.WorkCompanion || !readingWorkActive) { return; }
    var percent = Math.max(0, Math.min(100, Math.round((Number(ratio) || 0) * 100)));
    if (percent !== reading.workPercent) { reading.workPercent = percent; }
    var sync = reading.syncMode === "boundary" ? "字词同步"
      : (reading.syncMode === "audio-time" ? "音频时间近似"
      : (reading.syncMode === "estimated" ? "设备时间近似" : ""));
    try {
      window.WorkCompanion.update(READING_WORK_ID, {
        category: "chat",
        priority: 60,
        motion: "reading",
        label: reading.paused ? "朗读已暂停"
          : (reading.followPaused ? "朗读中 · 自动跟随已暂停" : "朗读中…"),
        detail: "朗读进度 " + percent + "%"
          + (sync ? " · " + sync : "")
          + (reading.followPaused ? " · 由你控制页面位置" : ""),
        active: true
      });
    } catch (err) { /* 忽略 */ }
  }

  function readingWorkFinish(ok, label) {
    if (!window.WorkCompanion || !readingWorkActive) { return; }
    readingWorkActive = false;
    try {
      window.WorkCompanion.finish(READING_WORK_ID, {
        motion: ok ? "success" : "waiting",
        label: label || (ok ? "朗读完成" : "朗读已停止"),
        detail: ok ? "已读到结尾" : "可以随时再次点朗读",
        holdMs: ok ? 4500 : 3000
      });
    } catch (err) { /* 忽略 */ }
  }

  function removeReadingOverlay() {
    if (reading.overlay && reading.overlay.parentNode) {
      reading.overlay.parentNode.removeChild(reading.overlay);
    }
    reading.overlay = null;
  }

  function removeReadingPulseLayer() {
    if (reading.pulseRaf) {
      window.cancelAnimationFrame(reading.pulseRaf);
      reading.pulseRaf = 0;
    }
    reading.pulses = [];
    if (reading.pulseLayer && reading.pulseLayer.parentNode) {
      reading.pulseLayer.parentNode.removeChild(reading.pulseLayer);
    }
    reading.pulseLayer = null;
  }

  function clearReadingProgress() {
    try {
      if (reading.root) { reading.root.classList.remove("is-reading-aloud"); }
      removeReadingOverlay();
      removeReadingPulseLayer();
      if (window.CSS && window.CSS.highlights) {
        window.CSS.highlights.delete(READING_HIGHLIGHT);
      }
    } catch (err) { /* 高亮是增强项，绝不影响朗读 */ }
    reading.view = null;
    reading.root = null;
    reading.text = "";
    reading.map = [];
    reading.lastRatio = -1;
    reading.lastScrollAt = 0;
    reading.scrollTarget = -1;
    reading.scrollGuardUntil = 0;
    reading.followPaused = false;
    reading.unitStart = -1;
    reading.unitEnd = -1;
    reading.unitKind = "";
    reading.syncMode = "";
    reading.lastIndex = -1;
    reading.paused = false;
    reading.workPercent = -1;
  }

  function buildReadingMap(root) {
    var text = "";
    var map = [];
    if (!root) { return { text: text, map: map }; }
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    var node = walker.nextNode();
    var lastSpace = false;
    while (node) {
      var value = String(node.nodeValue || "");
      for (var i = 0; i < value.length; i++) {
        var ch = value.charAt(i);
        if (/\s/.test(ch)) {
          if (!text || lastSpace) { continue; }
          text += " ";
          map.push({ node: node, offset: i });
          lastSpace = true;
          continue;
        }
        text += ch;
        map.push({ node: node, offset: i });
        lastSpace = false;
      }
      node = walker.nextNode();
    }
    return { text: text, map: map };
  }

  function beginReadingProgress(view) {
    clearReadingProgress();
    if (!view) { return; }
    var root = view.answerBody || view.content;
    if (!root) { return; }
    var built = buildReadingMap(root);
    if (!built.map.length) { return; }
    reading.view = view;
    reading.root = root;
    reading.text = built.text;
    reading.map = built.map;
    try { root.classList.add("is-reading-aloud"); } catch (err) { /* 忽略 */ }
    readingWorkBegin();
  }

  function readingRange(start, end) {
    if (!reading.map.length) { return null; }
    var map = reading.map;
    start = Math.max(0, Math.min(map.length - 1, start));
    end = Math.max(start + 1, Math.min(map.length, end));
    var first = map[start];
    var last = map[end - 1];
    if (!first || !last) { return null; }
    try {
      var range = document.createRange();
      range.setStart(first.node, first.offset);
      range.setEnd(last.node, last.offset + 1);
      return range;
    } catch (err) {
      return null;
    }
  }

  function paintReadingFallback(range) {
    if (!reading.root || !range) { return; }
    if (!reading.overlay) {
      reading.overlay = document.createElement("div");
      reading.overlay.className = "reading-overlay";
      reading.overlay.setAttribute("aria-hidden", "true");
      reading.root.appendChild(reading.overlay);
    }
    reading.overlay.innerHTML = "";
    var rootRect = reading.root.getBoundingClientRect();
    var rects = range.getClientRects ? range.getClientRects() : [];
    for (var i = 0; i < rects.length; i++) {
      var rect = rects[i];
      if (!rect || rect.width < 1 || rect.height < 1) { continue; }
      var chip = document.createElement("span");
      chip.style.left = Math.round(rect.left - rootRect.left) + "px";
      chip.style.top = Math.round(rect.top - rootRect.top) + "px";
      chip.style.width = Math.round(rect.width) + "px";
      chip.style.height = Math.round(rect.height) + "px";
      reading.overlay.appendChild(chip);
    }
  }

  function isCjk(ch) {
    return /[\u3400-\u9fff\uf900-\ufaff]/.test(ch);
  }

  function isWordChar(ch) {
    return /[A-Za-z0-9_'\u00c0-\u024f]/.test(ch);
  }

  function readingUnitFor(index, kind) {
    var text = reading.text;
    var max = text.length;
    index = Math.max(0, Math.min(max - 1, Number(index) || 0));
    while (index < max - 1 && /\s/.test(text.charAt(index))) { index += 1; }
    var start = index;
    var end = start + 1;
    if (kind === "sentence") {
      while (end < max && end - start < 96) {
        var sentenceChar = text.charAt(end);
        end += 1;
        if ("。！？!?；;".indexOf(sentenceChar) >= 0) { break; }
      }
    } else if (kind === "word" && isWordChar(text.charAt(start))) {
      while (end < max && isWordChar(text.charAt(end))) { end += 1; }
    } else if (!isCjk(text.charAt(start))
        && text.charCodeAt(start) >= 0xd800
        && text.charCodeAt(start) <= 0xdbff
        && end < max) {
      var nextCode = text.charCodeAt(end);
      if (nextCode >= 0xdc00 && nextCode <= 0xdfff) { end += 1; }
    }
    return { start: start, end: end, kind: kind || "char" };
  }

  function ensureReadingPulseCanvas() {
    if (!reading.root) { return null; }
    if (reading.pulseLayer && reading.pulseLayer.isConnected) {
      return reading.pulseLayer;
    }
    var canvas = document.createElement("canvas");
    canvas.className = "reading-pulse-layer";
    canvas.setAttribute("aria-hidden", "true");
    reading.root.appendChild(canvas);
    reading.pulseLayer = canvas;
    return canvas;
  }

  function easeReadingPulse(value) {
    value = Math.max(0, Math.min(1, value));
    return 1 - Math.pow(1 - value, 3);
  }

  function drawReadingPulses(timestamp) {
    reading.pulseRaf = 0;
    var canvas = reading.pulseLayer;
    if (!canvas || !reading.root || !canvas.isConnected) { return; }
    var rootRect = reading.root.getBoundingClientRect();
    var dpr = Math.max(1, Math.min(2, Number(window.devicePixelRatio) || 1));
    var width = Math.max(1, Math.round(rootRect.width));
    var height = Math.max(1, Math.round(rootRect.height));
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = width + "px";
      canvas.style.height = height + "px";
    }
    var ctx = canvas.getContext ? canvas.getContext("2d") : null;
    if (!ctx) { return; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    var reduce = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var remaining = [];
    var activeScale = 1;
    var activeColor = "";
    var needsFrame = false;
    var i;
    for (i = 0; i < reading.pulses.length; i++) {
      var pulse = reading.pulses[i];
      if (!pulse) { continue; }
      var age = Math.max(0, timestamp - pulse.startedAt);
      var enter = easeReadingPulse(age / 190);
      var leave = pulse.leavingAt ? Math.max(0, Math.min(1, (timestamp - pulse.leavingAt) / 170)) : 0;
      if (leave >= 1) { continue; }
      var scale = reduce ? 1 : (0.98 + 0.12 * enter);
      var alpha = reduce ? 1 : Math.min(1, age / 120);
      if (!reduce && !leave && age < 190) { needsFrame = true; }
      if (leave && leave < 1) { needsFrame = true; }
      if (leave) {
        scale = 1.1 - 0.1 * easeReadingPulse(leave);
        alpha *= 1 - easeReadingPulse(leave);
      }
      ctx.save();
      ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
      ctx.fillStyle = pulse.color;
      ctx.font = pulse.font;
      ctx.translate(pulse.x, pulse.y);
      ctx.scale(scale, scale);
      ctx.fillText(pulse.text, 0, 0);
      ctx.restore();
      if (!leave) {
        activeScale = Math.max(activeScale, scale);
        activeColor = pulse.color;
      }
      remaining.push(pulse);
    }
    canvas.dataset.scale = activeScale.toFixed(3);
    canvas.dataset.fontWeight = "600";
    canvas.dataset.color = activeColor;
    reading.pulses = remaining;
    if (needsFrame) {
      reading.pulseRaf = window.requestAnimationFrame(drawReadingPulses);
    } else {
      reading.pulseRaf = 0;
    }
  }

  function paintReadingPulse(range) {
    if (!reading.root || !range) { return; }
    var rects = range.getClientRects ? range.getClientRects() : [];
    if (rects.length !== 1) { return; }
    var rect = rects[0];
    var text = String(range.toString() || "");
    if (!text || /^\s+$/.test(text) || !rect || rect.width < 1 || rect.height < 1) {
      return;
    }
    var canvas = ensureReadingPulseCanvas();
    if (!canvas) { return; }
    var parent = range.startContainer;
    if (parent && parent.nodeType !== 1) { parent = parent.parentElement; }
    var style = parent && window.getComputedStyle ? window.getComputedStyle(parent) : null;
    var rootRect = reading.root.getBoundingClientRect();
    var fontSize = style ? style.fontSize : "16px";
    var fontStyle = style ? style.fontStyle : "normal";
    var fontFamily = style ? style.fontFamily : "sans-serif";
    var color = window.getComputedStyle
      ? window.getComputedStyle(document.documentElement).getPropertyValue("--brand-2").trim()
      : "";
    var now = typeof performance !== "undefined" && performance.now
      ? performance.now() : Date.now();
    var i;
    for (i = 0; i < reading.pulses.length; i++) {
      if (!reading.pulses[i].leavingAt) { reading.pulses[i].leavingAt = now; }
    }
    reading.pulses.push({
      text: text,
      x: rect.left - rootRect.left + rect.width / 2,
      y: rect.top - rootRect.top + rect.height / 2,
      font: fontStyle + " 600 " + fontSize + " " + fontFamily,
      color: color || "#1d4ed8",
      startedAt: now,
      leavingAt: 0
    });
    while (reading.pulses.length > 4) { reading.pulses.shift(); }
    if (!reading.pulseRaf) {
      reading.pulseRaf = window.requestAnimationFrame(drawReadingPulses);
    }
  }

  function scrollReadingRange(range) {
    var area = $("chatArea");
    if (!area || !range) { return; }
    if (reading.followPaused) { return; }
    var rect = range.getBoundingClientRect();
    var areaRect = area.getBoundingClientRect();
    if (!rect || !areaRect || !rect.height) { return; }
    var topGuard = areaRect.top + Math.min(150, areaRect.height * 0.22);
    var bottomGuard = areaRect.bottom - Math.min(150, areaRect.height * 0.20);
    if (rect.top >= topGuard && rect.bottom <= bottomGuard) { return; }
    var now = Date.now();
    if (now - reading.lastScrollAt < 120) { return; }
    reading.lastScrollAt = now;
    if (now < reading.scrollGuardUntil) { return; }
    var wanted = area.scrollTop + rect.top - topGuard;
    if (reading.scrollTarget < 0) {
      // 第一次允许从会话底部回到本条回答开头。
      reading.scrollTarget = Math.max(0, wanted);
    } else if (wanted > reading.scrollTarget + 2) {
      // 之后只随朗读进度向下，不让逐字重排把页面往回拽。
      reading.scrollTarget = wanted;
    }
    var target = reading.scrollTarget;
    var reduce = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    reading.scrollGuardUntil = now + 500;
    try {
      area.scrollTo({
        top: Math.max(0, target),
        behavior: reduce ? "auto" : "smooth"
      });
    } catch (err) {
      area.scrollTop = Math.max(0, target);
    }
  }

  function setReadingUnit(index, options) {
    if (!reading.map.length) { return; }
    options = options || {};
    var ratio = Number(options.ratio);
    if (!isFinite(ratio)) {
      ratio = Math.max(0, Math.min(1, Number(index) / Math.max(1, reading.text.length)));
    }
    ratio = Math.max(0, Math.min(1, ratio));
    if (reading.paused) {
      readingWorkUpdate(Math.max(0, reading.lastRatio));
      return;
    }
    reading.lastRatio = ratio;
    var mode = String(options.syncMode || reading.syncMode || "estimated");
    reading.syncMode = mode;
    var kind = String(options.unitKind || "char");
    // 服务端音频只有播放时间，没有边界事件：只在单个字符附近做明确标记的近似推进。
    if (mode !== "boundary") { kind = "char"; }
    var at = Math.max(0, Math.min(reading.text.length - 1, Number(index) || 0));
    if (reading.lastIndex >= 0 && at < reading.lastIndex) { at = reading.lastIndex; }
    var unit = readingUnitFor(at, kind);
    if (unit.start === reading.unitStart && unit.end === reading.unitEnd
        && unit.kind === reading.unitKind) {
      readingWorkUpdate(ratio);
      return;
    }
    reading.lastIndex = at;
    reading.unitStart = unit.start;
    reading.unitEnd = unit.end;
    reading.unitKind = unit.kind;
    var range = readingRange(unit.start, unit.end);
    if (!range) { return; }
    var nativeHighlight = false;
    try {
      if (window.CSS && window.CSS.highlights && typeof window.Highlight === "function") {
        window.CSS.highlights.set(READING_HIGHLIGHT, new window.Highlight(range));
        nativeHighlight = true;
      }
    } catch (err) { /* 不支持高亮时仍继续滚动 */ }
    if (!nativeHighlight) { paintReadingFallback(range); }
    paintReadingPulse(range);
    readingWorkUpdate(ratio);
    scrollReadingRange(range);
  }

  function updateReadingProgress(ratio, options) {
    if (!reading.map.length) { return; }
    ratio = Math.max(0, Math.min(1, Number(ratio) || 0));
    var charIndex = Math.floor(ratio * reading.text.length);
    setReadingUnit(charIndex, Object.assign({ ratio: ratio }, options || {}));
  }

  function pauseReadingFollow() {
    if (!reading.view || reading.followPaused) { return; }
    reading.followPaused = true;
    readingWorkUpdate(Math.max(0, reading.lastRatio));
    toast("已暂停朗读自动跟随；朗读继续，页面位置由你控制");
  }

  function bindReadingUserPriority() {
    var area = $("chatArea");
    if (!area) { return; }
    area.addEventListener("wheel", pauseReadingFollow, { passive: true });
    area.addEventListener("touchmove", pauseReadingFollow, { passive: true });
    area.addEventListener("pointerdown", function (event) {
      if (isOwnAction(event && event.target)) { return; }
      pauseReadingFollow();
    }, { passive: true });
    document.addEventListener("keydown", function (event) {
      if (!reading.view) { return; }
      if (["PageUp", "PageDown", "ArrowUp", "ArrowDown", "Home", "End", " "].indexOf(event.key) < 0) {
        return;
      }
      var tag = event.target ? String(event.target.tagName || "").toUpperCase() : "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") { return; }
      pauseReadingFollow();
    }, false);
  }

  function cancelSpeak() {
    readingWorkFinish(false, "朗读已停止");
    if (pendingTimer) { window.clearTimeout(pendingTimer); pendingTimer = null; }
    browserToken += 1;
    stopServerSpeak();               // 本机合成：中断在途请求 + 停音频 + 释放对象 URL
    if (TTS && (speaking || TTS.speaking || TTS.pending)) {
      try { TTS.cancel(); } catch (err) { /* 忽略 */ }
    }
    speaking = false;
    clearReadingProgress();
    markAnswerBtn(false);
    updateSpeakBtn();
  }

  function pauseSpeech() {
    if (!speaking || reading.paused) { return false; }
    var paused = false;
    try {
      if (serverSession && serverSession.audio) {
        serverSession.audio.pause();
        paused = true;
      } else if (TTS && typeof TTS.pause === "function") {
        TTS.pause();
        paused = true;
      }
    } catch (err) { /* 暂停不可用时不假装已暂停 */ }
    if (!paused) { return false; }
    reading.paused = true;
    if (answerBtn && typeof window.setAnswerPauseState === "function") {
      try { window.setAnswerPauseState(answerBtn, true); } catch (err) { /* 忽略 */ }
    }
    readingWorkUpdate(Math.max(0, reading.lastRatio));
    updateSpeakBtn();
    return true;
  }

  function resumeSpeech() {
    if (!speaking || !reading.paused) { return false; }
    reading.paused = false;
    if (answerBtn && typeof window.setAnswerPauseState === "function") {
      try { window.setAnswerPauseState(answerBtn, false); } catch (err) { /* 忽略 */ }
    }
    try {
      if (serverSession && serverSession.audio) {
        var started = serverSession.audio.play();
        if (started && typeof started.catch === "function") {
          started.catch(function () {
            toast("无法继续播放朗读音频，请重新点「朗读」", "bad");
          });
        }
      } else if (TTS && typeof TTS.resume === "function") {
        TTS.resume();
      }
    } catch (err) { /* 恢复失败由原朗读错误路径处理 */ }
    readingWorkUpdate(Math.max(0, reading.lastRatio));
    updateSpeakBtn();
    return true;
  }

  // 由「回答底部朗读按钮」触发的朗读：记住按钮，便于结束后切回「朗读」
  var answerBtn = null;

  function markAnswerBtn(on) {
    if (answerBtn && typeof window.setAnswerSpeakState === "function") {
      try { window.setAnswerSpeakState(answerBtn, on); } catch (err) { /* 绝不影响朗读 */ }
    }
    if (on && answerBtn && typeof window.setAnswerPauseState === "function") {
      try { window.setAnswerPauseState(answerBtn, false); } catch (err) { /* 忽略 */ }
    }
    if (!on) { answerBtn = null; }
  }

  // 浏览器 / 设备语音：按 180 字左右分段连续播放，长文本不再截断。
  function speakBrowser(text, btn, view) {
    if (!TTS || !text) { return false; }
    var pieces = splitForServer(text, CONFIG.browserChunkChars);
    if (!pieces.length) { return false; }
    browserToken += 1;
    var token = browserToken;
    try { TTS.cancel(); } catch (err) { /* 忽略 */ }

    var totalChars = 0;
    var i;
    for (i = 0; i < pieces.length; i++) { totalChars += pieces[i].length; }
    if (!totalChars) { return false; }

    var index = 0;
    var spokenChars = 0;
    var errorCount = 0;
    var estimateTimer = null;
    var estimateGraceTimer = null;
    beginReadingProgress(view);
    speaking = true;
    answerBtn = btn || null;
    markAnswerBtn(true);
    updateSpeakBtn();

    function stopEstimate() {
      if (estimateTimer) { window.clearInterval(estimateTimer); estimateTimer = null; }
      if (estimateGraceTimer) { window.clearTimeout(estimateGraceTimer); estimateGraceTimer = null; }
    }

    function finish(ok) {
      stopEstimate();
      if (token !== browserToken) { return; }
      var completed = ok !== false;
      if (completed) { updateReadingProgress(1); }
      readingWorkFinish(completed, completed ? "朗读完成" : "朗读失败");
      speaking = false;
      markAnswerBtn(false);
      clearReadingProgress();
      updateSpeakBtn();
    }

    function startEstimate(piece, baseChars) {
      stopEstimate();
      var startedAt = Date.now();
      var pausedAt = 0;
      var pausedTotal = 0;
      // 中文正常语速约 4~5 字/秒；仅用于没有 onboundary 的浏览器，
      // 并在状态栏明确标记“设备时间近似”。有原生边界时不启用。
      var expectedMs = Math.max(1200, piece.length * 210);
      // 先留出一个短暂时间窗：支持边界的引擎会在几十毫秒内给出首个事件，
      // 不能让它与独立计时器同时推进，否则动画会跑到真实读音之前。
      estimateGraceTimer = window.setTimeout(function () {
        estimateGraceTimer = null;
        estimateTimer = window.setInterval(function () {
          if (token !== browserToken || !speaking) { stopEstimate(); return; }
          var nowAt = Date.now();
          if (reading.paused) {
            if (!pausedAt) { pausedAt = nowAt; }
            return;
          }
          if (pausedAt) {
            pausedTotal += nowAt - pausedAt;
            pausedAt = 0;
          }
          var elapsed = nowAt - startedAt - pausedTotal;
          var within = Math.min(piece.length, piece.length * elapsed / expectedMs);
          updateReadingProgress((baseChars + within) / totalChars, {
            syncMode: "estimated",
            unitKind: "sentence"
          });
          if (elapsed > expectedMs) { stopEstimate(); }
        }, 350);
      }, 650);
    }

    function next() {
      if (token !== browserToken || !speaking) { return; }
      if (index >= pieces.length) { finish(errorCount === 0); return; }
      var piece = pieces[index];
      var baseChars = spokenChars;
      var pieceBoundarySeen = false;
      var utter = new window.SpeechSynthesisUtterance(piece);
      utter.lang = CONFIG.lang;
      var voice = pickVoice();
      // 音色赋值必须容错：某些浏览器上若传进去的不是真正的 SpeechSynthesisVoice 对象，
      // 这一句会抛 TypeError 并打断整个朗读。
      if (voice) { try { utter.voice = voice; } catch (err) { /* 忽略 */ } }
      utter.rate = 1;
      utter.pitch = 1;
      utter.onboundary = function (event) {
        if (token !== browserToken || !speaking) { return; }
        var at = Number(event && event.charIndex) || 0;
        at = Math.max(0, Math.min(piece.length - 1, at));
        pieceBoundarySeen = true;
        stopEstimate();
        var kind = String((event && event.name) || "char");
        updateReadingProgress((baseChars + at) / totalChars, {
          syncMode: "boundary",
          unitKind: kind
        });
      };
      utter.onend = function () {
        if (token !== browserToken) { return; }
        stopEstimate();
        spokenChars = baseChars + piece.length;
        index += 1;
        updateReadingProgress(spokenChars / totalChars, {
          syncMode: pieceBoundarySeen ? "boundary" : "estimated"
        });
        next();
      };
      utter.onerror = function () {
        if (token !== browserToken || !speaking) { return; }
        stopEstimate();
        errorCount += 1;
        spokenChars = baseChars + piece.length;
        index += 1;
        if (errorCount >= 3) { finish(false); return; }
        next();
      };
      startEstimate(piece, baseChars);
      try {
        TTS.speak(utter);
      } catch (err) {
        stopEstimate();
        errorCount += 1;
        spokenChars = baseChars + piece.length;
        index += 1;
        if (errorCount >= 3) { finish(false); return; }
        next();
      }
    }

    next();
    return true;
  }

  /* ---------------- 服务端朗读（云端 TTS / 本机 Matcha） ----------------
     服务端 /api/tts 先尝试云端（OpenAI 兼容 /audio/speech），再尝试本机 Matcha。
     两级都不可用 → 如实返回中文原因，前端据此跳到本设备语音。 */

  // 页面打开时探测一次。warm=1 让服务端把本机 Matcha 模型加载好（首次合成少等约 2 秒）；
  // 云端仍然只做可达性探测，不占 CPU。
  function probeServerTts() {
    if (!CONFIG.serverTts || typeof window.fetch !== "function") {
      SRV.ready = false;
      SRV.reason = "当前浏览器不支持 fetch，无法使用云端朗读";
      updateSpeakBtn();
      return;
    }
    window.fetch(CONFIG.ttsStatusUrl + "?warm=1", { method: "GET", cache: "no-store" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (info) {
        if (!info) { SRV.ready = false; SRV.reason = "朗读接口没有响应"; updateSpeakBtn(); return; }
        SRV.engine = String(info.engine || "");
        SRV.ready = !!info.ready;
        SRV.reason = String(info.reason || "");
        SRV.hint = String(info.hint || "");
        // 服务端给出的链路（现在只有 cloud → browser）与"当前生效的是谁"
        SRV.chain = info.chain || [];
        SRV.activeProvider = String(info.active_provider || "");
        SRV.chainOrder = info.chain_order || [];
        SRV.browserFallback = !!info.browser_fallback;
        if (info.max_chars) {
          SRV.maxChars = Math.max(120, Math.min(CONFIG.ttsCharsPerRequest, Number(info.max_chars)));
        }
        updateSpeakBtn();
      })
      .catch(function () {
        SRV.ready = false;
        SRV.reason = "朗读接口不可用（服务未启动或网络中断）";
        updateSpeakBtn();
      });
  }

  // 把长文本按句子切成不超过 limit 的片段（保留正文、空格、标点与顺序）。
  // 空格必须留在片段里：TTS 的 charIndex 与拼接后的全文索引才有同一坐标系，
  // 否则中英混排时“当前正在读哪个字”会越读越偏。
  // 注意：不用后行断言(?<=...)，老浏览器会整份脚本解析失败，麦克风也会一起坏掉。
  function splitForServer(text, limit) {
    var source = String(text || "").replace(/\s+/g, " ").trim();
    if (!source) { return []; }
    var ends = "。！？!?…；;";
    var pieces = [], start = 0, i;
    for (i = 0; i < source.length; i++) {
      var ch = source.charAt(i);
      if (ends.indexOf(ch) >= 0) {
        pieces.push(source.slice(start, i + 1));
        start = i + 1;
        continue;
      }
      if (i - start + 1 >= limit) {
        var cut = i + 1;
        var space = source.lastIndexOf(" ", cut - 1);
        if (space > start + Math.floor(limit * 0.55)) { cut = space + 1; }
        pieces.push(source.slice(start, cut));
        start = cut;
        i = cut - 1;
      }
    }
    if (start < source.length) { pieces.push(source.slice(start)); }
    // 打包：把相邻句子并成一包（默认每包最多 150 字，再大首声会等太久）
    // 这样 1200 字的回答只需个位数请求，而不是每句一次
    var packLimit = Math.min(limit, 150);
    if (packLimit < 120) { packLimit = Math.min(limit, 120); }
    var merged = [];
    for (i = 0; i < pieces.length; i++) {
      var last = merged.length ? merged[merged.length - 1] : "";
      if (last && last.length + pieces[i].length <= packLimit) {
        merged[merged.length - 1] = last + pieces[i];
      } else {
        merged.push(pieces[i]);
      }
    }
    return merged;
  }

  // 释放一次朗读会话：中断在途请求、停掉音频、回收所有对象 URL
  function releaseSession(session) {
    if (!session) { return; }
    if (session.controller) {
      try { session.controller.abort(); } catch (err) { /* 忽略 */ }
      session.controller = null;
    }
    if (session.audio) {
      try { session.audio.pause(); } catch (err) { /* 忽略 */ }
      try { session.audio.removeAttribute("src"); } catch (err) { /* 忽略 */ }
      session.audio = null;
    }
    for (var i = 0; i < session.urls.length; i++) {
      try { window.URL.revokeObjectURL(session.urls[i]); } catch (err) { /* 忽略 */ }
    }
    session.urls = [];
  }

  function stopServerSpeak() {
    serverToken += 1;                       // 令牌递增：所有在途回调随即失效，旧音频不会盖住新朗读
    if (serverSession) { releaseSession(serverSession); serverSession = null; }
  }

  // 送服务端的最小请求体：**只带文本**。
  // 为什么这么小：服务端只需要文本；云端音色/语速在设置页，本机 Matcha 的参数在 .env（TTS_LOCAL_*）。
  // 教训： 清掉旧字段时漏掉了这个函数，云端分支一调用就 ReferenceError、
  // 静默落到设备语音 —— 现在由 tests/ui/verify_tts_engine.py 的「云端可用」场景守住。
  function serverBody(text) {
    return { text: String(text || "") };
  }

  // 用服务端接口（云端 / 本机 Matcha）逐段合成并连播；返回 false = 这段文本没法走服务端（交给设备语音）
  function speakServer(text, btn, view) {
    var pieces = splitForServer(text, SRV.maxChars);
    if (!pieces.length) { return false; }

    stopServerSpeak();                      // 先作废上一轮（含在途请求与旧音频）
    var token = ++serverToken;
    var session = { token: token, audio: null, urls: [], controller: null };
    serverSession = session;
    var index = 0;
    var failed = false;
    var totalChars = 0;
    var playedChars = 0;
    var charOffsets = [];
    var p;
    for (p = 0; p < pieces.length; p++) {
      charOffsets.push(totalChars);
      totalChars += pieces[p].length;
    }

    speaking = true;
    answerBtn = btn || null;
    beginReadingProgress(view);
    markAnswerBtn(true);
    updateSpeakBtn();

    function finish(completed) {
      if (serverSession === session) { serverSession = null; }
      releaseSession(session);
      if (completed) { updateReadingProgress(1); }
      readingWorkFinish(!!completed, completed ? "朗读完成" : "朗读已停止");
      speaking = false;
      markAnswerBtn(false);
      clearReadingProgress();
      updateSpeakBtn();
    }

    /* 失败：先把状态清干净，再给明确中文原因；降级时也先提示，绝不静默切换。
       按新链路：服务端失败 → **本设备语音**；设备语音不可用 → 同通道的浏览器语音兜底。 */
    function fail(message) {
      if (failed) { return; }
      failed = true;
      finish(false);
      var detail = message || "服务端语音合成失败";
      if (SRV.reason) { detail = SRV.reason + "（" + detail + "）"; }
      if (SRV.hint) { detail += "；" + SRV.hint; }
      if (!TTS) {
        toast("服务端朗读不可用：" + detail + "；本设备也没有可用的语音", "bad");
        return;
      }
      var dv = browserVoiceInfo();
      if (dv.voice) {
        toast("服务端朗读不可用：" + detail + "。已改用本设备语音（"
          + deviceNameHint() + "｜" + dv.voice.name + "）。", "bad");
      } else {
        toast("服务端朗读不可用：" + detail + "。本设备也没有可用音色，已用浏览器默认语音。", "bad");
      }
      speakBrowser(text, btn, view);
    }

    function playNext() {
      if (session.token !== serverToken || failed) { return; }
      if (index >= pieces.length) { finish(true); return; }

      var controller = (typeof window.AbortController === "function") ? new window.AbortController() : null;
      session.controller = controller;
      var timer = window.setTimeout(function () {          // 超时保护：不会一直转圈
        if (controller) { try { controller.abort(); } catch (err) { /* 忽略 */ } }
      }, CONFIG.ttsTimeoutMs);

      window.fetch(CONFIG.ttsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(serverBody(pieces[index])),
        cache: "no-store",
        signal: controller ? controller.signal : undefined
      }).then(function (res) {
        if (!res.ok) {
          return res.json().then(function (body) {
            var detail = body && body.detail ? body.detail : null;
            if (detail && detail.hint) { SRV.hint = String(detail.hint); }
            throw new Error(detail && detail.message ? detail.message : ("接口返回 HTTP " + res.status));
          }, function () { throw new Error("接口返回 HTTP " + res.status); });
        }
        // 本机 Matcha 对英文词支持有限：服务端用响应头如实标记；每次页面只提示一次。
        var warn = (res.headers && typeof res.headers.get === "function")
          ? String(res.headers.get("X-TTS-Warnings") || "") : "";
        if (warn.indexOf("latin") >= 0 && !SRV.latinWarned) {
          SRV.latinWarned = true;
          toast("本机朗读对英文词支持有限（PDF / CPU 等可能被跳过）；可在 .env 设 TTS_LOCAL_LATIN=fallback 改用设备语音", "bad");
        }
        return res.blob();
      }).then(function (blob) {
        window.clearTimeout(timer);
        if (session.token !== serverToken || failed) { return; }
        if (!blob || !blob.size) { throw new Error("返回的音频是空的"); }
        var url = window.URL.createObjectURL(blob);
        session.urls.push(url);
        var audio = new window.Audio(url);
        session.audio = audio;
        var baseChars = charOffsets[index] || 0;
        var piece = pieces[index] || "";
        audio.ontimeupdate = function () {
          if (session.token !== serverToken || failed) { return; }
          var duration = Number(audio.duration) || 0;
          var current = Number(audio.currentTime) || 0;
          if (duration <= 0) { return; }
          var within = piece.length * Math.min(1, current / duration);
          updateReadingProgress((baseChars + within) / Math.max(1, totalChars), {
            syncMode: "audio-time"
          });
        };
        audio.onended = function () {
          if (session.token !== serverToken) { return; }
          playedChars = baseChars + piece.length;
          updateReadingProgress(playedChars / Math.max(1, totalChars), {
            syncMode: "audio-time"
          });
          index += 1;
          playNext();                                     // 播完一段再取下一段
        };
        audio.onerror = function () { fail("音频播放失败"); };
        var started = audio.play();
        if (started && typeof started.catch === "function") {
          started.catch(function () { fail("浏览器拦截了自动播放，请先点一下页面再试"); });
        }
      }).catch(function (error) {
        window.clearTimeout(timer);
        if (session.token !== serverToken || failed) { return; }   // 已被新朗读 / 停止取代：不报错
        if (error && error.name === "AbortError") {
          fail("本机朗读超时（" + Math.round(CONFIG.ttsTimeoutMs / 1000) + " 秒未响应）");
          return;
        }
        fail(error && error.message ? error.message : "本机朗读请求失败");
      });
    }

    playNext();
    return true;
  }

  // 混合引擎判定：本机有接口就优先本机；确定不可用时才走浏览器语音
  function ttsAvailable() {
    if (CONFIG.serverTts && SRV.ready !== false) { return true; }
    return !!TTS;
  }

  // 统一入口（自动朗读与手动朗读都走这里）
  //   链路：云端 TTS（填了才用）→ 本机 Matcha（就绪就用）→ 本设备语音 → 浏览器语音
  function speak(text, btn, view) {
    if (!text) { return; }
    if (!btn && view && view.content && view.content.querySelector) {
      btn = view.content.querySelector('[data-act="speak"]');
    }
    // 第 1 级：服务端朗读（云端 / 本机 Matcha）。都不可用 → SRV.ready === false，直接跳过
    if (CONFIG.serverTts && SRV.ready !== false && speakServer(text, btn, view)) { return; }
    // 第 2/3 级：本设备语音，不可用时同一条 Web Speech 通道兜底
    speakBrowser(text, btn, view);
  }

  // 顶栏提示：如实显示"现在是谁在念"
  //   服务端有引擎（云端 / 本机 Matcha）→ 显示实际生效的那一级；否则 → 本设备语音
  function engineLabel() {
    var i, items, downgraded, item;
    if (SRV.ready === true) {
      var active = SRV.activeProvider;
      var name = active === "cloud" ? "云端 TTS"
        : (active === "local" ? "本机朗读（Matcha）" : (SRV.engine || "服务端朗读"));
      items = SRV.chain || [];
      for (i = 0; i < items.length; i++) {
        if (items[i].provider === active) { item = items[i]; break; }
      }
      if (item && item.label) { name = item.label; }
      downgraded = "";
      if (active === "local") {
        for (i = 0; i < items.length; i++) {
          if (items[i].provider === "cloud" && items[i].ready === false) {
            downgraded = "｜云端未就绪，已用本机朗读";
            break;
          }
        }
      }
      return name + downgraded;
    }
    // 第 2/3 级：本设备语音 / 浏览器语音（同一条 Web Speech 通道）
    var binfo = browserVoiceInfo();
    var nm = binfo.voice ? binfo.voice.name : "";
    var kind = binfo.isLocal || binfo.isSystemish ? "本设备语音" : "浏览器语音";
    if (!binfo.voice) { return kind + "（" + deviceNameHint() + "）｜没有可用音色"; }
    var tag = binfo.isLocal ? "本机音色" : (binfo.isSystemish ? "设备系统语音" : "可能是云端音色");
    if (SRV.ready === null) { return kind + "（" + deviceNameHint() + "）｜音色 " + nm + "（" + tag + "）｜正在探测云端…"; }
    return kind + "（" + deviceNameHint() + "）｜音色 " + nm + "（" + tag + "）";
  }

  // 排障接口：在浏览器控制台执行 voiceDiagnostics() 就能看清"现在用哪个引擎、为什么、
  // 兜底会挑哪个音色"。也用于自动化验收（避免为测试开专门的暗门）。
  window.voiceDiagnostics = function () {
    var binfo = browserVoiceInfo();
    return {
      ready: SRV.ready,
      engine: SRV.engine,
      activeProvider: SRV.activeProvider,
      chainOrder: SRV.chainOrder || [],
      chain: SRV.chain || [],
      reason: SRV.reason,
      hint: SRV.hint,
      browserFallback: !!SRV.browserFallback,
      autoSpeak: !!CONFIG.autoSpeak,
      speaking: !!speaking,
      readingActive: !!(reading.view && reading.map.length),
      readingRatio: Number(reading.lastRatio) || 0,
      readingFollowPaused: !!reading.followPaused,
      readingPaused: !!reading.paused,
      readingSyncMode: String(reading.syncMode || ""),
      readingUnit: reading.unitStart >= 0
        ? String(reading.text.slice(reading.unitStart, reading.unitEnd)) : "",
      readingPulseCount: reading.pulses.length,
      readingPulseCanvas: !!(reading.pulseLayer && reading.pulseLayer.isConnected),
      // 链路：云端 → 本机 Matcha → 本设备语音 → 浏览器语音。SRV.ready 是服务端是否有一级可用。
      cloudReady: SRV.ready === true,
      deviceName: deviceNameHint(),
      engineLabel: engineLabel(),
      browser: {
        voice: binfo.voice ? binfo.voice.name : "",
        isLocal: binfo.isLocal,
        isSystemish: binfo.isSystemish,
        hasLocal: binfo.hasLocal,
        localCount: binfo.localCount,
        systemCount: binfo.systemCount,
        total: binfo.total
      }
    };
  };

  // 供 index.html 的回答底部「朗读」按钮调用
  window.voiceSpeakAnswer = function (view, btn) {
    if (!ttsAvailable()) {
      toast("这台设备没有可用的语音（服务端朗读不可用、系统也没有语音），请改用 Chrome / Edge 浏览器。", "bad");
      return;
    }
    if (speaking && answerBtn && answerBtn === btn) { cancelSpeak(); toast("已停止朗读"); return; }
    // 手动点过一次朗读后，本次页面内的后续回答自动朗读；刷新后仍回到默认关闭。
    CONFIG.autoSpeak = true;
    updateSpeakBtn();
    cancelSpeak();                                  // 停掉上一次朗读并取消待朗读
    var text = answerTextFrom(view);
    if (!text) { toast("这段回答没有可朗读的内容"); return; }
    speak(text, btn, view);
  };
  window.voicePauseReading = pauseSpeech;
  window.voiceResumeReading = resumeSpeech;
  window.voiceCancelReading = cancelSpeak;

  // 回答全文后才朗读（延迟若干秒、整段播放，不逐字）
  function scheduleSpeak(view, force) {
    if (!view || view.__restored === true) { return; }
    if (!CONFIG.autoSpeak && !force) { return; }
    if (!ttsAvailable()) { return; }
    var text = answerTextFrom(view);
    if (!text) { return; }
    cancelSpeak();
    pendingTimer = window.setTimeout(function () {
      pendingTimer = null;
      speak(text, null, view);
    }, CONFIG.speakDelayMs);
    updateSpeakBtn();
  }

  // 从回答气泡取"正文"：去掉思考过程 / 参考资料 / 复制按钮等附属区
  function answerTextFrom(view) {
    if (!view || !(view.answerBody || view.content)) { return ""; }
    var clone = (view.answerBody || view.content).cloneNode(true);
    var drop = clone.querySelectorAll(
      ".think-host, .think-box, .refs, .img-refs, "
      + ".chat-material-card, .context-warning, .no-context-actions, "
      + ".answer-note, .meta, .chips, .msg-actions, .reading-pulse-layer"
    );
    for (var i = 0; i < drop.length; i++) {
      if (drop[i].parentNode) { drop[i].parentNode.removeChild(drop[i]); }
    }
    var text = String(clone.textContent || "").replace(/\s+/g, " ").trim();
    if (text.indexOf("（已停止生成）") >= 0) { return ""; }   // 用户主动停止时不朗读
    return text;
  }

  // 顶栏开关外观：开＝绿字 + 浅绿底，关＝灰字 + 浅灰底；正在朗读时加一圈浅绿外光
  function updateSpeakBtn() {
    if (!speakBtn) { return; }
    var on = !!CONFIG.autoSpeak;
    var busy = !!speaking;
    if (speakBtn.__txt) { speakBtn.__txt.textContent = on ? "自动朗读·开" : "自动朗读·关"; }
    if (speakBtn.__ico) { speakBtn.__ico.innerHTML = on ? SPEAK_ON_SVG : SPEAK_OFF_SVG; }
    if (speakBtn.classList) {
      speakBtn.classList.toggle("on", on);
      speakBtn.classList.toggle("off", !on);
      speakBtn.classList.toggle("speaking", busy);
    }
    speakBtn.setAttribute("aria-pressed", on ? "true" : "false");
    if (busy) {
      speakBtn.title = (reading.paused
        ? "朗读已暂停；点回答下方「继续」恢复，点这里或页面空白处停止"
        : (on
          ? "正在朗读…点这里或页面空白处停止；再点一次顺手关掉自动朗读"
          : "正在朗读…点这里或页面空白处立即停止朗读"))
        + "｜引擎：" + engineLabel();
    } else if (pendingTimer) {
      speakBtn.title = "回答已显示，约 3 秒后开始朗读；点这里或页面空白处可取消｜引擎：" + engineLabel();
    } else if (on) {
      speakBtn.title = "自动朗读已开启：AI 回答显示后约 3 秒整段朗读（点击关闭）｜引擎：" + engineLabel();
    } else {
      speakBtn.title = "自动朗读已关闭：点击开启（开启后回答显示约 3 秒开始朗读）｜引擎：" + engineLabel();
    }
  }

  /* ---------------- 点空白处 / 文字即停止朗读 ---------------- */

  // 有自己含义的控件（按钮 / 链接 / 输入框 / 会话项 / 输入区 …）：
  // 点它们是「做那件事」，一律放行，不当作「停止朗读」，避免两个动作同时发生
  function isOwnAction(node) {
    if (!node || typeof node.closest !== "function") { return true; }
    return !!node.closest(
      "button, a, input, textarea, select, label, summary, [contenteditable='true'], " +
      "[role='button'], [role='link'], [role='tab'], " +
      ".mini, .ic-btn, .act, .chip, .session, .composer-inner, #sideMask"
    );
  }

  // 朗读中：点空白处 / 回答文字 ⇒ 立刻停止（手机上比再去找那个小按钮好按）
  function bindTapToStop() {
    document.addEventListener("click", function (event) {
      if (!speaking && !pendingTimer) { return; }                  // 没在朗读：什么都不做
      if (event.button && event.button !== 0) { return; }           // 右键 / 中键不算
      if (event.target && isOwnAction(event.target)) { return; }    // 控件照原样响应
      // 正在拖选文字（准备复制）时不打断朗读
      var sel = null;
      try { sel = window.getSelection ? window.getSelection() : null; } catch (err) { sel = null; }
      if (sel && String(sel.toString() || "").trim()) { return; }
      var wasSpeaking = !!speaking;
      cancelSpeak();
      toast(wasSpeaking ? "已停止朗读" : "已取消本次朗读");
    }, true);
  }

  /* ---------------- 历史语音输入实现（已停用） ----------------
     聊天输入统一为 textarea 后，init() 不再调用这些函数。当前页面没有
     麦克风、按住说话或语音模式切换入口；保留实现只为降低一次性删除风险。 */

  // 返回空字符串 = 可用；否则返回中文原因
  function recognitionAvailable() {
    if (!SR) {
      return "当前浏览器不支持按住说话，请切换「文字输入」（会调用设备自带输入法），或使用 Chrome / Edge / Safari。";
    }
    if (window.isSecureContext === false) {
      return "移动端麦克风需要 HTTPS，或从电脑本机 http://127.0.0.1:8100/web 打开。当前请先切换「文字输入」，会调用设备自带输入法。";
    }
    return "";
  }

  function buildRecog() {
    var node = new SR();
    node.lang = CONFIG.lang;
    node.continuous = false;        // 说一句即结束（稳定优先）
    node.interimResults = true;     // 实时显示识别中的文字
    node.maxAlternatives = 1;

    node.onstart = function () {
      listening = true;
      updateMicBtn();
      toast("正在聆听…（说完自动结束；再点按钮或按 Ctrl+空格 可停止）");
    };

    node.onresult = function (event) {
      var interim = "";
      for (var i = event.resultIndex; i < event.results.length; i++) {
        var res = event.results[i];
        var piece = res[0] ? String(res[0].transcript || "") : "";
        if (res.isFinal) { finalText += piece; } else { interim += piece; }
      }
      var shown = (prefixText + finalText + interim).trim();
      var box = currentInput();
      if (box) { box.value = shown; }
      syncAutoGrow();
      if (interim) { toast("识别中：" + interim); }
    };

    node.onerror = function (event) {
      listening = false;
      holdActive = false;
      updateHoldBtn();
      updateMicBtn();
      var code = String((event && event.error) || "");
      var map = {
        "not-allowed": "麦克风权限被拒绝：请点地址栏左侧的锁/ⓘ 图标，把“麦克风”改为“允许”，然后刷新页面重试。",
        "service-not-allowed": "浏览器拒绝了语音识别服务（可能被系统或企业策略禁用）。",
        "audio-capture": "没有检测到麦克风，请确认麦克风已连接并被系统识别。",
        "no-speech": "没有听到声音，请靠近麦克风再说一次。",
        "network": "语音识别需要联网（Chrome 会把音频送到识别服务），请检查网络后重试。",
        "aborted": "语音识别已停止。"
      };
      toast(map[code] || ("语音识别出错：" + code), "bad");
    };

    node.onend = function () {
      listening = false;
      holdActive = false;
      updateHoldBtn();
      updateMicBtn();
      var text = (prefixText + finalText).trim();
      if (!text) {
        toast("没有识别到内容，请再试一次");
        return;
      }
      var box = currentInput();
      if (box) { box.value = text; }
      syncAutoGrow();
      if (CONFIG.autoSend) {
        toast("识别完成，正在自动发送…");
        window.setTimeout(function () {
          var btn = $("send");
          var generating = btn && (btn.getAttribute("data-mode") === "stop" || String(btn.textContent || "").indexOf("停止") >= 0);
          if (btn && !generating) { btn.click(); }
        }, 250);
      } else {
        toast("识别完成，请点发送");
      }
    };

    return node;
  }

  function startListen() {
    var problem = recognitionAvailable();
    if (problem) { toast(problem, "bad"); return; }
    if (listening) { return; }
    cancelSpeak();                       // 开始说话时先停朗读，避免"自问自答"混在一起
    var box = currentInput();
    prefixText = box ? String(box.value || "").trim() : "";
    if (prefixText) { prefixText += " "; }
    finalText = "";
    if (!recog) {
      try {
        recog = buildRecog();
      } catch (err) {
        toast("语音识别初始化失败：" + (err && err.message ? err.message : err), "bad");
        return;
      }
    }
    try {
      recog.start();
    } catch (err) {
      // InvalidStateError：上一次识别尚未结束，按"正在识别"处理
      listening = true;
      updateMicBtn();
    }
  }

  function stopListen() {
    if (!recog || !listening) { return; }
    try { recog.stop(); } catch (err) { /* 忽略 */ }
  }

  function toggleListen() {
    if (listening) { stopListen(); } else { startListen(); }
  }

  // ---------- 移动端：按住说话 / 文字输入 ----------

  function isMobileComposer() {
    return !!(window.matchMedia && window.matchMedia("(max-width: 900px)").matches);
  }

  function localRecorderAvailable() {
    return !!(window.isSecureContext !== false &&
      window.navigator && window.navigator.mediaDevices &&
      typeof window.navigator.mediaDevices.getUserMedia === "function" &&
      typeof window.MediaRecorder !== "undefined");
  }

  function isFirefoxBrowser() {
    return /firefox/i.test(String((window.navigator && window.navigator.userAgent) || ""));
  }

  function recorderMimeType() {
    if (typeof window.MediaRecorder === "undefined" || !window.MediaRecorder.isTypeSupported) { return ""; }
    var candidates = [
      "audio/webm;codecs=opus",
      "audio/ogg;codecs=opus",
      "audio/mp4",
      "audio/webm"
    ];
    for (var i = 0; i < candidates.length; i++) {
      try {
        if (window.MediaRecorder.isTypeSupported(candidates[i])) { return candidates[i]; }
      } catch (err) { /* 继续尝试下一格式 */ }
    }
    return "";
  }

  function recorderExtension(mime) {
    var type = String(mime || "").toLowerCase();
    if (type.indexOf("mp4") >= 0) { return "m4a"; }
    if (type.indexOf("ogg") >= 0) { return "ogg"; }
    return "webm";
  }

  function stopMediaTracks() {
    if (!mediaStream) { return; }
    try {
      mediaStream.getTracks().forEach(function (track) { try { track.stop(); } catch (err) { /* 忽略 */ } });
    } catch (err) { /* 忽略 */ }
    mediaStream = null;
  }

  function finishHoldUpload() {
    holdBusy = false;
    updateHoldBtn();
  }

  async function uploadHoldRecording(blob, mime) {
    if (!blob || blob.size < 200) {
      finishHoldUpload();
      toast("录音内容为空，请按住靠近麦克风再说一次", "bad");
      return;
    }
    holdBusy = true;
    updateHoldBtn();
    toast("正在使用本机电脑识别…");
    var controller = new AbortController();
    var timer = window.setTimeout(function () { controller.abort(); }, CONFIG.asrTimeoutMs);
    try {
      var form = new FormData();
      form.append("audio", blob, "hold." + recorderExtension(mime));
      var response = await fetch(CONFIG.localAsrUrl, {
        method: "POST",
        body: form,
        signal: controller.signal
      });
      var data = await response.json().catch(function () { return null; });
      if (!response.ok) {
        var detail = data && (data.detail || data.message);
        throw new Error(typeof detail === "string" ? detail : "本机语音识别返回 HTTP " + response.status);
      }
      var text = String((data && data.text) || "").trim();
      if (!text) { throw new Error("本机语音识别没有返回文字"); }
      var box = currentInput();
      if (box) {
        box.value = (prefixText + text).trim();
        syncAutoGrow();
      }
      toast("识别完成");
      if (CONFIG.autoSend) {
        window.setTimeout(function () {
          var btn = $("send");
          var generating = btn && (btn.getAttribute("data-mode") === "stop" || String(btn.textContent || "").indexOf("停止") >= 0);
          if (btn && !generating) { btn.click(); }
        }, 180);
      }
    } catch (err) {
      var message = err && err.name === "AbortError"
        ? "本机语音识别超时，请重试"
        : ("本机语音识别失败：" + ((err && err.message) || "未知错误"));
      toast(message, "bad");
    } finally {
      window.clearTimeout(timer);
      finishHoldUpload();
    }
  }

  async function startLocalRecording() {
    holdActive = true;
    holdBusy = false;
    recordChunks = [];
    holdStartedAt = 0;
    updateHoldBtn();
    var box = currentInput();
    prefixText = box ? String(box.value || "").trim() : "";
    if (prefixText) { prefixText += " "; }
    cancelSpeak();
    try {
      var firefox = isFirefoxBrowser();
      // Android Firefox 的复杂音频约束和 timeslice MediaRecorder 更容易触发
      // 浏览器层崩溃；这里只用最基础的麦克风请求与浏览器默认编码。
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: firefox ? true : {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        }
      });
      if (!holdActive) {
        stopMediaTracks();
        return;
      }
      var mime = firefox ? "" : recorderMimeType();
      mediaRecorder = (!firefox && mime)
        ? new MediaRecorder(mediaStream, { mimeType: mime })
        : new MediaRecorder(mediaStream);
      mediaRecorder.ondataavailable = function (event) {
        if (event.data && event.data.size) { recordChunks.push(event.data); }
      };
      mediaRecorder.onerror = function (event) {
        stopMediaTracks();
        holdActive = false;
        finishHoldUpload();
        toast("录音失败：" + ((event && event.error && event.error.message) || "设备录音不可用"), "bad");
      };
      mediaRecorder.onstop = function () {
        var recorder = mediaRecorder;
        var elapsed = holdStartedAt ? (Date.now() - holdStartedAt) : 0;
        var actualMime = (recorder && recorder.mimeType) || mime || "audio/webm";
        var blob = new Blob(recordChunks, { type: actualMime });
        recordChunks = [];
        holdStartedAt = 0;
        mediaRecorder = null;
        stopMediaTracks();
        if (elapsed < CONFIG.minRecordMs) {
          finishHoldUpload();
          toast("按住时间太短，请按住说完后再松开", "bad");
          return;
        }
        uploadHoldRecording(blob, actualMime);
      };
      if (firefox) {
        mediaRecorder.start();
      } else {
        mediaRecorder.start(250);
      }
      holdStartedAt = Date.now();
    } catch (err) {
      holdActive = false;
      finishHoldUpload();
      stopMediaTracks();
      toast("无法使用麦克风：" + ((err && err.message) || "权限被拒绝"), "bad");
    }
  }

  function stopLocalRecording() {
    if (!mediaRecorder) { return; }
    try {
      if (mediaRecorder.state !== "inactive") {
        holdBusy = true;
        updateHoldBtn();
        mediaRecorder.stop();
      }
    } catch (err) {
      holdBusy = false;
      updateHoldBtn();
      stopMediaTracks();
    }
  }

  function updateHoldBtn() {
    if (!holdBtn) { return; }
    var zone = $("dropZone");
    var generating = !!(zone && zone.classList && zone.classList.contains("busy"));
    holdBtn.classList.toggle("pressing", !!holdActive);
    holdBtn.disabled = !!holdBusy || generating;
    holdBtn.setAttribute("aria-pressed", holdActive ? "true" : "false");
    var strong = holdBtn.querySelector(".ht-copy strong");
    var small = holdBtn.querySelector(".ht-copy small");
    if (strong) {
      strong.textContent = generating ? "正在生成"
        : (holdBusy ? "正在识别" : (holdActive ? "正在聆听" : "按住说话"));
    }
    if (small) {
      small.textContent = generating ? "点右侧停止"
        : (holdBusy ? "正在使用本机电脑处理" : "释放自动发送");
    }
  }

  function bindComposerBusyState() {
    var zone = $("dropZone");
    if (!zone || typeof window.MutationObserver !== "function") { return; }
    var observer = new MutationObserver(function () { updateHoldBtn(); });
    observer.observe(zone, { attributes: true, attributeFilter: ["class"] });
  }

  function setMobileInputMode(mode) {
    var composer = $("dropZone");
    if (!composer || !composer.classList) { return; }
    var voiceMode = mode !== "text";
    composer.classList.toggle("voice-mode", voiceMode);
    composer.classList.toggle("text-mode", !voiceMode);
    if (voiceModeBtn) { voiceModeBtn.setAttribute("aria-pressed", voiceMode ? "true" : "false"); }
    if (textModeBtn) { textModeBtn.setAttribute("aria-pressed", voiceMode ? "false" : "true"); }
    if (voiceMode) {
      var box = currentInput();
      if (box) { try { box.blur(); } catch (err) { /* 忽略 */ } }
    } else {
      holdActive = false;
      updateHoldBtn();
      window.setTimeout(function () {
        var box = currentInput();
        if (!box) { return; }
        try {
          box.focus();
          var end = String(box.value || "").length;
          box.setSelectionRange(end, end);
        } catch (err) { /* 设备不支持 setSelectionRange 时也保持聚焦 */ }
      }, 30);
    }
  }

  function startHoldToTalk(event) {
    if (!isMobileComposer() || holdActive) { return; }
    var zone = $("dropZone");
    if (zone && zone.classList && zone.classList.contains("busy")) { return; }
    if (event) {
      event.preventDefault();
      if (event.pointerId !== undefined && event.currentTarget && event.currentTarget.setPointerCapture) {
        try { event.currentTarget.setPointerCapture(event.pointerId); } catch (err) { /* 忽略 */ }
      }
    }
    if (localRecorderAvailable()) {
      startLocalRecording();
      return;
    }
    var problem = recognitionAvailable();
    if (problem) {
      holdActive = false;
      updateHoldBtn();
      toast(problem, "bad");
      setMobileInputMode("text");
      return;
    }
    holdActive = true;
    updateHoldBtn();
    startListen();
  }

  function stopHoldToTalk() {
    if (!holdActive) { return; }
    holdActive = false;
    updateHoldBtn();
    if (mediaRecorder) {
      stopLocalRecording();
    } else {
      stopListen();
    }
  }

  function bindMobileVoiceInput() {
    holdBtn = $("holdToTalk");
    voiceModeBtn = $("voiceModeBtn");
    textModeBtn = $("textModeBtn");
    if (!holdBtn) { return; }

    holdBtn.addEventListener("pointerdown", function (event) {
      if (event.pointerType === "mouse" && event.button !== 0) { return; }
      startHoldToTalk(event);
    });
    holdBtn.addEventListener("pointerup", function (event) {
      event.preventDefault();
      stopHoldToTalk();
    });
    holdBtn.addEventListener("pointercancel", stopHoldToTalk);
    holdBtn.addEventListener("lostpointercapture", stopHoldToTalk);
    holdBtn.addEventListener("contextmenu", function (event) { event.preventDefault(); });
    holdBtn.addEventListener("keydown", function (event) {
      if (event.key !== " " && event.key !== "Enter") { return; }
      event.preventDefault();
      if (!event.repeat) { startHoldToTalk(event); }
    });
    holdBtn.addEventListener("keyup", function (event) {
      if (event.key !== " " && event.key !== "Enter") { return; }
      event.preventDefault();
      stopHoldToTalk();
    });

    if (voiceModeBtn) {
      voiceModeBtn.addEventListener("click", function () { setMobileInputMode("voice"); });
    }
    if (textModeBtn) {
      textModeBtn.addEventListener("click", function () { setMobileInputMode("text"); });
    }
    setMobileInputMode("voice");
  }

  function updateMicBtn() {
    if (!micBtn) { return; }
    if (listening) {
      micBtn.innerHTML = MIC_STOP_SVG;
      micBtn.classList.add("listening");
      micBtn.setAttribute("aria-label", "停止语音输入");
      micBtn.title = "正在识别，点击停止（快捷键 Ctrl+空格）";
    } else {
      micBtn.innerHTML = MIC_SVG;
      micBtn.classList.remove("listening");
      micBtn.setAttribute("aria-label", "语音输入");
      micBtn.title = SR
        ? "点击开始语音输入（快捷键 Ctrl+空格 / Ctrl+回车）"
        : "当前浏览器不支持语音输入，请使用 Chrome / Edge 浏览器";
    }
    // 手机端：录音中始终保留麦克风，不被发送箭头顶掉
    var composer = $("dropZone");
    if (composer && composer.classList) { composer.classList.toggle("listening", !!listening); }
  }

  /* ---------------- 界面注入（不改动原有布局） ----------------
     注意：这里**不再**往 <head> 注入 <style>。原来 injectStyle() 会在运行时追加一整段
     样式，带来两个实际问题：
       1) 它注入得比页面自己的 <style> 更晚，用的是 `#speakBtn.speak-toggle{display:inline-flex}`
          —— 与页面里 `.topbar #speakBtn` 同优先级却"后写的赢"，
          于是手机端想把朗读键收进「⋯」菜单时怎么都隐藏不掉（实测顶栏因此仍是两行 97px）；
       2) 按钮在 JS 跑起来之前是无样式的一瞬（FOUC）。
     这些规则属于对话页自己的组件，已移入 index.html 的 <style>，与其它按钮同一处维护。 */

  var MIC_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3.5" width="6" height="10.5" rx="3"/><path d="M19 11.5a7 7 0 0 1-14 0"/><path d="M12 18v3"/></svg>';
  var MIC_STOP_SVG = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8.5" y="8.5" width="7" height="7" rx="2" fill="currentColor"/><path d="M19 11.5a7 7 0 0 1-14 0"/><path d="M12 18v3"/></svg>';

  // 🎤 语音输入：放进输入框内最右侧（发送键左边），不再占用顶栏空间
  function buildMic() {
    var composer = document.querySelector(".composer-inner") || $("dropZone");
    var fallback = document.querySelector(".top-actions");
    var host = composer || fallback;
    if (!host) { return; }
    micBtn = document.createElement("button");
    micBtn.id = "voiceBtn";
    micBtn.type = "button";
    micBtn.className = "ic-btn";
    micBtn.innerHTML = MIC_SVG;
    if (!SR) { micBtn.setAttribute("data-unsupported", "1"); }
    micBtn.addEventListener("click", function () {
      if (!SR) { toast("当前浏览器不支持语音输入，请使用 Chrome / Edge 浏览器。", "bad"); return; }
      toggleListen();
    });
    var sendNode = $("send");
    if (composer && sendNode && sendNode.parentNode === composer) {
      composer.insertBefore(micBtn, sendNode);
    } else {
      host.appendChild(micBtn);
    }
    updateMicBtn();
  }

  // 🔊 自动朗读总开关（顶栏，按钮式：开＝绿/浅绿，关＝灰/浅灰）；单条回答的朗读按钮在回答底部左下角
  // 开关图标：开＝喇叭 + 声波；关＝喇叭 + 斜杠（与回答按钮「停止朗读」同一套图形语言）
  var SPEAK_ON_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6.5 9H3v6h3.5L11 19V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 6a8.5 8.5 0 0 1 0 12"/></svg>';
  var SPEAK_OFF_SVG = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6.5 9H3v6h3.5L11 19V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M5 18 19 6"/></svg>';

  function buildSpeakToggle() {
    // 本机离线合成可用时也有这个开关；只有「本机不可用 + 浏览器也没有语音」才不显示
    if (!TTS && SRV.ready === false) { return; }
    var actions = document.querySelector(".top-actions");
    if (!actions) { return; }
    var clearBtn = $("clearBtn");

    speakBtn = document.createElement("button");
    speakBtn.id = "speakBtn";
    speakBtn.type = "button";
    speakBtn.className = "mini speak-toggle";
    speakBtn.style.verticalAlign = "middle";
    var ico = document.createElement("span");
    ico.className = "st-ico";
    var txt = document.createElement("span");
    txt.className = "st-txt";
    speakBtn.appendChild(ico);
    speakBtn.appendChild(txt);
    speakBtn.__ico = ico;
    speakBtn.__txt = txt;
    speakBtn.addEventListener("click", function () {
      // 正在朗读 / 排队等待朗读：这一下先「停止」；开着自动朗读时顺手关掉（与旧版行为一致）
      if (speaking || pendingTimer) {
        cancelSpeak();
        if (CONFIG.autoSpeak) {
          CONFIG.autoSpeak = false;
          updateSpeakBtn();
          toast("已停止朗读，并关闭自动朗读（再次点击可开启）");
        } else {
          updateSpeakBtn();
          toast("已停止朗读");
        }
        return;
      }
      // 空闲：纯粹的开关，切换「自动朗读」的 开 / 关
      CONFIG.autoSpeak = !CONFIG.autoSpeak;
      updateSpeakBtn();
      toast(CONFIG.autoSpeak
        ? "已开启自动朗读：AI 回答显示后约 3 秒开始朗读"
        : "已关闭自动朗读");
    });
    if (clearBtn && clearBtn.nextSibling) {
      actions.insertBefore(speakBtn, clearBtn.nextSibling);
    } else {
      actions.insertBefore(speakBtn, clearBtn || null);
    }
    updateSpeakBtn();
  }

  /* ---------------- 快捷键 ---------------- */

  function bindShortcuts() {
    document.addEventListener("keydown", function (event) {
      // 组合键：Ctrl/Cmd + 空格 或 Ctrl/Cmd + 回车（不影响输入框内的回车发送）
      var combo = (event.ctrlKey || event.metaKey) &&
        (event.code === "Space" || event.key === "Enter" || event.key === " ");
      if (combo) {
        event.preventDefault();
        toggleListen();
        return;
      }
      if (!CONFIG.allowBareSpaceShortcut) { return; }
      // 裸空格/回车：仅在"未聚焦在输入类元素上"时生效，避免影响回车发送与正常输入
      var tag = document.activeElement ? String(document.activeElement.tagName || "").toUpperCase() : "";
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || tag === "BUTTON") { return; }
      if (event.code === "Space" || event.key === "Enter") {
        event.preventDefault();
        toggleListen();
      }
    }, false);
  }

  /* ---------------- 与既有逻辑衔接（零改动挂钩） ---------------- */

  function hookAnswer() {
    if (typeof window.finalizeAnswer !== "function") { return; }
    var original = window.finalizeAnswer;
    window.finalizeAnswer = function (view, fullText, references, persist) {
      var out = original.apply(this, arguments);
      try {
        // 正常定稿后按当前朗读设置决定是否朗读；刷新恢复的历史不自动朗读。
        scheduleSpeak(view, false);
      } catch (err) { /* 绝不影响回答显示 */ }
      return out;
    };
  }

  function hookSend() {
    var btn = $("send");
    if (!btn) { return; }
    // 捕获阶段：用户主动发送/停止时，先掐掉上一次朗读与待朗读计时
    btn.addEventListener("click", function () { cancelSpeak(); }, true);
  }

  /* chat 页的「语音设置」面板已按新规则移除。
     朗读链路固定为：云端 TTS（在「设置」页配置，填了才用）→ 本机 Matcha（离线）→ 本设备语音 → 浏览器语音。
     云端配置入口保留在「设置」页的「朗读（TTS）」一节。 */

  function init() {
    // 聊天输入统一为一个原生 textarea。语音朗读仍由本文件负责，
    // 但不再注入麦克风、按住说话、语音模式切换或 Ctrl+Space 语音输入。
    probeServerTts();        // 探测服务端朗读（云端 / 本机 Matcha）——链路前两级
    buildSpeakToggle();
    bindTapToStop();
    bindReadingUserPriority();
    hookAnswer();
    hookSend();
    if (TTS) {
      loadVoices();
      if (typeof TTS.onvoiceschanged !== "undefined") {
        TTS.onvoiceschanged = loadVoices;
      }
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();


