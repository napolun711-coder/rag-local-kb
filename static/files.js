
// ============================================================
// 文件管理页脚本（零依赖、离线可用）
// ============================================================
// 权限分两档（后端说了算）：
//   · 只读（列表 / 概览 / 原件）：手机也能看；
//   · 写操作（删除 / 打开文件夹 / 立即扫描）：仅本机 + 令牌 —— 手机页面不显示按钮。
// 令牌只有本机能从 GET /api/app/control 读到，手机那边拿不到，所以这里拿不到就
// 把写操作全部藏起来（藏起来只是体验，真正的拦截在后端 guard）。

const $ = function (id) { return document.getElementById(id); };

// 聚焦但不滚动页面：这一页的确认框在底部 sticky 条里，浏览器默认会把聚焦的元素
// 滚进视口 —— 用户会被从表格当前位置弹走（preventScroll 在很老的浏览器上不支持，
// 那就退回一次普通聚焦，不影响功能）。
function focusQuiet(node) {
  if (!node) { return; }
  try { node.focus({ preventScroll: true }); } catch (err) { node.focus(); }
}
const state = {
  token: "",
  local: false,
  canOpenFolder: false,
  page: 1,
  size: 20,
  // 本机表用**独立**的一套分页状态，和知识库表的 page/size 不共用 ——
  // 共用会出现「翻一张表，另一张跟着跳页」。
  // 后端 /api/files/list 不给本机记录分页参数（既没有 limit/offset，也**有意**留了
  // 一个防线性变慢的上限 LOCAL_ONLY_LIMIT，见 app/routers/files.py），
  // 所以这一套只能在**前端切片**：一次拿到全部再由前端切页。
  // 取舍是接口体积没变小，但表格高度与滚动距离可控。
  localPage: 1,
  localSize: 20,
  // 后端报的**真实**本机记录条数，可能大于 localRows.length（撞上上限时）——
  // 用来在分页条上明说「另有 N 条未载入」，不让截断变成静默行为
  localTotal: 0,
  status: "",
  q: "",
  topicId: "",
  topics: [],
  topicsEnabled: false,
  rows: [],
  localRows: [],
  upstreamOk: true,
  selected: {},          // key -> {doc_id, file_id, name}
  busy: false,
  deleteMode: false,
  deleteItems: [],
  deleteReturnFocus: null,
  pollTimer: null,
  ingestRefreshTimer: 0
};

// 本机表展示所有还需要用户关注或仍在处理的状态；已入库的归知识库文档表。
const LOCAL_VISIBLE_STATES = {
  review: 1, needs_ocr: 1, blocked: 1, failed: 1,
  skipped: 1, ingesting: 1, processing: 1
};

function escapeHtml(text) {
  return String(text === undefined || text === null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmtSize(bytes) {
  const value = Number(bytes || 0);
  if (!value) { return "—"; }
  if (value < 1024) { return value + " B"; }
  if (value < 1024 * 1024) { return (value / 1024).toFixed(1) + " KB"; }
  return (value / 1024 / 1024).toFixed(1) + " MB";
}

function setNotice(text, kind) {
  const node = $("notice");
  if (!text) { node.hidden = true; node.textContent = ""; return; }
  node.hidden = false;
  node.className = "notice" + (kind ? " " + kind : "");
  node.textContent = text;
  // 反馈落在页面顶部的横幅，而删除按钮在页面底部的 sticky 条里 —— 不把横幅带回视口，
  // 用户点完删除只会觉得「什么都没发生」。用 block:"nearest" 做最小滚动，并且
  // 只在它确实不在视口里时才滚（否则每次轮询刷新 / 检索都会把页面拽回顶部）。
  const box = node.getBoundingClientRect();
  const view = window.innerHeight || document.documentElement.clientHeight || 0;
  if (view && (box.top < 0 || box.bottom > view)) {
    node.scrollIntoView({ block: "nearest" });
  }
}

// 瞬时成功反馈走共享 toast（自动消失）；失败与状态说明仍留在页内横幅 setNotice。
// 兜底：ui.js 是 defer 加载的，极早期若还没执行到就退回横幅，不至于把反馈吞掉。
function flash(text, kind) {
  if (window.RAGKB && RAGKB.toast) { RAGKB.toast(text, kind); }
  else { setNotice(text, kind); }
}

async function fetchJSON(url, options) {
  const response = await fetch(url, Object.assign({ cache: "no-store" }, options || {}));
  let body = null;
  try { body = await response.json(); } catch (err) { body = null; }
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : {};
    const message = (detail && detail.message) || ("请求失败（HTTP " + response.status + "）");
    const error = new Error(message);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return body || {};
}

// 清空筛选。顶栏「重置筛选」与空态里的「清空筛选 / 清空搜索」共用这一份，
// 避免两处逻辑分叉（改了状态却忘把输入框也清掉）。
function resetFilters() {
  state.status = ""; state.q = ""; state.topicId = ""; state.page = 1; state.localPage = 1;
  $("statusFilter").value = ""; $("searchInput").value = ""; $("topicFilter").value = "";
  loadList();
}

// ---------- 启动：先问清「这一页是谁在用什么权限」 ----------
async function init() {
  $("refreshBtn").addEventListener("click", function () { loadAll(); });
  $("statusFilter").addEventListener("change", function () {
    state.status = this.value; state.page = 1; state.localPage = 1; loadList();
  });
  $("topicFilter").addEventListener("change", function () {
    state.topicId = this.value; state.page = 1; state.localPage = 1; loadList();
  });
  $("pageSize").addEventListener("change", function () { state.size = Number(this.value); state.page = 1; loadList(); });
  $("resetFilters").addEventListener("click", resetFilters);
  $("newTopicBtn").addEventListener("click", createTopic);
  let searchTimer = null;
  $("searchInput").addEventListener("input", function () {
    const value = this.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.q = value.trim(); state.page = 1; state.localPage = 1; loadList();
    }, 350);
  });
  $("prevPage").addEventListener("click", function () { if (state.page > 1) { state.page -= 1; loadList(); } });
  $("nextPage").addEventListener("click", function () { state.page += 1; loadList(); });
  // 本机表翻页只重渲染这张表：数据已经在内存里（见 state.localPage 的说明），不必再打一次接口
  $("localPrevPage").addEventListener("click", function () {
    if (state.localPage > 1) { state.localPage -= 1; renderLocalRows(); }
  });
  $("localNextPage").addEventListener("click", function () {
    state.localPage += 1; renderLocalRows();
  });

  // Local catalog mode: no session/folder/inbox probes.
  document.body.classList.add("catalog-mode");
  state.local = ["127.0.0.1", "localhost", "::1"].indexOf(location.hostname) >= 0;
  state.token = state.local ? "local" : "";
  state.canOpenFolder = false;
  state.topicId = "";
  $("topicFilter").hidden = true;
  $("newTopicBtn").hidden = true;
  $("scanBtn").hidden = true;
  $("openInboxBtn").hidden = true;
  $("openOriginalsBtn").hidden = true;
  $("stopBtn").hidden = true;
  $("deleteModeBtn").hidden = !state.local;
  $("subTitle").textContent = state.local
    ? "仅管理本应用中的留档、解析记录和知识库索引；原始文件及其所在文件夹不会被修改或删除"
    : "只读 · 数据操作仅在电脑端可用";
  $("checkAll").addEventListener("change", function () { toggleAll(this.checked, state.rows, $("rows")); });
  $("checkAllLocal").addEventListener("change", function () { toggleAll(this.checked, localPageSlice().rows, $("localRows")); });
  $("deleteModeBtn").addEventListener("click", function () {
    if (!state.deleteMode) { enterDeleteMode(); return; }
    openDeleteDialog(this);
  });
  $("cancelDeleteModeBtn").addEventListener("click", exitDeleteMode);
  $("cancelDelete").addEventListener("click", function () { closeDeleteDialog(true); });
  $("deleteDialogBackdrop").addEventListener("click", function () { closeDeleteDialog(true); });
  $("deleteConfirmation").addEventListener("input", updateDeleteConfirmButton);
  $("confirmDelete").addEventListener("click", function () { doDelete(); });
  $("deleteDialog").addEventListener("keydown", handleDeleteDialogKeydown);
  $("detailClose").addEventListener("click", closeDetail);
  $("detailBackdrop").addEventListener("click", closeDetail);
  await loadAll();
}

// ---------- 取数 ----------
async function loadAll() {
  await loadTopics();
  await Promise.all([loadOverview(), loadList()]);
}

// 主题筛选与行内标签共用这一份 active topics；记忆库未启用时隐藏主题控件。
async function loadTopics() {
  const previous = state.topicId || "";
  state.topics = [];
  state.topicsEnabled = false;
  $("topicFilter").innerHTML = "";
  $("topicFilter").hidden = true;
  $("newTopicBtn").hidden = true;
  try {
    const data = await fetchJSON("/api/topics");
    state.topicsEnabled = !!data.enabled;
    state.topics = Array.isArray(data.topics) ? data.topics : [];
  } catch (err) {
    state.topicsEnabled = false;
  }
  if (!state.topicsEnabled || !state.local) {
    state.topicId = "";
    return;
  }
  const options = ['<option value="">全部主题</option>'];
  state.topics.forEach(function (topic) {
    options.push('<option value="' + escapeHtml(topic.id) + '">' + escapeHtml(topic.name) + '</option>');
  });
  $("topicFilter").innerHTML = options.join("");
  $("topicFilter").hidden = false;
  $("newTopicBtn").hidden = false;
  state.topicId = state.topics.some(function (item) { return item.id === previous; }) ? previous : "";
  $("topicFilter").value = state.topicId;
}

async function createTopic() {
  const name = window.RAGKB && typeof window.RAGKB.confirmText === "function"
    ? await window.RAGKB.confirmText({
        title: "新建主题",
        body: "主题用于整理知识库文档。",
        inputLabel: "主题名称",
        placeholder: "输入主题名称",
        confirmText: "创建主题",
        cancelText: "取消"
      })
    : window.prompt("输入新主题名称：", "");
  if (!name || !name.trim()) { return; }
  try {
    const data = await fetchJSON("/api/topics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim(), kind: "topic" })
    });
    const topic = data.topic || {};
    await loadTopics();
    state.topicId = topic.id || "";
    $("topicFilter").value = state.topicId;
    state.page = 1;
    state.localPage = 1;
    flash("已创建主题：" + (topic.name || name.trim()), "ok");
    await loadList();
  } catch (err) {
    flash("创建主题失败：" + err.message, "bad");
  }
}

async function loadOverview() {
  try {
    const data = await fetchJSON("/api/files/overview");
    renderOverview(data);
    renderInbox(data.inbox || {});
    renderFolders(data.folders || []);
    if (!(data.upstream || {}).ok) {
      setNotice("知识库暂时不可用：" + ((data.upstream || {}).message || "") +
        " 下面只显示应用内留档，等它恢复后点「刷新」即可。", "bad");
    }
  } catch (err) {
    setNotice("读取概览失败：" + err.message, "bad");
  }
}

async function loadList() {
  try {
    const query = "status=" + encodeURIComponent(state.status) +
      "&page=" + state.page + "&page_size=" + state.size +
      "&q=" + encodeURIComponent(state.q) +
      "&topic_id=" + encodeURIComponent(state.topicId || "");
    const data = await fetchJSON("/api/files/list?" + query);
    state.rows = data.documents || [];
    state.localRows = (data.local_only || []).filter(function (item) {
      return Boolean(LOCAL_VISIBLE_STATES[String(item.state || "")]);
    });
    state.localTotal = state.localRows.length;
    state.upstreamOk = !(data.upstream && data.upstream.ok === false);
    if (!state.upstreamOk) { state.selected = {}; }
    applyUpstreamLabels();
    renderRows();
    renderLocalRows();
    renderPager(data.pagination || {});
    if (data.notice) { setNotice(data.notice, data.upstream && data.upstream.ok ? "" : "bad"); }
  } catch (err) {
    setNotice("读取文件列表失败：" + err.message, "bad");
    state.rows = [];
    renderRows();
  }
}

// ---------- 渲染 ----------
function applyUpstreamLabels() {
  const ok = state.upstreamOk;
  const knowledgeTitle = $("knowledgeTitle");
  const knowledgeHint = $("knowledgeTableHint");
  const localTitle = $("localTitle");
  const localHint = $("localHint");
  if (knowledgeTitle) {
    knowledgeTitle.textContent = ok ? "知识库文档" : "知识库文档（暂不可用）";
  }
  if (knowledgeHint) {
    knowledgeHint.hidden = ok;
    knowledgeHint.textContent = ok ? "" : "知识服务不可用，当前无法显示知识库文档；下方仅显示应用内留档。";
  }
  if (localTitle) {
    localTitle.textContent = "本机待处理与已跳过";
  }
  if (localHint) {
    localHint.textContent = ok
      ? "这里显示本机解析记录：待确认、需修复、入库中与已跳过。待确认请在对话页处理；已跳过的可继续入库。"
      : "知识服务不可用；以下为本机记录，入库操作暂不可用。";
  }
  const checkAll = $("checkAll");
  const checkAllLocal = $("checkAllLocal");
  if (checkAll) { checkAll.disabled = !ok; }
  if (checkAllLocal) { checkAllLocal.disabled = !ok; }
  if (!ok && state.deleteMode) {
    state.deleteMode = false;
    state.selected = {};
    document.body.classList.remove("delete-mode");
  }
}

function statCell(key, value, extra) {
  return '<div class="stat"><div class="k">' + escapeHtml(key) + '</div><div class="v">' +
    escapeHtml(value) + '</div>' + (extra ? '<div class="k">' + escapeHtml(extra) + '</div>' : '') + '</div>';
}

function renderOverview(data) {
  const counts = data.counts || {};
  const local = data.local || {};
  const pipeline = data.pipeline || {};
  const inbox = data.inbox || {};
  const upstreamOk = !(data.upstream && data.upstream.ok === false);
  state.upstreamOk = upstreamOk;
  applyUpstreamLabels();
  const unknown = "—";
  $("stats").innerHTML =
    statCell("知识库文档", upstreamOk ? (counts.all || 0) : unknown) +
    statCell("已完成", upstreamOk ? (counts.processed || 0) : unknown) +
    statCell("处理中 / 排队", upstreamOk ? ((counts.processing || 0) + (counts.parsing || 0) +
      (counts.analyzing || 0) + (counts.pending || 0)) : unknown) +
    statCell("失败", upstreamOk ? (counts.failed || 0) : unknown) +
    statCell("应用内留档", local.jobs || 0, "已加入知识库 " + (local.in_knowledge_base || 0)) +
    statCell("等你确认", local.waiting_review || 0) +
    statCell("入库中", local.ingesting || 0) +
    statCell("已跳过", local.skipped || 0) +
    statCell("收件箱等待", inbox.waiting || 0);

  const chips = [];
  chips.push('<span class="chip ' + (!upstreamOk || pipeline.busy ? "warn" : "ok") + '">知识库管线：' +
    (!upstreamOk ? "未知（知识服务不可用）"
      : (pipeline.busy ? "忙碌（正在建索引，删除会被拒绝）" : "空闲")) + '</span>');
  if (pipeline.recovery_required) {
    chips.push('<span class="chip bad">需要恢复：' +
      escapeHtml(pipeline.recovery_message || "请到 LightRAG 侧处理") + '</span>');
  }
  chips.push('<span class="chip ' + ((data.upstream || {}).ok ? "ok" : "bad") + '">知识库连接：' +
    ((data.upstream || {}).ok ? "正常" : "不可用") + '</span>');
  if (inbox.enabled) {
    chips.push('<span class="chip ' + (inbox.running ? "ok" : "warn") + '">自动扫描来源文件夹：' +
      (inbox.running ? ("运行中（每 " + inbox.poll_seconds + " 秒）") : "已开启但线程没跑") + '</span>');
  } else {
    chips.push('<span class="chip">自动扫描来源文件夹：未开启（设置页 ⑧ 可开）</span>');
  }
  $("pipelineChips").innerHTML = chips.join("");
  $("upstreamHint").textContent = (data.upstream || {}).ok
    ? ("地址：" + ((data.upstream || {}).url || "") + "　·　知识库文档 " +
       (local.knowledge_files || 0) + " 份")
    : ((data.upstream || {}).message || "");
}

function renderInbox(inbox) {
  const dirs = inbox.dirs || {};
  const rel = function (key) { return ((dirs[key] || {}).relative) || ""; };
  $("inboxStats").innerHTML =
    statCell("等待处理", inbox.waiting || 0, rel("inbox")) +
    statCell("正在等长稳", inbox.settling || 0) +
    statCell("已受理", inbox.accepted || 0, "等待确认") +
    statCell("重复跳过", inbox.duplicate || 0, "内容指纹相同") +
    statCell("受理失败", inbox.failed || 0, "源文件保留") +
    statCell("源文件保留", inbox.source_files || 0, rel("inbox"));
  $("inboxHint").textContent = inbox.hint || "";
  $("scanBtn").disabled = !inbox.enabled || !state.token;
  $("scanBtn").title = inbox.enabled ? "立即扫描收件箱" : "请先在设置页启用收件箱扫描";
  $("openInboxBtn").hidden = !state.canOpenFolder;
  $("openOriginalsBtn").hidden = true;
  const events = (inbox.events || []).map(function (item) {
    const label = item.kind === "accepted" ? "已受理" : (item.kind === "duplicate" ? "重复" : "失败");
    const cls = item.kind === "accepted" ? "ok" : (item.kind === "duplicate" ? "warn" : "bad");
    return '<li><span class="t">' + escapeHtml(item.at) + '</span><span class="badge ' + cls + '">' +
      label + '</span> ' + escapeHtml(item.name) + ' <span class="muted">' +
      escapeHtml(item.detail) + '</span></li>';
  });
  $("inboxEvents").innerHTML = events.length ? events.join("") : '<li class="muted">暂无处理动态</li>';
}

function renderFolders(folders) {
  $("folders").innerHTML = folders.map(function (item) {
    const open = state.canOpenFolder
      ? '<button type="button" class="open" data-folder="' + escapeHtml(item.key) + '">打开</button>'
      : '<span class="muted">（当前页面可打开）</span>';
    return '<div class="folder-row"><span class="nm">' + escapeHtml(item.label) + '</span>' +
      '<span class="pt">' + escapeHtml(item.relative) + '</span>' +
      '<span>' + (item.files || 0) + ' 个文件 · ' + fmtSize(item.bytes) +
      (item.truncated ? '（只统计了前 2000 个）' : '') + '</span>' + open + '</div>';
  }).join("") || '<div class="muted">没有可显示的目录</div>';
  Array.prototype.slice.call($("folders").querySelectorAll("[data-folder]")).forEach(function (node) {
    node.addEventListener("click", function () { openFolder(node.getAttribute("data-folder")); });
  });
}

function statusClass(row) {
  if (row.status === "failed") { return "bad"; }
  if (row.status === "processed") { return "ok"; }
  if (!row.status) { return "info"; }
  return "warn";
}

// 「原件」列只说明原件是什么 / 在不在；打开动作统一放在「操作」列。
// 原来这一列也有一个「打开」按钮，和「操作」列的「打开原件」是同一个
// row.origin.url —— 每行两个按钮、键盘要 Tab 两次。≤900px 收起本列后，
// 「操作」列仍是唯一入口，功能不受影响。
function originCell(row) {
  if (row.can_open && row.origin) {
    const labels = { source: "原始文件", archive: "应用内留档", knowledge: "知识库文档" };
    const label = labels[row.origin.kind] || "文件";
    return '<span class="badge info">' + escapeHtml(label) + '</span>';
  }
  const why = (row.origin && row.origin.reason) || "未找到可用文件";
  return '<span class="muted" title="' + escapeHtml(why) + '">未找到可用文件</span>';
}

function originActionsHtml(row) {
  const origin = row.origin || {};
  if (!row.can_open || !origin.url) { return ""; }
  return '<button type="button" class="open" data-open-url="' + escapeHtml(origin.url) + '">打开</button> ' +
    '<button type="button" class="open" data-download-url="' + escapeHtml(origin.download_url || (origin.url + "?download=true")) + '">下载</button> ';
}

// 选择记录始终保留 doc_id + file_id；具体动作只提交该动作适用的标识，
// 服务端不会根据文件名或客户端路径猜测删除对象。
function rowKey(row) { return (row.doc_id || "") + "|" + (row.file_id || ""); }

// 空态里的「清空筛选 / 清空搜索」按钮。表格每次重绘都会换掉整段 innerHTML，
// 旧按钮连监听一起消失，所以渲染完再绑一次（只绑当前这颗）。
function wireEmptyCta(container) {
  const btn = container.querySelector("[data-clear-filters]");
  if (btn) { btn.addEventListener("click", resetFilters); }
}

function topicOptionsHtml(selected) {
  let html = '<option value="">未关联</option>';
  (state.topics || []).forEach(function (topic) {
    html += '<option value="' + escapeHtml(topic.id) + '"' +
      (topic.id === selected ? " selected" : "") + '>' + escapeHtml(topic.name) + '</option>';
  });
  return html;
}

function topicCellHtml(row, attr, index) {
  if (!state.local || !state.topicsEnabled) {
    return row.topic_name
      ? '<span class="badge info">' + escapeHtml(row.topic_name) + '</span>'
      : '<span class="muted">—</span>';
  }
  return '<select class="topic-select" ' + attr + '="' + index + '" aria-label="关联主题">' +
    topicOptionsHtml(row.topic_id || "") + '</select>';
}

function wireTopicSelectors(container) {
  container.querySelectorAll("select[data-topic-row], select[data-topic-local]").forEach(function (node) {
    node.addEventListener("change", async function () {
      const local = node.hasAttribute("data-topic-local");
      const index = Number(node.getAttribute(local ? "data-topic-local" : "data-topic-row"));
      const row = local ? state.localRows[index] : state.rows[index];
      if (!row) { return; }
      const previous = row.topic_id || "";
      const topicId = node.value;
      const filePath = String(row.file_path || "");
      if (!filePath) {
        node.value = previous;
        flash("这一行没有文档名，无法关联主题", "bad");
        return;
      }
      try {
        if (topicId) {
          await fetchJSON("/api/files/topic", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              file_path: filePath,
              topic_id: topicId,
              name: row.name || "",
              file_id: row.file_id || ""
            })
          });
          row.topic_id = topicId;
          row.topic_name = (state.topics.find(function (item) { return item.id === topicId; }) || {}).name || "";
          flash("已关联主题", "ok");
        } else {
          await fetchJSON("/api/files/topic?file_path=" + encodeURIComponent(filePath), { method: "DELETE" });
          row.topic_id = "";
          row.topic_name = "";
          flash("已解除主题关联", "ok");
        }
        // 正在按主题筛选时，改完标签要让这一行按新归属重新出现/消失。
        if (state.topicId) { loadList(); }
      } catch (err) {
        node.value = previous;
        flash("关联主题失败：" + err.message, "bad");
      }
    });
  });
}

function renderRows() {
  const rows = state.rows;
  // 表头「全选」只代表**渲染那一刻**表里的这批行：翻页 / 换筛选 / 重渲染之后表里
  // 换了一批，旧的勾选状态就不再成立（否则会出现「全选是勾上的、本页一条都没选」）
  $("checkAll").checked = false;
  if (!rows.length) {
    // 筛空了必须给出路：只写「没有符合条件的文档」，用户不知道是搜错了还是库空了
    const filtered = !!(state.status || state.q || state.topicId);
    $("rows").innerHTML = '<tr><td colspan="9" class="muted">' + (!state.upstreamOk
      ? '知识服务暂不可用，当前不显示知识库文档；应用内留档见下方'
      : (filtered
      ? '没有符合条件的文档 <button type="button" class="open" data-clear-filters>清空筛选</button>'
      : '知识库中还没有文档，可在对话页上传资料')) + '</td></tr>';
    wireEmptyCta($("rows"));
    updateSelectionUi();
    return;
  }
  $("rows").innerHTML = rows.map(function (row, index) {
    const checked = state.selected[rowKey(row)] ? " checked" : "";
    const local = row.has_local
      ? '<span class="badge ok">' + escapeHtml(row.local_state_label || "有") + '</span>'
      : '<span class="muted">—</span>';
    const name = escapeHtml(row.name || row.file_path || row.doc_id);
    const path = (row.file_path && row.file_path !== row.name) ? row.file_path : "";
    const extra = path ? '<span class="path">' + escapeHtml(path) + '</span>' : "";
    // 窄屏下这一格是单行省略（见 ≤620px 的样式），完整文件名/路径靠 title 才看得到
    const full = (row.name || "") + (path ? " — " + path : "");
    const chunks = (row.chunks_count === null || row.chunks_count === undefined) ? "—" : row.chunks_count;
    return '<tr>' +
      '<td class="select-cell"><input type="checkbox" data-row="' + index + '"' + checked +
        (state.busy || !state.deleteMode ? ' disabled' : '') + ' aria-label="选择"></td>' +
      '<td class="name" title="' + escapeHtml(full) + '">' + name + extra + '</td>' +
      '<td class="hide-md">' + topicCellHtml(row, "data-topic-row", index) + '</td>' +
      '<td><span class="badge ' + statusClass(row) + '">' + escapeHtml(row.status_label || "—") + '</span>' +
      (row.error_msg ? ' <span class="muted" title="' + escapeHtml(row.error_msg) + '">!</span>' : '') + '</td>' +
      '<td>' + chunks + '</td>' +
      '<td class="hide-md">' + local + '</td>' +
      '<td class="hide-md">' + originCell(row) + '</td>' +
      '<td>' + escapeHtml(String(row.updated_at || "").replace("T", " ").slice(0, 16)) + '</td>' +
      '<td>' + originActionsHtml(row) +
      '<button type="button" class="open" data-detail-doc="' + escapeHtml(row.doc_id || "") + '">详情</button></td>' +
      '</tr>';
  }).join("");
  wireRowEvents($("rows"));
  wireTopicSelectors($("rows"));
  updateSelectionUi();
}

function renderLocalRows() {
  const slice = localPageSlice();
  const rows = slice.rows;
  $("checkAllLocal").checked = false;   // 同 renderRows：换了一页，旧的「全选」状态不再成立
  $("checkAllLocal").disabled = !state.upstreamOk;
  if (!rows.length) {
    // 本机表只受搜索词影响（状态筛选不作用于它），所以只有 q 非空时才给「清空搜索」
    $("localRows").innerHTML = '<tr><td colspan="5" class="muted">' + (state.q || state.topicId
      ? '没有匹配的本机记录 <button type="button" class="open" data-clear-filters>清空筛选</button>'
      : '暂无待处理或已跳过的文件') + '</td></tr>';
    wireEmptyCta($("localRows"));
    renderLocalPager();
    return;
  }
  $("localRows").innerHTML = rows.map(function (row, index) {
    const checked = state.selected[rowKey(row)] ? " checked" : "";
    // 本机行这一格是「文件名 + 状态与原因」；原因可能很长，窄屏会被省略号截掉，
    // 所以完整内容放进 title。状态和错误原因都来自服务端，不在前端猜。
    const stateName = String(row.state || "");
    const stateLabel = String(row.local_state_label || "");
    const detail = String(row.local_detail || "");
    const note = (detail && stateLabel && detail.indexOf(stateLabel) === 0)
      ? detail
      : [stateLabel, detail].filter(Boolean).join(" · ");
    const resumeButton = stateName === "skipped"
      ? '<button type="button" class="resume" data-resume-local="' + (slice.start + index) + '"'
        + (state.busy || state.deleteMode ? ' disabled' : '') + '>继续入库</button> '
      : '';
    const handleButton = (stateName === "review" || stateName === "needs_ocr"
        || stateName === "blocked" || stateName === "failed")
      ? '<a class="open" href="/" title="回到对话页处理这份文件">去处理</a> '
      : '';
    const busyText = (stateName === "ingesting" || stateName === "processing")
      ? '<span class="muted">入库中…</span> ' : '';
    return '<tr>' +
      // data-local 必须是**在 state.localRows 里的绝对下标**：切过页之后
      // 表内下标不再等于数组下标，直接用 index 会勾错行
      '<td class="select-cell"><input type="checkbox" data-local="' + (slice.start + index) + '"' + checked
        + (state.upstreamOk && state.deleteMode && !state.busy ? '' : ' disabled') + ' aria-label="选择"></td>' +
      '<td class="name" title="' + escapeHtml(row.name + (note ? " — " + note : "")) + '">' +
      escapeHtml(row.name) +
      (note ? '<span class="path">' + escapeHtml(note) + '</span>' : "") + '</td>' +
      '<td class="hide-md">' + topicCellHtml(row, "data-topic-local", slice.start + index) + '</td>' +
      '<td>' + escapeHtml(String(row.created_at || "").slice(0, 16)) + '</td>' +
      '<td>' + busyText + handleButton + resumeButton + originActionsHtml(row) +
      '<button type="button" class="open" data-detail-local="' + (slice.start + index) + '">详情</button></td>' +
      '</tr>';
  }).join("");
  wireRowEvents($("localRows"));
  wireTopicSelectors($("localRows"));
  renderLocalPager();
  updateSelectionUi();
}

// 当前页的本机行，以及它在 state.localRows 里的起始下标。
// 本机表只能前端切片（后端不给本机记录分页参数，见 state.localPage 的说明），
// 「第几页」就是对内存里这 50 条数组切片 —— 所以翻页不需要再打一次接口。
function localPageSlice() {
  const total = state.localRows.length;
  const pages = Math.max(1, Math.ceil(total / state.localSize));
  // 删除 / 换筛选之后总条数会变少，当前页可能已经不存在了，先夹回最后一页
  if (state.localPage > pages) { state.localPage = pages; }
  const start = total ? (state.localPage - 1) * state.localSize : 0;
  return { start: start, rows: state.localRows.slice(start, start + state.localSize) };
}

// 本机表的分页控件。文案要写清「当前范围 + 总数」，只说「第 N 页」用户看不出还剩多少。
function renderLocalPager() {
  const slice = localPageSlice();
  const total = state.localRows.length;
  const pages = Math.max(1, Math.ceil(total / state.localSize));
  const end = Math.min(slice.start + state.localSize, total);
  // 后端截断时必须说出去。少几条不提示，用户会当成"本机就这些" ——
  // 而这正是这条改造要修的毛病（原来是硬截 50 条且只字不提）。
  // 截断时的「共 N 条」要写成「共 N 条**已载入**」：不写这三个字，
  // 读起来就是"本机一共 N 条"，而真实条数比它多。
  var cut = state.localTotal > total;
  var label = total
    ? "第 " + (slice.start + 1) + "–" + end + " 条，共 " + total + " 条" + (cut ? "已载入" : "") +
      "（第 " + state.localPage + " / " + pages + " 页）"
    : "共 0 条";
  if (cut) {
    label += "　·　另有 " + (state.localTotal - total) + " 条未载入（接口单次上限 " + total + " 条）";
  }
  $("localPageInfo").textContent = label;
  $("localPrevPage").disabled = state.localPage <= 1;
  $("localNextPage").disabled = state.localPage >= pages;
}

function renderPager(pagination) {
  const total = pagination.total_count || 0;
  const pages = pagination.total_pages || 1;
  $("pageInfo").textContent = "第 " + (pagination.page || 1) + " / " + pages + " 页，共 " + total + " 条";
  $("prevPage").disabled = !pagination.has_prev;
  $("nextPage").disabled = !pagination.has_next;
}

function wireRowEvents(container) {
  Array.prototype.slice.call(container.querySelectorAll("[data-open-url]")).forEach(function (node) {
    node.addEventListener("click", function () {
      const url = node.getAttribute("data-open-url");
      if (url) { window.open(url, "_blank", "noopener"); }
    });
  });
  Array.prototype.slice.call(container.querySelectorAll("[data-download-url]")).forEach(function (node) {
    node.addEventListener("click", function () {
      const url = node.getAttribute("data-download-url");
      if (!url) { return; }
      const link = document.createElement("a");
      link.href = url;
      link.download = "";
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
    });
  });
  Array.prototype.slice.call(container.querySelectorAll("[data-detail-doc]")).forEach(function (node) {
    node.addEventListener("click", function () { openDocumentDetail(node.getAttribute("data-detail-doc")); });
  });
  Array.prototype.slice.call(container.querySelectorAll("[data-detail-local]")).forEach(function (node) {
    node.addEventListener("click", function () {
      const row = state.localRows[Number(node.getAttribute("data-detail-local"))];
      if (row) { openLocalDetail(row); }
    });
  });
  Array.prototype.slice.call(container.querySelectorAll("[data-resume-local]")).forEach(function (node) {
    node.addEventListener("click", function () {
      const row = state.localRows[Number(node.getAttribute("data-resume-local"))];
      if (row) { resumeLocal(row, node); }
    });
  });
  Array.prototype.slice.call(container.querySelectorAll("[data-row]")).forEach(function (node) {
    node.addEventListener("change", function () {
      const row = state.rows[Number(node.getAttribute("data-row"))];
      if (row) { toggleRow(row, node.checked); }
    });
  });
  Array.prototype.slice.call(container.querySelectorAll("[data-local]")).forEach(function (node) {
    node.addEventListener("change", function () {
      const row = state.localRows[Number(node.getAttribute("data-local"))];
      if (row) { toggleRow(row, node.checked); }
    });
  });
}

// 继续入库后，本机行会停在 ingesting；定时向知识库查一次真实索引状态并刷新列表，
// 避免用户只看到“已继续入库”然后这一行再也不动。
function scheduleIngestRefresh(attempts) {
  if (state.ingestRefreshTimer) {
    clearTimeout(state.ingestRefreshTimer);
    state.ingestRefreshTimer = 0;
  }
  if (attempts <= 0) { return; }
  state.ingestRefreshTimer = setTimeout(async function () {
    state.ingestRefreshTimer = 0;
    const ingesting = state.localRows.filter(function (row) {
      return (row.state === "ingesting" || row.state === "processing") && row.file_id;
    });
    if (!ingesting.length) {
      await loadOverview().catch(function () {});
      return;
    }
    await Promise.all(ingesting.map(function (row) {
      return fetch("/ingest/knowledge-status/" + encodeURIComponent(row.file_id), {
        method: "POST",
        cache: "no-store"
      }).catch(function () { return null; });
    }));
    await loadList().catch(function () {});
    const still = state.localRows.some(function (row) {
      return row.state === "ingesting" || row.state === "processing";
    });
    if (still) { scheduleIngestRefresh(attempts - 1); }
    else { await loadOverview().catch(function () {}); }
  }, 6000);
}

async function resumeLocal(row, button) {
  if (state.busy || state.deleteMode || !row.file_id) { return; }
  state.busy = true;
  button.disabled = true;
  button.textContent = "正在入库…";
  updateSelectionUi();
  try {
    await fetchJSON("/api/ingest/action/" + encodeURIComponent(row.file_id), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "resume" })
    });
    flash("已继续入库，正在建立索引", "ok");
    await loadAll();
    scheduleIngestRefresh(10);
  } catch (err) {
    setNotice("继续入库失败：" + err.message, "bad");
  } finally {
    state.busy = false;
    if (document.body.contains(button)) {
      button.disabled = false;
      button.textContent = "继续入库";
    }
    renderRows();
    renderLocalRows();
    updateSelectionUi();
  }
}

// ---------- 选择 ----------
function selectedItems() {
  return Object.keys(state.selected).map(function (key) { return state.selected[key]; });
}

function enterDeleteMode() {
  if (state.busy || !state.local || !state.upstreamOk) { return; }
  state.deleteMode = true;
  state.selected = {};
  document.body.classList.add("delete-mode");
  renderRows();
  renderLocalRows();
  updateSelectionUi();
}

function exitDeleteMode() {
  if (state.busy) { return; }
  state.deleteMode = false;
  state.selected = {};
  closeDeleteDialog(false);
  document.body.classList.remove("delete-mode");
  $("checkAll").checked = false;
  $("checkAllLocal").checked = false;
  renderRows();
  renderLocalRows();
  updateSelectionUi();
}

function toggleRow(row, checked) {
  if (state.busy || !state.deleteMode) { return; }
  const key = rowKey(row);
  if (checked) {
    state.selected[key] = {
      doc_id: row.doc_id || "",
      file_id: row.file_id || "",
      name: row.name || ""
    };
  } else {
    delete state.selected[key];
  }
  updateSelectionUi();
}

// 表头「全选」：改 state 之外还要把表内复选框一起同步。
// 原实现只改 state，用户点「全选」时行里的勾一个都不动（只有底部计数变了）——
// 而现在表头勾选还可能是被翻页重置过的，两边更容易对不上。
// 这里刻意**不**整表重渲染：重渲染会立刻把用户刚点上的表头「全选」打回未勾选。
function toggleAll(checked, rows, container) {
  if (!state.upstreamOk || state.busy || !state.deleteMode) { return; }
  rows.forEach(function (row) { toggleRow(row, checked); });
  if (!container) { return; }
  Array.prototype.slice.call(container.querySelectorAll('input[type="checkbox"]'))
    .forEach(function (node) { node.checked = checked; });
}

function updateSelectionUi() {
  const count = selectedItems().length;
  $("deleteModeBtn").hidden = !state.local;
  $("deleteModeBtn").disabled = state.busy || !state.upstreamOk ||
    (state.deleteMode && count === 0);
  $("deleteModeBtn").textContent = state.deleteMode
    ? (count ? "删除所选（" + count + "）" : "删除所选")
    : "删除";
  $("deleteModeBtn").classList.toggle("danger", state.deleteMode);
  $("cancelDeleteModeBtn").hidden = !state.deleteMode;
  $("cancelDeleteModeBtn").disabled = state.busy;
  $("checkAll").disabled = !state.deleteMode || !state.upstreamOk || state.busy;
  $("checkAllLocal").disabled = !state.deleteMode || !state.upstreamOk || state.busy;
}

function openDeleteDialog(trigger) {
  if (state.busy || !state.deleteMode) { return; }
  const items = selectedItems().map(function (item) {
    return {
      doc_id: item.doc_id || "",
      file_id: item.file_id || "",
      name: item.name || ""
    };
  });
  if (!items.length) { return; }
  state.deleteItems = items;
  state.deleteReturnFocus = trigger || null;
  $("deleteSelectionCount").textContent = "已选择 " + items.length + " 个文件";
  $("deleteKnowledgeCopyLabel").hidden = !items.some(function (item) {
    return !!item.doc_id;
  });
  $("deleteKnowledgeCopy").checked = false;
  $("deleteConfirmation").value = "";
  updateDeleteConfirmButton();
  $("deleteDialogBackdrop").hidden = false;
  $("deleteDialog").hidden = false;
  focusQuiet($("deleteConfirmation"));
}

function updateDeleteConfirmButton() {
  const ready = !state.busy &&
    $("deleteConfirmation").value.trim().toLowerCase() === "yes";
  $("confirmDelete").disabled = !ready;
  $("confirmDelete").classList.toggle("ready", ready);
}

function closeDeleteDialog(restoreFocus) {
  const wasOpen = !$("deleteDialog").hidden;
  $("deleteDialog").hidden = true;
  $("deleteDialogBackdrop").hidden = true;
  $("deleteConfirmation").value = "";
  $("deleteKnowledgeCopy").checked = false;
  $("deleteKnowledgeCopyLabel").hidden = true;
  $("confirmDelete").disabled = true;
  $("confirmDelete").classList.remove("ready");
  state.deleteItems = [];
  const trigger = state.deleteReturnFocus;
  state.deleteReturnFocus = null;
  if (wasOpen && restoreFocus && trigger && document.body.contains(trigger)) {
    focusQuiet(trigger);
  }
}

function deleteDialogFocusables() {
  return Array.prototype.slice.call(
    $("deleteDialog").querySelectorAll('button, input, [tabindex]:not([tabindex="-1"])')
  ).filter(function (node) {
    return !node.disabled && !node.hidden && !node.closest("[hidden]");
  });
}

function handleDeleteDialogKeydown(event) {
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeDeleteDialog(true);
    return;
  }
  if (event.key !== "Tab") { return; }
  const nodes = deleteDialogFocusables();
  if (!nodes.length) { return; }
  const first = nodes[0];
  const last = nodes[nodes.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    focusQuiet(last);
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    focusQuiet(first);
  }
}

// ---------- 详情 ----------
function detailRow(key, value) {
  return '<div class="k">' + escapeHtml(key) + '</div><div class="v">' + escapeHtml(value == null || value === "" ? "—" : value) + '</div>';
}

function renderDetail(doc) {
  const local = doc.local_job || {};
  const origin = doc.origin || {};
  const originLabel = origin.kind === "archive"
    ? "应用内留档"
    : (origin.kind === "source" ? "原始文件" : "文件");
  const parts = [];
  parts.push('<div class="detail-grid">');
  parts.push(detailRow("名称", doc.name || doc.file_path || ""));
  parts.push(detailRow("文档 ID", doc.doc_id || ""));
  parts.push(detailRow("状态", doc.status_label || doc.status || doc.local_state_label || ""));
  parts.push(detailRow("分块", doc.chunks_count));
  parts.push(detailRow("track_id", doc.track_id || local.track_id || ""));
  parts.push(detailRow("更新时间", doc.updated_at || doc.created_at || ""));
  parts.push(detailRow("应用内留档 ID", local.file_id || doc.file_id || ""));
  parts.push(detailRow("处理状态", local.state || doc.local_state_label || ""));
  parts.push(detailRow("质量门", local.quality_level || doc.quality_level || ""));
  parts.push(detailRow("质量分", local.quality_score || doc.quality_score || ""));
  parts.push(detailRow(originLabel, origin.path || local.source_path || ""));
  parts.push(detailRow("文件说明", origin.reason || ""));
  parts.push('</div>');
  if (doc.can_open && origin.url) {
    parts.push('<div class="detail-actions">' +
      '<a href="' + escapeHtml(origin.url) + '" target="_blank" rel="noopener">打开' + escapeHtml(originLabel) + '</a>' +
      '<a href="' + escapeHtml(origin.download_url || (origin.url + "?download=true")) + '" download>下载' + escapeHtml(originLabel) + '</a>' +
      '</div>');
  }
  if (doc.content_summary) {
    parts.push('<div class="detail-section"><h3>内容摘要</h3><div>' + escapeHtml(doc.content_summary) + '</div></div>');
  }
  if (doc.error_msg || doc.error || local.message) {
    parts.push('<div class="detail-section"><h3>错误信息</h3><div>' + escapeHtml(doc.error_msg || doc.error || local.message) + '</div></div>');
  }
  $("detailBody").innerHTML = parts.join("");
  $("detailPanel").hidden = false;
  $("detailBackdrop").hidden = false;
  focusQuiet($("detailClose"));
}

function closeDetail() {
  $("detailPanel").hidden = true;
  $("detailBackdrop").hidden = true;
}

async function openDocumentDetail(docId) {
  if (!docId) { return; }
  try {
    const data = await fetchJSON("/api/files/detail/" + encodeURIComponent(docId));
    renderDetail(data.document || {});
  } catch (err) {
    setNotice("读取文档详情失败：" + err.message, "bad");
  }
}

function openLocalDetail(row) {
  renderDetail(row || {});
}

// ---------- 删除 ----------
function deletionResultText(job) {
  const succeeded = Number(job.succeeded || 0);
  const failed = Number(job.failed || 0);
  const pending = Number(job.pending || 0);
  if (failed) {
    return "操作完成：成功 " + succeeded + " 项，失败 " + failed + " 项。" +
      (pending ? "另有 " + pending + " 项知识库移除仍在处理中。" : "") +
      "请查看结果后重试失败项。";
  }
  return pending
    ? ("已删除 " + succeeded + " 项；知识库移除仍在处理中。")
    : ("已删除 " + succeeded + " 项。");
}

async function doDelete() {
  if (state.busy || $("confirmDelete").disabled) { return; }
  const items = state.deleteItems.map(function (item) {
    return {
      doc_id: item.doc_id || "",
      file_id: item.file_id || "",
      name: item.name || ""
    };
  });
  if (!items.length) { return; }
  const deleteKnowledgeUploadCopy = $("deleteKnowledgeCopy").checked &&
    !$("deleteKnowledgeCopyLabel").hidden;
  state.busy = true;
  updateSelectionUi();
  $("confirmDelete").disabled = true;
  $("confirmDelete").textContent = "正在提交…";
  try {
    const data = await fetchJSON("/api/files/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        confirmation: "Yes",
        delete_knowledge_upload_copy: deleteKnowledgeUploadCopy,
        items: items
      })
    });
    const jobId = data.job && data.job.id;
    if (!jobId) { throw new Error("删除任务未返回任务编号"); }
    closeDeleteDialog(false);
    state.selected = {};
    state.deleteMode = false;
    document.body.classList.remove("delete-mode");
    setNotice("删除任务已开始…", "");
    renderRows();
    renderLocalRows();
    let job = data.job;
    for (let i = 0; i < 120; i += 1) {
      await new Promise(function (resolve) { setTimeout(resolve, 1000); });
      const status = await fetchJSON("/api/files/delete/" + encodeURIComponent(jobId));
      job = status.job || job;
      setNotice("删除进度 " + (job.completed || 0) + " / " + (job.total || 0) +
        "，成功 " + (job.succeeded || 0) + "，失败 " + (job.failed || 0) +
        (job.pending ? "，待确认 " + job.pending : ""), job.failed ? "bad" : "");
      if (job.status === "completed") { break; }
    }
    if (job.status !== "completed") {
      setNotice("任务仍在处理中，请稍后刷新查看结果。", "");
      scheduleRefresh();
      return;
    }
    setNotice(
      deletionResultText(job),
      job.failed ? "bad" : (job.pending ? "" : "ok")
    );
    await loadAll();
    scheduleRefresh();
  } catch (err) {
    setNotice("删除失败：" + err.message, "bad");
  } finally {
    state.busy = false;
    $("confirmDelete").textContent = "删除";
    updateDeleteConfirmButton();
    renderRows();
    renderLocalRows();
    updateSelectionUi();
  }
}

function scheduleRefresh() {
  if (state.pollTimer) { clearTimeout(state.pollTimer); }
  let ticks = 0;
  const tick = async function () {
    ticks += 1;
    await loadList();
    if (ticks < 3) { state.pollTimer = setTimeout(tick, 4000); }
  };
  state.pollTimer = setTimeout(tick, 4000);
}

// ---------- 打开文件夹 / 立即扫描（仅本机） ----------
async function openFolder(target) {
  try {
    const data = await fetchJSON("/api/files/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: state.token, target: target })
    });
    flash(data.message || "已打开文件夹", "ok");
  } catch (err) {
    setNotice(err.message, "bad");
  }
}

async function scanInbox() {
  $("scanBtn").disabled = true;
  $("scanBtn").textContent = "正在扫描…";
  try {
    const data = await fetchJSON("/api/inbox/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: state.token, reason: "web-manual" })
    });
    flash(data.message || "扫描完成", "ok");
    await loadAll();
  } catch (err) {
    setNotice(err.message, "bad");
  } finally {
    $("scanBtn").disabled = false;
    $("scanBtn").textContent = "立即扫描收件箱";
  }
}

init().catch(function (err) {
  setNotice("页面初始化失败：" + err.message, "bad");
});

// ============================================================
// 会话心跳 + 「服务已停止」提示（ 修复）
// ============================================================
// 这一页也必须挂心跳 ——  的「关窗口才停服」是靠「页面还在就报平安」实现的：
// 从对话页点进「文件管理」时，对话页的 pagehide 已经用 sendBeacon 预约了「25 秒后停服」，
// 只有新页面继续 keepalive 才能把这次预约取消。这一页漏引 session.js 时，用户在文件管理页
// 翻资料，服务会连同 LightRAG / Qdrant 一起在 25 秒后被停掉
// （真机事故：logs\stop.log 里两次「原因：tab-close」，LightRAG 侧没有任何报错）。
function renderSession(info) {
  if (!info || !info.ready) { return; }
  const lan = info.mode === "lan";
  $("stopBtn").hidden = !info.canStop;          // 手机端不显示（硬调后端也是 403）
  if (info.stopping) {
    const left = Number(info.pending);
    $("stopping").hidden = false;
    $("stoppingText").textContent = (left > 0)
      ? "服务将在 " + left + " 秒后停止" + (info.busy ? "（有文件正在处理，已延长等待）" : "") + "。"
      : "服务已停止，本页面已不再连接后端。";
  } else {
    $("stopping").hidden = true;
  }
  if (info.offline && !info.stopping) {
    $("offline").hidden = false;
    $("offlineText").textContent = lan
      ? "电脑上的这个服务已经停止，手机暂时用不了。"
      : "本应用与知识库服务已在这台电脑上停止。";
    $("offlineHint").textContent = lan
      ? "请在电脑上从项目根目录重新启动服务重新启动；启动完成后这个页面会自动恢复，也可以点「重新检测」。"
      : "想再次使用：从项目根目录重新启动服务（通常需要数十秒）。服务起来后这个页面会自动恢复。";
  } else {
    $("offline").hidden = true;
  }
}

// 遮罩上的「重新检测」：电脑上重新启动服务后点一下就能接着用（不用重新输地址）
$("offlineRetry").addEventListener("click", function () {
  const session = window.RAGKB_SESSION;
  if (!session) { window.location.reload(); return; }
  $("offlineText").textContent = "正在检测…";
  session.refresh().then(function () {
    renderSession(session.state);
    loadAll();
  });
});

// 「停止全部服务」：只有本机页面会显示（令牌 + guard 都在后端兜底）
$("stopBtn").addEventListener("click", async function () {
  const session = window.RAGKB_SESSION;
  if (!session || session.state.stopping) { return; }
  const busyNote = session.state.busy ? "注意：有文件正在解析 / 入库。\n\n" : "";
  const detail = busyNote + "共享的 Qdrant / Ollama 不会被停止；再次使用请从项目根目录重新启动服务。";
  const ok = window.RAGKB && typeof window.RAGKB.confirm === "function"
    ? await window.RAGKB.confirm({
        title: "停止全部服务？",
        body: "停止「本地 RAG 知识库」，以及本次拉起的知识库服务？",
        detail: detail,
        confirmText: "停止全部服务",
        cancelText: "取消",
        danger: true
      })
    : window.confirm("停止「本地 RAG 知识库」，以及本次拉起的知识库服务？\n\n" + detail);
  if (!ok) { return; }
  session.stopNow();
});

window.addEventListener("ragkb-session", function (event) { renderSession(event.detail); });
if (window.RAGKB_SESSION) { renderSession(window.RAGKB_SESSION.state); }
