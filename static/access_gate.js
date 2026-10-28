(function () {
  "use strict";
  var root = document.documentElement;
  var TIMEOUT_MS = 4000;
  var finished = false;
  var timedOut = false;
  var timer = 0;
  var controller = null;

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
