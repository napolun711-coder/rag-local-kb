/* ============================================================================
 * ui.js —— 全站共用的反馈组件
 * ----------------------------------------------------------------------------
 * 为什么要有这个文件：
 *
 * 1) 全局反馈原来只有对话页顶栏里的一个文字 span（#toast）——
 *    setToast() 把字写进去、没有图标、**不会自动消失**，手机上还会被
 *    text-overflow:ellipsis 截断。"复制成功 / 查询出错 / 请求失败"全靠它。
 *    而且 files / settings 两页根本没有这套东西，各用各的页内 notice。
 *
 * 2) 危险动作的确认原来是原生 window.confirm —— 样式与全站无关、手机上是一句
 *    系统弹窗，而且**没有任何"后果说明"的位置**（"仍要入库"这类动作要写清后果）。
 *
 * 样式在 theme.css（.ragkb-toast / .ragkb-modal），所有页面共用同一套令牌与外观。
 *
 * 对外只有两个方法：
 *   RAGKB.toast(text, kind)      kind: "" | "ok" | "bad" | "info"
 *   RAGKB.confirm(opts)          -> Promise<boolean>
 *                              opts: {title, body, detail, confirmText, cancelText, danger}
 *   RAGKB.confirmText(opts)      -> Promise<string|null>
 *                              opts 额外支持 inputLabel、placeholder、requiredValue
 * ========================================================================= */
(function () {
  "use strict";

  var TOAST_MS = { bad: 6500, "": 3200, ok: 3200, info: 3800 };
  var MAX_TOASTS = 3;

  var ICONS = {
    ok: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l5 5L20 6"/></svg>',
    bad: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.6v5.2"/><path d="M12 16.2h.01"/></svg>',
    info: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.4"/><path d="M12 7.8h.01"/></svg>',
    "": '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.4"/><path d="M12 7.8h.01"/></svg>'
  };

  var toastBox = null;

  function ensureToastBox() {
    if (toastBox && toastBox.isConnected) { return toastBox; }
    toastBox = document.createElement("div");
    toastBox.className = "ragkb-toasts";
    // 读屏播报：普通提示用 polite，不打断用户当前朗读
    toastBox.setAttribute("role", "status");
    toastBox.setAttribute("aria-live", "polite");
    document.body.appendChild(toastBox);
    return toastBox;
  }

  function drop(node) {
    if (!node || !node.parentNode) { return; }
    node.classList.add("out");
    window.setTimeout(function () {
      if (node.parentNode) { node.parentNode.removeChild(node); }
    }, 180);
  }

  /**
   * 显示一条瞬时反馈。
   * @param {string} text  文案（空字符串 = 清除现有的同类提示）
   * @param {string} kind  "" | "ok" | "bad" | "info"
   */
  function toast(text, kind) {
    var box = ensureToastBox();
    var body = String(text == null ? "" : text);
    if (!body) { return null; }        // setToast("") 的语义是"清掉"，这里就是什么都不做
    var level = (kind === "ok" || kind === "bad" || kind === "info") ? kind : "";

    var node = document.createElement("div");
    node.className = "ragkb-toast" + (level ? " " + level : "");

    var ico = document.createElement("span");
    ico.className = "pt-ico";
    ico.innerHTML = ICONS[level] || ICONS[""];
    var txt = document.createElement("span");
    txt.className = "pt-txt";
    txt.textContent = body;
    var close = document.createElement("button");
    close.type = "button";
    close.className = "pt-close";
    close.setAttribute("aria-label", "关闭提示");
    close.textContent = "×";
    close.addEventListener("click", function () { drop(node); });

    node.appendChild(ico);
    node.appendChild(txt);
    node.appendChild(close);
    box.appendChild(node);

    // 只留最近几条：堆满屏幕的提示等于没有提示。
    // 这里必须**同步移除**：drop() 只是加淡出 class、180ms 后才删节点，
    // 在 while 里用它判断 children.length 永远不会变小 —— 一旦同时出现第 4 条提示
    // （多文件上传、OCR 成功 + 上传状态叠加等），主线程会死循环，页面卡死/浏览器崩溃。
    while (box.children.length > MAX_TOASTS) {
      var oldest = box.firstChild;
      if (!oldest) { break; }
      box.removeChild(oldest);
    }

    var ms = TOAST_MS[level] != null ? TOAST_MS[level] : TOAST_MS[""];
    var timer = window.setTimeout(function () { drop(node); }, ms);
    node.addEventListener("mouseenter", function () { window.clearTimeout(timer); });
    node.addEventListener("mouseleave", function () {
      timer = window.setTimeout(function () { drop(node); }, 1200);   // 鼠标离开后再给一点时间
    });
    return node;
  }

  /* ---------------- 自绘确认框 ----------------
     替代 window.confirm。相比原生弹窗多出来的东西：
       · 与全站同一套外观（令牌 / 圆角 / 字体），手机上不再是一句系统弹窗；
       · 有 detail 区域可以写清**后果**（"仍要入库会把当前正文直接写进知识库…"）；
       · 键盘可用：Esc 取消、打开即聚焦确认键、关闭后把焦点还给原来的元素；
       · role="dialog" + aria-modal，读屏能识别。 */
  var modal = null;
  var modalResolve = null;
  var lastFocus = null;
   var modalMode = "confirm";
   var modalRequiredValue = "";
   var modalInput = null;

  function buildModal() {
    var wrap = document.createElement("div");
    wrap.className = "ragkb-modal";
    wrap.hidden = true;
    wrap.setAttribute("role", "dialog");
    wrap.setAttribute("aria-modal", "true");
    wrap.setAttribute("aria-labelledby", "ragkbModalTitle");
    wrap.innerHTML = '<div class="ragkb-modal-box">'
      + '<h2 class="ragkb-modal-title" id="ragkbModalTitle"></h2>'
      + '<div class="ragkb-modal-body"></div>'
      + '<div class="ragkb-modal-detail" hidden></div>'
      + '<div class="ragkb-modal-field" hidden>'
      + '<label class="ragkb-modal-label" for="ragkbModalInput"></label>'
      + '<input class="ragkb-modal-input" id="ragkbModalInput" type="text" autocomplete="off" spellcheck="false">'
      + '</div>'
      + '<div class="ragkb-modal-actions">'
      + '<button type="button" class="ragkb-modal-cancel"></button>'
      + '<button type="button" class="ragkb-modal-ok"></button>'
      + '</div></div>';
    wrap.querySelector(".ragkb-modal-cancel").addEventListener("click", function () { settle(false); });
    wrap.querySelector(".ragkb-modal-ok").addEventListener("click", requestAccept);
    modalInput = wrap.querySelector(".ragkb-modal-input");
    modalInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        requestAccept();
      }
    });
    modalInput.addEventListener("input", function () {
      modalInput.classList.remove("bad");
      modalInput.removeAttribute("aria-invalid");
    });
    wrap.addEventListener("click", function (event) {
      if (event.target === wrap) { settle(false); }     // 点遮罩 = 取消
    });
    document.body.appendChild(wrap);
    return wrap;
  }

   function requestAccept() {
     if (!modal || modal.hidden) { return; }
     if (modalMode === "text") {
       var value = String((modalInput && modalInput.value) || "").trim();
       if (modalRequiredValue && value !== modalRequiredValue) {
         modalInput.classList.add("bad");
         modalInput.setAttribute("aria-invalid", "true");
         modalInput.focus();
         return;
       }
       settle(value);
       return;
     }
     settle(true);
   }

  function settle(value) {
    if (!modal || modal.hidden) { return; }
     var result = value;
     if (modalMode === "text") {
       result = value ? String(value).trim() : null;
     }
    modal.hidden = true;
    var resolve = modalResolve;
    modalResolve = null;
     modalMode = "confirm";
     modalRequiredValue = "";
    if (lastFocus && lastFocus.focus) {
      try { lastFocus.focus(); } catch (error) { /* 元素可能已经不在文档里 */ }
    }
    lastFocus = null;
     if (resolve) { resolve(result); }
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && modal && !modal.hidden) {
      event.stopPropagation();      // 别再触发页面自己的 Esc（收侧栏 / 收图谱）
      settle(false);
    }
  }, true);

   function prepareModal(opts, mode) {
    var options = opts || {};
    if (!modal) { modal = buildModal(); }
    // 只允许一个确认框。理论上遮罩挡住了底下的点击、不该出现第二个，
    // 但键盘/脚本触发是可能的 —— 那样第二个调用会覆盖 modalResolve，
    // 第一个 Promise 永远不 settle（调用方的 await 就挂死了）。所以先把旧的按"取消"结掉。
    if (!modal.hidden) { settle(false); }
    lastFocus = document.activeElement;
     modalMode = mode;
     modalRequiredValue = String(options.requiredValue || "");

    var title = modal.querySelector(".ragkb-modal-title");
    title.textContent = options.title || "确认";
    title.className = "ragkb-modal-title" + (options.danger ? " danger" : "");
    modal.querySelector(".ragkb-modal-body").textContent = options.body || "";

    var detail = modal.querySelector(".ragkb-modal-detail");
    if (options.detail) {
      detail.textContent = options.detail;
      detail.hidden = false;
    } else {
      detail.textContent = "";
      detail.hidden = true;
    }

     var field = modal.querySelector(".ragkb-modal-field");
     var label = modal.querySelector(".ragkb-modal-label");
     if (mode === "text") {
       field.hidden = false;
       label.textContent = options.inputLabel || "确认内容";
       modalInput.value = options.value || "";
       modalInput.placeholder = options.placeholder || "";
       modalInput.classList.remove("bad");
       modalInput.removeAttribute("aria-invalid");
     } else {
       field.hidden = true;
       label.textContent = "";
       modalInput.value = "";
       modalInput.placeholder = "";
       modalInput.classList.remove("bad");
       modalInput.removeAttribute("aria-invalid");
     }

    var cancel = modal.querySelector(".ragkb-modal-cancel");
    cancel.textContent = options.cancelText || "取消";
    var ok = modal.querySelector(".ragkb-modal-ok");
    ok.textContent = options.confirmText || "确认";
    ok.className = "ragkb-modal-ok" + (options.danger ? " danger" : " primary");

    modal.hidden = false;
     if (mode === "text") {
       modalInput.focus();
       modalInput.select();
     } else {
       ok.focus();
     }
    return new Promise(function (resolve) { modalResolve = resolve; });
  }

   function confirmDialog(opts) {
     return prepareModal(opts, "confirm");
   }

   function confirmText(opts) {
     return prepareModal(opts, "text");
   }

  // 与 theme.js 共用同一个 RAGKB 命名空间：theme.js 先执行，不能把它覆盖掉
  window.RAGKB = window.RAGKB || {};
  window.RAGKB.toast = toast;
  window.RAGKB.confirm = confirmDialog;
   window.RAGKB.confirmText = confirmText;
})();
