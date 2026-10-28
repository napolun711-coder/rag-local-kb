/* ============================================================================
 * theme.js —— 外观模式
 * ----------------------------------------------------------------------------
 * 三种模式：
 *   auto  —— 跟随系统（prefers-color-scheme），默认值；
 *   light —— 固定浅色；
 *   dark  —— 固定深色。
 *
 * 为什么不用 media query 直接写深色：
 *   用户要能手动覆盖系统设置，所以三种模式要能循环切换并存下来。
 *   这里把「模式」存 localStorage，把「解析后的主题」写到 <html data-theme>，
 *   CSS 只需要一套 :root[data-theme="dark"] 覆盖（见 theme.css）。
 *
 * 加载位置：必须在 <head> 里、theme.css 之后同步加载（不要 defer）——
 *   否则深色模式会先闪一帧浅色。它只设置属性，不依赖 DOM 是否就绪。
 *
 * 对外：
 *   RAGKB.theme.get()        -> "auto" | "light" | "dark"
 *   RAGKB.theme.set(mode)
 *   RAGKB.theme.cycle()
 *   RAGKB.theme.resolved()   -> "light" | "dark"
 *   document 上的 "ragkb-theme-change" 事件（detail: {mode, theme}）
 *   任何带 data-theme-toggle 的按钮都会被自动接管（图标 / 文案 / 点击循环）。
 * ========================================================================= */
(function () {
  "use strict";

  var KEY = "ragkb-theme";
  var MODES = ["auto", "light", "dark"];
  var mq = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function readMode() {
    try {
      var value = localStorage.getItem(KEY);
      return MODES.indexOf(value) >= 0 ? value : "auto";
    } catch (err) {
      return "auto";        // 隐私模式 / 存储被禁：按默认处理
    }
  }

  function systemDark() { return !!(mq && mq.matches); }

  function resolved(mode) {
    if (mode === "dark") { return "dark"; }
    if (mode === "light") { return "light"; }
    return systemDark() ? "dark" : "light";
  }

  var ICONS = {
    // 自动：左半太阳、右半月亮
    auto: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16Z" fill="currentColor" stroke="none"/></svg>',
    light: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>',
    dark: '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79Z"/></svg>'
  };

  function label(mode) {
    if (mode === "auto") {
      return "外观：跟随系统（当前" + (systemDark() ? "深色" : "浅色") + "）· 点击切换为浅色";
    }
    if (mode === "light") { return "外观：浅色 · 点击切换为深色"; }
    return "外观：深色 · 点击切换为跟随系统";
  }

  function updateButtons(mode) {
    var nodes = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (node.getAttribute("data-mode") !== mode) {
        node.innerHTML = ICONS[mode] || ICONS.auto;
        node.setAttribute("data-mode", mode);
      }
      var text = label(mode);
      node.setAttribute("title", text);
      node.setAttribute("aria-label", text);
    }
  }

  function apply(mode) {
    var root = document.documentElement;
    // 解析后的主题才是 CSS 的输入；模式本身留在 data-theme-mode 便于调试
    root.setAttribute("data-theme", resolved(mode));
    root.setAttribute("data-theme-mode", mode);
    updateButtons(mode);
  }

  function announce(mode) {
    document.dispatchEvent(new CustomEvent("ragkb-theme-change", {
      detail: { mode: mode, theme: resolved(mode) }
    }));
  }

  function set(mode) {
    if (MODES.indexOf(mode) < 0) { mode = "auto"; }
    try { localStorage.setItem(KEY, mode); } catch (err) { /* 存不了就只影响下次启动 */ }
    apply(mode);
    announce(mode);
  }

  function cycle() {
    var mode = readMode();
    set(MODES[(MODES.indexOf(mode) + 1) % MODES.length]);
  }

  function bind() {
    var nodes = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < nodes.length; i++) {
      var node = nodes[i];
      if (node.__ragkbThemeBound) { continue; }
      node.__ragkbThemeBound = true;
      node.addEventListener("click", cycle);
    }
    updateButtons(readMode());
  }

  // 尽早应用：在 <head> 同步执行，避免深色下先闪一帧浅色
  apply(readMode());

  // auto 模式下系统主题变化时立刻跟随（并通知画布等需要重绘的地方）
  function onSystemChange() {
    if (readMode() === "auto") {
      apply("auto");
      announce("auto");
    }
  }
  if (mq) {
    if (mq.addEventListener) { mq.addEventListener("change", onSystemChange); }
    else if (mq.addListener) { mq.addListener(onSystemChange); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }

  window.RAGKB = window.RAGKB || {};
  window.RAGKB.theme = {
    get: readMode,
    set: set,
    cycle: cycle,
    resolved: function () { return resolved(readMode()); },
    systemDark: systemDark
  };
})();
