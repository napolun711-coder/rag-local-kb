/* 关闭窗口即停服 + 手机 / 局域网适配
 * ------------------------------------------------------------------
 * 浏览器区分不了「关窗口」和「刷新 / 跳到设置页」，所以这里只做两件小事：
 *   1) 页面在的时候每 15 秒报一次「我在」（keepalive）—— 刷新、跳设置页、多开标签都不会误停；
 *   2) 本机页面离开时用 sendBeacon 预约一个延迟停止（关窗口也能把请求发出去）。
 * 真正的判断在后端（app/routers/control.py）。
 *
 * 页面分两种，靠 GET /api/app/session 分辨（看 state.mode）：
 *   本机页面 http://127.0.0.1:8100/web
 *     · keepalive 取消预约，关窗口 25 秒后停服；顶栏有「停止全部服务」按钮；
 *   手机 / 局域网页面 http://192.168.x.x:8100/web
 *     · 拿不到令牌 —— 停不了服务，顶栏不显示停止按钮，页面离开也不预约停止；
 *     · keepalive 只会把「电脑上已经预约好的停止」往后推（默认 180 秒宽限）：
 *       你在手机上用，电脑就不会把服务关掉；手机切后台 / 锁屏时浏览器会暂停定时器，
 *       180 秒是留给你回到前台的余量；
 *     · 后端真的被停了（电脑上关了窗口 / 点了停止）会置位 offline，页面提示
 *       「服务已停止」；之后仍会继续探测 —— 电脑重新启动服务后页面自己恢复。
 */
(function () {
  "use strict";

  var HEARTBEAT_MS = 15000;          // 心跳间隔：要明显小于后端倒计时（本机 25 秒 / 手机 180 秒）
  var OFFLINE_MISSES = 2;            // 连续失败几次算「服务已停止」（手机网络抖动时别乱提示）

  var state = {
    ready: false,      // 已经和 /api/app/session 通过话
    mode: "local",     // local = 电脑本机页面；lan = 手机 / 局域网页面
    enabled: false,    // 后端允许「关窗口即停服」且 lifecycle 脚本齐全
    canStop: false,    // 能显示并使用「停止全部服务」按钮（只有本机）
    keepalive: false,  // 允许续命（本机总是；手机看 LAN_KEEPALIVE）
    offline: false,    // 后端已不可达（服务被停了 / 没启动）
    token: "",         // 只有本机页面读得到（手机拿不到，因此停不了服务）
    delay: 25,         // 本机关窗口后停服的秒数
    lanDelay: 180,     // 手机续命的宽限秒数
    busyDelay: 300,
    busy: false,
    pending: null,     // 后端已预约的停止还剩多少秒（null = 没预约）
    stopping: false,   // 用户点了「停止全部服务」
    misses: 0          // 连续失败次数（offline 判定用）
  };
  var beat = null;
  var tick = null;
  var bound = false;

  function emit() {
    try {
      window.dispatchEvent(new CustomEvent("ragkb-session", { detail: Object.assign({}, state) }));
    } catch (error) {
      /* 老浏览器不支持 CustomEvent 时忽略：界面只是不显示停止按钮 */
    }
  }

  function request(path, options) {
    return fetch(path, options).then(function (response) {
      return response.ok ? response.json() : null;
    }).catch(function () {
      return null;
    });
  }

  function post(path, body) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ token: state.token }, body || {})),
      cache: "no-store"
    });
  }

  function read() {
    return request("/api/app/session", { cache: "no-store" });
  }

  // ---------- 心跳：页面还在，就不要停 ----------
  // 本机页面 = POST keepalive（取消预约）；手机页面 = POST keepalive（把预约往后推）；
  // 关掉了手机续命（LAN_KEEPALIVE=false）时退化为 GET session，只用来探测后端还在不在。
  function heartbeat() {
    if (state.stopping) { return; }
    var job = state.keepalive ? post("/api/app/keepalive", {}) : read();
    job.then(function (data) {
      if (!data) { onMiss(); return; }
      state.misses = 0;
      state.offline = false;
      state.busy = Boolean(data.busy);
      state.pending = (typeof data.pending_seconds === "number") ? data.pending_seconds : null;
      emit();
    });
  }

  // 后端联系不上：本机页面基本可以立刻断定「服务被停了」；
  // 手机可能是 WiFi 抖了一下，多等一轮再提示。注意这里不停止探测 ——
  // 电脑重新启动服务后，页面会自己恢复（不用手动刷新）。
  function onMiss() {
    state.misses += 1;
    if (state.mode === "local" || state.misses >= OFFLINE_MISSES) {
      state.offline = true;
      state.pending = 0;
      state.stopping = false;
      if (tick) { window.clearInterval(tick); tick = null; }
    }
    emit();
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeat();
    beat = window.setInterval(heartbeat, HEARTBEAT_MS);
  }

  function stopHeartbeat() {
    if (beat) { window.clearInterval(beat); beat = null; }
  }

  // ---------- 回到前台：立刻补一次心跳（手机切后台 / 锁屏会把定时器冻住） ----------
  function onShow() {
    if (document.visibilityState === "hidden") { return; }
    heartbeat();
  }

  // ---------- 离开页面：本机页面预约停止（刷新 / 跳转时会被下一个页面取消） ----------
  // 手机 / 局域网页面不发：手机离开不改变电脑上的任何东西（它的续命自然到期即可）。
  function onLeave() {
    if (!state.enabled || state.mode !== "local" || state.stopping) { return; }
    var body = JSON.stringify({ token: state.token, reason: "tab-close" });
    var sent = false;
    try {
      if (navigator.sendBeacon) {
        sent = navigator.sendBeacon("/api/app/leave", new Blob([body], { type: "application/json" }));
      }
    } catch (error) {
      sent = false;
    }
    if (!sent) {
      // sendBeacon 不可用（或队列满）时退化为 keepalive 模式的 fetch
      try {
        fetch("/api/app/leave", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: body,
          keepalive: true
        });
      } catch (error) {
        /* 页面已经在卸载，发不出去就只能依赖「停止」按钮或桌面快捷方式 */
      }
    }
  }

  // ---------- 手动停止：顶栏按钮 ----------
  function stopNow() {
    if (!state.enabled || state.stopping) { return Promise.resolve(false); }
    state.stopping = true;
    emit();
    stopHeartbeat();
    return post("/api/app/stop", { delay: 3, reason: "web-stop" }).then(function (data) {
      if (!data) {
        state.stopping = false;
        emit();
        return false;
      }
      state.busy = Boolean(data.busy);
      state.pending = Number(data.pending_seconds) || 3;
      startTick();
      emit();
      return true;
    });
  }

  function startTick() {
    if (tick) { return; }
    tick = window.setInterval(function () {
      if (state.pending === null) { return; }
      state.pending = Math.max(0, Number(state.pending) - 1);
      if (state.pending === 0) {
        window.clearInterval(tick);
        tick = null;
        stopHeartbeat();
      }
      emit();
    }, 1000);
  }

  function cancel() {
    state.stopping = false;
    state.pending = null;
    startHeartbeat();
    emit();
  }

  // ---------- 初始化 ----------
  function applySession(data) {
    state.mode = data.mode === "lan" ? "lan" : "local";
    state.enabled = Boolean(data.enabled);
    state.canStop = Boolean(data.can_stop);
    state.keepalive = Boolean(data.keepalive);
    state.delay = Number(data.delay_seconds) || 25;
    state.lanDelay = Number(data.lan_delay_seconds) || 180;
    state.busyDelay = Number(data.busy_delay_seconds) || 300;
    state.busy = Boolean(data.busy);
    state.pending = (typeof data.pending_seconds === "number") ? data.pending_seconds : null;
  }

  function bind() {
    if (bound) { return; }
    bound = true;
    document.addEventListener("visibilitychange", onShow);
    window.addEventListener("pageshow", onShow);       // 从浏览器缓存回退时定时器可能已停
    window.addEventListener("pagehide", onLeave);
    window.addEventListener("beforeunload", onLeave);
  }

  // 令牌只有本机读得到（手机 / 局域网调 /api/app/control 一律 403），
  // 所以「停止全部服务」按钮只有本机页面能真正用起来。
  function fetchToken() {
    return request("/api/app/control", { cache: "no-store" }).then(function (data) {
      if (!data) { return; }
      state.token = data.token || "";
      if (typeof data.pending_seconds === "number") { state.pending = data.pending_seconds; }
      emit();
    });
  }

  function init() {
    return read().then(function (data) {
      state.ready = true;
      if (!data) {
        emit();          // 后端没起来：安静地不启用（页面自己会提示服务不可用）
        return;
      }
      applySession(data);
      bind();
      if (state.keepalive) { startHeartbeat(); }
      emit();
      if (state.canStop) { return fetchToken(); }
    }).catch(function () {
      state.ready = true;
      emit();
    });
  }

  window.RAGKB_SESSION = {
    state: state,
    stopNow: stopNow,
    cancel: cancel,
    refresh: init
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
