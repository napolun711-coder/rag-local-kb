
let state = { items: [], selected: null, status: null };
const $ = function (id) { return document.getElementById(id); };
function setToast(text, bad) {
  if (window.RAGKB && typeof window.RAGKB.toast === "function") {
    window.RAGKB.toast(text || "", bad ? "bad" : "ok");
    return;
  }
  const n = $("mode");
  if (n) { n.textContent = text || ""; n.style.color = bad ? "var(--bad)" : "var(--muted)"; }
}
function askConfirm(options) {
  if (window.RAGKB && typeof window.RAGKB.confirm === "function") {
    return window.RAGKB.confirm(options);
  }
  return Promise.resolve(window.confirm(options.title + "\n\n" + options.body));
}
async function api(path, options) {
  const init = Object.assign({cache: "no-store"}, options || {});
  let res;
  try { res = await fetch(path, init); } catch (e) { return {ok: false, status: 0, data: null}; }
  let data = null; try { data = await res.json(); } catch (e) { data = null; }
  if (res.status === 401) {
    const detail = data && data.detail;
    if (detail && (detail.error === "device_auth_required" || detail.error === "chat_token_required")) {
      location.replace(detail.pair_url || "/pair");
    }
  }
  return {ok: res.ok, status: res.status, data: data};
}
function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
    return ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[ch];
  });
}
function badge(status) { return '<span class="badge ' + esc(status) + '">' + esc(status) + "</span>"; }
function time(value) { if (!value) return ""; const d = new Date(value); return isNaN(d.getTime()) ? esc(value) : d.toLocaleString(); }
async function loadStatus() {
  const out = await api("/api/memory/status");
  if (!out.ok || !out.data) { setToast("状态读取失败", true); return; }
  state.status = out.data;
  const s = out.data;
  const workerRunning = s.worker_running !== undefined ? s.worker_running : s.worker;
  $("status").innerHTML = [
    "记忆 " + (s.memory_count || 0),
    "待确认 " + (s.pending_memory_count || 0),
    "任务队列 " + ((s.jobs && s.jobs.total) || 0),
    "上下文注入 " + (s.context_enabled ? "开" : "关"),
    "快记忆 " + (s.fast_memory ? "开" : "关"),
    "自动提取 " + (s.auto_extract ? "开" : "关"),
    "聊天话题过滤 " + (s.chat_topic_filter ? "开" : "关"),
    "worker " + (workerRunning ? "运行中" : "已停") +
      (s.worker_configured && !workerRunning ? "（配置开）" : ""),
  ].map(function (x) { return '<span class="chip">' + esc(x) + "</span>"; }).join("");
  if (s.enabled === false) { setToast("记忆系统未启用（MEMORY_ENABLED=off）", true); }
  else { setToast("本机可编辑 · 写操作仅限本机", false); }
}
async function loadItems() {
  const params = new URLSearchParams({limit: "100"});
  const st = $("statusFilter").value; if (st) params.set("status", st);
  const kind = $("kindFilter").value; if (kind) params.set("kind", kind);
  const q = $("query").value.trim(); if (q) params.set("q", q);
  const out = await api("/api/memory/items?" + params.toString());
  const list = $("list");
  if (!out.ok || !out.data) { list.innerHTML = '<div class="ui-state ui-error">读取失败：' + esc(out.status) + "</div>"; return; }
  state.items = out.data.items || [];
  if (!out.data.enabled) { list.innerHTML = '<div class="ui-state ui-empty">记忆系统未启用。</div>'; return; }
  if (!state.items.length) { list.innerHTML = '<div class="ui-state ui-empty">没有符合条件的记忆。</div>'; return; }
  list.innerHTML = "";
  state.items.forEach(function (item) {
    const card = document.createElement("div");
    card.className = "card" + (state.selected && state.selected.id === item.id ? " sel" : "");
    card.innerHTML = '<div class="subject">' + esc(item.subject || "(无主题)") + " " + badge(item.status) + "</div>"
      + '<div class="content">' + esc((item.content || "").slice(0, 220)) + "</div>"
      + '<div class="meta">' + esc(item.kind) + " · " + esc(item.scope) + " · " + esc(item.sensitivity)
      + " · 置信 " + esc(item.confidence) + " · " + esc(time(item.updated_at)) + "</div>";
    card.addEventListener("click", function () { selectItem(item.id); });
    list.appendChild(card);
  });
}
async function selectItem(id) {
  const out = await api("/api/memory/items/" + encodeURIComponent(id) + "?include_deleted=true");
  if (!out.ok || !out.data) { setToast("读取记忆失败", true); return; }
  const m = out.data.memory;
  state.selected = m;
  const src = await api("/api/memory/items/" + encodeURIComponent(id) + "/sources");
  const ev = await api("/api/memory/items/" + encodeURIComponent(id) + "/events?limit=50");
  renderDetail(m, src.data || {}, ev.data || {});
  loadItems();
}
function renderDetail(m, sources, events) {
  const box = $("detail");
  const sourceHtml = (sources.items || []).length
    ? sources.items.map(function (s) { return '<div class="source">' + esc(s.quote || "(无 quote)") + '<br><span class="muted">message ' + esc(s.message_id || "-") + " · session " + esc(s.session_id || "-") + "</span></div>"; }).join("")
    : '<div class="ui-state ui-empty">没有来源记录。</div>';
  const eventHtml = (events.items || []).length
    ? events.items.slice(0, 20).map(function (e) { return '<div class="source">' + esc(e.action) + " · v" + esc(e.version) + " · " + esc(e.actor) + " · " + esc(time(e.created_at)) + "</div>"; }).join("")
    : '<div class="ui-state ui-empty">没有事件记录。</div>';
  box.innerHTML = '<h2>' + esc(m.subject || "(无主题)") + " " + badge(m.status) + "</h2>"
    + '<div class="muted">' + esc(m.kind) + " · " + esc(m.scope) + " · " + esc(m.sensitivity) + " · " + esc(m.source_type)
    + " · 置信 " + esc(m.confidence) + " · 重要度 " + esc(m.importance) + " · v" + esc(m.version) + "</div>"
    + '<div class="field"><label>内容</label><textarea id="editContent">' + esc(m.content || "") + "</textarea></div>"
    + '<div class="field"><label>主题词</label><input id="editSubject" value="' + esc(m.subject || "") + '"></div>'
    + '<div class="field"><label>类型</label><select id="editKind">'
    + ["preference","goal","fact","constraint","decision","habit","profile","background"].map(function (k) { return '<option value="' + k + '"' + (m.kind === k ? " selected" : "") + ">" + k + "</option>"; }).join("")
    + '</select></div>'
    + '<div class="field"><label>重要度</label><select id="editImportance">'
    + [1,2,3,4,5].map(function (n) { return '<option value="' + n + '"' + (Number(m.importance) === n ? " selected" : "") + ">" + n + "</option>"; }).join("")
    + '</select></div>'
    + '<div class="field"><label>敏感级别</label><select id="editSensitivity">'
    + ["normal","sensitive"].map(function (v) { return '<option value="' + v + '"' + (m.sensitivity === v ? " selected" : "") + ">" + v + "</option>"; }).join("")
    + '</select></div>'
    + '<div class="field"><label><input type="checkbox" id="editPinned"' + (m.pinned ? " checked" : "") + '> 置顶</label></div>'
    + '<div class="actions">'
    + '<button class="primary" id="save">保存修改</button>'
    + (m.status === "pending" ? '<button id="confirm">确认</button><button id="reject">拒绝</button>' : "")
    + ((m.status === "active" || m.status === "conflicted") ? '<button id="expire">标记失效</button>' : "")
    + (m.status === "deleted" ? '<button id="restore">恢复</button>' : '<button class="danger" id="del">删除</button>')
    + '<button class="danger" id="purge">彻底忘记</button>'
    + "</div>"
    + '<div class="sources"><div class="muted">来源 quote</div>' + sourceHtml + '<div class="muted" style="margin-top:8px">事件</div>' + eventHtml + "</div>";
  $("save").addEventListener("click", saveEdit);
  if ($("confirm")) $("confirm").addEventListener("click", function () { action("confirm"); });
  if ($("reject")) $("reject").addEventListener("click", function () { action("reject"); });
  if ($("expire")) $("expire").addEventListener("click", function () { action("expire"); });
  if ($("restore")) $("restore").addEventListener("click", function () { action("restore"); });
  if ($("del")) $("del").addEventListener("click", function () { remove(false); });
  $("purge").addEventListener("click", function () { remove(true); });
}
async function saveEdit() {
  if (!state.selected) return;
  const out = await api("/api/memory/items/" + encodeURIComponent(state.selected.id), {
    method: "PATCH", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      content: $("editContent").value,
      subject: $("editSubject").value,
      kind: $("editKind").value,
      importance: Number($("editImportance").value),
      sensitivity: $("editSensitivity").value,
      pinned: !!($("editPinned") && $("editPinned").checked)
    }),
  });
  if (!out.ok) { setToast(out.status === 403 ? "写操作仅限本机；局域网只能查看" : "保存失败", true); return; }
  setToast("已保存", false); await selectItem(state.selected.id); await loadStatus(); await loadSupervisor();
}
async function action(kind) {
  if (!state.selected) return;
  const out = await api("/api/memory/items/" + encodeURIComponent(state.selected.id) + "/" + kind, {method: "POST"});
  if (!out.ok) { setToast("操作失败（" + out.status + "）", true); return; }
  setToast("已" + kind, false); await selectItem(state.selected.id); await loadStatus(); await loadSupervisor();
}
async function remove(purge) {
  if (!state.selected) return;
  const ok = await askConfirm({
    title: purge ? "彻底删除这条记忆？" : "删除这条记忆？",
    body: purge
      ? "彻底删除后不可恢复，相关的来源记录也会被清理。"
      : "删除后这条记忆将不再参与后续对话。",
    detail: purge ? "这是一项不可撤销的破坏性操作。" : "",
    confirmText: purge ? "彻底删除" : "删除",
    cancelText: "保留",
    danger: true
  });
  if (!ok) return;
  const out = await api("/api/memory/items/" + encodeURIComponent(state.selected.id) + "?purge=" + (purge ? "true" : "false"), {method: "DELETE"});
  if (!out.ok) { setToast("删除失败（" + out.status + "）", true); return; }
  state.selected = null; $("detail").innerHTML = '<div class="ui-state ui-empty">已删除。选择左侧一条记忆查看详情。</div>';
  await loadItems(); await loadStatus(); await loadSupervisor();
}
const FINDING_ACTION_TEXT = {
  review_source_or_disable: "核对来源；没有可靠来源就停用或删除",
  review_and_expire: "核对有效期；过期就标记失效",
  review_sensitivity: "核对敏感内容；必要时改为敏感并重新确认",
  review_and_disable: "检查是否是提示注入或错误指令；必要时停用",
  review_confidence: "确认内容是否可信；不确认就保持待确认或删除",
  reconfirm_or_expire: "重新确认现状；不再成立就标记失效",
  merge_or_remove: "核对重复条目，保留一条并删除其余",
  review_active_memories: "长期记忆数量偏多，逐条清理不再需要的条目"
};
const FINDING_MESSAGE_TEXT = {
  missing_source: "这条生效记忆缺少可核对的原始来源，建议核对后保留或停用。",
  expired: "这条记忆已超过有效期，建议核对现状后标记失效。",
  sensitive_active: "这条敏感记忆正在参与管理，建议核对是否仍需保留。",
  unsafe_instruction: "这条记忆包含类似指令注入的内容，建议人工检查后停用。",
  low_confidence: "这条记忆置信度偏低，建议确认内容是否可靠。",
  stale: "这条记忆长期未重新确认，建议核对是否仍然成立。",
  duplicate_content: "多条记忆内容高度重复，建议只保留最准确的一条。",
  active_budget_exceeded: "生效记忆数量超过建议预算，建议清理不再需要的条目。"
};
async function loadSupervisor() {
  const statusOut = await api("/api/memory/supervisor/status");
  const findingsOut = await api("/api/memory/supervisor/findings?status=open&limit=100");
  const stateNode = $("supervisorState");
  const list = $("supervisorList");
  if (!statusOut.ok || !statusOut.data || !findingsOut.ok || !findingsOut.data) {
    stateNode.textContent = "监督建议读取失败";
    stateNode.style.color = "var(--bad)";
    list.innerHTML = "";
    return;
  }
  const s = statusOut.data;
  const items = findingsOut.data.items || [];
  stateNode.style.color = "var(--muted)";
  stateNode.textContent = (s.enabled ? "自动监督已开启" : "自动监督已关闭")
    + " · 待处理建议 " + (s.open_findings || items.length)
    + (s.last_scan_at ? (" · 上次扫描 " + time(s.last_scan_at)) : " · 尚未扫描")
    + " · 策略只读，由后台配置";
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = '<div class="ui-state ui-empty">没有待处理建议。</div>';
    return;
  }
  items.forEach(function (item) {
    const card = document.createElement("div");
    card.className = "finding";
    const action = String((item.detail || {}).suggested_action || "");
    card.innerHTML = '<div class="top"><span class="severity ' + esc(item.severity) + '">' + esc(item.severity) + "</span>"
      + '<strong>' + esc((item.detail || {}).subject || item.code) + "</strong></div>"
      + '<div class="msg">' + esc(FINDING_MESSAGE_TEXT[item.code] || item.message) + "</div>"
      + '<div class="why">' + esc(FINDING_ACTION_TEXT[action] || action || "请人工核对") + "</div>"
      + '<div class="acts"></div>';
    const acts = card.querySelector(".acts");
    if (item.memory_id) {
      const view = document.createElement("button");
      view.type = "button";
      view.textContent = "查看记忆";
      view.addEventListener("click", function () { selectItem(item.memory_id); });
      acts.appendChild(view);
    }
    const resolved = document.createElement("button");
    resolved.type = "button";
    resolved.textContent = "已处理";
    resolved.addEventListener("click", function () { resolveFinding(item.id, "resolved"); });
    acts.appendChild(resolved);
    const ignored = document.createElement("button");
    ignored.type = "button";
    ignored.textContent = "忽略";
    ignored.addEventListener("click", function () { resolveFinding(item.id, "ignored"); });
    acts.appendChild(ignored);
    list.appendChild(card);
  });
}
async function resolveFinding(id, status) {
  const out = await api("/api/memory/supervisor/findings/" + encodeURIComponent(id) + "/resolve?status=" + encodeURIComponent(status), {method: "POST"});
  if (!out.ok) { setToast("建议状态更新失败（" + out.status + "）", true); return; }
  setToast(status === "resolved" ? "已标记为处理" : "已忽略该建议", false);
  await loadSupervisor();
}
async function scanSupervisor() {
  $("supervisorScan").disabled = true;
  try {
    const out = await api("/api/memory/supervisor/scan", {method: "POST"});
    if (!out.ok || !out.data) { setToast("扫描失败（" + out.status + "）", true); return; }
    setToast("扫描完成：检查 " + (out.data.scanned || 0) + " 条，发现 " + (out.data.findings || 0) + " 条建议", false);
    await loadSupervisor();
  } finally {
    $("supervisorScan").disabled = false;
  }
}
$("supervisorRefresh").addEventListener("click", loadSupervisor);
$("supervisorScan").addEventListener("click", scanSupervisor);
$("refresh").addEventListener("click", async function () { await loadStatus(); await loadItems(); await loadSupervisor(); });
$("search").addEventListener("click", loadItems);
$("query").addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); loadItems(); } });
$("statusFilter").addEventListener("change", loadItems);
$("kindFilter").addEventListener("change", loadItems);
async function memoryExport() {
  const out = await api("/api/memory/export");
  if (!out.ok || !out.data) {
    setToast(out.status === 403 ? "导出仅限本机" : "导出失败", true);
    return;
  }
  const blob = new Blob([JSON.stringify(out.data, null, 2)], {type: "application/json"});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "memory-export-" + new Date().toISOString().slice(0, 10) + ".json";
  document.body.appendChild(link); link.click(); link.remove();
  setTimeout(function () { URL.revokeObjectURL(link.href); }, 1000);
  setToast("已导出 " + (out.data.items || []).length + " 条记忆", false);
}
async function memoryImport(file) {
  if (!file) return;
  let payload = null;
  try { payload = JSON.parse(await file.text()); }
  catch (e) { setToast("导入文件不是合法 JSON", true); return; }
  const out = await api("/api/memory/import", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  });
  if (!out.ok || !out.data) { setToast(out.status === 403 ? "导入仅限本机" : "导入失败", true); return; }
  setToast("导入完成：新增 " + (out.data.imported || 0) + "，跳过 " + (out.data.skipped || 0)
    + "，错误 " + (out.data.errors || 0), false);
  await loadStatus(); await loadItems(); await loadSupervisor();
}
async function memoryClear() {
  let entered = null;
  if (window.RAGKB && typeof window.RAGKB.confirmText === "function") {
    entered = await window.RAGKB.confirmText({
      title: "清空全部记忆数据？",
      body: "这会清空全部会话、消息、记忆、来源、事件与任务，不可恢复。",
      detail: "备份不会自动删除。请输入 CLEAR ALL 继续。",
      inputLabel: "输入 CLEAR ALL 确认",
      placeholder: "CLEAR ALL",
      requiredValue: "CLEAR ALL",
      confirmText: "清空全部数据",
      cancelText: "取消",
      danger: true
    });
  } else {
    entered = window.prompt("这会清空全部会话、消息、记忆、来源、事件与任务，不可恢复。\n请输入 CLEAR ALL 确认：", "");
  }
  if (String(entered || "").trim() !== "CLEAR ALL") { setToast("未确认，已取消", false); return; }
  const out = await api("/api/memory/clear", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({confirm: "CLEAR ALL", purge_backups: false}),
  });
  if (!out.ok || !out.data) { setToast(out.status === 403 ? "清空仅限本机" : "清空失败", true); return; }
  state.selected = null; $("detail").innerHTML = '<div class="ui-state ui-empty">已清空。</div>';
  setToast("已清空全部数据（备份未删除）", false);
  await loadStatus(); await loadItems(); await loadSupervisor();
}
$("exportBtn").addEventListener("click", memoryExport);
$("importFile").addEventListener("change", function (event) {
  memoryImport(event.target.files && event.target.files[0]);
  event.target.value = "";
});
$("clearBtn").addEventListener("click", memoryClear);
loadStatus(); loadItems(); loadSupervisor();
