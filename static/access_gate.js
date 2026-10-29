(function () {
  "use strict";
  var root = document.documentElement;
  var TIMEOUT_MS = 4000;
  var finished = false;
  var timedOut = false;
  var timer = 0;
  var controller = null;
  var CSRF_COOKIE = "ragkb_csrf";
  var CSRF_HEADER = "X-RAGKB-CSRF";

  function isSameOrigin(input) {
    var raw = (typeof input === "string") ? input : ((input && input.url) || "");
    if (!raw || typeof window.URL !== "function") { return true; }
    try {
      return new window.URL(raw, window.location.href).origin === window.location.origin;
    } catch (err) {
      return false;
    }
  }

  function readCookie(name) {
    var prefix = name + "=";
    var parts = String(document.cookie || "").split(";");
    for (var i = 0; i < parts.length; i += 1) {
      var item = parts[i].replace(/^\s+/, "");
      if (item.indexOf(prefix) === 0) {
        try { return decodeURIComponent(item.slice(prefix.length)); } catch (err) { return ""; }
      }
    }
    return "";
  }

  function installCsrfFetch() {
    if (typeof window.fetch !== "function" || typeof window.Headers !== "function") { return; }
    var original = window.fetch;
    window.fetch = function (input, init) {
      var method = String((init && init.method) || (input && input.method) || "GET").toUpperCase();
      if (isSameOrigin(input) && method !== "GET" && method !== "HEAD" && method !== "OPTIONS") {
        var token = readCookie(CSRF_COOKIE);
        if (token) {
          var options = init || {};
          var headers = new window.Headers(options.headers || {});
          if (!headers.has(CSRF_HEADER)) { headers.set(CSRF_HEADER, token); }
          options.headers = headers;
          return original.call(window, input, options);
        }
      }
      return original.call(window, input, init);
    };
  }

  installCsrfFetch();

  function isLoopbackHost() {
    var host = String(location.hostname || "").toLowerCase();
    return host === "localhost" || host === "127.0.0.1"
      || host === "::1" || host === "[::1]" || host === "0.0.0.0";
  }

  function stopTimer() {
    if (timer) {
      window.clearTimeout(timer);
      timer = 0;
    }
  }

  function reveal() {
    if (finished) { return; }
    finished = true;
    stopTimer();
    try { root.style.visibility = ""; } catch (err) {}
  }

  function block(reason) {
    if (finished) { return; }
    finished = true;
    stopTimer();
    // 本地回环页面不把授权状态接口故障当成越权；局域网页面保持失败关闭。
    if (isLoopbackHost()) {
      try { root.style.visibility = ""; } catch (err) {}
      return;
    }
    location.replace("/access_required?reason=" + encodeURIComponent(reason || "check_failed"));
  }

  try { root.style.visibility = "hidden"; } catch (err) {}

  var options = { cache: "no-store" };
  if (typeof window.AbortController === "function") {
    controller = new window.AbortController();
    options.signal = controller.signal;
    timer = window.setTimeout(function () {
      timedOut = true;
      controller.abort();
    }, TIMEOUT_MS);
  } else {
    timer = window.setTimeout(function () {
      block("timeout");
    }, TIMEOUT_MS);
  }

  fetch("/api/app/access/status", options)
    .then(function (response) {
      if (!response.ok) { throw new Error("HTTP " + response.status); }
      return response.json();
    })
    .then(function (data) {
      if (!data || typeof data !== "object" || typeof data.enabled === "undefined"
          || typeof data.authorized === "undefined") {
        throw new Error("incomplete access status");
      }
      if (data && data.enabled && !data.local && !data.authorized) {
        finished = true;
        stopTimer();
        location.replace("/pair");
        return;
      }
      reveal();
    })
    .catch(function () {
      block(timedOut ? "timeout" : "check_failed");
    });
})();
