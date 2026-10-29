
const FIELDS = [
    ["llm", "binding"], ["llm", "model"], ["llm", "host"], ["llm", "api_key"],
    ["embedding", "binding"], ["embedding", "model"], ["embedding", "host"], ["embedding", "api_key"], ["embedding", "dim"], ["embedding", "send_dim"],
    ["rerank", "mode"], ["rerank", "provider"], ["rerank", "model"], ["rerank", "host"],
    ["rerank", "api_key"], ["rerank", "timeout"],
    ["vector_store", "type"], ["vector_store", "qdrant_url"],
    ["graph", "working_dir"], ["graph", "input_dir"],
    ["paths", "lightrag_home"],
    ["app", "max_upload_size"], ["app", "allowed_extensions"], ["app", "log_level"],
    ["app", "skill_routing"], ["app", "generation_mode"],
    ["memory", "enabled"], ["memory", "context_enabled"], ["memory", "fast_memory"],
    ["memory", "context_budget"], ["memory", "recent_history_messages"], ["memory", "recent_history_tokens"],
    ["memory", "upstream_history"], ["memory", "upstream_memory"],
    ["memory", "recall_enabled"], ["memory", "recall_budget"], ["memory", "recall_max_items"],
    ["memory", "auto_extract"], ["memory", "worker"], ["memory", "extract_every_turns"],
    ["memory", "extract_max_messages"], ["memory", "extract_max_input_tokens"],
    ["memory", "extract_debounce_seconds"],
    ["runtime", "app_port"], ["runtime", "app_port_scan_limit"],
    ["runtime", "shell_port"], ["runtime", "engine_port"],
    // 入库质量校验（Quality Gate）
    // 不再有「质量校验开关」—— QUALITY_ENABLED 已失效，页面不给入口、也不回写它
    ["quality", "profile"], ["quality", "min_chars"], ["quality", "min_readable_ratio"],
    ["quality", "max_garbled_ratio"], ["quality", "max_repeat_ratio"], ["quality", "min_chars_per_page"],
    ["quality", "pass_score"], ["quality", "max_unocr_images"],
    // 入库确认门：通过质检后是否停下来等确认
    ["ingest", "confirm"], ["ingest", "ttl_hours"],
    // 文件夹扫描：只生成待复核作业，不自动入库
    ["inbox", "enabled"], ["inbox", "poll_seconds"], ["inbox", "settle_seconds"],
    ["inbox", "dir"], ["inbox", "confirm"],
    // OCR 文字识别
    ["ocr", "mode"], ["ocr", "provider"], ["ocr", "image_ocr"], ["ocr", "lang"], ["ocr", "device"],
    ["ocr", "base_url"], ["ocr", "model"], ["ocr", "token"], ["ocr", "max_file_mb"],
    ["ocr", "timeout_seconds"], ["ocr", "local_model_dir"],
    // 朗读 / 语音（TTS）： 的云端 TTS + 链路顺序（保存后立即生效，优先于 .env）
    ["tts", "tts_cloud_host"], ["tts", "tts_cloud_api_key"], ["tts", "tts_cloud_model"],
    ["tts", "tts_cloud_voice"], ["tts", "tts_cloud_timeout"], ["tts", "tts_provider_chain"]
];

// 密钥字段：留空 = 保持原值（只显示掩码提示）
const SECRET_FIELDS = ["api_key", "token", "tts_cloud_api_key"];
// 三态字段："" / "true" / "false"
const TRI_STATE_FIELDS = [
    "send_dim", "enabled", "image_ocr", "context_enabled", "fast_memory",
    "recall_enabled", "auto_extract", "worker"
];

function el(group, key) { return document.getElementById(group + "_" + key); }

// 朗读方式预设：自动 = 云端优先；设备 = 仅浏览器语音；自定义保留高级链路。
const TTS_VOICE_PRESETS = {
    auto: { chain: "cloud" },
    device: { chain: "browser" }
};

function isLocalTtsHost(value) {
    try {
        const host = String(new URL(String(value || "")).hostname || "").toLowerCase();
        return host === "localhost" || host === "::1" || host === "127.0.0.1"
            || host.endsWith(".localhost") || host.startsWith("127.");
    } catch (err) {
        return false;
    }
}

function applyTtsVoicePreset(name) {
    const preset = TTS_VOICE_PRESETS[name];
    if (!preset) { return; }
    const chain = el("tts", "tts_provider_chain");
    const model = el("tts", "tts_cloud_model");
    const voice = el("tts", "tts_cloud_voice");
    if (chain) { chain.value = preset.chain; refreshDirty(chain); }
    if (model && preset.model !== undefined) {
        model.value = preset.model;
        refreshDirty(model);
    }
    if (voice && preset.voice !== undefined) {
        voice.value = preset.voice;
        refreshDirty(voice);
    }
    renderTtsSummaryFromFields();
}

function renderTtsSummaryFromFields() {
    const preset = document.getElementById("tts_voice_preset");
    const labels = {
        auto: "自动（云端优先）",
        device: "设备浏览器语音",
        custom: "自定义"
    };
    const key = preset ? preset.value : "custom";
    const label = labels[key] || "自定义";
    const host = (el("tts", "tts_cloud_host") || {}).value || "";
    const summary = document.getElementById("sum-tts");
    if (summary) {
        summary.textContent = label + (key !== "device" && host ? (" · " + host) : "");
    }
    const nav = document.getElementById("nav-tts");
    if (nav) {
        nav.textContent = key === "device" ? "设备"
            : (key === "auto" ? (host ? "云端→设备" : "设备") : "自定义");
        nav.className = "state" + ((key === "device" || !host) ? " on" : " warn");
    }
}

function currentTtsVoicePreset() {
    const chain = (el("tts", "tts_provider_chain") || {}).value || "";
    if (!chain || chain === "cloud" || chain === "cloud,browser") { return "auto"; }
    if (chain === "browser") { return "device"; }
    return "custom";
}

function syncTtsVoicePreset() {
    const node = document.getElementById("tts_voice_preset");
    if (node) { node.value = currentTtsVoicePreset(); }
}

function syncRerankMode() {
    const mode = (el("rerank", "mode") || {}).value || "off";
    const off = mode === "off";
    const cloud = mode === "cloud";
    const provider = el("rerank", "provider");
    if (provider) { provider.disabled = !cloud; }
    ["model", "host", "api_key", "timeout"].forEach(function (key) {
        const node = el("rerank", key);
        if (node) { node.disabled = off; }
    });
    const testButton = document.getElementById("testRerankBtn");
    if (testButton) { testButton.disabled = off; }
}

function syncOcrProvider() {
    const provider = (el("ocr", "provider") || {}).value || "auto";
    const remote = ["auto", "local_model", "cloud"].indexOf(provider) >= 0;
    ["base_url", "model"].forEach(function (key) {
        const node = el("ocr", key);
        if (node) { node.disabled = !remote; }
    });
    const token = el("ocr", "token");
    if (token) { token.disabled = !remote; }
    const clear = document.querySelector('[data-clear-secret="ocr_token"]');
    if (clear) { clear.disabled = !remote; }
    const test = document.getElementById("testOcrBtn");
    if (test) {
        test.disabled = (el("ocr", "mode") || {}).value === "off" || provider === "none";
    }
}

// 密钥字段的 id：留空 = 保持原值，所以"空"不算改动，
// 否则一打开页面什么都没做就会显示"有 N 项未保存"。
const SECRET_IDS = {};
const SECRET_PAIRS = {};     // id -> [group, key]，保存时拼 clear_secrets 用
// 已确认「清除」、但还没保存的密钥 id：空值在密钥字段上表示保持原值，
// 所以「清除」只能单独记一笔，不能靠把输入框清空来表达。
var CLEAR_PENDING = {};
// 磁盘上密钥的掩码文案（空 = 未配置），撤销清除 / 重新加载时还原占位符
var SECRET_DISPLAY = {};
FIELDS.forEach(function (pair) {
    if (SECRET_FIELDS.indexOf(pair[1]) >= 0) {
        const id = pair[0] + "_" + pair[1];
        SECRET_IDS[id] = true;
        SECRET_PAIRS[id] = [pair[0], pair[1]];
    }
});

// ---------- 未保存改动（脏状态）----------
// BASELINE = load() 完成时每个字段的快照，DIRTY = 与快照不同的字段 id。
// 为什么用 input 而不是 change：文本框只在失焦时才触发 change，
// 用户改完最后一个字段直接点「重新加载」就漏掉了。
var BASELINE = {};
var DIRTY = {};
var ACTION_BAR_FORCED = false;
// 字段的 <label> 没有 for，顺手把标签文本收起来，供"会丢哪几项"的说明用
var FIELD_LABELS = {};

function fieldLabel(id) { return FIELD_LABELS[id] || id; }
function dirtyIds() { return Object.keys(DIRTY); }

function isDirty(node) {
    if (BASELINE[node.id] === undefined) { return false; }   // 不在 FIELDS 里的控件不参与
    // 已标记清除的密钥：输入框虽然是空的（与快照相同），但确实是一项未保存的改动。
    // 必须放在"值没变"判断之前，否则空值会先被当成未改动。
    if (SECRET_IDS[node.id] && CLEAR_PENDING[node.id]) { return true; }
    if (node.value === BASELINE[node.id]) { return false; }
    // 密钥留空 = 保持原值，空值一律不算改动（快照本身也是空的，清空后同理）
    if (SECRET_IDS[node.id] && node.value.trim() === "") { return false; }
    return true;
}

function refreshDirty(node) {
    if (!node || BASELINE[node.id] === undefined) { return; }
    if (isDirty(node)) { DIRTY[node.id] = true; } else { delete DIRTY[node.id]; }
    renderDirty();
}

function renderDirty() {
    const node = document.getElementById("dirty");
    const count = dirtyIds().length;
    if (node) {
        node.textContent = count ? ("有 " + count + " 项未保存的改动") : "";
        node.hidden = count === 0;
    }
    renderActionBar();
}

function renderActionBar() {
    const bar = document.getElementById("actionBar");
    const button = document.getElementById("settingsActionsBtn");
    if (!bar) { return; }
    const visible = dirtyIds().length > 0 || ACTION_BAR_FORCED;
    bar.hidden = !visible;
    document.body.classList.toggle("has-action-bar", visible);
    if (button) { button.setAttribute("aria-expanded", visible ? "true" : "false"); }
    if (!visible) { closeBarMore(); }
}

function snapshotFields() {
    BASELINE = {};
    FIELDS.forEach(function (pair) {
        const node = el(pair[0], pair[1]);
        if (node) { BASELINE[node.id] = node.value; }
    });
    DIRTY = {};
    renderDirty();
}

// ---------- 密钥清除（C2）----------
// 密钥留空保存 = 保持原值，因此「清除」不能靠提交空值表达：
// 这里只做页面上的标记，保存时另行提交 clear_secrets: [[group, key], …]。
function secretButton(id) {
    return document.querySelector('[data-clear-secret="' + id + '"]');
}

function secretPlaceholder(display) {
    return display ? ("已配置 " + display + "，留空保持不变") : "留空表示不设置";
}

function renderSecretButton(id) {
    const btn = secretButton(id);
    if (!btn) { return; }
    const pending = !!CLEAR_PENDING[id];
    const configured = !!SECRET_DISPLAY[id];
    // 没配过、也没标记清除时不给入口，免得点一个没有意义的按钮
    btn.hidden = !(configured || pending);
    btn.classList.toggle("pending", pending);
    btn.textContent = pending ? "撤销清除" : "清除";
    btn.setAttribute("aria-pressed", pending ? "true" : "false");
    btn.title = pending ? "撤销清除，继续使用已保存的密钥" : "清除已保存的密钥（保存后生效）";
}

function setSecretPending(id, pending, keepValue) {
    const node = document.getElementById(id);
    if (pending) { CLEAR_PENDING[id] = true; } else { delete CLEAR_PENDING[id]; }
    if (node) {
        // 只有显式标记 / 撤销清除才清空输入框；
        // 「标记后又输入了新密钥」的自动撤销必须保留用户刚敲进去的值。
        if (!keepValue) { node.value = ""; }
        node.placeholder = pending
            ? "保存后将清除（保存前仍保留原值）"
            : secretPlaceholder(SECRET_DISPLAY[id] || "");
    }
    renderSecretButton(id);
    if (node) { refreshDirty(node); }
}

function initSecretClear() {
    document.querySelectorAll("[data-clear-secret]").forEach(function (btn) {
        btn.addEventListener("click", function () {
            const id = btn.getAttribute("data-clear-secret");
            if (CLEAR_PENDING[id]) { setSecretPending(id, false); return; }
            const node = document.getElementById(id);
            const typed = node ? node.value.trim() : "";
            askConfirm({
                title: "清除已保存的密钥？",
                body: "保存后这条密钥会从配置文件里移除，依赖它的模型 / 服务将不可用"
                    + "（重启服务后完全生效）。",
                detail: typed ? "输入框里还有未保存的新密钥；确认清除会以「清除」为准。" : "",
                confirmText: "标记为清除",
                cancelText: "保留",
                danger: true
            }).then(function (ok) { if (ok) { setSecretPending(id, true); } });
        });
    });
}

// 一个监听器管 50 个字段：字段本身是静态的，但 load() 会反复重写 value，
// 逐字段挂监听就得在每次重绘后重挂，漏一个就少一个脏字段；冒泡没有这个问题。
function initDirtyTracking() {
    const box = document.querySelector(".main");
    if (!box) { return; }
    const handler = function (event) {
        const node = event.target;
        if (!node || !node.id || (node.tagName !== "INPUT" && node.tagName !== "SELECT")) { return; }
        // 标记清除后又输入了新密钥：以新输入的值为准，自动撤销清除标记
        if (SECRET_IDS[node.id] && CLEAR_PENDING[node.id] && node.value.trim() !== "") {
            setSecretPending(node.id, false, true);   // true = 别把刚输入的值清掉
        }
        if (["llm_host", "llm_model", "embedding_host", "embedding_model"].indexOf(node.id) >= 0) {
            LAST_TEST_CHECKS = [];
        }
        refreshDirty(node);
        renderRouteOverview();
        renderDeploymentReadiness();
    };
    box.addEventListener("input", handler);
    box.addEventListener("change", handler);
}

window.addEventListener("beforeunload", function (event) {
    if (!dirtyIds().length) { return; }
    event.preventDefault();
    event.returnValue = "";   // 现代浏览器只认"设置过 returnValue"，提示文案由浏览器给
});

// ui.js 没加载出来时（缓存 / 404）退化为原生 confirm：宁可难看，
// 也不能把"会丢弃改动"这件事悄悄吞掉。
function askConfirm(opts) {
    if (window.RAGKB && typeof window.RAGKB.confirm === "function") { return window.RAGKB.confirm(opts); }
    return Promise.resolve(window.confirm(opts.title + "\n\n" + opts.body + (opts.detail ? "\n" + opts.detail : "")));
}

function notify(text, kind) {
    if (window.RAGKB && typeof window.RAGKB.toast === "function") { window.RAGKB.toast(text, kind); }
}

function setStatus(text, ok) {
    const node = document.getElementById("status");
    node.textContent = text;
    // 三态：undefined = 中性、true = 成功、false = 失败（与保存/测试的调用点一致）
    node.className = "status" + (ok === true ? " ok" : (ok === false ? " bad" : ""));
}

async function load(keepStatus) {
    if (!keepStatus) { setStatus("读取配置中..."); }
    try {
        const response = await fetch("/api/settings");
        // 先留一份可解析的响应体：500 时后端通常仍带 {detail/error}，
        // 直接 .json() 后取 data.settings 会抛 TypeError，用户只看到 JS 报错。
        const data = await response.json().catch(function () { return null; });
        if (!response.ok) {
            setStatus("读取失败：" + describeError(data), false);
            return;
        }
        if (!data || !data.settings) {
            setStatus("读取失败：服务端返回的数据不完整", false);
            return;
        }
        FIELDS.forEach(function (pair) {
            const node = el(pair[0], pair[1]);
            if (!node) { return; }
            const value = (data.settings[pair[0]] || {})[pair[1]];
            if (SECRET_FIELDS.indexOf(pair[1]) >= 0) {
                SECRET_DISPLAY[node.id] = value ? String(value) : "";
                delete CLEAR_PENDING[node.id];   // 重新加载 = 回到磁盘状态，撤销未保存的清除标记
                node.value = "";
                node.placeholder = secretPlaceholder(SECRET_DISPLAY[node.id]);
                renderSecretButton(node.id);
            } else if (TRI_STATE_FIELDS.indexOf(pair[1]) >= 0) {
                const text = String(value === null || value === undefined ? "" : value).toLowerCase();
                node.value = (text === "true") ? "true" : ((text === "false") ? "false" : "");
            } else {
                node.value = (value === null || value === undefined) ? "" : value;
            }
        });
        // 值都写完后，根据链路/模型反推出当前音色预设
        syncTtsVoicePreset();
        syncRerankMode();
        syncOcrProvider();
        // 值都写完了才拍快照：这是"磁盘上的配置"，之后与它不一致的就是未保存改动
        snapshotFields();
        renderRouteOverview();
        document.getElementById("fileinfo").textContent = data.settings_file + (data.settings_exists ? "（已存在）" : "（尚未创建，保存后生成）");
        // 存**完整响应**，不是 data.settings：折叠摘要要读 lightrag_effective /
        // app_effective / quality_effective / ocr_effective / inbox_effective 这几段，
        // 只留 settings 的话，refreshTtsOptions() 的回调会把摘要改回占位符（详见该函数）。
        LAST_SETTINGS = data;
        LAST_TEST_CHECKS = [];
        renderEffective(data);
        renderRuntime(data);
        renderDeploymentReadiness();
        refreshIndexCompatibility();
        renderSummaries(data);
        renderIngestHint(data);
        renderInboxHint(data);
        renderOcrHint(data);
        renderTtsSummary(data);
        refreshTtsOptions();
        refreshMemoryStatus();
        if (!keepStatus) {
            setStatus(data.restart_needed
                ? "已加载：有改动等待重启生效（重启服务后）"
                : "已加载");
        }
    } catch (err) {
        setStatus("读取失败：" + err, false);
    }
}

// 「重新加载」= 丢弃页面上的改动、回到磁盘上的配置。
// 原来直接 load()：改了几十个字段后点一下全没了，连问都不问一句。
function reload() {
    const ids = dirtyIds();
    if (!ids.length) { load(); return; }
    const names = ids.map(fieldLabel);
    const shown = names.slice(0, 8).join("、") + (names.length > 8 ? "…" : "");
    askConfirm({
        title: "放弃未保存的改动？",
        body: "有 " + ids.length + " 项改动还没保存。重新加载会用磁盘上的配置覆盖页面，这些改动会丢失"
            + "（「保存配置」不会替你保留它们）。",
        detail: "会丢失：" + shown + (names.length > 8 ? "（共 " + names.length + " 项）" : ""),
        confirmText: "放弃并重新加载",
        cancelText: "继续编辑",
        danger: true
    }).then(function (ok) { if (ok) { load(); } });
}

// 保存失败的原因后端给的是 {error, messages: [...]}：messages 是逐条中文说明，
// 原样 JSON.stringify(data.detail) 等于把裸 JSON 甩给用户。
function describeError(data) {
    if (!data) { return "服务端没有返回内容"; }
    if (Array.isArray(data.messages) && data.messages.length) { return data.messages.join("；"); }
    const detail = data.detail;
    if (Array.isArray(detail)) {
        // FastAPI 的校验错误就是这种形状（detail 是 [{loc,msg,type}, …]）
        const items = detail.map(function (item) {
            if (item && typeof item === "object") { return item.msg || item.message || item.error || JSON.stringify(item); }
            return String(item);
        });
        if (items.length) { return items.join("；"); }
    } else if (detail && typeof detail === "object") {
        if (Array.isArray(detail.messages) && detail.messages.length) { return detail.messages.join("；"); }
        if (detail.message) { return String(detail.message); }
        if (detail.error) { return String(detail.error); }
    } else if (detail) {
        return String(detail);
    }
    if (data.error) { return String(data.error); }
    return JSON.stringify(data);
}

async function save() {
    // 防重入，与 test() 同一套写法：保存要发 POST、回来还要 load() 重绘整个表单，
    // 全程几百毫秒到数秒。连点两下就是两次写入 + 两次重绘，状态栏还会互相覆盖
    // （先返回的那次说"已保存"，后返回的可能说"保存失败"）。
    const btn = document.getElementById("saveBtn");
    if (btn && btn.disabled) { return; }
    const body = {};
    FIELDS.forEach(function (pair) {
        const node = el(pair[0], pair[1]);
        if (!node) { return; }
        if (!body[pair[0]]) { body[pair[0]] = {}; }
        body[pair[0]][pair[1]] = node.value.trim();
    });
    // 密钥清除单独提交：空字符串在后端表示「保持原值」，表达不了清除。
    const clears = Object.keys(CLEAR_PENDING).filter(function (id) { return !!SECRET_PAIRS[id]; });
    if (clears.length) {
        body.clear_secrets = clears.map(function (id) { return SECRET_PAIRS[id]; });
    }
    if (btn) {
        btn.disabled = true;
        btn.setAttribute("aria-busy", "true");
        // 文案保持「保存配置」不动：底栏已经在说"保存中..."，按钮再变一次是噪声；
        // 这里只需要让它看起来按不动（.bar button[disabled] 已有样式）。
    }
    setStatus("保存中...");
    try {
        const response = await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
        const data = await response.json();
        if (!response.ok) { setStatus("保存失败：" + describeError(data), false); return; }
        await load(true);
        // 保存成功后再写状态栏（load 会重绘页面，顺序反了提示就看不见了）
        const preview = (data && data.ocr_preview) || {};
        let extra = "";
        if (preview.reason) {
            extra = " 注意：OCR 重启后仍不会启用——" + preview.reason;
        } else if (preview.enabled_after_restart) {
            extra = " OCR 已设为「" + (preview.configured_mode || "") + "」，重启后启用。";
        }
        // 入库确认：保存值与进程值不同就明说，别让用户以为「保存成功 = 已生效」
        const gateNow = (data && data.ingest_confirm_effective) || {};
        const gateSaved = String((((data || {}).settings || {}).ingest || {}).confirm || "");
        if (gateSaved && gateSaved !== gateNow.mode) {
            extra += " 入库确认已设为「" + gateSaved + "」，重启后生效（当前仍在用 " + gateNow.mode + "）。";
        }
        setStatus(data.note + extra + "（保存时间 " + (data.updated_at || "") + "）", true);
        // 底栏在手机上会折行、文案又长，再给一条即时反馈
        notify("配置已保存", "ok");
    } catch (err) {
        setStatus("保存失败：" + err, false);
    } finally {
        // 必须在 finally 里解锁：上面 `return` 的失败分支和 catch 都要能再点。
        if (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); }
    }
}

async function test() {
    const btn = document.getElementById("testBtn");
    if (btn && btn.disabled) { return; }        // 测试要跑好几秒，连点会叠着发请求
    if (btn) { btn.disabled = true; btn.textContent = "测试中..."; btn.setAttribute("aria-busy", "true"); }
    setStatus("正在测试连接...");
    renderTest([], true);
    try {
        const data = await (await fetch("/api/settings/test", { method: "POST" })).json();
        const checks = data.checks || [];
        LAST_TEST_CHECKS = checks;
        renderTest(checks, false);
        renderDeploymentReadiness();
        const failed = checks.filter(function (item) { return !item.ok; }).length;
        // 明细渲染在 ⑫ 卡片里，而它默认是收起的 —— 不主动展开，底栏只说"存在异常项"，
        // 用户根本看不到哪一项没过。
        revealTestResult();
        const allOk = (data.all_ok === undefined) ? (failed === 0) : data.all_ok;
        setStatus(failed
            ? ("测试未通过：" + failed + " / " + checks.length + " 项异常，明细在「生效配置 → 连接测试」")
            : ("测试全部通过（共 " + checks.length + " 项）"), allOk);
    } catch (err) {
        setStatus("测试失败：" + err, false);
    } finally {
        if (btn) { btn.disabled = false; btn.removeAttribute("aria-busy"); btn.textContent = "测试全部服务"; }
    }
}

async function testService(key, btn) {
    const labels = { llm: "生成模型", embedding: "嵌入模型", rerank: "重排模型", knowledge: "知识服务" };
    const statusNode = document.getElementById("serviceTest-" + key);
    const originalText = btn ? btn.textContent : "";
    if (btn && btn.disabled) { return; }
    if (btn) {
        btn.disabled = true;
        btn.setAttribute("aria-busy", "true");
        btn.textContent = "测试中...";
    }
    if (statusNode) {
        statusNode.textContent = "测试中...";
        statusNode.className = "service-test-status is-pending";
    }
    setStatus("正在测试" + (labels[key] || "服务") + "连接...");
    try {
        const response = await fetch("/api/settings/test", { method: "POST" });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(describeError(data));
        }
        const checks = data.checks || [];
        LAST_TEST_CHECKS = checks;
        renderTest(checks, false);
        renderDeploymentReadiness();
        const check = serviceCheckResult(key);
        if (!check) {
            if (statusNode) {
                statusNode.textContent = "服务端未返回该服务的独立检查项";
                statusNode.className = "service-test-status is-pending";
            }
            setStatus("测试完成，但服务端未返回" + (labels[key] || "该服务") + "的独立结果；请查看「诊断 → 连接测试」。");
            return;
        }
        if (statusNode) {
            statusNode.textContent = (check.ok ? "已通过" : "未通过") + "：" + (check.detail || "无详情");
            statusNode.className = "service-test-status " + (check.ok ? "is-ok" : "is-bad");
        }
        setStatus((labels[key] || "服务") + (check.ok ? "连接测试通过" : "连接测试失败") + "：" + (check.detail || ""), check.ok);
    } catch (err) {
        if (statusNode) {
            statusNode.textContent = "测试失败：" + ((err && err.message) || "未知错误");
            statusNode.className = "service-test-status is-bad";
        }
        setStatus("测试失败：" + ((err && err.message) || "未知错误"), false);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.removeAttribute("aria-busy");
            btn.textContent = originalText;
        }
    }
}

async function testOcrLocal() {
    const button = document.getElementById("testOcrBtn");
    const status = document.getElementById("ocrTestStatus");
    if (button && button.disabled) { return; }
    if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
    }
    if (status) {
        status.textContent = "正在检查 OCR 提供方...";
        status.className = "service-test-status is-pending";
    }
    try {
        const response = await fetch("/api/ocr/test", { method: "POST" });
        const data = await response.json().catch(function () { return null; });
        const result = (data && data.status) || {};
        const ok = !!(response.ok && data && data.ok);
        const language = result.language_tag || result.lang || "";
        const detail = ok
            ? ("可用 · " + (result.provider || "OCR") + (language ? " · 语言 " + language : ""))
            : ((data && data.message) || result.reason || "不可用");
        if (status) {
            status.textContent = detail;
            status.className = "service-test-status " + (ok ? "is-ok" : "is-bad");
        }
        notify("OCR：" + detail, ok ? "ok" : "bad");
    } catch (err) {
        if (status) {
            status.textContent = "检查失败：" + err;
            status.className = "service-test-status is-bad";
        }
        notify("OCR 检查失败：" + err, "bad");
    } finally {
        if (button) {
            button.disabled = false;
            button.removeAttribute("aria-busy");
        }
    }
}

async function testTtsCloud() {
    const button = document.getElementById("testTtsBtn");
    const status = document.getElementById("ttsTestStatus");
    if (button && button.disabled) { return; }
    if (button) {
        button.disabled = true;
        button.setAttribute("aria-busy", "true");
    }
    if (status) {
        status.textContent = "正在测试云端 TTS...";
        status.className = "service-test-status is-pending";
    }
    try {
        const response = await fetch("/api/tts/test", { method: "POST" });
        const data = await response.json().catch(function () { return null; });
        const ok = !!(response.ok && data && data.ok);
        const detail = ok
            ? ((data && data.message) || "云端 TTS 可用")
            : ((data && data.message) || "云端 TTS 不可用");
        if (status) {
            status.textContent = detail;
            status.className = "service-test-status " + (ok ? "is-ok" : "is-bad");
        }
        notify(detail, ok ? "ok" : "bad");
    } catch (err) {
        if (status) {
            status.textContent = "测试失败：" + err;
            status.className = "service-test-status is-bad";
        }
        notify("云端 TTS 测试失败：" + err, "bad");
    } finally {
        if (button) {
            button.disabled = false;
            button.removeAttribute("aria-busy");
        }
    }
}

// 刻意走 saveCollapsed()：折叠状态存在 localStorage 里，
// 只摘 class 的话下次刷新会被存下来的"收起"覆盖回去，用户以为没生效。
function revealTestResult() {
    const card = document.getElementById("sec-status");
    if (!card) { return; }
    card.classList.remove("collapsed");
    saveCollapsed();
    syncExpanded();
    card.scrollIntoView({ block: "nearest" });
}

load();
function renderTest(checks, pending) {
    const box = document.getElementById("testresult");
    box.innerHTML = "";
    if (pending) {
        const waiting = document.createElement("li");
        waiting.className = "muted";
        waiting.textContent = "测试中...";
        box.appendChild(waiting);
        return;
    }
    (checks || []).forEach(function (item) {
        const line = document.createElement("li");
        line.className = item.ok ? "ok" : "bad";
        line.textContent = (item.ok ? "✓ " : "✗ ") + item.name + "：" + item.detail;
        box.appendChild(line);
    });
}

function renderRuntime(data) {
    const services = (data && data.runtime_effective && data.runtime_effective.services) || [];
    const body = document.querySelector("#runtimeTable tbody");
    const state = document.getElementById("runtimeState");
    if (!body) { return; }
    body.innerHTML = "";
    let healthy = 0;
    services.forEach(function (item) {
        const tr = document.createElement("tr");
        const name = document.createElement("td");
        name.textContent = ({ engine: "LightRAG 引擎", shell: "HTTP 外壳", app: "主应用" })[item.service] || item.service;
        const url = document.createElement("td");
        url.textContent = item.url || "—";
        const health = document.createElement("td");
        if (item.running) { healthy += 1; }
        health.textContent = item.running ? "可用" : "未运行";
        health.className = item.running ? "ok" : "bad";
        tr.appendChild(name);
        tr.appendChild(url);
        tr.appendChild(health);
        body.appendChild(tr);
    });
    if (state) {
        state.textContent = healthy === 3
            ? "三个服务均可用。配置变更仍需重启后生效。"
            : ("当前可用服务 " + healthy + " / 3。请用项目根目录的启动脚本启动完整服务组。");
        state.className = "note-line" + (healthy === 3 ? " ok" : " warn");
    }
}

function renderEffective(data) {
    const effective = (data && data.lightrag_effective) || {};
    const rows = [
        ["生成模型", ["LLM_BINDING", "LLM_MODEL"]],
        ["生成模型接口地址", ["LLM_BINDING_HOST"]],
        ["嵌入模型", ["EMBEDDING_BINDING", "EMBEDDING_MODEL"]],
        ["嵌入模型接口地址", ["EMBEDDING_BINDING_HOST"]],
        ["向量维度", ["EMBEDDING_DIM"]],
        ["重排序（Rerank）", ["RERANK_BINDING", "RERANK_MODEL"]],
        ["向量库类型", ["LIGHTRAG_VECTOR_STORAGE"]],
        ["知识库数据目录", ["WORKING_DIR"]],
        ["文档输入目录", ["INPUT_DIR"]]
    ];
    const body = document.querySelector("#effectiveTable tbody");
    body.innerHTML = "";
    rows.forEach(function (row) {
        const values = row[1].map(function (key) { return effective[key] ? String(effective[key]) : ""; }).filter(function (item) { return item; });
        const tr = document.createElement("tr");
        const th = document.createElement("td");
        th.textContent = row[0];
        const td = document.createElement("td");
        td.textContent = values.length ? values.join(" / ") : "—";
        tr.appendChild(th);
        tr.appendChild(td);
        body.appendChild(tr);
    });
    const app = (data && data.app_effective) || {};
    const sizeBytes = Number(app.max_upload_size || 0);
    const sizeText = sizeBytes ? (sizeBytes + " 字节（约 " + (sizeBytes / 1048576).toFixed(1) + " MB）") : "—";
    const routingOn = String(app.skill_routing || "").toLowerCase() === "on";
    const routingSaved = String(app.skill_routing_saved || "").toLowerCase();
    const routingText = (routingOn ? "开启（技能优先）" : "关闭（直通 LightRAG）")
        + (app.skill_routing_restart_needed
            ? "；已保存为 " + (routingSaved === "on" ? "开启" : "关闭") + "，重启后生效"
            : "");
    const genText = "知识服务 + OpenAI 兼容生成模型";
    const appRows = [
        ["LightRAG 地址", app.lightrag_url || "—"],
        ["上传大小上限", sizeText],
        ["允许的扩展名", (app.allowed_extensions && app.allowed_extensions.length) ? app.allowed_extensions.join(" ") : "（全部允许）"],
        ["日志级别", app.log_level || "—"],
        ["问答技能路由", "当前版本未启用"],
        ["回答生成方式", genText],
        ["生成模型", effective.LLM_MODEL || "—"],
        ["RAG 嵌入模型", [effective.EMBEDDING_MODEL, effective.EMBEDDING_BINDING].filter(function (item) { return item; }).join(" / ") || "—"]
    ];
    const appBody = document.querySelector("#appTable tbody");
    appBody.innerHTML = "";
    appRows.forEach(function (row) {
        const tr = document.createElement("tr");
        const th = document.createElement("td");
        th.textContent = row[0];
        const td = document.createElement("td");
        td.textContent = row[1];
        tr.appendChild(th);
        tr.appendChild(td);
        appBody.appendChild(tr);
    });
    const quality = (data && data.quality_effective) || {};
    const ocr = (data && data.ocr_effective) || {};
    const preview = (data && data.ocr_preview) || {};
    const gate = (data && data.ingest_confirm_effective) || {};
    const gateTtl = (gate.pending_review_ttl_hours === undefined || gate.pending_review_ttl_hours === null)
        ? 24 : gate.pending_review_ttl_hours;
    const qualityRows = [
        ["配置改动是否已生效", (data && data.restart_needed) ? "有改动等待重启（重启服务后）" : "与当前进程一致"],
        // 确认门的一行结论 + 保留时长（超时按「跳过」处理，不删资料）
        ["入库确认", gate.mode === "always"
            ? "always（每个文件都停下来等确认）"
            : (gate.mode === "auto" ? "auto（建议入库的自动放行，其余停下问）" : "off（通过校验就直接入库）")],
        ["待确认保留时长", (gateTtl ? (gateTtl + " 小时") : "不自动归档") + "；超时按「跳过」处理（原件与解析结果保留）"],
        ["质量校验", "恒定开启（没有总开关）"
            + (quality.switch_ignored ? "；.env 里的 QUALITY_ENABLED=false 已被忽略" : "")],
        ["严格程度预设", quality.profile || "—"],
        ["最少有效字符数", quality.min_chars],
        ["可读字符占比下限", quality.min_readable_ratio],
        ["乱码比例上限", quality.max_garbled_ratio],
        ["重复行比例上限", quality.max_repeat_ratio],
        ["每页字符数下限", quality.min_chars_per_page],
        ["综合质量分及格线", quality.pass_score],
        ["未识别图片容忍数", quality.max_unocr_images],
        ["允许整篇 OCR 的格式", (quality.ocr_routes && quality.ocr_routes.length) ? quality.ocr_routes.join(" / ") : "—"],
        ["OCR 状态", ocr.enabled
            ? ("已启用（" + (ocr.provider || "") + "）")
            : (preview.enabled_after_restart
                ? "已保存但尚未生效（重启服务后启用）"
                : (preview.reason ? ("未启用：" + preview.reason) : "未启用（不会外发任何文件）"))],
        ["OCR 是否外发", ocr.external ? "会把文件发送到外部服务" : "不外发（本机或未启用）"],
        ["内嵌图片纳入 OCR", ocr.image_ocr ? "是（未识别图片会停下等确认）" : "否（忽略图片，只入库正文）"],
        ["OCR 引擎", ocr.engine || "—"],
        ["OCR 接口", ocr.base_url || "Windows 内置"],
        ["OCR 模型", ocr.model || "—"],
        ["OCR Token", ocr.token_set ? "已配置" : "未配置"],
        ["OCR 语言", ocr.language_tag || ocr.lang || "—"],
        ["可用 OCR 语言包", (ocr.available_languages && ocr.available_languages.length)
            ? ocr.available_languages.join(" / ") : "—"],
        ["OCR 单文件上限", ocr.max_file_mb ? (ocr.max_file_mb + " MB") : "—"],
        ["单张识别超时", ocr.timeout_seconds ? (ocr.timeout_seconds + " 秒") : "—"]
    ];
    const qualityBody = document.querySelector("#qualityTable tbody");
    qualityBody.innerHTML = "";
    qualityRows.forEach(function (row) {
        const tr = document.createElement("tr");
        const th = document.createElement("td");
        th.textContent = row[0];
        const td = document.createElement("td");
        const value = (row[1] === null || row[1] === undefined || row[1] === "") ? "—" : row[1];
        td.textContent = String(value);
        tr.appendChild(th);
        tr.appendChild(td);
        qualityBody.appendChild(tr);
    });

    const injectedBox = document.getElementById("injected");

    injectedBox.innerHTML = "";
    const injected = (data && data.injected_env) || {};
    const keys = Object.keys(injected);
    if (keys.length) {
        const label = document.createElement("span");
        label.className = "muted injected-label";
        label.textContent = "将由启动器注入：";
        injectedBox.appendChild(label);
        keys.forEach(function (key) {
            const chip = document.createElement("span");
            chip.className = "chip";
            chip.textContent = key + "=" + injected[key];
            injectedBox.appendChild(chip);
        });
    }
}

// ---------- 分组导航 / 折叠 / 状态摘要（避免设置页一长到底） ----------
const SECTIONS = [
    { id: "sec-llm", num: "①", name: "生成模型", group: "检索与生成" },
    { id: "sec-embedding", num: "②", name: "嵌入模型", group: "检索与生成" },
    { id: "sec-rerank", num: "③", name: "重排序模型", group: "检索与生成" },
    { id: "sec-vector", num: "④", name: "向量存储", group: "检索与生成" },
    { id: "sec-paths", num: "⑤", name: "存储路径", group: "数据与入库" },
    { id: "sec-quality", num: "⑥", name: "质量校验", badge: "quality", group: "数据与入库" },
    { id: "sec-ingest", num: "⑦", name: "入库确认", badge: "ingest", group: "数据与入库" },
    { id: "sec-inbox", num: "⑧", name: "文件夹轮询", badge: "inbox", group: "数据与入库" },
    { id: "sec-ocr", num: "⑨", name: "OCR 识别", badge: "ocr", group: "数据与入库" },
    { id: "sec-app", num: "⑩", name: "上传与运行", group: "应用与安全" },
    { id: "sec-access", num: "A", name: "局域网授权", group: "应用与安全" },
    { id: "sec-memory", num: "M", name: "记忆系统", group: "应用与安全" },
    { id: "sec-runtime", num: "R", name: "运行与端口", group: "应用与安全" },
    { id: "sec-tts", num: "⑪", name: "朗读（TTS）", badge: "tts", group: "语音" },
    { id: "sec-status", num: "⑫", name: "生效配置", group: "诊断" }
];
const COLLAPSE_KEY = "local-rag-kb.settings.collapsed";
const SETTINGS_PROFILE_KEY = "local-rag-kb.settings.profile-view";
const LAST_SECTION_KEY = "local-rag-kb.settings.last-section";
let ACTIVE_NAV_ID = "";
const SETTINGS_PROFILES = {
    local: { label: "全本地" },
    cloud: { label: "云端服务" },
    hybrid: { label: "混合" }
};

function controlValue(id) {
    const node = document.getElementById(id);
    return node ? String(node.value || "").trim() : "";
}

function isPrivateEndpointHost(host) {
    if (!host) { return false; }
    if (host === "localhost" || host === "::1" || host === "0.0.0.0") { return true; }
    if (host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".lan")
        || host.endsWith(".internal") || host.endsWith(".home.arpa")) { return true; }
    if (/^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host)
        || /^169\.254\./.test(host) || /^172\.(1[6-9]|2\d|3[01])\./.test(host)) { return true; }
    return /^(fc|fd)[0-9a-f]{2}:/i.test(host) || /^fe80:/i.test(host);
}

function endpointScope(value) {
    const raw = String(value || "").trim();
    if (!raw) {
        return { kind: "unknown", label: "待确认", detail: "接口地址未填写；可能沿用现有配置。" };
    }
    let host = "";
    try {
        const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : "http://" + raw);
        host = String(url.hostname || "").toLowerCase().replace(/^\[|\]$/g, "");
    } catch (err) {
        return { kind: "unknown", label: "待确认", detail: "接口地址无法解析。" };
    }
    const local = isPrivateEndpointHost(host);
    return {
        kind: local ? "local" : "cloud",
        label: local ? "本地/局域网" : "云端",
        detail: host || "接口地址已填写"
    };
}

function setRouteState(key, state) {
    const kind = state.kind || "unknown";
    const badge = document.getElementById("route-" + key);
    const note = document.getElementById("route-" + key + "-note");
    if (badge) {
        badge.className = "route-state is-" + kind;
        badge.textContent = state.label || "待确认";
        badge.setAttribute("data-route-kind", kind);
    }
    if (note) { note.textContent = state.detail || ""; }
    return kind;
}

function renderRouteOverview() {
    if (!document.getElementById("routeCount")) { return; }
    const kinds = [];
    const mark = function (key, state) { kinds.push(setRouteState(key, state)); };

    const llm = endpointScope(controlValue("llm_host"));
    mark("llm", llm.kind === "unknown" ? llm : {
        kind: llm.kind,
        label: llm.label,
        detail: llm.detail + " · OpenAI 兼容接口"
    });

    const embedding = endpointScope(controlValue("embedding_host"));
    if (embedding.kind !== "unknown") {
        mark("embedding", {
            kind: embedding.kind,
            label: embedding.label,
            detail: embedding.detail + " · Ollama 兼容接口"
        });
    } else if (controlValue("embedding_binding") === "ollama") {
        mark("embedding", { kind: "local", label: "本地", detail: "Ollama；地址未填写，沿用本机默认值。" });
    } else {
        mark("embedding", embedding);
    }

    const rerankMode = controlValue("rerank_mode");
    const rerankProvider = controlValue("rerank_provider");
    const rerankLabels = { aliyun: "阿里云 DashScope", cohere: "Cohere", jina: "Jina" };
    if (rerankMode === "off" || !rerankMode) {
        mark("rerank", { kind: "off", label: "关闭", detail: "重排序已关闭。" });
    } else if (rerankMode === "local") {
        mark("rerank", { kind: "local", label: "本地", detail: "Cohere 兼容的本地 /rerank 服务。" });
    } else {
        const rerankScope = endpointScope(controlValue("rerank_host"));
        const provider = rerankLabels[rerankProvider] || rerankProvider;
        mark("rerank", {
            kind: "cloud",
            label: "云端",
            detail: rerankScope.kind === "unknown"
                ? provider + "；接口地址按提供方默认配置。"
                : provider + " · " + rerankScope.detail
        });
    }

    const vectorType = controlValue("vector_store_type");
    if (!vectorType) {
        mark("vector", { kind: "unknown", label: "待确认", detail: "存储类型未填写；可能沿用现有配置。" });
    } else if (vectorType === "local") {
        mark("vector", { kind: "local", label: "本地", detail: "本地文件向量库。" });
    } else {
        mark("vector", { kind: "unknown", label: "只读", detail: "当前页面未启用该存储类型。" });
    }

    const ocrSection = document.getElementById("sec-ocr");
    const ocrMode = controlValue("ocr_mode");
    const ocrProvider = controlValue("ocr_provider");
    if (ocrSection && ocrSection.getAttribute("data-unavailable") === "true") {
        mark("ocr", { kind: "unsupported", label: "未接入", detail: "当前版本将 OCR 卡片标为只读。" });
    } else if (ocrMode === "off" || ocrProvider === "none") {
        mark("ocr", { kind: "off", label: "关闭", detail: "OCR 已关闭，不外发文件。" });
    } else if (ocrProvider === "cloud") {
        mark("ocr", { kind: "cloud", label: "云端", detail: "兼容 OCR 接口；识别时会发送图片。" });
    } else if (ocrProvider === "local_model") {
        mark("ocr", { kind: "local", label: "本地模型", detail: "本机或局域网兼容 OCR 接口。" });
    } else if (ocrProvider === "windows") {
        mark("ocr", { kind: "local", label: "Windows", detail: "Windows 内置 OCR，不外发文件。" });
    } else if (ocrProvider === "auto") {
        const effective = (LAST_SETTINGS && LAST_SETTINGS.ocr_effective) || {};
        mark("ocr", {
            kind: effective.external ? "cloud" : (effective.enabled ? "local" : "unknown"),
            label: effective.external ? "云端" : (effective.enabled ? "自动/本地" : "自动"),
            detail: effective.enabled ? ("当前选中：" + (effective.provider || "—")) : (effective.reason || "等待可用提供方")
        });
    } else {
        mark("ocr", { kind: "unknown", label: "待确认", detail: "模式或提供方未选择；可能沿用现有配置。" });
    }

    const ttsChain = String(controlValue("tts_tts_provider_chain") || "cloud").toLowerCase();
    const ttsHost = controlValue("tts_tts_cloud_host");
    const ttsModel = controlValue("tts_tts_cloud_model");
    const ttsEffective = (LAST_SETTINGS && LAST_SETTINGS.tts_effective) || {};
    if (ttsEffective.ready) {
        mark("tts", { kind: "cloud", label: "云端优先", detail: "云端 TTS 已配置。" });
    } else if (ttsChain === "browser") {
        mark("tts", { kind: "local", label: "设备语音", detail: "使用 Edge/Chromium 设备音色。" });
    } else if (ttsHost && ttsModel) {
        mark("tts", { kind: "cloud", label: "云端优先", detail: "云端配置完成后优先使用云端，失败时设备语音兜底。" });
    } else {
        mark("tts", { kind: "local", label: "设备语音", detail: "云端配置不完整，使用设备语音。" });
    }

    const counts = { local: 0, cloud: 0, off: 0, unknown: 0, unsupported: 0 };
    kinds.forEach(function (kind) {
        counts[counts[kind] === undefined ? "unknown" : kind] += 1;
    });
    const parts = [];
    if (counts.local) { parts.push("本地 " + counts.local); }
    if (counts.cloud) { parts.push("云端 " + counts.cloud); }
    if (counts.off) { parts.push("关闭 " + counts.off); }
    if (counts.unknown) { parts.push("待确认 " + counts.unknown); }
    if (counts.unsupported) { parts.push("未接入 " + counts.unsupported); }
    document.getElementById("routeCount").textContent = parts.join(" · ");

    const selected = document.querySelector('input[name="settings-profile"]:checked');
    const selectedProfile = selected ? selected.value : "local";
    const warning = document.getElementById("routeWarning");
    if (warning) {
        let text = "";
        let isWarn = false;
        if (selectedProfile === "local" && counts.cloud) {
            text = "当前视图为全本地，但现有字段中有 " + counts.cloud + " 项云端服务；保存前请核对，摘要不会自动修改配置。";
            isWarn = true;
        } else if (selectedProfile === "cloud" && counts.local) {
            text = "当前视图为云端服务，但现有字段中有 " + counts.local + " 项本地或局域网服务。";
        }
        warning.textContent = text;
        warning.hidden = !text;
        warning.className = "route-warning" + (isWarn ? " is-warn" : "");
    }
}

function effectiveLightragValue(key) {
    const effective = (LAST_SETTINGS && LAST_SETTINGS.lightrag_effective) || {};
    return effective[key] ? String(effective[key]).trim() : "";
}

function serviceCheckPattern(key) {
    return {
        llm: /生成模型/i,
        embedding: /嵌入模型/i,
        rerank: /重排模型/i,
        knowledge: /知识服务|LightRAG|外壳/i
    }[key] || null;
}

function serviceCheckResult(key) {
    const pattern = serviceCheckPattern(key);
    if (!pattern) { return null; }
    return (LAST_TEST_CHECKS || []).find(function (item) {
        return pattern.test(String((item && item.name) || ""));
    }) || null;
}

function setReadinessState(key, state) {
    const badge = document.getElementById("readiness-" + key);
    const detail = document.getElementById("readiness-" + key + "-detail");
    const kind = state.kind || "info";
    if (badge) {
        badge.className = "readiness-state is-" + kind;
        badge.textContent = state.label || "待检查";
    }
    if (detail) { detail.textContent = state.detail || ""; }
    return kind;
}

function renderIndexCompatibility() {
    const data = LAST_INDEX_COMPATIBILITY;
    const button = document.getElementById("indexAcceptButton");
    if (!data) {
        if (button) { button.hidden = true; }
        return setReadinessState("index", {
            kind: "info",
            label: "待检查",
            detail: "正在核对嵌入模型、维度和索引工作目录。"
        });
    }
    const state = String(data.state || "");
    const current = data.current || {};
    const recorded = data.recorded || {};
    const currentText = [current.embedding_model, current.embedding_dim ? current.embedding_dim + " 维" : ""]
        .filter(function (item) { return !!item; }).join(" · ");
    const recordedText = [recorded.embedding_model, recorded.embedding_dim ? recorded.embedding_dim + " 维" : ""]
        .filter(function (item) { return !!item; }).join(" · ");
    if (button) {
        button.hidden = ["mismatch", "unrecorded", "unreadable"].indexOf(state) < 0;
    }
    if (state === "compatible") {
        return setReadinessState("index", {
            kind: "ready",
            label: "兼容",
            detail: "当前索引身份与记录一致" + (currentText ? "：" + currentText : "") + "。"
        });
    }
    if (state === "mismatch") {
        return setReadinessState("index", {
            kind: "blocked",
            label: "需重建",
            detail: "索引记录为 " + (recordedText || "未知") + "，当前为 "
                + (currentText || "未知") + "。重建完成前会阻止问答和新增入库。"
        });
    }
    if (state === "unrecorded") {
        return setReadinessState("index", {
            kind: "blocked",
            label: "未记录",
            detail: "当前工作目录没有索引身份基线。确认索引为空或已完成重建后，再记录当前身份。"
        });
    }
    if (state === "unreadable") {
        return setReadinessState("index", {
            kind: "blocked",
            label: "记录异常",
            detail: "索引身份记录无法读取。完成索引核验后，可重新记录当前身份。"
        });
    }
    return setReadinessState("index", {
        kind: "pending",
        label: "不可核对",
        detail: "知识服务或嵌入身份暂不可用，暂时无法核对索引兼容性。"
    });
}

function renderServiceTestStatus(key) {
    const node = document.getElementById("serviceTest-" + key);
    if (!node) { return; }
    const check = serviceCheckResult(key);
    if (!check) {
        node.textContent = "尚未测试";
        node.className = "service-test-status";
        return;
    }
    node.textContent = (check.ok ? "已通过" : "未通过") + "：" + (check.detail || "无详情");
    node.className = "service-test-status " + (check.ok ? "is-ok" : "is-bad");
}

function renderDeploymentReadiness() {
    const card = document.getElementById("localReadiness");
    if (!card) { return; }

    const services = (LAST_SETTINGS && LAST_SETTINGS.runtime_effective
        && LAST_SETTINGS.runtime_effective.services) || [];
    const running = services.filter(function (item) { return !!item.running; }).length;
    const knowledgeCheck = serviceCheckResult("knowledge");
    let appKind = "info";
    if (knowledgeCheck) {
        appKind = setReadinessState("app", knowledgeCheck.ok ? {
            kind: services.length && running < services.length ? "pending" : "ready",
            label: "已测试",
            detail: knowledgeCheck.detail + (services.length ? " · 运行服务 " + running + " / " + services.length : "")
        } : {
            kind: "blocked",
            label: "失败",
            detail: knowledgeCheck.detail || "知识服务连接测试未通过。"
        });
    } else if (services.length) {
        appKind = setReadinessState("app", {
            kind: running === services.length ? "ready" : (running ? "pending" : "blocked"),
            label: running === services.length ? "运行中" : (running ? "部分运行" : "未运行"),
            detail: "运行服务 " + running + " / " + services.length + "；尚未执行知识服务连接测试。"
        });
    } else {
        appKind = setReadinessState("app", {
            kind: "info",
            label: "待检查",
            detail: "尚未读取到运行状态。"
        });
    }

    const llmHost = effectiveLightragValue("LLM_BINDING_HOST");
    const llmModel = effectiveLightragValue("LLM_MODEL");
    const llmCheck = serviceCheckResult("llm");
    let llmKind = "info";
    if (llmCheck) {
        llmKind = setReadinessState("llm", {
            kind: llmCheck.ok ? "ready" : "blocked",
            label: llmCheck.ok ? "已通过" : "失败",
            detail: llmCheck.detail || "生成模型连接测试失败。"
        });
    } else if (llmHost && llmModel) {
        llmKind = setReadinessState("llm", {
            kind: "pending",
            label: "待测试",
            detail: llmModel + " · " + llmHost
        });
    } else {
        llmKind = setReadinessState("llm", {
            kind: "info",
            label: "未完成",
            detail: "缺少生成模型接口地址或模型名称。"
        });
    }

    const embeddingHost = effectiveLightragValue("EMBEDDING_BINDING_HOST");
    const embeddingModel = effectiveLightragValue("EMBEDDING_MODEL");
    const embeddingCheck = serviceCheckResult("embedding");
    let embeddingKind = "info";
    if (embeddingCheck) {
        embeddingKind = setReadinessState("embedding", {
            kind: embeddingCheck.ok ? "ready" : "blocked",
            label: embeddingCheck.ok ? "已通过" : "失败",
            detail: embeddingCheck.detail || "嵌入模型连接测试失败。"
        });
    } else if (embeddingHost && embeddingModel) {
        embeddingKind = setReadinessState("embedding", {
            kind: "pending",
            label: "待测试",
            detail: embeddingModel + " · " + embeddingHost
        });
    } else {
        embeddingKind = setReadinessState("embedding", {
            kind: "info",
            label: "未完成",
            detail: "缺少嵌入模型接口地址或模型名称。"
        });
    }

    const vectorType = effectiveLightragValue("LIGHTRAG_VECTOR_STORAGE") || controlValue("vector_store_type");
    const vectorKind = setReadinessState("vector", vectorType === "local" ? {
        kind: "ready",
        label: "本地文件",
        detail: "当前版本使用本地文件向量库。"
    } : {
        kind: "pending",
        label: vectorType ? "待确认" : "沿用默认",
        detail: vectorType
            ? "当前存储类型：" + vectorType + "；请确认后端支持与数据迁移方案。"
            : "未在页面指定存储类型，可能沿用现有配置。"
    });

    const workingDir = effectiveLightragValue("WORKING_DIR") || controlValue("graph_working_dir");
    const inputDir = effectiveLightragValue("INPUT_DIR") || controlValue("graph_input_dir");
    setReadinessState("paths", {
        kind: "info",
        label: workingDir || inputDir ? "已配置" : "默认路径",
        detail: "数据目录：" + (workingDir || "沿用默认") + " · 输入目录：" + (inputDir || "沿用默认") + "。可写性仍需实际验证。"
    });

    const rerank = effectiveLightragValue("RERANK_BINDING");
    const rerankCheck = serviceCheckResult("rerank");
    const ttsEffective = (LAST_SETTINGS && LAST_SETTINGS.tts_effective) || {};
    const ocrUnavailable = !!(document.getElementById("sec-ocr")
        && document.getElementById("sec-ocr").getAttribute("data-unavailable") === "true");
    setReadinessState("optional", {
        kind: "info",
        label: "部分未接入",
        detail: "重排：" + ((!rerank || rerank.toLowerCase() === "null")
            ? "关闭"
            : (rerankCheck ? rerankCheck.detail : "已配置，待测试"))
            + " · OCR：" + (ocrUnavailable ? "未接入" : "请查看 OCR 卡片")
            + " · 朗读：" + (ttsEffective.ready ? "云端已配置" : "设备语音")
    });

    const indexKind = renderIndexCompatibility();
    const optionalKinds = [appKind, llmKind, embeddingKind, indexKind, vectorKind];
    const overall = document.getElementById("readinessOverall");
    if (overall) {
        let kind = "pending";
        let label = "待验证";
        if (optionalKinds.indexOf("blocked") >= 0) {
            kind = "blocked";
            label = "需处理";
        } else if (appKind === "ready" && llmKind === "ready" && embeddingKind === "ready"
            && indexKind === "ready"
            && (vectorKind === "ready" || vectorKind === "info")) {
            kind = "ready";
            label = "已就绪";
        }
        overall.className = "readiness-state is-" + kind;
        overall.textContent = label;
    }

    renderServiceTestStatus("llm");
    renderServiceTestStatus("embedding");
}

function readSettingsProfile() {
    try {
        const value = localStorage.getItem(SETTINGS_PROFILE_KEY);
        return SETTINGS_PROFILES[value] ? value : "local";
    } catch (err) {
        return "local";
    }
}

function applySettingsProfile(profile, persist) {
    const key = SETTINGS_PROFILES[profile] ? profile : "local";
    const main = document.querySelector(".main");
    if (main) { main.setAttribute("data-settings-profile", key); }
    document.querySelectorAll('input[name="settings-profile"]').forEach(function (node) {
        node.checked = node.value === key;
    });
    document.querySelectorAll("[data-profile-option]").forEach(function (node) {
        const selected = node.getAttribute("data-profile-option") === key;
        node.classList.toggle("is-selected", selected);
        node.setAttribute("data-selected", selected ? "true" : "false");
    });
    const current = document.getElementById("schemeCurrent");
    if (current) { current.textContent = SETTINGS_PROFILES[key].label; }
    document.querySelectorAll("[data-local-only-note]").forEach(function (node) {
        node.hidden = key !== "local";
    });
    const readiness = document.getElementById("localReadiness");
    if (readiness) { readiness.hidden = key === "cloud"; }
    renderRouteOverview();
    renderDeploymentReadiness();
    if (persist) {
        try { localStorage.setItem(SETTINGS_PROFILE_KEY, key); } catch (err) { /* 忽略 */ }
    }
}

function initSettingsProfile() {
    document.querySelectorAll('input[name="settings-profile"]').forEach(function (node) {
        node.addEventListener("change", function () {
            if (node.checked) { applySettingsProfile(node.value, true); }
        });
    });
    applySettingsProfile(readSettingsProfile(), false);
}

function buildNav() {
    const box = document.getElementById("navlist");
    if (!box) { return; }
    box.innerHTML = "";
    let currentGroup = "";
    SECTIONS.forEach(function (item) {
        if (item.group && item.group !== currentGroup) {
            const group = document.createElement("div");
            group.className = "nav-group";
            group.textContent = item.group;
            box.appendChild(group);
            currentGroup = item.group;
        }
        const node = document.createElement("div");
        node.className = "nav-item";
        node.setAttribute("data-target", item.id);
        const ico = document.createElement("span");
        ico.className = "ico";
        ico.textContent = item.num;
        const name = document.createElement("span");
        name.textContent = item.name;
        node.appendChild(ico);
        node.appendChild(name);
        if (item.badge) {
            const badge = document.createElement("span");
            badge.className = "state";
            badge.id = "nav-" + item.badge;
            node.appendChild(badge);
        }
        node.setAttribute("role", "button");
        node.setAttribute("tabindex", "0");
        node.onclick = function () { gotoSection(item.id); };
        bindActivateKey(node);       // 键盘路径复用同一个 onclick
        box.appendChild(node);
    });
}

function readCollapsed() {
    try {
        const raw = localStorage.getItem(COLLAPSE_KEY);
        if (!raw) { return null; }
        const list = JSON.parse(raw);
        return Array.isArray(list) ? list : null;
    } catch (err) {
        return null;   // 隐私模式 / 禁用存储时退回默认
    }
}

function saveCollapsed() {
    const list = [];
    SECTIONS.forEach(function (item) {
        const card = document.getElementById(item.id);
        if (card && card.classList.contains("collapsed")) { list.push(item.id); }
    });
    try { localStorage.setItem(COLLAPSE_KEY, JSON.stringify(list)); } catch (err) { /* 忽略 */ }
}

function applyCollapsed() {
    // 单开手风琴：刷新后优先恢复最后查看的分组，其余收起。
    const saved = readCollapsed();
    let preferred = "";
    try { preferred = localStorage.getItem(LAST_SECTION_KEY) || ""; } catch (err) { preferred = ""; }
    if (!document.getElementById(preferred)) {
        if (saved) {
            preferred = (SECTIONS.find(function (item) { return saved.indexOf(item.id) < 0; }) || {}).id || "";
        }
        if (!preferred) { preferred = "sec-llm"; }
    }
    SECTIONS.forEach(function (item) {
        const card = document.getElementById(item.id);
        if (!card) { return; }
        card.classList.toggle("collapsed", item.id !== preferred);
    });
    syncExpanded();
}

function toggleSection(id) {
    const card = document.getElementById(id);
    if (!card) { return; }
    const opening = card.classList.contains("collapsed");
    if (opening) {
        SECTIONS.forEach(function (item) {
            if (item.id === id) { return; }
            const other = document.getElementById(item.id);
            if (other) { other.classList.add("collapsed"); }
        });
    }
    card.classList.toggle("collapsed");
    saveCollapsed();
    syncExpanded();
}

function expandAll(expand) {
    SECTIONS.forEach(function (item) {
        const card = document.getElementById(item.id);
        if (!card) { return; }
        card.classList.toggle("collapsed", !expand);
    });
    saveCollapsed();
    syncExpanded();
}

function setActiveNav(id) {
    let active = null;
    document.querySelectorAll(".nav-item").forEach(function (node) {
        const selected = node.getAttribute("data-target") === id;
        node.classList.toggle("active", selected);
        if (selected) { active = node; }
    });
    if (active && ACTIVE_NAV_ID !== id) {
        ACTIVE_NAV_ID = id;
        try { localStorage.setItem(LAST_SECTION_KEY, id); } catch (err) { /* 忽略 */ }
    }
    const list = document.getElementById("navlist");
    if (!list || !active) { return; }
    const listRect = list.getBoundingClientRect();
    const itemRect = active.getBoundingClientRect();
    const pad = 4;
    if (itemRect.top < listRect.top + pad) {
        list.scrollTop -= (listRect.top + pad - itemRect.top);
    } else if (itemRect.bottom > listRect.bottom - pad) {
        list.scrollTop += (itemRect.bottom - (listRect.bottom - pad));
    }
    if (itemRect.left < listRect.left + pad) {
        list.scrollLeft -= (listRect.left + pad - itemRect.left);
    } else if (itemRect.right > listRect.right - pad) {
        list.scrollLeft += (itemRect.right - (listRect.right - pad));
    }
}

function gotoSection(id) {
    const card = document.getElementById(id);
    if (!card) { return; }
    SECTIONS.forEach(function (item) {
        if (item.id === id) { return; }
        const other = document.getElementById(item.id);
        if (other) { other.classList.add("collapsed"); }
    });
    card.classList.remove("collapsed");
    saveCollapsed();
    syncExpanded();
    setActiveNav(id);
    try { history.replaceState(null, "", "#" + id); } catch (err) { /* 忽略 */ }
    card.scrollIntoView({ behavior: "smooth", block: "start" });
    setSettingsNavOpen(false);
}

function setSettingsNavOpen(open) {
    const nav = document.getElementById("settingsNav");
    const backdrop = document.getElementById("settingsNavBackdrop");
    const button = document.getElementById("settingsDirBtn");
    const shouldOpen = !!open && window.matchMedia
        && window.matchMedia("(max-width: 900px)").matches;
    if (nav) { nav.classList.toggle("is-open", shouldOpen); }
    if (backdrop) { backdrop.hidden = !shouldOpen; }
    if (button) { button.setAttribute("aria-expanded", shouldOpen ? "true" : "false"); }
    document.body.classList.toggle("nav-open", shouldOpen);
    if (shouldOpen) {
        const close = document.getElementById("settingsNavClose");
        if (close) { close.focus(); }
    }
}

function closeBarMore() {
    const menu = document.getElementById("barSecondary");
    const button = document.getElementById("barMoreBtn");
    if (menu) { menu.classList.remove("is-open"); }
    if (button) { button.setAttribute("aria-expanded", "false"); }
}

function initShellControls() {
    const dirBtn = document.getElementById("settingsDirBtn");
    const navClose = document.getElementById("settingsNavClose");
    const backdrop = document.getElementById("settingsNavBackdrop");
    const actionsBtn = document.getElementById("settingsActionsBtn");
    const moreBtn = document.getElementById("barMoreBtn");
    const menu = document.getElementById("barSecondary");

    if (dirBtn) {
        dirBtn.addEventListener("click", function () {
            const nav = document.getElementById("settingsNav");
            setSettingsNavOpen(!(nav && nav.classList.contains("is-open")));
        });
    }
    if (navClose) { navClose.addEventListener("click", function () { setSettingsNavOpen(false); }); }
    if (backdrop) { backdrop.addEventListener("click", function () { setSettingsNavOpen(false); }); }
    if (actionsBtn) {
        actionsBtn.addEventListener("click", function () {
            if (dirtyIds().length) { return; }
            ACTION_BAR_FORCED = !ACTION_BAR_FORCED;
            renderActionBar();
        });
    }
    if (moreBtn && menu) {
        moreBtn.addEventListener("click", function (event) {
            event.stopPropagation();
            const open = !menu.classList.contains("is-open");
            menu.classList.toggle("is-open", open);
            moreBtn.setAttribute("aria-expanded", open ? "true" : "false");
        });
        menu.addEventListener("click", function (event) { event.stopPropagation(); });
    }
    document.addEventListener("click", function () { closeBarMore(); });
    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") { return; }
        closeBarMore();
        setSettingsNavOpen(false);
    });
}

function initScrollSpy() {
    let scheduled = false;

    function update() {
        scheduled = false;
        const header = document.querySelector(".header");
        const offset = (header ? header.getBoundingClientRect().height : 0) + 24;
        let current = SECTIONS.length ? SECTIONS[0].id : "";
        let bestVisible = -1;
        const viewportBottom = window.innerHeight;
        SECTIONS.forEach(function (item) {
            const card = document.getElementById(item.id);
            if (!card) { return; }
            const rect = card.getBoundingClientRect();
            const visible = Math.min(rect.bottom, viewportBottom) - Math.max(rect.top, offset);
            if (visible >= bestVisible) {
                bestVisible = visible;
                current = item.id;
            }
        });
        if (current) { setActiveNav(current); }
    }

    function schedule() {
        if (scheduled) { return; }
        scheduled = true;
        window.requestAnimationFrame(update);
    }

    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    schedule();
}

function initQuickActions() {
    const box = document.getElementById("settingsQuickActions");
    const topBtn = document.getElementById("topBtn");
    const lastBtn = document.getElementById("lastPositionBtn");
    if (!box || !topBtn || !lastBtn) { return; }
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function lastSection() {
        try {
            const id = localStorage.getItem(LAST_SECTION_KEY) || "";
            return document.getElementById(id) ? id : "";
        } catch (err) {
            return "";
        }
    }

    function update() {
        const enoughContent = document.documentElement.scrollHeight > window.innerHeight + 240;
        box.hidden = !(enoughContent && window.scrollY > 360);
        lastBtn.hidden = !lastSection();
    }

    lastBtn.addEventListener("click", function () {
        const id = lastSection();
        if (id) { gotoSection(id); }
    });
    topBtn.addEventListener("click", function () {
        window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    });
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    update();

    let hash = "";
    try {
        hash = decodeURIComponent((location.hash || "").replace(/^#/, ""));
    } catch (err) {
        hash = "";
    }
    if (hash && document.getElementById(hash)) {
        window.setTimeout(function () { gotoSection(hash); }, 0);
    }
}

// 折叠状态下的「当前值摘要」：不用展开就能看到关键状态
function setSummary(id, values, fallback) {
    const node = document.getElementById(id);
    if (!node) { return; }
    const text = values.filter(function (item) { return item; }).join(" · ") || fallback;
    node.textContent = text;
    node.title = text;
}

function renderSummaries(data) {
    const settings = (data && data.settings) || {};
    const lr = (data && data.lightrag_effective) || {};
    const app = (data && data.app_effective) || {};
    const quality = (data && data.quality_effective) || {};
    const ocr = (data && data.ocr_effective) || {};
    const preview = (data && data.ocr_preview) || {};
    // 有些值在配置里就是**字符串** "null"（实测：rerank.binding 的值就是 "null"，
    // 含义是"不启用重排序"）。原样输出会让摘要显示一个用户看不懂的 null，
    // 这里统一归一化成空串，交给 fallback 说人话（"未启用" / "未设置"）。
    const BLANK_WORDS = ["null", "none", "undefined", "nil"];
    const norm = function (value) {
        if (value === null || value === undefined) { return ""; }
        const text = String(value).trim();
        return BLANK_WORDS.indexOf(text.toLowerCase()) >= 0 ? "" : text;
    };
    const pick = function (group, key) { return norm((settings[group] || {})[key]); };
    const uploadMb = Number(app.max_upload_size || 0);
    setSummary("sum-llm", [pick("llm", "binding"), pick("llm", "model") || norm(lr.LLM_MODEL)], "未设置");
    setSummary("sum-embedding", [pick("embedding", "binding"), pick("embedding", "model") || norm(lr.EMBEDDING_MODEL),
        lr.EMBEDDING_DIM ? (lr.EMBEDDING_DIM + " 维") : ""], "未设置");
    const rerankMode = pick("rerank", "mode");
    const rerankProvider = pick("rerank", "provider");
    setSummary("sum-rerank", [
        rerankMode === "off" ? "" : (rerankMode === "local" ? "本地" : ("云端 · " + (rerankProvider || "cohere"))),
        pick("rerank", "model") || norm(lr.RERANK_MODEL)
    ], "未启用");
    setSummary("sum-vector", [norm(lr.LIGHTRAG_VECTOR_STORAGE)], "—");
    setSummary("sum-paths", [norm(lr.WORKING_DIR)], "—");
    setSummary("sum-app", [uploadMb ? (Math.round(uploadMb / 1048576 * 10) / 10 + " MB 上限") : "",
        norm(app.log_level),
        norm(lr.LLM_MODEL) ? ("生成 " + norm(lr.LLM_MODEL)) : "生成未配置"], "—");
    const runtimeServices = (((data && data.runtime_effective) || {}).services) || [];
    const runtimeHealthy = runtimeServices.filter(function (item) { return item.running; }).length;
    const runtimeApp = runtimeServices.filter(function (item) { return item.service === "app"; })[0];
    setSummary("sum-runtime", [
        runtimeHealthy + " / 3 服务可用",
        runtimeApp && runtimeApp.port ? ("应用端口 " + runtimeApp.port) : ""
    ], "—");
    setSummary("sum-status", [norm(app.lightrag_url)], "只读汇总");
    // 朗读（TTS）：摘要里说清「云端配了没」，不用展开也看得见
    var cloudPick = pick("tts", "tts_cloud_host");
    var chainPick = String(pick("tts", "tts_provider_chain") || "").toLowerCase();
    const ttsEffective = (data && data.tts_effective) || {};
    var ttsModePick = ttsEffective.ready
        ? "云端优先"
        : (chainPick === "browser" ? "仅设备浏览器语音" : "设备浏览器语音");
    setSummary("sum-tts", [
        ttsModePick,
        cloudPick ? ("云端 " + cloudPick) : ""]
        , "—");
    // 质检恒定开启 —— 摘要里说清"关不掉"，用户如果改过 .env 就点明"已被忽略"
    setSummary("sum-quality", ["恒定开启 · " + (quality.profile || "standard")
        + (quality.switch_ignored ? "（QUALITY_ENABLED=false 已忽略）" : "")], "—");
    // 入库确认：摘要里同时说清「保存的」与「当前生效的」，避免误以为没保存成功
    const gate = (data && data.ingest_confirm_effective) || {};
    const gateSaved = pick("ingest", "confirm");
    setSummary("sum-ingest", [
        INGEST_MODE_TEXT[gate.mode] || INGEST_MODE_TEXT.always,
        (gateSaved && gateSaved !== gate.mode) ? ("已保存 " + gateSaved + " · 待重启") : ""
    ], "—");
    setSummary("sum-ocr", [ocr.enabled
        ? ((pick("ocr", "mode") || "已启用") + " · " + (ocr.provider || "") + (ocr.external ? " · 会外发" : " · 不外发"))
        : (preview.enabled_after_restart
            ? "已保存 · 待重启后启用"
            : (preview.reason ? "未启用 · " + preview.reason : "未启用 · 不会外发任何文件"))], "—");
    // 文件夹轮询：摘要里说清「开没开 / 多久扫一次 / 收件箱确认方式」
    const inbox = (data && data.inbox_effective) || {};
    const inboxSaved = pick("inbox", "enabled");
    const inboxWaiting = Boolean(inboxSaved && ((inboxSaved === "true") !== Boolean(inbox.enabled)));
    setSummary("sum-inbox", [
        inbox.enabled ? ("已开启 · 每 " + (inbox.poll_seconds || 10) + " 秒") : "未开启（收件箱不扫描）",
        "只进入复核队列",
        inboxWaiting ? "已保存 · 待重启" : ""
    ], "—");
    const navInbox = document.getElementById("nav-inbox");
    if (navInbox) {
        navInbox.textContent = inbox.enabled ? "复核" : "关";
        navInbox.className = "state" + (inbox.enabled ? " on" : "");
        navInbox.title = "现在生效：文件夹轮询 " + (inbox.enabled ? "开启" : "关闭")
            + "，来源只进入本机复核队列"
            + (inboxWaiting ? "（已保存 " + inboxSaved + "，重启后生效）" : "");
    }
    const navQuality = document.getElementById("nav-quality");
    if (navQuality) {
        // 质检关不掉 —— 导航徽标恒定显示「开」，被忽略的 .env 开关写进 title
        navQuality.textContent = "开";
        navQuality.className = "state on";
        navQuality.title = "现在生效：质量校验恒定开启（" + (quality.profile || "standard") + "）"
            + (quality.switch_ignored ? "；.env 里的 QUALITY_ENABLED=false 已被忽略" : "");
    }
    const navIngest = document.getElementById("nav-ingest");
    if (navIngest) {
        // 保存了 off / auto 但进程还在用 always 时标成「待重启」，否则最容易被误解成「没保存上」
        const waiting = Boolean(gateSaved && gateSaved !== gate.mode);
        navIngest.textContent = INGEST_MODE_TAG[gate.mode] || "逐个";
        navIngest.className = "state" + ((gate.mode === "off" || waiting) ? " warn" : " on");
        navIngest.title = "现在生效：" + gate.mode + (waiting ? ("（已保存 " + gateSaved + "，重启后生效）") : "");
    }
    const navOcr = document.getElementById("nav-ocr");
    if (navOcr) {
        // 「待重启」：配置已打开但进程还在用旧值，避免用户误以为没保存成功
        const waiting = Boolean(preview.enabled_after_restart && !ocr.enabled);
        navOcr.textContent = ocr.enabled ? (pick("ocr", "mode") || "开") : (waiting ? "待重启" : "关");
        navOcr.className = "state" + (ocr.external ? " warn" : (ocr.enabled ? " on" : (waiting ? " warn" : "")));
    }
    // 朗读（TTS）：导航项上标出「云端→设备 / 仅设备 / 自定义」
    const navTts = document.getElementById("nav-tts");
    if (navTts) {
        const chainPick = String(pick("tts", "tts_provider_chain") || "").toLowerCase();
        const cloudPick = pick("tts", "tts_cloud_host");
        const ttsReady = Boolean(((data && data.tts_effective) || {}).ready);
        navTts.textContent = ttsReady ? "云端→设备" : "设备";
        navTts.className = "state" + (ttsReady ? " warn" : " on");
    }
}

// 入库确认：三种方式的人话说法 + 「保存了什么 / 现在生效什么」的说明
const INGEST_MODE_TEXT = {
    always: "逐个确认（每个文件都等你点头）",
    auto: "智能放行（建议入库的自动入库，其余问你）",
    off: "不确认（通过校验就直接入库）"
};
const INGEST_MODE_TAG = { always: "逐个", auto: "智能", off: "直通" };
// 文件夹轮询固定进入复核队列，不提供自动入库选项。
const INBOX_MODE_TAG = { review: "复核" };
const INBOX_MODE_TEXT = {
    review: "只进入本机复核队列，不会自动写入知识库"
};

function renderIngestHint(data) {
    const box = document.getElementById("ingestState");
    if (!box) { return; }
    const gate = (data && data.ingest_confirm_effective) || {};
    const settings = (data && data.settings) || {};
    const ingest = settings.ingest || {};
    const saved = String(ingest.confirm || "");
    const savedTtl = String(ingest.ttl_hours || "");
    const mode = gate.mode || "always";
    const ttl = (gate.pending_review_ttl_hours === undefined || gate.pending_review_ttl_hours === null)
        ? 24 : gate.pending_review_ttl_hours;
    const waiting = [];
    if (saved && saved !== mode) { waiting.push("确认方式 → " + saved); }
    if (savedTtl && savedTtl !== String(ttl)) { waiting.push("保留时长 → " + savedTtl + " 小时"); }
    if (waiting.length) {
        box.className = "note-line warn";
        box.textContent = "已保存但还没生效：" + waiting.join("、") + "。当前进程仍按 " + mode + " 执行——"
            + "重启服务后新设置生效（重启服务后生效）。";
        return;
    }
    box.className = "note-line" + (mode === "off" ? " warn" : " ok");
    box.textContent = "现在生效：" + mode + " —— " + (INGEST_MODE_TEXT[mode] || "")
        + "。停在确认门的文件保留 " + ttl + " 小时，超时按「跳过」处理：原件、解析结果与建议都会留着，随时可以再点「仍要入库」。"
        + (mode === "off" ? "注意：这时错内容也会进知识库，出错只能靠删数据。" : "");
}

// 文件夹轮询：同样把「保存了什么 / 现在生效什么 / 为什么还不能用」讲清楚
function renderInboxHint(data) {
    const box = document.getElementById("inboxState");
    if (!box) { return; }
    const inbox = (data && data.inbox_effective) || {};
    const settings = (data && data.settings) || {};
    const saved = settings.inbox || {};
    const polling = inbox.poll_seconds || 10;
    const settle = inbox.settle_seconds === undefined || inbox.settle_seconds === null
        ? 5 : inbox.settle_seconds;
    const waiting = [];
    if (String(saved.enabled || "") && ((String(saved.enabled) === "true") !== Boolean(inbox.enabled))) {
        waiting.push("开关 → " + String(saved.enabled));
    }
    if (String(saved.poll_seconds || "") && String(saved.poll_seconds) !== String(polling)) {
        waiting.push("扫描间隔 → " + String(saved.poll_seconds) + " 秒");
    }
    if (String(saved.settle_seconds || "") && String(saved.settle_seconds) !== String(settle)) {
        waiting.push("稳定等待 → " + String(saved.settle_seconds) + " 秒");
    }
    if (String(saved.confirm || "") && String(saved.confirm) !== String(inbox.confirm)) {
        waiting.push("确认方式 → " + String(saved.confirm));
    }
    if (waiting.length) {
        box.className = "note-line warn";
        box.textContent = "已保存但还没生效：" + waiting.join("、") + "。当前进程仍是 "
            + (inbox.enabled ? "开启" : "关闭") + " / 每 " + polling + " 秒 / 只进入复核"
            + "——重启服务后新设置生效（重启服务后生效）。";
        return;
    }
    box.className = "note-line";
    box.textContent = "现在生效：" + (inbox.enabled ? "开启" : "关闭")
        + "，每 " + polling + " 秒扫一次收件箱，文件长稳 " + settle + " 秒后受理；"
        + "结果只进入本机复核队列，不会自动写入知识库。"
        + "目录：" + ((inbox.dirs || {}).inbox || "data/inbox")
        + "；源文件保留在原处，状态记录：" + ((inbox.dirs || {}).state || "data/inbox-state.json") + "。";
}

// 把「保存了什么 / 现在生效什么 / 为什么还不能用」写成一句人话
function renderOcrHint(data) {
    const box = document.getElementById("ocrState");
    if (!box) { return; }
    const ocr = (data && data.ocr_effective) || {};
    const preview = (data && data.ocr_preview) || {};
    const parts = [];
    if (ocr.configured && !ocr.enabled && preview.reason) {
        parts.push("已配置但当前不可用：" + preview.reason + "。");
    } else if (preview.enabled_after_restart && !ocr.enabled) {
        parts.push("已保存：模式 " + (preview.configured_mode || "") + "、提供方 " + (preview.configured_provider || "")
            + "；重启服务后启用（当前生效值仍是 " + (ocr.mode || "off") + "）。");
    } else if (ocr.enabled) {
        parts.push("当前已启用：" + (ocr.mode || "") + " · " + (ocr.provider || "")
            + (ocr.external ? "（会把文件发送到外部服务）" : "（不外发）") + "。");
    } else {
        parts.push("当前未启用（不会向外部发送任何文件）。");
    }
    if (preview.pending_env && preview.pending_env.length) {
        parts.push("尚未生效的项：" + preview.pending_env.join(" / ") + "。");
    }
    if (data && data.restart_needed) {
        parts.push("重启：双击项目根目录的 重启服务。");
    }
    const selected = (data && data.ocr_providers || []).filter(function (item) { return item.selected; })[0];
    if (selected) {
        parts.push("提供方就绪情况：" + (selected.label || selected.name) + " — " + (selected.available ? "可用" : (selected.detail || "不可用")));
    }
    box.textContent = parts.join(" ");
    box.className = "note-line" + (
        (ocr.configured && !ocr.enabled) || preview.enabled_after_restart ? " warn"
            : (ocr.enabled ? " ok" : "")
    );
}

// ---------- 朗读（TTS）：链路与云端诊断（数据来自 GET /api/tts/options）----------
// 为什么不复用 /api/tts/status：那个接口会顺带预热模型、还会探活外部服务；
// 设置页只要「配了没 / 缺什么 / 现在生效的是谁」，读 options 更快也更省。
var TTS_OPT = null;
// 最近一次 /api/settings 的**完整响应对象**：options 的异步回调回来后要用它重绘摘要与导航角标。
// 注意是整份响应（含 *_effective 各段），不是 data.settings —— 见 refreshTtsOptions() 里的说明。
var LAST_SETTINGS = null;
var LAST_TEST_CHECKS = [];
var LAST_INDEX_COMPATIBILITY = null;

async function refreshIndexCompatibility() {
    try {
        const response = await fetch("/api/knowledge/index-compatibility", { cache: "no-store" });
        const data = await response.json().catch(function () { return null; });
        if (!response.ok) {
            LAST_INDEX_COMPATIBILITY = {
                state: "unavailable",
                message: describeError(data)
            };
        } else {
            LAST_INDEX_COMPATIBILITY = data || { state: "unavailable" };
        }
    } catch (err) {
        LAST_INDEX_COMPATIBILITY = {
            state: "unavailable",
            message: String(err)
        };
    }
    renderDeploymentReadiness();
}

async function acceptIndexCompatibility() {
    const options = {
        title: "确认索引已经重建",
        body: "此操作只记录当前嵌入模型、维度和向量存储身份，不会清理或重建任何索引。"
            + "只有在你已经完成索引重建，或确认这是全新的空索引时才继续。",
        detail: "错误确认会让后续检索继续使用不匹配的旧向量。",
        inputLabel: "输入 REBUILD 确认",
        requiredValue: "REBUILD",
        placeholder: "REBUILD",
        confirmText: "记录当前索引身份",
        cancelText: "取消",
        danger: true
    };
    let confirmation = null;
    if (window.RAGKB && typeof window.RAGKB.confirmText === "function") {
        confirmation = await window.RAGKB.confirmText(options);
    } else {
        confirmation = window.prompt(options.body + "\n\n" + options.detail + "\n\n输入 REBUILD 确认：");
    }
    if (String(confirmation || "").trim() !== "REBUILD") { return; }
    try {
        const response = await fetch("/api/knowledge/index-compatibility/accept", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirmation: "REBUILD" })
        });
        const data = await response.json().catch(function () { return null; });
        if (!response.ok) {
            notify("记录失败：" + describeError(data), "bad");
            return;
        }
        LAST_INDEX_COMPATIBILITY = data;
        renderDeploymentReadiness();
        notify("已记录当前索引身份", "ok");
    } catch (err) {
        notify("记录失败：" + err, "bad");
    }
}

function refreshTtsOptions() {
    if (typeof window.fetch !== "function") { return; }
    window.fetch("/api/tts/options", { cache: "no-store" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
            TTS_OPT = data;
            renderTtsSummary(null, data);
            // 摘要要拿**完整的 /api/settings 响应**重绘。这里原来传的是
            // renderSummaries({ settings: LAST_SETTINGS })，只喂了 settings 一段，
            // 于是 lightrag_effective / app_effective / ocr_effective / inbox_effective
            // 全成空对象，每次加载与每次保存后都把摘要改写成假的占位符
            //（实测：生成模型显示"未设置"、向量存储与存储路径显示"—"、
            //  OCR 显示"未启用·不会外发"、文件夹轮询显示"未开启"，而实际都是开着的）。
            // LAST_SETTINGS 为空（响应还没回来）时不重绘 —— 保留 renderSummaries(data) 画好的那份。
            if (LAST_SETTINGS) { renderSummaries(LAST_SETTINGS); }
        })
        .catch(function () { /* 读不到就保持上一次的显示，不影响保存 */ });
}

/* ---------- 局域网授权通用错误文案 ---------- */
function chatErrorText(data, status) {
    var detail = data && data.detail;
    if (typeof detail === "string" && detail) { return detail; }
    if (detail && typeof detail === "object") { return detail.message || JSON.stringify(detail); }
    return "HTTP " + status;
}

/* ---------- 局域网设备一键授权 ---------- */
var ACCESS_POLL_TIMER = null;
var ACCESS_AUTO_REVEALED = false;

function accessDeviceErrorText(data, status) {
    if (typeof chatErrorText === "function") { return chatErrorText(data, status); }
    var detail = data && data.detail;
    return (detail && (detail.message || detail)) || ("HTTP " + status);
}

function accessTimeText(value) {
    var date = value ? new Date(value) : null;
    if (!date || isNaN(date.getTime())) { return "—"; }
    return date.toLocaleString("zh-CN", { hour12: false });
}

function renderAccessDevices(items, error) {
    var box = document.getElementById("accessDeviceList");
    if (!box) { return; }
    box.textContent = "";
    if (error) {
        var err = document.createElement("div");
        err.className = "access-device-empty";
        err.textContent = error;
        box.appendChild(err);
        return;
    }
    if (!items || !items.length) {
        var empty = document.createElement("div");
        empty.className = "access-device-empty";
        empty.textContent = "暂无已授权设备";
        box.appendChild(empty);
        return;
    }
    if (!ACCESS_AUTO_REVEALED) {
        ACCESS_AUTO_REVEALED = true;
        var card = box.closest("section");
        if (card && card.classList.contains("collapsed")) {
            toggleSection(card.id);
        }
        var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        box.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    }
    items.forEach(function (item) {
        var row = document.createElement("div");
        row.className = "access-device";
        var main = document.createElement("div");
        main.className = "access-device-main";
        var name = document.createElement("div");
        name.className = "access-device-name";
        name.textContent = item.name || "未命名设备";
        var meta = document.createElement("div");
        meta.className = "access-device-meta";
        meta.textContent = "授权时间 " + accessTimeText(item.created_at)
            + " · 最近使用 " + accessTimeText(item.last_seen_at)
            + (item.last_ip ? " · " + item.last_ip : "");
        main.appendChild(name);
        main.appendChild(meta);
        var del = document.createElement("button");
        del.type = "button";
        del.className = "btn-clear";
        del.textContent = "删除";
        del.addEventListener("click", function () {
            askConfirm({
                title: "删除已授权设备？",
                body: "设备「" + (item.name || "未命名设备") + "」将立即失去访问权限，需要重新在电脑上允许。",
                confirmText: "删除设备",
                danger: true
            }).then(function (ok) {
                if (!ok) { return; }
                fetch("/api/app/access/devices/" + encodeURIComponent(item.id), {
                    method: "DELETE",
                    cache: "no-store"
                }).then(function (res) {
                    return res.json().catch(function () { return {}; }).then(function (data) {
                        if (!res.ok) { throw new Error(accessDeviceErrorText(data, res.status)); }
                        setStatus("已删除授权设备：" + (item.name || "未命名设备"), true);
                        loadAccessDevices();
                    });
                }).catch(function (err) {
                    setStatus("删除设备失败：" + ((err && err.message) || "未知错误"), false);
                });
            });
        });
        row.appendChild(main);
        row.appendChild(del);
        box.appendChild(row);
    });
}

function loadAccessDevices() {
    fetch("/api/app/access/devices", { cache: "no-store" })
        .then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (data) {
                if (!res.ok) {
                    renderAccessDevices([], res.status === 403
                        ? "仅电脑本机可以查看和管理授权设备"
                        : accessDeviceErrorText(data, res.status));
                    return;
                }
                renderAccessDevices(data.devices || []);
            });
        })
        .catch(function () { renderAccessDevices([], "读取已授权设备失败，请刷新重试"); });
}

function renderAccessRequests(items, error) {
    var box = document.getElementById("accessRequestList");
    if (!box) { return; }
    box.textContent = "";
    if (error) {
        var err = document.createElement("div");
        err.className = "access-device-empty";
        err.textContent = error;
        box.appendChild(err);
        return;
    }
    if (!items || !items.length) {
        var empty = document.createElement("div");
        empty.className = "access-device-empty";
        empty.textContent = "暂无新设备请求。平板打开上面的地址后会出现在这里。";
        box.appendChild(empty);
        return;
    }
    items.forEach(function (item) {
        var row = document.createElement("div");
        row.className = "access-device access-request";
        var main = document.createElement("div");
        main.className = "access-device-main";
        var name = document.createElement("div");
        name.className = "access-device-name";
        name.textContent = item.name || "未命名设备";
        var meta = document.createElement("div");
        meta.className = "access-device-meta";
        meta.textContent = "请求时间 " + accessTimeText(item.created_at)
            + (item.client_ip ? " · " + item.client_ip : "");
        main.appendChild(name);
        main.appendChild(meta);

        var actions = document.createElement("div");
        actions.className = "access-actions";
        var ignore = document.createElement("button");
        ignore.type = "button";
        ignore.className = "access-ignore";
        ignore.textContent = "忽略";
        ignore.addEventListener("click", function () {
            ignore.disabled = true;
            fetch("/api/app/access/requests/" + encodeURIComponent(item.id), {
                method: "DELETE",
                cache: "no-store"
            }).then(function () {
                loadAccessRequests();
            }).catch(function () {
                ignore.disabled = false;
                setStatus("忽略设备请求失败，请重试", false);
            });
        });
        var allow = document.createElement("button");
        allow.type = "button";
        allow.className = "access-allow";
        allow.textContent = "允许";
        allow.addEventListener("click", function () {
            allow.disabled = true;
            ignore.disabled = true;
            fetch("/api/app/access/requests/" + encodeURIComponent(item.id) + "/approve", {
                method: "POST",
                cache: "no-store"
            }).then(function (res) {
                return res.json().catch(function () { return {}; }).then(function (data) {
                    if (!res.ok) { throw new Error(accessDeviceErrorText(data, res.status)); }
                    setStatus("已允许「" + (item.name || "未命名设备") + "」连接", true);
                    loadAccessRequests();
                    setTimeout(loadAccessDevices, 1800);
                });
            }).catch(function (err) {
                allow.disabled = false;
                ignore.disabled = false;
                setStatus("允许设备失败：" + ((err && err.message) || "未知错误"), false);
            });
        });
        actions.appendChild(ignore);
        actions.appendChild(allow);
        row.appendChild(main);
        row.appendChild(actions);
        box.appendChild(row);
    });
}

function loadAccessRequests() {
    fetch("/api/app/access/requests", { cache: "no-store" })
        .then(function (res) {
            return res.json().catch(function () { return {}; }).then(function (data) {
                if (!res.ok) {
                    renderAccessRequests([], res.status === 403
                        ? "仅电脑本机可以确认新设备"
                        : accessDeviceErrorText(data, res.status));
                    return;
                }
                renderAccessRequests(data.requests || []);
            });
        })
        .catch(function () {
            renderAccessRequests([], "读取新设备请求失败，正在自动重试");
        });
}

function refreshAccessOverview() {
    loadAccessRequests();
    loadAccessDevices();
}

function bindAccessDevices() {
    var refresh = document.getElementById("accessDevicesRefresh");
    if (refresh) { refresh.addEventListener("click", refreshAccessOverview); }
    refreshAccessOverview();
    if (ACCESS_POLL_TIMER) { clearInterval(ACCESS_POLL_TIMER); }
    ACCESS_POLL_TIMER = setInterval(function () {
        if (!document.hidden) { loadAccessRequests(); }
    }, 2000);
}

bindAccessDevices();

function refreshMemoryStatus() {
    if (typeof window.fetch !== "function") { return; }
    window.fetch("/api/memory/status", { cache: "no-store" })
        .then(function (res) { return res.ok ? res.json() : null; })
        .then(function (data) {
            var state = document.getElementById("memoryState");
            var count = document.getElementById("memoryCount");
            var ctx = document.getElementById("memoryContext");
            var queue = document.getElementById("memoryQueue");
            var supervisor = document.getElementById("memorySupervisor");
            var path = document.getElementById("memoryPath");
            if (!data) {
                if (state) { state.textContent = "读取失败（服务端未响应）"; }
                return;
            }
            if (state) {
                state.textContent = data.enabled
                    ? (data.ready
                        ? "已启用 · 就绪"
                        : ("已启用 · 未就绪：" + (data.error || "初始化失败")))
                    : "未启用（MEMORY_ENABLED=off）";
            }
            if (count) {
                count.textContent = data.enabled
                    ? ((data.memory_count || 0) + " 条（待确认 "
                        + (data.pending_memory_count || 0) + "）")
                    : "—";
            }
            if (ctx) {
                ctx.textContent = data.enabled
                    ? ((data.context_enabled ? "开" : "关")
                        + " · Tier 1 " + (data.fast_memory ? "开" : "关")
                        + " · 上游历史 " + (data.upstream_history || "auto"))
                    : "—";
            }
            if (queue) {
                if (!data.enabled) {
                    queue.textContent = "—";
                } else {
                    var jobs = data.jobs || {};
                    queue.textContent = (jobs.queued || 0) + " 排队 · "
                        + (jobs.running || 0) + " 运行 · "
                        + (jobs.failed || 0) + " 失败"
                        + (data.auto_extract ? "" : " · 自动提取关");
                }
            }
            if (supervisor) {
                var sup = data.supervisor || {};
                if (!sup.ready) {
                    supervisor.textContent = data.enabled ? "未就绪" : "—";
                } else {
                    var policy = sup.policy || {};
                    supervisor.textContent = (sup.enabled ? "已开启" : "已关闭")
                        + " · 待处理建议 " + (sup.open_findings || 0)
                        + (policy.scan_interval_seconds ? (" · 每 " + policy.scan_interval_seconds + " 秒") : "");
                }
            }
            if (path) { path.textContent = data.path || "—"; }
            setSummary("sum-memory", [
                data.enabled ? "已启用" : "未启用",
                data.enabled ? (data.context_enabled ? "上下文开" : "上下文关") : ""
            ], "—");
        })
        .catch(function () {
            var state = document.getElementById("memoryState");
            if (state) { state.textContent = "读取失败（网络错误）"; }
        });
}

function renderTtsSummary(data, options) {
    var box = document.getElementById("ttsState");
    var snap = options || TTS_OPT;
    if (!snap) {
        if (box) { box.textContent = "正在读取朗读设置..."; }
        return;
    }
    var cloud = snap.cloud || {};
    var runtime = snap.runtime || {};
    var chain = (runtime.chain_order && runtime.chain_order.length)
        ? runtime.chain_order.join(" → ")
        : "";
    var active = runtime.active_provider || "";

    var parts = [];
    if (runtime.ready) {
        parts.push("云端 TTS 已配置：" + (cloud.host || cloud.endpoint || "")
            + "（模型 " + (cloud.model || "未填") + "）。");
    } else if (cloud.configured) {
        parts.push("云端 TTS 已配置，当前朗读方式使用设备语音。");
    } else {
        parts.push("使用设备语音。");
    }
    if (chain) { parts.push("链路：" + chain + (active ? "；当前生效：" + active : "") + "。"); }
    if (cloud.hint) { parts.push(cloud.hint); }
    if (box) {
        box.textContent = parts.join(" ");
        box.className = "note-line" + (runtime.ready ? " ok" : "");
    }
}

// ---------- 可达性 ----------
// .card-head / .nav-item 都是 div + onclick：鼠标点得到，Tab 走不到、回车也没反应。
// 键盘路径统一走 node.click()，与鼠标共用同一个 onclick，不另写一套逻辑免得走岔。
function bindActivateKey(node) {
    node.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar") { return; }
        event.preventDefault();      // 空格默认会滚动页面
        node.click();
    });
}

function markUnavailableSettings() {
    const sections = ["sec-ingest"];
    sections.forEach(function (id) {
        const card = document.getElementById(id);
        if (!card || card.getAttribute("data-unavailable") === "true") { return; }
        card.setAttribute("data-unavailable", "true");
        card.querySelectorAll("input, select, button").forEach(function (node) {
            node.disabled = true;
        });
        const head = card.querySelector(".card-head");
        const note = document.createElement("p");
        note.className = "hint";
        note.textContent = "当前版本尚未接入此功能；以下字段只读保留，不会写入运行配置。";
        if (head && head.parentNode) { head.insertAdjacentElement("afterend", note); }
    });
    ["paths_lightrag_home", "app_skill_routing", "vector_store_qdrant_url",
        "ocr_device", "ocr_local_model_dir"].forEach(function (id) {
        const node = document.getElementById(id);
        if (node) { node.disabled = true; }
    });
}

// <label> 既没有 for、也没把控件包在里面，输入框/下拉因此没有可访问名（读屏只念"编辑框"）。
// 从同一行的 <label> 文本补 aria-label；已经写了更具体说明的不覆盖。
function labelFields() {
    document.querySelectorAll(".row label").forEach(function (label) {
        const row = label.parentNode;
        const field = row ? row.querySelector("input,select") : null;
        if (!field) { return; }
        const text = label.textContent.trim();
        if (!text) { return; }
        if (!field.hasAttribute("aria-label")) { field.setAttribute("aria-label", text); }
        // 确认框里要认得出是哪一组的字段（"模型名称"在① ② ③ 三组里都有）
        const card = row.closest(".card");
        const head = card ? card.querySelector(".card-head h2") : null;
        const group = head ? head.textContent.replace(/（.*?）/g, "").trim() : "";
        if (field.id) { FIELD_LABELS[field.id] = (group ? group + " · " : "") + text; }
    });
}

// 折叠状态只有卡片上的 .collapsed 一个真相源，这里把它同步到 aria-expanded，
// 让读屏用户也知道这一组现在是展开还是收起。
function syncExpanded() {
    SECTIONS.forEach(function (item) {
        const card = document.getElementById(item.id);
        if (!card) { return; }
        const head = card.querySelector(".card-head");
        if (head) {
            head.setAttribute("aria-expanded", card.classList.contains("collapsed") ? "false" : "true");
        }
    });
    document.querySelectorAll(".nav-item").forEach(function (node) {
        const card = document.getElementById(node.getAttribute("data-target") || "");
        node.setAttribute("aria-expanded", (card && !card.classList.contains("collapsed")) ? "true" : "false");
    });
}

function initA11y() {
    document.querySelectorAll(".card-head").forEach(function (head) {
        head.setAttribute("role", "button");
        head.setAttribute("tabindex", "0");
        head.setAttribute("aria-expanded", "true");
        bindActivateKey(head);
    });
    labelFields();
    syncExpanded();
}

function initLayout() {
    initShellControls();
    initSettingsProfile();
    markUnavailableSettings();
    renderRouteOverview();
    renderDeploymentReadiness();
    buildNav();
    applyCollapsed();
    initScrollSpy();
    initQuickActions();
    initA11y();
    initDirtyTracking();
    initSecretClear();
    renderActionBar();
}

initLayout();

