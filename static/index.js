
const $ = function (id) { return document.getElementById(id); };
const chatArea = $("chatArea");
const messages = $("messages");
const input = $("input");
const sendBtn = $("send");
let busy = false;
// send() 现在要先 await 服务端会话（可能有一次网络往返），
// 用它挡住"准备期间再次点击/回车"造成的重复发送。
let sendInFlight = false;
// 同一时刻只允许一个“新建会话”网络请求：initSessions 与用户发送可能同时到达，
// 没有它就会各建一个空会话（甚至把同一句话写进两个会话）。
let sessionCreatePromise = null;
let controller = null;
let requestStartedAt = 0;   // 本轮提问的起始时间：思考框「用时 N 秒」与秒表都按它计算
/* 当前进程生效的回答生成方式（来自 /api/settings 的 app_effective，不是"已保存待重启"的值）。
   "" / "upstream" / "local"；未知时不显示任何本机提示，宁可不提示也不误报。 */
let GEN_MODE = "";
let GEN_MODEL = "";

/* 全局瞬时反馈 —— 实现已换成 static/ui.js 的 RAGKB.toast（居中浮层 + 图标 + 自动消失）。
   这里保留 setToast 这个薄壳，是因为全页有 39 处调用，而"把文案丢给提示栏"的语义没变。
   换掉的旧实现有三个实际问题：文案写在顶栏的一个 span 里（无图标、不自动消失，
   手机上还会被 text-overflow:ellipsis 截断），"复制成功 / 查询出错 / 上传失败"全靠它。 */
function setToast(text, kind) {
  if (window.RAGKB && window.RAGKB.toast) { window.RAGKB.toast(text, kind); return; }
  // 兜底：ui.js 没加载成功时不要静默丢掉反馈
  if (text) { console.info("[toast]", kind || "", text); }
}

/* ---------- 滚动跟随策略 ----------
   原来每片都硬跳到底部（chatArea.scrollTop = scrollHeight），而且 .chat 上还挂着
   scroll-behavior:smooth，两者互相打架：要么追不上、要么把人拽走。
   现在改为：只有「用户本来就在底部附近」时才自动跟随；用户上滑看历史时不抢滚动，
   改为浮出「回到底部」。
   注意：吸顶**不再**关闭跟随。锚点钉在留白处靠的是 CSS sticky，跟随回答靠的是这里的
   stickToBottom —— 两者互不干扰：列表继续往下滚、提问被 sticky 按住不动，最新文字始终可见。 */
const FOLLOW_SLACK = 90;
let stickToBottom = true;
let jumpSmoothUntil = 0;     // 点「回到底部」后的平滑滚动窗口：期间不依据滚动位置改跟随状态
let programmaticTop = -999;  // 我们自己设过的 scrollTop（用来区分"程序滚动"与"用户上滑"）

/* 阅读栏高度落定前（开场块收起时它只有几十 px）滚动几何全是假的 ——
   实测：此时算出来的 A 会被夹到最小值、contentBottom 虚高，跟着滚一下就会被浏览器夹回去。 */
function paneUsable() {
  return chatArea.clientHeight >= 120;
}

// 程序化滚动统一走它：既真的滚，又记下"这一下是我们自己干的"
function setScrollTop(value) {
  programmaticTop = value;
  chatArea.scrollTop = value;
}

/* 锚点状态（详见下面「当前提问：落位锚点」一节）。
   放在这里是因为 realContentHeight() 要用到补白高度，而它先于那一节被定义。 */
let pinnedQuestion = null;   // 哪一条是本轮的提问（.is-current，纯语义标记、不带样式）
let pinSettling = false;     // 落位动画进行中（抑制跟随，避免与动画打架）
let pinSettled = false;      // 本轮落位已结束 / 被用户打断 → 此后不再自动动 scrollTop
let pinAnchor = 0;           // A：落位目标，提问气泡顶离阅读栏顶的像素
let pinWantScroll = 0;       // 本轮落位动画的目标 scrollTop（measurePinGeometry 一次算出）
let pinTopPad = 0;           // #messages 的顶部留白（提问上方不够 A 时补）
let pinPadHeight = 0;        // #pinSpace 的底部补白高度（我们自己设的，不回读 DOM）
let pinSettleTimer = 0;      // 落位动画的 rAF 句柄
let pinSettleRetries = 0;    // 落位/补白的补救次数（布局没跟上时最多再试几次，见 maintainPinSpace）
let pinPending = false;      // 本轮还没量几何（阅读栏高度未落定）→ 滴流期间先别碰版面

/* 「真实内容」相关的三把尺子（吸顶脚手架不算，它只是撑杆，不该被滚进视野）：
   · realContentHeight() —— 真实内容高（含顶部留白、不含底部补白）
   · contentBottom()     —— 内容真正能滚到的最后一屏：内容底正好贴着阅读栏底
   · realScrollRange()   —— 真实内容还能滚多少（0 = 没得滚） */
function realContentHeight() {
  return Math.max(0, messages.scrollHeight - pinPadHeight);
}

function contentBottom() {
  return Math.max(0, panePadTop() + realContentHeight() - chatArea.clientHeight);
}

function realScrollRange() {
  return contentBottom();
}

function nearBottom() {
  // 阅读栏还没高度时（开场块正收起）一切都没意义：当作"就在底部"，别误判成用户上滑
  if (!paneUsable()) { return true; }
  return (contentBottom() - chatArea.scrollTop) <= FOLLOW_SLACK;
}

function updateJumpButton() {
  const btn = $("jumpBottom");
  if (!btn) { return; }
  if (!paneUsable()) { return; }   // 布局没落定前不动它，免得按钮乱闪
  // 用真实内容判「能不能滚」：吸顶补白撑出来的假范围不算（否则按钮指向一段空气）
  btn.hidden = realScrollRange() <= 4 || stickToBottom;
}

/* 跟进一步：贴向真实内容底（吸顶补白只是脚手架，不该被滚进视野）。
   小间隙直接贴死（每帧增长只有几个 px，看不出跳）；大间隙分帧追，
   避免「落位刚结束、回答已经窜出一大截」那一下猛跳。 */
function followStep() {
  if (!paneUsable()) { return; }
  const gap = contentBottom() - chatArea.scrollTop;
  if (gap <= 0) { return; }
  setScrollTop(chatArea.scrollTop + (gap <= 48 ? gap : gap * 0.4));
}

// force = true：用户主动产生的内容（发问、新建/切换会话）→ 无条件回到底部
function scrollBottom(force) {
  if (force) { stickToBottom = true; }
  // 定位动画进行中：让位给动画（动画自己会把提问送到锚点）
  if (pinSettling) { updateJumpButton(); return; }
  if (!stickToBottom) { updateJumpButton(); return; }
  if (!paneUsable()) { updateJumpButton(); return; }
  if (force) {
    const bottom = contentBottom();
    if (bottom > 0 && Math.abs(bottom - chatArea.scrollTop) > 0.5) { setScrollTop(bottom); }
  } else {
    followStep();
  }
  updateJumpButton();
}

// 程序化滚动同样会触发 scroll：只有**真正由用户**滚动才据此改跟随状态
chatArea.addEventListener("scroll", function () {
  const top = chatArea.scrollTop;
  // 落位动画 / 「回到底部」平滑滚动 / 我们自己设的 scrollTop（跟随、位置修复）都不算用户操作，
  // 否则一次自动跟随就会被当成"用户上滑"
  if (pinSettling || Date.now() < jumpSmoothUntil || Math.abs(top - programmaticTop) < 2) {
    updateJumpButton();
    return;
  }
  stickToBottom = nearBottom();
  // 用户主动上滑 = 别抢滚动：落位动画还没跑完就立刻停掉（此后本轮不再自动动 scrollTop）
  if (!stickToBottom && pinnedQuestion && !pinSettled) { abortAnchorSettle(); }
  updateJumpButton();
}, { passive: true });

// 用户在落位动画期间动手（滚轮 / 触摸）→ 立刻让步，不跟人抢滚动
chatArea.addEventListener("wheel", function () {
  if (!pinSettling) { return; }
  abortAnchorSettle();
  stickToBottom = nearBottom();
  updateJumpButton();
}, { passive: true });

/* 流式生成期间：用户在聊天区里按下鼠标 / 手指点正文 = 他要看当前位置，别再自动跟随。
   生成照常继续，画面停在当前帧；底部浮出「回到底部」，点它恢复跟随。
   排除交互元素（按钮 / 链接 / 输入框 / 折叠块 / 编辑面板），否则点「复制」「朗读」
   也会被当成"暂停"。只在 busy（正在生成）时生效 —— 空闲时点一下不该改变跟随状态。 */
function isChatControl(target) {
  return Boolean(target && target.closest
    && target.closest("button, a, input, textarea, select, summary, label, [role='button'], "
      + ".msg-actions, .edit-panel, .review-panel"));
}

function pauseFollowOnPress(event) {
  if (!busy) { return; }
  if (isChatControl(event && event.target)) { return; }
  if (pinSettling) { abortAnchorSettle(); }
  if (stickToBottom) {
    stickToBottom = false;
    updateJumpButton();
  }
}

chatArea.addEventListener("mousedown", pauseFollowOnPress, { passive: true });
chatArea.addEventListener("touchstart", pauseFollowOnPress, { passive: true });

/* ---------- 当前提问：落位锚点（只滚一次，之后整条交给跟随）----------
   目标行为：
     1) 回车后把这一条提问送到阅读栏**上部留白** A 处（不贴边、也不沉底），
        历史顺势**上移出屏**（420ms 过渡动画，不是瞬移）；
     2) 落位后回答在它**下方**流式生长：列表跟随真实内容底（最新文字始终可见）；
     3) 提问**不吸附、不浮层** —— 它就是文档流里的一条普通消息：
        下方没被填满时它纹丝不动；填满之后每长一行就把它往上顶一行；
        输出够长时它自然滚出屏幕上缘（这就是"提问随回答一起走"的自然结果）；
     4) 用户上滑看历史 → 不抢滚动（跟随停住），浮出「↓ 回到底部」；
        落位动画还没跑完就被打断 → 立刻停掉，本轮不再自动动 scrollTop；
     5) 流结束不跳动。

   为什么不再用 sticky：sticky 必须配不透明底 + z-index 才能压住滚动内容，
   于是提问变成一层浮层 —— 盖住历史，而且回答再长它也永远赖在 A 不消失，
   提问与回答被割成两块（观感很差）。

   几何模型（阅读栏高度落定后量一次，三个派生值）：
     qY（气泡顶的内容坐标）    = 气泡 rect.top − 阅读栏 rect.top + scrollTop
     A  = clamp(round(0.18 × 阅读栏高), 72, 140)
     顶部留白 needTop       = max(0, A − qY)                     // 提问本就在 A 之上（新会话第一条）
     落位目标 pinWantScroll = max(0, qY + needTop − A)           // 需要往下滚多少（本轮常量）
     底部补白 padBottom(t)   = max(0, pinWantScroll + 阅读栏高 − 阅读栏上下内边距 − 真实内容高(t))
                               └ 只为"落位这一下滚得动"而存在，随回答变长自动收到 0；
                                 它归零的那一刻，正是跟随开始把提问往上顶的那一刻。

   三个位分开表达，避免互相打架（声明在上一节「滚动跟随策略」里）：
     pinnedQuestion —— 哪一条是本轮的提问；pinSettling —— 落位动画进行中；
     pinSettled —— 本轮落位已结束/被打断（此后不再自动动 scrollTop）；
     pinPending —— 本轮几何还没量（阅读栏高度未落定），滴流期间的维护先别碰版面。
*/
// 这一条成为新的"本轮提问"：旧的取消标记，一屏只有一个
function markCurrentQuestion(el) {
  if (pinnedQuestion && pinnedQuestion !== el && pinnedQuestion.classList) {
    pinnedQuestion.classList.remove("is-current");
  }
  if (pinSettleTimer) { cancelAnimationFrame(pinSettleTimer); pinSettleTimer = 0; }
  pinSettling = false;
  pinSettled = false;
  pinnedQuestion = el || null;
  if (pinnedQuestion && pinnedQuestion.classList) { pinnedQuestion.classList.add("is-current"); }
}

// 清空/切换会话时丢弃标记（否则会指向已脱离 DOM 的节点）
function clearCurrentQuestion() {
  if (pinnedQuestion && pinnedQuestion.classList) { pinnedQuestion.classList.remove("is-current"); }
  pinnedQuestion = null;
  pinSettled = false;
  pinSettling = false;
  if (pinSettleTimer) { cancelAnimationFrame(pinSettleTimer); pinSettleTimer = 0; }
}

/* 换会话 / 清空：锚点脚手架（顶部留白 + 底部补白 + 没走完的动画）一起复位 */
function resetPinGeometry() {
  if (pinSettleTimer) { cancelAnimationFrame(pinSettleTimer); pinSettleTimer = 0; }
  pinSettling = false;
  pinPending = false;
  pinSettled = false;
  pinWantScroll = 0;
  pinTopPad = 0;
  pinSettleRetries = 0;
  messages.style.paddingTop = "";
  dropPinSpace();
  pinAnchor = pinAnchorOffset();
}

/* 用户放弃落位（上滑 / 滚轮 / 点「回到底部」）：停掉没跑完的动画，本轮不再自动动 scrollTop。
   提问本来就活在文档流里，不需要"撤掉"什么 —— 它自己会随内容一起滚走。 */
function abortAnchorSettle() {
  if (pinSettleTimer) { cancelAnimationFrame(pinSettleTimer); pinSettleTimer = 0; }
  pinSettling = false;
  pinSettled = true;
}

/* 锚点位置 A：离阅读栏顶约 18% 屏高（舒适的上部留白，不贴死屏幕边也不沉底）。
   阅读栏还没落定时（开场块正收起，它可能只有几十 px）用窗口高度估一个 ——
   直接拿 42px 的阅读栏去算，A 会被夹到最小值 72，锚点就永久偏了（实测踩过）。 */
function pinAnchorOffset() {
  const pane = chatArea.clientHeight;
  const base = pane >= 120 ? pane : Math.round(window.innerHeight * 0.75);
  return Math.round(Math.min(140, Math.max(72, base * 0.18)));
}

function panePadTop() { return parseFloat(window.getComputedStyle(chatArea).paddingTop) || 0; }

function panePadBottom() { return parseFloat(window.getComputedStyle(chatArea).paddingBottom) || 0; }

// 版面能滚到的绝对末位（含补白）：落位动画的硬上限
function maxScroll() { return Math.max(0, chatArea.scrollHeight - chatArea.clientHeight); }


/* 顶部留白：提问上方本来就不够 A 时（新会话第一条）补 #messages 的 padding-top。
   用 padding 而不是 spacer 节点：padding 不参与 flex gap，第一条消息上方不会多出一个 gap 的距离。 */
function ensurePinTop() {
  messages.style.paddingTop = pinTopPad ? (pinTopPad + "px") : "";
}

/* 底部补白 #pinSpace：**只为**让"锚点够得着"而存在（发送那一刻提问下方还是空的，
   页面滚不动、提问只能停在底部 —— 这正是"看起来毫无反应"的根因）。
   高度直接按滚动方程反解（含阅读栏自身的上下内边距），随回答变长自动收到 0，
   不留常驻几百 px 的幽灵空白。 */
function ensurePinSpace() {
  // 只有真的要往下滚（want > 0）才需要补白：want = 0 时锚点靠顶部留白/sticky 就够了，
  // 补白只会凭空多出一截可滚的空白（实测第一句就多垫了 495px）
  const short = pinWantScroll > 0
    ? pinWantScroll + chatArea.clientHeight - panePadTop() - panePadBottom() - realContentHeight()
    : 0;
  const need = Math.max(0, Math.ceil(short));
  pinPadHeight = need;
  let pad = document.getElementById("pinSpace");
  if (!need) {
    if (pad && pad.parentNode) { pad.parentNode.removeChild(pad); }
    return;
  }
  if (!pad) {
    pad = document.createElement("div");
    pad.id = "pinSpace";
    pad.setAttribute("aria-hidden", "true");
    pad.style.flex = "0 0 auto";
  }
  if (pad.parentNode !== messages || messages.lastElementChild !== pad) {
    messages.appendChild(pad);           // 永远垫在列表最后
  }
  const h = need + "px";
  if (pad.style.height !== h) { pad.style.height = h; }
}

function dropPinSpace() {
  pinPadHeight = 0;
  const pad = document.getElementById("pinSpace");
  if (pad && pad.parentNode) { pad.parentNode.removeChild(pad); }
}

/* 量一次锚点几何（发送后等布局落定再调 / 窗口尺寸变化后调）：一次测量，三个派生值。
   量的时候提问还在锚点**下方**（毫无吸附），拿到的 rect 就是干净的版面位置。 */
function measurePinGeometry() {
  pinPending = false;          // 本轮几何已量：滴流期间的维护从这里开始接管
  pinAnchor = pinAnchorOffset();
  if (!pinnedQuestion || !pinnedQuestion.isConnected) {
    pinWantScroll = 0;
    pinTopPad = 0;
    ensurePinTop();
    ensurePinSpace();
    return;
  }
  const bubble = pinnedQuestion.querySelector(".bubble") || pinnedQuestion;
  const measured = bubble.getBoundingClientRect().top - chatArea.getBoundingClientRect().top + chatArea.scrollTop;
  const baseY = measured - pinTopPad;                        // 扣掉上次留下的顶部留白 → 干净基准
  pinTopPad = Math.max(0, Math.round(pinAnchor - baseY));    // 提问本就在 A 之上 → 补顶部留白
  const anchoredY = baseY + pinTopPad;
  pinWantScroll = Math.max(0, Math.round(anchoredY - pinAnchor));   // 还要往下滚多少（本轮常量）
  ensurePinTop();
  ensurePinSpace();
}

/* 发送后**等阅读栏高度落定**再落锚。
   为什么：开场块收起是 0.5s 过渡，这期间阅读栏高度会从几十 px 一路涨到最终值
   （实测 42 → 418 → 636 → 703 → 719），太早量出来的 A 会被夹到最小值 72 并**永久偏着**，
   而且此刻的滚动几何（可滚范围 / 内容底）全是假的。
   判据：连续 3 帧高度不变（≈50ms），最长等 1.2s 兜底。 */
let pinWaitStart = 0;
let pinWaitLastH = -1;
let pinWaitStable = 0;
function waitLayoutThenPin() {
  if (!pinnedQuestion || !pinnedQuestion.isConnected || pinSettled) { pinPending = false; return; }
  const now = performance.now();
  if (!pinWaitStart) { pinWaitStart = now; }
  const h = chatArea.clientHeight;
  if (h > 0 && h === pinWaitLastH) { pinWaitStable += 1; } else { pinWaitStable = 0; }
  pinWaitLastH = h;
  if ((h >= 120 && pinWaitStable >= 2) || (now - pinWaitStart) > 1200) {
    pinWaitStart = 0;
    pinWaitLastH = -1;
    pinWaitStable = 0;
    measurePinGeometry();
    settlePin();
    return;
  }
  requestAnimationFrame(waitLayoutThenPin);
}

/* 把提问滑到锚点：rAF 逐帧插值（easeOutCubic / 420ms）。
   为什么不用 scrollTo({behavior:'smooth'})：节点刚插入、布局还没落定时它会被夹住，直接不动。
   为什么不用"定时器重试"：那会和动画互相覆盖（实测出现过 311 → 22 的反向瞬移）。
   动画期间目标按当前布局重算，但**只允许单调推进** —— 布局变化只会让它提前到位，不会反向跳回去。 */
function settlePin() {
  if (pinSettleTimer) { cancelAnimationFrame(pinSettleTimer); pinSettleTimer = 0; }
  if (pinPending || !pinnedQuestion || !pinnedQuestion.isConnected || pinSettled) {
    pinSettling = false;
    return;
  }
  // 开场块还占着（阅读栏高度为 0）：下一帧再来，不算失败
  if (chatArea.clientHeight < 40) {
    pinSettling = true;
    pinSettleTimer = requestAnimationFrame(settlePin);
    return;
  }
  const capOf = function () {
    return Math.max(0, Math.min(pinWantScroll, maxScroll()));
  };
  const from = chatArea.scrollTop;
  const cap = capOf();
  if (Math.abs(cap - from) < 1.5) {
    pinSettling = false;
    pinSettleRetries = 0;
    pinSettled = true;                            // 已经在锚点上：本轮落位结束，不再自动动 scrollTop
    ensurePinSpace();
    stickToBottom = true;                         // 是我们的动画把人送到锚点的，跟随即刻恢复
    updateJumpButton();
    return;
  }
  // 版面还滚不动（布局未落定）：下一帧重试，最多 3 次，避免字体/开场块收起把这一跳拖掉
  if (cap <= 0 && from <= 0 && pinWantScroll > 0 && pinSettleRetries < 3) {
    pinSettleRetries += 1;
    pinSettling = true;
    pinSettleTimer = requestAnimationFrame(settlePin);
    return;
  }
  const t0 = performance.now();
  const DUR = 420;
  const start = from;
  let furthest = from;
  let done = false;
  pinSettling = true;
  (function step(now) {
    if (done) { return; }
    const p = Math.min(1, (now - t0) / DUR);
    const ease = 1 - Math.pow(1 - p, 3);            // easeOutCubic：快起慢收，像"滑上去"
    const limit = capOf();
    let next = start + (limit - start) * ease;
    if (limit >= start) { next = Math.max(furthest, Math.min(next, limit)); }
    else { next = Math.min(furthest, Math.max(next, limit)); }
    furthest = next;
    chatArea.scrollTop = next;
    if (p < 1) {
      pinSettleTimer = requestAnimationFrame(step);
      return;
    }
    done = true;
    pinSettleTimer = 0;
    pinSettling = false;
    pinSettleRetries = 0;
    pinSettled = true;                               // 本轮落位结束：之后不再自动动 scrollTop
    ensurePinSpace();                                // 定稿一次补白：别多滚出几 px 的空白
    stickToBottom = true;                            // 落位后立刻恢复跟随（最新文字始终可见）
    updateJumpButton();
  })(t0);
}

/* 滴流每帧维护锚点脚手架：补白随回答增长自动收小（收 0 的那一刻，跟随开始把提问往上顶）。 */
function maintainPinSpace() {
  if (pinPending || !pinnedQuestion || !pinnedQuestion.isConnected) { return; }
  ensurePinSpace();
  if (pinSettled || pinSettling) { return; }        // 落位已结束 / 动画进行中：不再动 scrollTop
  // 补白到这一帧才够（例如开场块收起把阅读栏撑大、或布局刚落定）→ 差一点就再滑一次，
  // 次数由 pinSettleRetries 兜住，避免一直空转
  if (pinWantScroll > 0 && Math.abs(chatArea.scrollTop - pinWantScroll) > 2 && pinSettleRetries < 6) {
    pinSettleRetries += 1;
    settlePin();
  }
}

/* 尺寸变化（含手机横竖屏）：断点会改阅读栏高度与 .chat 内边距 → 锚点 A 与派生值都要重算 */
function relayoutPin() {
  const oldAnchor = pinAnchor;
  if (!pinnedQuestion || !pinnedQuestion.isConnected) {
    pinAnchor = pinAnchorOffset();
    updateJumpButton();
    return;
  }
  // 本轮落位早已结束：别去动顶部留白，否则会把用户正读的位置顶走（手机地址栏伸缩会狂发 resize）
  if (pinSettled) { updateJumpButton(); return; }
  measurePinGeometry();
  // 锚点位置几乎没变就不重播动画
  if (Math.abs(pinAnchor - oldAnchor) >= 4) { settlePin(); }
}

// force=false：只在「用户本来就在底部附近」时才跟随
function ensureFollow() {
  if (pinSettling) { updateJumpButton(); return; }
  if (stickToBottom) { scrollBottom(false); }
}

(function bindJumpBottom() {
  const btn = $("jumpBottom");
  if (!btn) { return; }
  btn.addEventListener("click", function () {
    // 规范第 4 条：点「回到底部」= 放弃落位 + 收掉补白（它只会把底部垫高、点不到真正的内容底）
    abortAnchorSettle();
    dropPinSpace();
    stickToBottom = true;
    jumpSmoothUntil = Date.now() + 600;   // 平滑滚动期间不据滚动位置改跟随状态（否则按钮会闪）
    chatArea.scrollTo({ top: contentBottom(), behavior: "smooth" });
    updateJumpButton();
  });
})();

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------- Markdown 表格：识别用的四个小工具 ----------
   模型回答里的表格很常见（资料分析尤其多），但渲染器一直没有表格分支，
   于是 `| 项目 | 标准 |` 只能原样吐竖线 —— CSS 里的 .bubble table 是一段死样式。
   表格的**唯一判据是分隔行**（`| --- | --- |`）：只有"本行含竖线 + 下一行是分隔行"
   才算表格开头，这样正文里偶尔出现的单个竖线不会被误判成表格。 */
function mdSplitRow(text) {
  let body = String(text).trim();
  if (body.charAt(0) === "|") { body = body.slice(1); }
  if (body.charAt(body.length - 1) === "|") { body = body.slice(0, -1); }
  return body.split("|").map(function (cell) { return cell.trim(); });
}

function mdIsTableSeparator(text) {
  const cells = mdSplitRow(text);
  if (!cells.length) { return false; }
  return cells.every(function (cell) { return /^:?-+:?$/.test(cell); });
}

// 「这一行是表格的数据行」——流式提交时用它判断表格有没有结束（见 safeMarkdownBoundary）
function mdIsTableRowLine(text) {
  const line = String(text);
  return /\S/.test(line) && line.indexOf("|") >= 0;
}

function mdIsTableStart(lines, index) {
  return mdIsTableRowLine(lines[index]) && index + 1 < lines.length
    && mdIsTableSeparator(lines[index + 1]);
}

// 分隔行里的 `:---` / `---:` / `:---:` 决定各列对齐方式
function mdAlignOf(text) {
  return mdSplitRow(text).map(function (cell) {
    const left = cell.charAt(0) === ":";
    const right = cell.charAt(cell.length - 1) === ":";
    if (left && right) { return "center"; }
    if (right) { return "right"; }
    return "";
  });
}

// 极简 Markdown：代码块 / 行内代码 / 粗体 / 标题 / 列表 / 引用 / 分隔线 / 链接 / 表格
function renderMarkdown(source) {
  const lines = String(source || "").split("\n");
  let html = "";
  let inCode = false;
  let listType = "";
  let buffer = [];
  function closeList() { if (listType) { html += "</" + listType + ">"; listType = ""; } }
  function inline(text) {
    return escapeHtml(text)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, "<a href=\"$2\" target=\"_blank\" rel=\"noreferrer\">$1</a>");
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      if (!inCode) { closeList(); inCode = true; buffer = []; }
      else { html += "<pre><code>" + escapeHtml(buffer.join("\n")) + "</code></pre>"; inCode = false; }
      continue;
    }
    if (inCode) { buffer.push(line); continue; }
    if (/^\s*$/.test(line)) { closeList(); continue; }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = Math.min(heading[1].length + 2, 5);
      html += "<h" + level + ">" + inline(heading[2]) + "</h" + level + ">";
      continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { closeList(); html += "<hr>"; continue; }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) { closeList(); html += "<blockquote>" + inline(quote[1]) + "</blockquote>"; continue; }
    const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ordered) {
      if (listType !== "ol") { closeList(); html += "<ol>"; listType = "ol"; }
      html += "<li>" + inline(ordered[1]) + "</li>";
      continue;
    }
    const bullet = line.match(/^\s*[-*+]\s+(.*)$/);
    if (bullet) {
      if (listType !== "ul") { closeList(); html += "<ul>"; listType = "ul"; }
      html += "<li>" + inline(bullet[1]) + "</li>";
      continue;
    }
    if (mdIsTableStart(lines, i)) {
      closeList();
      const align = mdAlignOf(lines[i + 1]);
      // 一行一个 <tr>；单元格用 inline() 走同一套转义与行内语法
      const rowHtml = function (cells, tag) {
        let row = "<tr>";
        for (let c = 0; c < cells.length; c++) {
          const style = (align[c] && align[c] !== "left") ? " style=\"text-align:" + align[c] + "\"" : "";
          row += "<" + tag + style + ">" + inline(cells[c]) + "</" + tag + ">";
        }
        return row + "</tr>";
      };
      let body = "";
      let j = i + 2;
      for (; j < lines.length; j++) {
        if (!mdIsTableRowLine(lines[j])) { break; }
        body += rowHtml(mdSplitRow(lines[j]), "td");
      }
      // 外层套一个可横向滚动的壳：宽表格在 760px 阅读栏里不至于把整页撑破
      html += "<div class=\"md-table\"><table><thead>" + rowHtml(mdSplitRow(line), "th")
        + "</thead><tbody>" + body + "</tbody></table></div>";
      i = j - 1;                                  // 跳过已吃掉的数据行
      continue;
    }
    closeList();
    html += "<div>" + inline(line) + "</div>";
  }
  if (inCode) { html += "<pre><code>" + escapeHtml(buffer.join("\n")) + "</code></pre>"; }
  closeList();
  return html;
}

// 把 <think>…</think>（含流式未闭合的情况）拆出来单独折叠
function splitThink(source) {
  const text = String(source || "");
  const open = text.indexOf("<think>");
  if (open < 0) { return { answer: text, think: "", thinking: false }; }
  const rest = text.slice(open + 7);
  const close = rest.indexOf("</think>");
  if (close < 0) { return { answer: text.slice(0, open), think: rest, thinking: true }; }
  const think = rest.slice(0, close);
  const answer = (text.slice(0, open) + rest.slice(close + 8)).trim();
  return { answer: answer, think: think.trim(), thinking: false };
}
function addMessage(text, role) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + role;
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = (role === "user") ? "我" : "AI";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const content = document.createElement("div");
  if (role === "user") { content.textContent = text; } else { content.innerHTML = renderMarkdown(text); }
  bubble.appendChild(content);
  if (role === "user") {
    // 悬停显示的「复制消息」：绝对定位挂在气泡左侧，不占布局（手机上常显在气泡内右下角）
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "msg-copy";
    copy.title = "复制这条消息";
    copy.innerHTML = '<span class="mc-ico">' + COPY_SVG + '</span><span>复制消息</span>';
    copy.addEventListener("click", function (event) {
      event.stopPropagation();
      copyToClipboard(text, "已复制消息");
    });
    bubble.appendChild(copy);
  }
  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  messages.appendChild(wrap);
  if (role === "user") {
    markCurrentQuestion(wrap);   // 新提问接过吸顶；发送瞬间不在这里滚，由 send() 统一钉到顶部
  } else {
    // AI 历史消息：若当时没人吸顶，则让"最后一条提问"接手，保证刷新后最新的问答仍是吸顶的
    if (!pinnedQuestion) {
      const users = messages.querySelectorAll(".msg.user");
      if (users.length) { markCurrentQuestion(users[users.length - 1]); }
    }
    scrollBottom(true);          // 历史里的回答是静态内容，直接回到底部
  }
  return { wrap: wrap, bubble: bubble, content: content };
}

// 发送后立刻出现的「思考中」占位：**直接复用回答视图的结构**。
// 这样第一个字到达时不需要换掉 DOM 节点（旧实现是「先一个思考气泡，来了字再删掉换一个回答气泡」，
// 视觉上会闪一下），思考框就此沿用下去，保持同一套即时反馈。
function addTyping(startedAt) {
  const view = createAnswer(startedAt);
  view.__waiting = true;
  const box = createThinkBox(view.__began);
  view.thinkBox = box;
  view.thinkHost.appendChild(box);
  startThinkTimer(box);
  // 本机模式先把预期写在等待框里：首字 60–90 秒是实测值，不是"卡住了"。
  if (GEN_MODE === "local") {
    box.__titleLive.textContent = "本机模型生成中…";
  }
  // 20 秒还没等到第一个字：标题换成「仍在处理，请稍候…」（原行为保留）；
  // 本机模式给出可核对的时间预期，避免用户以为服务无响应。
  view.__waitTimer = window.setInterval(function () {
    if (!view.__waiting) { return; }
    if (Math.floor((Date.now() - view.__began) / 1000) >= 20) {
      box.__titleLive.textContent = (GEN_MODE === "local")
        ? "本机模型生成中，首字通常需 60–90 秒…"
        : "仍在处理，请稍候…";
    }
  }, 1000);
  return view;
}

// 第一个字到了 / 请求提前失败：停止「仍在处理」提示并清理定时器
function stopWaitTimer(view) {
  if (!view) { return; }
  view.__waiting = false;
  if (view.__waitTimer) { window.clearInterval(view.__waitTimer); view.__waitTimer = null; }
}

// ---------- 思考过程框：思考时展开，正文开始流式输出时自然收起 ----------
const thinkTimers = new Set();

function clearThinkTimer(box) {
  if (!box) { return; }
  if (box.__timer) { window.clearInterval(box.__timer); box.__timer = null; }
  thinkTimers.delete(box);
}

// 思考计时：每秒把秒数写进标题右侧（让用户确认系统仍在工作）
function startThinkTimer(box) {
  if (!box || box.__timer) { return; }
  thinkTimers.add(box);
  box.__timer = window.setInterval(function () {
    const s = Math.max(1, Math.round((Date.now() - box.__began) / 1000));
    box.__secs.textContent = s + "s";
  }, 1000);
}

// 切换会话 / 清空对话时兜底清理，避免遗留计时器
function clearAllThinkTimers() {
  thinkTimers.forEach(function (box) {
    if (box.__timer) { window.clearInterval(box.__timer); box.__timer = null; }
  });
  thinkTimers.clear();
}

function setThinkOpen(box, open) {
  box.setAttribute("data-open", open ? "1" : "0");
  if (box.__head) { box.__head.setAttribute("aria-expanded", open ? "true" : "false"); }
}

function createThinkBox(began) {
  const box = document.createElement("div");
  box.className = "think-box";
  box.setAttribute("data-state", "thinking");
  box.setAttribute("data-open", "0");     // 等待期只露一行标题；真的有思考内容了再展开
  const head = document.createElement("button");
  head.type = "button";
  head.className = "think-head";
  head.setAttribute("aria-expanded", "false");
  const ico = document.createElement("span");
  ico.className = "think-ico";
  const title = document.createElement("span");
  title.className = "think-title";
  const titleLive = document.createElement("span");
  titleLive.className = "tt tt-live";
  titleLive.textContent = "思考中…";
  const titleDone = document.createElement("span");
  titleDone.className = "tt tt-done";
  titleDone.textContent = "已完成思考";
  title.appendChild(titleLive);
  title.appendChild(titleDone);
  const secs = document.createElement("span");
  secs.className = "think-secs";
  secs.textContent = "0s";
  const chev = document.createElement("span");
  chev.className = "think-chev";
  chev.textContent = "▾";
  head.appendChild(ico);
  head.appendChild(title);
  head.appendChild(secs);
  head.appendChild(chev);
  const wrap = document.createElement("div");
  wrap.className = "think-wrap";
  const inner = document.createElement("div");
  const body = document.createElement("div");
  body.className = "think-body";
  inner.appendChild(body);
  wrap.appendChild(inner);
  box.appendChild(head);
  box.appendChild(wrap);
  box.__head = head;
  box.__body = body;
  box.__title = title;
  box.__titleLive = titleLive;
  box.__titleDone = titleDone;
  box.__secs = secs;
  box.__began = began || Date.now();
  box.__timer = null;
  box.__userToggled = false;
  head.title = "展开 / 收起思考过程";
  // 手动展开 / 收起后不再自动收起（尊重用户：正在读的内容不该被拿走）
  head.addEventListener("click", function () {
    box.__userToggled = true;
    setThinkOpen(box, box.getAttribute("data-open") !== "1");
  });
  return box;
}

// 思考框状态同步（滴流每帧都会调用，但只在"真的变化"时才动 DOM）：
//   · 等待期只露一行标题（收起的）→ 思考内容一到就自动展开、边想边显示
//   · 思考结束 → 标记 done：标题交叉淡入成「已完成思考（用时 N 秒）」、计时淡出、自动收起，把版面让给正文
//   · 用户手动展开 / 收起过 → 之后不再自动收起（尊重用户正在读的内容）
//   · 全程没有思考内容（模型没输出 <think>）→ 把等待期的空壳撤掉，不留一行"思考中…"挂在正文上方
function syncThink(view, think, thinking) {
  let box = view.thinkBox;
  if (!box) {
    if (!think && !thinking) { return; }
    box = createThinkBox(view.__began);
    view.thinkBox = box;
    view.thinkHost.appendChild(box);
  }
  if (!think && !String(box.__body.textContent || "").trim()) {
    clearThinkTimer(box);
    box.remove();
    view.thinkBox = null;
    return;
  }
  if (box.__body.textContent !== think) {
    const nearBottom = (box.__body.scrollTop || 0) + (box.__body.clientHeight || 0) + 24 >= (box.__body.scrollHeight || 0);
    box.__body.textContent = think;
    if (nearBottom) { box.__body.scrollTop = box.__body.scrollHeight || 0; }
  }
  const state = thinking ? "thinking" : "done";
  if (box.getAttribute("data-state") !== state) {
    box.setAttribute("data-state", state);
    if (thinking) {
      box.__titleLive.textContent = "思考中…";
      startThinkTimer(box);
    } else {
      clearThinkTimer(box);
      const used = Math.max(1, Math.round((Date.now() - box.__began) / 1000));
      box.__titleDone.textContent = "已完成思考（用时 " + used + " 秒）";
      if (!box.__userToggled) { setThinkOpen(box, false); }   // 自然收起，把版面让给正文
    }
  }
  // 有思考内容就展开（等待期是收起的；用户手动收起过则不动）
  if (thinking && think && !box.__userToggled && box.getAttribute("data-open") !== "1") {
    setThinkOpen(box, true);
  }
}

function createAnswer(startedAt) {
  const wrap = document.createElement("div");
  wrap.className = "msg ai";
  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = "AI";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  const content = document.createElement("div");
  // 结构固定为「思考框容器 + 正文」：正文开始流式时思考框原地收起，不会跳动
  const thinkHost = document.createElement("div");
  thinkHost.className = "think-host";
  const answerBody = document.createElement("div");
  answerBody.className = "answer-body";
  content.appendChild(thinkHost);
  content.appendChild(answerBody);
  bubble.appendChild(content);
  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  messages.appendChild(wrap);
  // 回答起始：**不再无条件贴底**。用户主动发问时 send() 已把问题钉到顶部，
  // 这里若强行贴底会把刚吸顶的问题顶走；只有"本来就在底部"时才跟随。
  ensureFollow();
  return {
    wrap: wrap, bubble: bubble, content: content,
    thinkHost: thinkHost, answerBody: answerBody, thinkBox: null,
    __began: startedAt || Date.now(), __answerText: null,
    // 流式滴流状态（见「流式渲染」一节）：到达与显示解耦
    __rawText: "", __shownLen: 0, __streamDone: false, __finalized: false,
    __references: [], __persist: true,
    // 本机已输出后失败 → 可手动换上游重试；重试结果另起一条回答。
    __retryUpstream: false, __retryQuestion: "", __upstreamRetry: false,
    __thinkLive: false, __waiting: false, __waitTimer: null,
    blocksEl: null, tailEl: null, tailText: null, cursorEl: null,
    __committed: 0, __painted: null
  };
}

function splitModelReferences(text) {
  // 模型有时会在回答结尾自带 References / 参考资料 清单。
  // 这段清单**不能直接丢掉**：LightRAG 的提示词要求的正是"按文档标题引用、一行一条"，
  // 丢掉它就只能靠猜（实测有的模型既不写清单、也不写 [n]）。
  // 所以拆成 {body, tail}：body 拿去展示，tail 用来判断"模型自称引了哪些文档"。
  const source = String(text || "");
  const lines = source.split("\n");
  const pattern = /^#{0,6}\s*(\*\*)?\s*(references?|参考资料|参考文献)\s*(\*\*)?\s*[:：]?\s*$/i;
  let cut = -1;
  for (let i = 0; i < lines.length; i++) {
    if (pattern.test(lines[i].trim())) { cut = i; }
  }
  if (cut <= 0) { return { body: source.trim(), tail: "" }; }
  return {
    body: lines.slice(0, cut).join("\n").trim(),
    tail: lines.slice(cut + 1).join("\n").trim()
  };
}

function stripModelReferences(text) {
  return splitModelReferences(text).body;
}

// 文档名归一化：去掉扩展名与空白/下划线/连字符/点，便于"正文只写了一半"时也能对上
function normalizeDocName(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/\.[a-z0-9]{1,6}$/i, "")
    .replace(/[\s_\-—–·・.、,，:：;；()\[\]【】]+/g, "");
}

// 归一化前缀键：取主干归一化后的前 DOC_PREFIX_LEN 个字。
// 12 字是实测定的：8 字时「屏幕截图_2026-07-18_…」与「屏幕截图_2026-07-20_…」
// 前 10 个字完全相同 → 会互相误判成"被引用"；12 字才能区分开。
const DOC_PREFIX_LEN = 12;

function docPrefixKey(filePath) {
  const base = String(filePath).split(/[\\/]/).pop() || String(filePath);
  const key = normalizeDocName(base);
  return key.length >= DOC_PREFIX_LEN ? key.slice(0, DOC_PREFIX_LEN) : "";
}

// 文档名匹配（逐级放宽）：
//   1) 出现完整文件名
//   2) 出现去掉扩展名的主干
//   3) 归一化前缀键出现，**且这个键在本批文档里唯一**（不唯一就整条规则弃用，宁可不认也不误认）
//      —— 命中 3) 的典型情形：正文写「个人AI混合增强RAG架构升级方案」，
//         文件名却是「个人AI混合增强RAG架构升级方案_v3.0.txt」，前两档都匹配不到。
function matchesDocument(text, normalizedText, item) {
  if (!text) { return false; }
  const base = String(item.file_path).split(/[\\/]/).pop() || String(item.file_path);
  if (text.indexOf(base) >= 0) { return true; }
  const stem = base.replace(/\.[^.]+$/, "");
  if (stem.length >= 4 && text.indexOf(stem) >= 0) { return true; }
  if (item.prefixUnique && item.prefixKey && normalizedText.indexOf(item.prefixKey) >= 0) { return true; }
  return false;
}


/* ================= 流式渲染：增量提交 + 逐帧滴流 =================
   上游（LightRAG → LLM）是 burst 式的：一次可能到几十上百字，间隔还不均匀，
   所以文字原来会「一批一批蹦出来」。这里把两件事拆开：
     · 到达（__rawText）：随时记下新文字，不做任何渲染；
     · 显示（__shownLen）：由 requestAnimationFrame 每帧只放出少量字符，匀速「渗」出来。
   渲染同时改成增量：已闭合的 Markdown 块追加渲染一次就不再动，
   尚未闭合的尾部按纯文本显示 —— 因此不会出现「`**粗` 先露星号、闭合后突然变粗体」的跳变，
   也不再有「每片整体 innerHTML 重绘」带来的主线程抖动与光标闪烁。 */

// 每帧放出多少字符：积压越多放得越快（既跟得上上游，又不会「一坨」砸到屏幕上）
// fast = 收流后清空积压，收紧尾时间但保留可见的推进感
function dripStep(backlog, fast) {
  if (backlog <= 0) { return 0; }
  const cap = fast ? 14 : 8;
  return Math.max(1, Math.min(cap, Math.ceil(backlog / 24)));
}

// 这一行的内联标记是否都已闭合（未闭合就不提交该行，避免中途变形）
function inlineBalanced(line) {
  const text = String(line || "");
  if ((text.match(/`/g) || []).length % 2 !== 0) { return false; }
  if ((text.match(/\*\*/g) || []).length % 2 !== 0) { return false; }
  const at = text.lastIndexOf("](");
  if (at >= 0 && text.indexOf(")", at) < 0) { return false; }
  return true;
}

// 可以安全提交为 HTML 的字符数：只到「最后一个完整且已闭合的块」为止
function safeMarkdownBoundary(text) {
  const source = String(text || "");
  const lastBreak = source.lastIndexOf("\n");
  if (lastBreak < 0) { return 0; }          // 连一行都没写完
  const lines = source.slice(0, lastBreak + 1).split("\n");
  lines.pop();                              // 去掉末尾空串
  let fenceOpen = false;
  let fenceStart = 0;
  let tableOpen = false;
  let offset = 0;
  let committed = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const lineStart = offset;
    offset += line.length + 1;              // +1 = 换行符
    if (/^\s*```/.test(line)) {
      if (!fenceOpen) { fenceOpen = true; fenceStart = lineStart; }
      else { fenceOpen = false; committed = offset; }
      continue;
    }
    if (fenceOpen) { continue; }
    if (!inlineBalanced(line)) { break; }   // 这一行还没闭合：它和它之后的都留在尾部
    // 表格是逐行到达的。提交到表格中间，下一片就只剩 `| 住宿费 | 500 |` 这样的续行
    // （appendBlocks 是把切片当独立 Markdown 渲染的，没有分隔行就认不出表格），
    // 只能渲染成一段竖线文字。所以整张表要等它结束（出现第一个非表格行）才提交。
    if (tableOpen) {
      if (mdIsTableRowLine(line)) { continue; }
      tableOpen = false;
      committed = offset;                   // 表格结束：连同这一行一起收下
      continue;
    }
    // 末尾那一行含竖线时也不能提交：它可能是表头，而分隔行还没到 ——
    // 提交了它，下一片就从 `| --- |` 开始，"上一行是表头"这个判据已经丢了。
    if (i === lines.length - 1 && mdIsTableRowLine(line)) { break; }
    if (mdIsTableStart(lines, i)) { tableOpen = true; continue; }
    committed = offset;
  }
  if (fenceOpen) { return Math.min(committed, fenceStart); }  // 代码块没写完：只提交到围栏之前
  return committed;
}

// 追加一段 HTML；相邻的同类型列表 / 引用合并，避免同一个列表被切成两段后多出间距
function appendBlocks(container, html) {
  const temp = document.createElement("div");
  temp.innerHTML = html;
  while (temp.firstChild) {
    const node = temp.firstChild;
    const prev = container.lastElementChild;
    const isEl = node.nodeType === 1;
    const sameList = isEl && prev &&
      ((prev.tagName === "UL" && node.tagName === "UL") || (prev.tagName === "OL" && node.tagName === "OL"));
    const sameQuote = isEl && prev && prev.tagName === "BLOCKQUOTE" && node.tagName === "BLOCKQUOTE";
    if (sameList || sameQuote) {
      while (node.firstChild) { prev.appendChild(node.firstChild); }
      temp.removeChild(node);
      continue;
    }
    container.appendChild(node);
  }
}

function ensureAnswerDom(view) {
  if (view.blocksEl) { return view; }
  const blocks = document.createElement("div");
  blocks.className = "answer-blocks";
  const tail = document.createElement("div");
  tail.className = "answer-tail";
  const tailText = document.createTextNode("");   // 尾部的文字用独立文本节点：光标不会被覆盖
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  tail.appendChild(tailText);
  tail.appendChild(cursor);
  view.answerBody.appendChild(blocks);
  view.answerBody.appendChild(tail);
  view.blocksEl = blocks;
  view.tailEl = tail;
  view.tailText = tailText;
  view.cursorEl = cursor;
  view.__committed = 0;
  view.__painted = null;
  return view;
}

// 画「当前应当显示的正文」：只追加新增的已闭合块，尾部按纯文本更新
function paintAnswer(view, text) {
  ensureAnswerDom(view);
  const source = String(text || "");
  // 目标比已画出来的短：说明结尾被裁掉了（模型自抄的 References 清单）→ 重建一次（至多一次）
  if (view.__committed > source.length) {
    view.blocksEl.innerHTML = "";
    view.tailText.nodeValue = "";
    view.__committed = 0;
  }
  const safe = safeMarkdownBoundary(source);
  if (safe > view.__committed) {
    appendBlocks(view.blocksEl, renderMarkdown(source.slice(view.__committed, safe)));
    view.__committed = safe;
  }
  const tail = source.slice(view.__committed);
  if (view.tailText.nodeValue !== tail) { view.tailText.nodeValue = tail; }
  view.cursorEl.hidden = !source;   // 正文还没开始时不要先冒出一个光标（思考阶段只显示思考框）
  view.__painted = source;
}

// 定稿时的一次性完整渲染：把最后那个未闭合的块也升级成正式 Markdown（整场只做一次）
function finishPaint(view, text) {
  ensureAnswerDom(view);
  const source = String(text || "");
  view.blocksEl.innerHTML = "";
  view.tailText.nodeValue = "";
  appendBlocks(view.blocksEl, renderMarkdown(source));
  view.__committed = source.length;
  view.__painted = source;
}

// 滴流主循环：一个 rAF 驱动所有正在生成的回答（正常情况下只有一个）
const activeDrips = new Set();
let dripRaf = 0;

function startDrip(view) {
  activeDrips.add(view);
  if (!dripRaf) { dripRaf = requestAnimationFrame(dripTick); }
}

function stopDrip(view) {
  activeDrips.delete(view);
  if (!activeDrips.size && dripRaf) { cancelAnimationFrame(dripRaf); dripRaf = 0; }
}

function dripTick() {
  dripRaf = 0;
  activeDrips.forEach(function (view) { advanceDrip(view); });
  if (activeDrips.size) { dripRaf = requestAnimationFrame(dripTick); }
}

function advanceDrip(view) {
  const raw = view.__rawText || "";
  if (!view.wrap.isConnected) {
    // 视图已被移出 DOM（切换 / 清空会话）：不必再逐帧画，但仍要把定稿流程走完，
    // 否则已生成的内容既不会被持久化、也拿不到「（已停止生成）」之类的中止标记
    view.__shownLen = raw.length;
  } else {
    const backlog = raw.length - view.__shownLen;
    if (backlog > 0) {
      view.__shownLen = Math.min(raw.length, view.__shownLen + dripStep(backlog, !!view.__streamDone));
      // 先维护补白（#pinSpace），再渲染：回答越长需要的补白越小，版面始终刚好能滚
      maintainPinSpace();
      const beforeTop = chatArea.scrollTop;
      renderRevealed(view, raw.slice(0, view.__shownLen));   // 内部按需跟随（贴真实内容底）
      // 规范第 5 条：流式过程中/结束时都**不跳动**。
      // 补白收缩会减少可滚范围，浏览器可能把 scrollTop 往回夹 —— 那就把内容原位补回去。
      // （落位动画期间除外：那会儿 scrollTop 由动画接管，插一脚只会打架）
      if (!pinSettling && chatArea.scrollTop < beforeTop) {
        setScrollTop(beforeTop);
      }
    }
  }
  if (view.__streamDone && view.__shownLen >= raw.length) {
    stopDrip(view);
    finalizeAnswer(view, raw, view.__references || [], view.__persist !== false);
  }
}

// 按「已经显示出来的那一部分」渲染：思考框与正文都跟着这一份走，交接因此天然是渐进的
function renderRevealed(view, shownRaw) {
  const parts = splitThink(shownRaw);
  const wasThinking = view.__thinkLive === true;
  syncThink(view, parts.think, parts.thinking);
  view.__thinkLive = !!parts.thinking;
  /* 刚刚跨过 </think>：思考行的收起与正文的出场**同一帧开始**。
     这里**刻意不做任何等待**（旧实现是固定 220ms 的硬等）：
     实测那会让"收卷帘在动、正文一动不动"整整持续 4 帧（≈200ms），
     看起来就是"停一下再生硬开始"。对标 dsh-chat-ux / dsh-thought-fold 的做法是
     **正文持续实时显示**（"流式文字始终可见"），收起与出字同时进行。
     两者同时跑不会互相打架：卷帘是 CSS grid-template-rows 过渡，正文是逐字滴流，各走各的。 */
  const answer = stripModelReferences(parts.answer);
  if (answer !== view.__painted) { paintAnswer(view, answer); }
  scrollBottom();
}

// 记下新到达的文字并启动滴流（到达与显示解耦：上游 burst 不再直接砸到屏幕上）
function setStreamTarget(view, rawText) {
  view.__rawText = String(rawText || "");
  if (typeof view.__shownLen !== "number") { view.__shownLen = 0; }
  startDrip(view);
}

// 收流：标记结束，剩下的积压由滴流放完后自动定稿
function finishStream(view, references, persist) {
  view.__references = references || [];
  view.__persist = persist !== false;
  view.__streamDone = true;
  setStreamTarget(view, view.__rawText || "");
}

// 正文里的引用编号：[1] / [1,2] / 【1】 / [[1]] 都认；[文字](链接) 这类非纯数字括号不算
function extractCitationIds(text) {
  const ids = {};
  const groups = String(text || "").match(/【[^】\n]{1,60}】|\[[^\]\n]{1,60}\]/g) || [];
  groups.forEach(function (group) {
    const inner = group.replace(/^[\[【]+|[\]】]+$/g, "").trim();
    // 整个括号里必须只有编号（或编号用逗号/顿号/分号分隔），否则当成普通方括号内容跳过
    if (!/^\d{1,3}([\s]*[,，、;；][\s]*\d{1,3})*$/.test(inner)) { return; }
    inner.split(/[,，、;；\s]+/).forEach(function (part) {
      if (part) { ids[String(Number(part))] = true; }
    });
  });
  return ids;
}

// 参考资料筛选（准确性优先）：
//   1) 正文优先：正文写了编号 [n]、或直接提到文件名（含去掉扩展名的主干）都算强证据；
//   2) 模型在结尾自抄的 References 清单单独降级，只在正文没有任何证据时才使用；
//   3) 同一文件的多个编号合并为一条，并带出 [n]，与正文里的编号对得上；
//   4) 正文和清单都没有命中时，不展示任何检索候选，只如实说明「未标注引用」。
function pickReferences(answer, references) {
  const split = splitModelReferences(answer);
  const body = split.body;
  const tail = split.tail;          // 模型自带的引用清单（原来被丢掉，现在用来认引用）
  const citedIds = extractCitationIds(body);
  const normalizedBody = normalizeDocName(body);
  const normalizedTail = normalizeDocName(tail);
  const files = [];
  const byFile = {};
  (references || []).forEach(function (item) {
    const name = String((item && item.file_path) || "");
    if (!name) { return; }
    if (!byFile[name]) {
      byFile[name] = { file_path: name, ids: [], cited: false, mentioned: false, viaTail: false };
      files.push(byFile[name]);
    }
    const id = String((item && item.reference_id) || "");
    if (id && byFile[name].ids.indexOf(id) < 0) { byFile[name].ids.push(id); }
  });
  // 先算"归一化前缀键"以及它在本批文档里是否唯一（唯一才允许用于模糊匹配）
  const prefixCount = {};
  files.forEach(function (item) {
    item.prefixKey = docPrefixKey(item.file_path);
    item.prefixUnique = false;
    if (item.prefixKey) { prefixCount[item.prefixKey] = (prefixCount[item.prefixKey] || 0) + 1; }
  });
  files.forEach(function (item) {
    if (item.prefixKey && prefixCount[item.prefixKey] === 1) { item.prefixUnique = true; }
  });
  files.forEach(function (item) {
    // 只把“正文里明确出现的东西”当作强证据：
    //   bodyCited = 正文写了 [n]；mentioned = 正文直接点名了文件；
    //   viaTail = 模型在结尾清单里自述——单独降级，不和正文证据混为一谈。
    item.bodyCited = item.ids.some(function (id) { return citedIds[id] === true; });
    item.mentioned = matchesDocument(body, normalizedBody, item);
    item.viaTail = matchesDocument(tail, normalizedTail, item);
  });
  const unknown = [];
  Object.keys(citedIds).forEach(function (id) {
    if (!files.some(function (item) { return item.ids.indexOf(id) >= 0; })) { unknown.push(id); }
  });
  unknown.sort(function (a, b) { return Number(a) - Number(b); });
  const byFirstId = function (a, b) {
    const left = a.ids.length ? Number(a.ids[0]) : 9999;
    const right = b.ids.length ? Number(b.ids[0]) : 9999;
    if (left !== right) { return left - right; }
    return a.file_path.localeCompare(b.file_path);
  };
  // 正文证据优先：只要正文引了/点名了任何文档，就只展示这些；
  // 正文没有证据时才看模型自述清单；两样都没有就**不展示任何候选**。
  const bodyUsed = files.filter(function (item) {
    return item.bodyCited || item.mentioned;
  }).sort(byFirstId);
  if (bodyUsed.length) {
    return { items: bodyUsed, total: files.length, mode: "cited", unknown: unknown };
  }
  const tailUsed = files.filter(function (item) { return item.viaTail; }).sort(byFirstId);
  if (tailUsed.length) {
    return { items: tailUsed, total: files.length, mode: "tail", unknown: unknown };
  }
  return { items: [], total: files.length, mode: "none", unknown: unknown };
}

// ---------- 参考资料双击打开原件 ----------
//
// 上游只给我们文档名（`<主干>.md`），原件在本机的哪里由后端算（app/services/origin.py）。
// 这里做两件小事：
//   1. 每渲染一组参考资料就问一次 /api/files/resolve，把「能打开 / 找不到」标在标签上 ——
//      找不到的灰掉并说明原因，用户不会点开一个空白页才发现；
//   2. 双击标签（或点标签里的 ↗ 按钮，手机上没有双击概念）在新标签打开原件。
// 结果按文件名缓存：同一轮问答里重复出现的文档不用反复问后端。
const REF_ORIGIN_CACHE = {};

function resolveRefOrigin(filePath) {
  const key = String(filePath || "");
  if (!key) { return Promise.resolve(null); }
  if (REF_ORIGIN_CACHE[key]) { return REF_ORIGIN_CACHE[key]; }
  REF_ORIGIN_CACHE[key] = fetch("/api/files/resolve?file_path=" + encodeURIComponent(key), { cache: "no-store" })
    .then(function (response) { return response.ok ? response.json() : null; })
    .catch(function () { return null; });
  return REF_ORIGIN_CACHE[key];
}

function openReference(node) {
  if (!node) { return; }
  const url = node.getAttribute("data-ref-url");
  if (!url) {
    setToast(node.getAttribute("data-ref-why") || "本机没有这份资料的原件", "bad");
    return;
  }
  window.open(url, "_blank", "noopener");
}

function wireRefs(container) {
  const nodes = Array.prototype.slice.call(container.querySelectorAll(".ref[data-ref]"));
  nodes.forEach(function (node) {
    const filePath = node.getAttribute("data-ref") || "";
    node.title = "正在确认本机是否有原件…";
    resolveRefOrigin(filePath).then(function (info) {
      if (info && info.available) {
        node.classList.add("ref-open");
        node.setAttribute("data-ref-url", info.url || "");
        node.title = (info.kind_label || "原件") + "：" + (info.name || "") +
          (info.relative ? "（" + info.relative + "）" : "") + "\n双击打开原件";
        return;
      }
      node.classList.add("ref-missing");
      const why = (info && info.reason) || "暂时问不到原件信息（服务可能刚重启，刷新即可）";
      node.setAttribute("data-ref-why", why);
      node.title = why;
    });
  });
}

// 双击标签打开原件（桌面）／点标签里的 ↗（手机，双击会被当成缩放）
document.addEventListener("dblclick", function (event) {
  const node = event.target && event.target.closest ? event.target.closest(".refs .ref[data-ref]") : null;
  if (node) { event.preventDefault(); openReference(node); }
});
document.addEventListener("keydown", function (event) {
  if (event.key !== "Enter" && event.key !== " ") { return; }
  const node = event.target && event.target.closest ? event.target.closest(".refs .ref.ref-open[data-ref]") : null;
  if (!node) { return; }
  event.preventDefault();
  openReference(node);
});
document.addEventListener("click", function (event) {
  const jump = event.target && event.target.closest ? event.target.closest(".refs .ref-jump") : null;
  if (jump) {
    event.preventDefault();
    event.stopPropagation();
    openReference(jump.closest(".ref"));
  }
});


// 底部操作条图标（左下＝朗读，右下＝复制）
const SPEAK_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6.5 9H3v6h3.5L11 19V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M18.5 6a8.5 8.5 0 0 1 0 12"/></svg>';
// 播放中：喇叭 + 斜杠（“静音 / 停止”造型）——再点一次即停止朗读
const SPEAK_OFF_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M11 5 6.5 9H3v6h3.5L11 19V5Z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/><path d="M5 18 19 6"/></svg>';
const COPY_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2.5"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/></svg>';
const PAUSE_SVG = '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><rect x="6.5" y="5" width="4" height="14" rx="1"/><rect x="13.5" y="5" width="4" height="14" rx="1"/></svg>';

// voice.js 复用：切换某条回答的「朗读 / 停止朗读」外观
window.setAnswerSpeakState = function (btn, on) {
  if (!btn || !btn.__label) { return; }
  btn.__label.textContent = on ? "停止朗读" : "朗读";
  if (btn.__ico) { btn.__ico.innerHTML = on ? SPEAK_OFF_SVG : SPEAK_SVG; }
  btn.classList.toggle("on", !!on);
  btn.title = on ? "停止朗读这段回答" : "朗读这段回答";
  if (btn.__pauseBtn) {
    btn.__pauseBtn.hidden = !on;
    if (!on) { window.setAnswerPauseState(btn, false); }
  }
};
window.setAnswerPauseState = function (btn, paused) {
  if (!btn || !btn.__pauseBtn) { return; }
  btn.__pauseBtn.__label.textContent = paused ? "继续" : "暂停";
  btn.__pauseBtn.title = paused ? "继续朗读这段回答" : "暂停朗读这段回答";
  btn.__pauseBtn.classList.toggle("on", !!paused);
  btn.__pauseBtn.setAttribute("aria-pressed", paused ? "true" : "false");
};

// 复制到剪贴板：优先用 Clipboard API；非安全上下文（例如手机通过 http://局域网IP 打开页面）
// 没有这个 API，退化为临时 textarea + execCommand —— 用户消息与回答的"复制"共用它，行为一致。
function copyToClipboard(text, okMessage) {
  const value = String(text == null ? "" : text);
  const done = function () { setToast(okMessage || "已复制", "ok"); };
  const fallback = function () {
    try {
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "-1000px";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      if (ok) { done(); } else { setToast("复制失败，请手动选中文本", "bad"); }
    } catch (err) {
      setToast("复制失败，请手动选中文本", "bad");
    }
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(value).then(done, fallback);
  } else {
    fallback();
  }
}

// 回答底部操作条：左下＝语音朗读，右下＝复制（参考资料下方不再单独挂复制按钮）
function appendAnswerActions(view, answerText) {
  const bar = document.createElement("div");
  bar.className = "msg-actions";

  // 本机已输出后失败才出现；点击是用户的显式操作，不会自动重发。
  if (view.__retryUpstream) {
    const retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "act retry-upstream";
    retryBtn.setAttribute("data-act", "retry-upstream");
    retryBtn.textContent = "换上游重试";
    retryBtn.title = "跳过本机模型，用上游重新回答这个问题（不会自动执行）";
    retryBtn.addEventListener("click", function () {
      retryWithUpstream(view, retryBtn);
    });
    bar.appendChild(retryBtn);
  }

  const speakBtn = document.createElement("button");
  speakBtn.type = "button";
  speakBtn.className = "act";
  speakBtn.setAttribute("data-act", "speak");
  const speakIco = document.createElement("span");
  speakIco.className = "act-ico";
  speakIco.innerHTML = SPEAK_SVG;
  const speakLabel = document.createElement("span");
  speakLabel.className = "act-label";
  speakLabel.textContent = "朗读";
  speakBtn.appendChild(speakIco);
  speakBtn.appendChild(speakLabel);
  speakBtn.__ico = speakIco;
  speakBtn.__label = speakLabel;
  speakBtn.title = "朗读这段回答";
  speakBtn.addEventListener("click", function () {
    if (typeof window.voiceSpeakAnswer === "function") { window.voiceSpeakAnswer(view, speakBtn); }
    else { setToast("当前浏览器不支持语音朗读", "bad"); }
  });

  const pauseBtn = document.createElement("button");
  pauseBtn.type = "button";
  pauseBtn.className = "act";
  pauseBtn.setAttribute("data-act", "pause-speak");
  pauseBtn.hidden = true;
  const pauseIco = document.createElement("span");
  pauseIco.className = "act-ico";
  pauseIco.innerHTML = PAUSE_SVG;
  const pauseLabel = document.createElement("span");
  pauseLabel.className = "act-label";
  pauseLabel.textContent = "暂停";
  pauseBtn.appendChild(pauseIco);
  pauseBtn.appendChild(pauseLabel);
  pauseBtn.__label = pauseLabel;
  pauseBtn.title = "暂停朗读这段回答";
  pauseBtn.setAttribute("aria-pressed", "false");
  pauseBtn.addEventListener("click", function () {
    const diag = typeof window.voiceDiagnostics === "function"
      ? window.voiceDiagnostics() : null;
    if (!diag) { return; }
    if (diag.readingPaused) {
      if (typeof window.voiceResumeReading === "function") { window.voiceResumeReading(); }
    } else if (typeof window.voicePauseReading === "function") {
      window.voicePauseReading();
    }
  });
  speakBtn.__pauseBtn = pauseBtn;

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "act";
  copyBtn.setAttribute("data-act", "copy");
  const copyIco = document.createElement("span");
  copyIco.className = "act-ico";
  copyIco.innerHTML = COPY_SVG;
  const copyLabel = document.createElement("span");
  copyLabel.className = "act-label";
  copyLabel.textContent = "复制";
  copyBtn.appendChild(copyIco);
  copyBtn.appendChild(copyLabel);
  copyBtn.__label = copyLabel;   // 便于后续复用同一套按钮状态切换
  copyBtn.title = "复制这段回答";
  copyBtn.addEventListener("click", function () { copyToClipboard(answerText, "已复制"); });

  bar.appendChild(speakBtn);
  bar.appendChild(pauseBtn);
  bar.appendChild(copyBtn);
  view.actions = bar;
  view.content.appendChild(bar);
}

// 图片引用卡片 ---------------------------------------------------------
// 图片证据（kind=image）不再混进"参考资料"的文件标签里按文件名合并 ——
// 同一份资料里的多张图会丢身份。这里按 image_id 去重，显示缩略图 + [img-n] + 文件名，
// 点击在新标签打开原图。缩略图走后端只读接口 /api/images/asset（按 file_id + image_id）。
function imageAssetUrl(item) {
  return "/api/images/asset?file_id=" + encodeURIComponent(String(item.file_id || "")) +
    "&image_id=" + encodeURIComponent(String(item.image_id || ""));
}

function imageOriginalUrl(item) {
  return "/api/files/origin/" + encodeURIComponent(String(item.file_id || ""));
}

function renderImageGallery(view, items) {
  const seen = {};
  const box = document.createElement("div");
  box.className = "img-refs";
  const head = document.createElement("div");
  head.className = "img-refs-head";
  head.textContent = "图片资料";
  box.appendChild(head);
  const grid = document.createElement("div");
  grid.className = "img-grid";
  let count = 0;

  (items || []).forEach(function (item) {
    if (!item) { return; }
    const key = String(item.image_id || item.file_id || "");
    if (!key || seen[key]) { return; }
    seen[key] = true;

    const label = String(item.label || "");
    const name = String(item.file_path || "图片资料");
    const card = document.createElement("button");
    card.type = "button";
    card.className = "img-card";
    card.setAttribute("data-image-id", String(item.image_id || ""));
    card.title = "打开原图：" + name;
    card.setAttribute("aria-label", (label ? label + " " : "") + name + "（打开原图）");

    const frame = document.createElement("span");
    frame.className = "img-frame";
    const thumb = document.createElement("img");
    thumb.className = "img-thumb";
    thumb.loading = "lazy";
    thumb.decoding = "async";
    thumb.alt = (label ? label + " " : "") + name;
    thumb.src = imageAssetUrl(item);
    thumb.addEventListener("error", function () {
      if (!thumb.parentNode) { return; }
      thumb.parentNode.removeChild(thumb);
      const missing = document.createElement("span");
      missing.className = "img-missing";
      missing.textContent = "预览不可用（点击打开原件）";
      frame.appendChild(missing);
    });
    frame.appendChild(thumb);

    const meta = document.createElement("span");
    meta.className = "img-meta";
    if (label) {
      const badge = document.createElement("span");
      badge.className = "img-label";
      badge.textContent = label;
      meta.appendChild(badge);
    }
    const caption = document.createElement("span");
    caption.className = "img-name";
    caption.textContent = name;
    meta.appendChild(caption);

    card.appendChild(frame);
    card.appendChild(meta);
    card.addEventListener("click", function () {
      window.open(imageOriginalUrl(item), "_blank", "noopener");
    });
    grid.appendChild(card);
    count += 1;
  });

  if (!count) { return; }
  box.appendChild(grid);
  view.content.appendChild(box);
}

/* 本机回答的内容校验提示：done 事件只给规则码，这里翻成一句人话。
   未知规则码保留"存在未识别的提示项"，规则码本身放在 title 里，便于排查。 */
const CONTENT_WARNING_LABELS = {
  low_citation_coverage: "引用覆盖偏低",
  unsupported_number: "含未在资料中核对的数字",
  refusal_with_evidence: "资料中有相关内容，但回答选择了拒答",
  too_short: "回答过短"
};

function contentWarningText(codes) {
  const seen = {};
  const names = [];
  (codes || []).forEach(function (code) {
    if (typeof code !== "string" || !code || seen[code]) { return; }
    seen[code] = true;
    if (CONTENT_WARNING_LABELS[code]) { names.push(CONTENT_WARNING_LABELS[code]); }
  });
  return names.join("、");
}

function appendContentNote(view, meta) {
  if (!meta || meta.mode !== "local") { return; }
  const codes = (meta.warnings || []).filter(function (code) { return typeof code === "string"; });
  const failed = meta.verified === false;
  if (!failed && !codes.length) { return; }
  const note = document.createElement("div");
  note.className = "answer-note" + (failed ? " bad" : " warn");
  const dot = document.createElement("span");
  dot.className = "an-dot";
  const text = document.createElement("span");
  if (failed) {
    text.textContent = "本机生成 · 未通过内容校验（已保留输出）";
  } else {
    const labels = contentWarningText(codes);
    const known = codes.filter(function (code) { return !!CONTENT_WARNING_LABELS[code]; }).length;
    text.textContent = "本机生成 · 校验提示：" + (labels || "存在未识别的提示项")
      + (labels && known < codes.length ? "等" : "");
  }
  note.appendChild(dot);
  note.appendChild(text);
  note.title = "规则码：" + (codes.length ? codes.join(", ") : "（无）")
    + "；提示不拦截回答，只说明可靠性需要留意。";
  view.content.appendChild(note);
}

// 手动重试出来的回答单独标注来源，避免和本机回答混淆；历史回放也保留。
function appendUpstreamRetryNote(view) {
  if (view.__upstreamRetry !== true) { return; }
  const note = document.createElement("div");
  note.className = "answer-note info";
  const dot = document.createElement("span");
  dot.className = "an-dot";
  const text = document.createElement("span");
  text.textContent = "上游重试 · 本次按你的操作跳过了本机模型";
  note.appendChild(dot);
  note.appendChild(text);
  view.content.appendChild(note);
}

function contextReasonLabel(reason) {
  const map = {
    assembly_failed: "上下文装配失败",
    session_missing: "会话不存在或已删除",
    settings_unavailable: "配置读取失败",
    persistence_unavailable: "会话库不可用"
  };
  return map[reason] || "上下文不可用";
}

function appendContextWarning(view) {
  if (!view || !view.__contextWarning || view.__contextWarningEl) { return; }
  const note = document.createElement("div");
  note.className = "context-warning";
  note.textContent = "本轮未带入会话上下文（"
    + contextReasonLabel(view.__contextWarning.reason)
    + "），回答可能缺少前文。";
  view.content.appendChild(note);
  view.__contextWarningEl = note;
}

function isNoContextText(text) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  if (!value) { return false; }
  if (value.length <= 200 && value.endsWith("[no-context]")) { return true; }
  return value === "No relevant context found for the query.";
}

function looksLikeNoInfoAnswer(text) {
  const value = String(text || "").replace(/\s+/g, " ").trim().toLowerCase();
  if (!value) { return false; }
  return /没有(找到|相关|提及|检索到|这方面的)|未(找到|提及)|no relevant|not found|no information|insufficient information/.test(value);
}

async function bypassContinue(view, button) {
  if (busy) { setToast("当前还在生成，请稍后再试", "bad"); return; }
  const question = String((view && view.__retryQuestion) || "").trim();
  if (!question) { setToast("找不到原问题，无法继续", "bad"); return; }
  if (button) { button.disabled = true; button.textContent = "正在继续…"; }
  const next = await streamAnswer(question, {
    forceBypass: true,
    sessionId: (view && view.__sessionId) || "",
    clientMessageId: (view && view.__clientMessageId) || "",
  });
  if (!button) { return; }
  if (next) {
    button.disabled = true;
    button.textContent = "已继续（未使用资料库）";
  } else {
    button.disabled = false;
    button.textContent = "继续对话（不查资料）";
  }
}

async function retryQuestion(view, button) {
  if (busy) { setToast("当前还在生成，请稍后再试", "bad"); return; }
  const question = String((view && view.__retryQuestion) || "").trim();
  if (!question) { setToast("找不到原问题，请重新输入", "bad"); return; }
  if (button) { button.disabled = true; button.textContent = "正在重试…"; }
  const next = await streamAnswer(question, {
    sessionId: (view && view.__sessionId) || "",
    clientMessageId: (view && view.__clientMessageId) || "",
  });
  if (!button) { return; }
  button.disabled = Boolean(next);
  button.textContent = next ? "已重试" : "重试";
}

function actionButton(label, handler) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", handler);
  return button;
}

function appendNoContextActions(view) {
  if (!view || !view.__noContext || view.__noContextEl) { return; }
  const box = document.createElement("div");
  box.className = "no-context-actions";
  const text = document.createElement("span");
  text.textContent = "知识库本轮没有检索到内容。可以重试、继续对话（不查资料），或先上传资料再问。";
  box.appendChild(text);
  box.appendChild(actionButton("重试", function () { retryQuestion(view, this); }));
  box.appendChild(actionButton("继续对话（不查资料）", function () { bypassContinue(view, this); }));
  box.appendChild(actionButton("上传资料", function () { pickFile("unified"); }));
  view.content.appendChild(box);
  view.__noContextEl = box;
}

function appendErrorActions(view) {
  if (!view || !view.__workError || view.__noContext || view.__errorActionsEl) { return; }
  const box = document.createElement("div");
  box.className = "no-context-actions";
  const text = document.createElement("span");
  text.textContent = "本轮没有拿到可靠回答。可以重试、继续对话（不查资料），或先上传资料。";
  box.appendChild(text);
  box.appendChild(actionButton("重试", function () { retryQuestion(view, this); }));
  box.appendChild(actionButton("继续对话（不查资料）", function () { bypassContinue(view, this); }));
  box.appendChild(actionButton("上传资料", function () { pickFile("unified"); }));
  view.content.appendChild(box);
  view.__errorActionsEl = box;
}

function appendBypassNote(view) {
  if (!view || !view.__bypass || view.__bypassNote) { return; }
  const note = document.createElement("div");
  note.className = "answer-note info";
  note.textContent = "本轮未使用知识库，仅依据会话上下文回答；没有 [n] 引用。";
  view.content.appendChild(note);
  view.__bypassNote = note;
}

function appendMemoryNote(view) {
  if (!view || !Array.isArray(view.__memoryDirect) || !view.__memoryDirect.length
      || view.__memoryNote) { return; }
  const note = document.createElement("div");
  note.className = "answer-note info";
  note.textContent = "本轮回答直接来自本地长期记忆，未调用生成模型。";
  view.content.appendChild(note);
  view.__memoryNote = note;
}

function finalizeAnswer(view, fullText, references, persist) {
  // 滴流收尾与异常 / 中断路径都可能触发定稿：只允许一次，否则参考资料与操作条会重复追加
  if (view.__finalized) { return; }
  view.__finalized = true;
  stopDrip(view);
  stopWaitTimer(view);   // 兜底：无论从哪条路径定稿，都不要留下"仍在处理"定时器
  const parts = splitThink(fullText);
  // 与流式渲染保持一致：模型自抄的结尾 References 清单一律裁掉（不再取决于本轮有没有检索结果，
  // 否则检索为空时会出现「流式时没有、定稿后又冒出来」的跳动）
  const clean = stripModelReferences(parts.answer);
  const shown = String(clean || "").trim();   // 纯空白回答按「没有返回内容」兜底，复制也拿到干净文本
  // 图片引用单独走卡片；只有 text 引用参与原来的"参考资料"筛选。
  const allRefs = Array.isArray(references) ? references : [];
  const imageRefs = allRefs.filter(function (item) {
    return item && item.kind === "image" && item.file_id && item.image_id;
  });
  const textRefs = allRefs.filter(function (item) {
    return !(item && item.kind === "image" && item.file_id && item.image_id)
;
  });
  // 思考过程：定格成「已完成思考（用时 N 秒）」并由 CSS 过渡自然收起。
  // 没有任何思考内容时 syncThink 会把等待期的空壳撤掉，这里再兜一次，保证定稿后不留空框。
  syncThink(view, parts.think, false);
  if (!parts.think && view.thinkBox) {
    clearThinkTimer(view.thinkBox);
    view.thinkBox.remove();
    view.thinkBox = null;
  }
  if (!view.thinkBox) { view.thinkHost.innerHTML = ""; }
  view.__answerText = shown;
  // 定稿才做一次完整渲染（把最后那个未闭合的块也升级成正式 Markdown），流式期间不再整体重绘
  finishPaint(view, shown || "（没有返回内容）");
  const picked = pickReferences(parts.answer, textRefs);
  if (isNoContextText(shown)
      || (!view.__bypass && looksLikeNoInfoAnswer(shown))) {
    view.__noContext = true;
  }
  if (view.__workStopped) {
    finishChatWork(view, {
      motion: "waiting",
      label: "已停止生成",
      detail: "保留已经显示的内容"
    });
  } else if (view.__workError) {
    finishChatWork(view, {
      motion: "error",
      label: "检索或生成失败，请查看原因",
      detailAction: function () { scrollWorkTarget(view.wrap); }
    });
  } else if (view.__noContext) {
    finishChatWork(view, {
      motion: "waiting",
      label: "暂未检索到相关资料",
      detail: "可以继续对话（不查资料）"
    });
  } else {
    finishChatWork(view, {
      motion: "success",
      label: "回答已完成",
      detail: view.__contextWarning
        ? "本轮未带入会话上下文，回答可能缺少前文"
        : ""
    });
  }
  if (view.cursorEl) { view.cursorEl.hidden = true; }
  if (imageRefs.length) { renderImageGallery(view, imageRefs); }
  // 准确性优先：只有正文引用/点名，或模型自述清单命中时才展示；
  // 一条都没命中时不再把“检索排名前 N”当参考资料，只留一句如实说明。
  if (picked.items.length || picked.unknown.length || (picked.total && picked.mode === "none")) {
    const refs = document.createElement("div");
    refs.className = "refs";
    let inner = "<b>参考资料：</b>";
    if (!picked.items.length) {
      if (picked.total) {
        inner += "<span class=\"ref-note\">本次回答未标注具体引用，未列出检索候选文档。</span>";
      }
    } else {
      if (picked.mode === "tail") {
        // 模型在结尾自己列了清单，但正文没有 [n] 或文件名：标明来源，避免当成正文实证。
        inner += "<span class=\"ref-note\">以下文档来自模型在回答末尾列出的引用清单（正文未标注 [n]）。</span>";
      }
      picked.items.forEach(function (item) {
        // 只有正文里真的写了 [n] 才带编号；自述清单/点名不硬塞编号。
        const nums = (item.bodyCited && item.ids.length)
          ? "<span class=\"ref-no\">" + item.ids.map(function (id) { return "[" + escapeHtml(id) + "]"; }).join("") + "</span> "
          : "";
        // 带上文件名，渲染完由 wireRefs() 问后端「本机有没有原件」，并支持双击打开
        inner += "<span class=\"ref\" data-ref=\"" + escapeHtml(item.file_path) + "\""
          + " role=\"button\" tabindex=\"0\" aria-label=\"参考资料：" + escapeHtml(item.file_path) + "\">" + nums +
          escapeHtml(item.file_path) +
          "<button type=\"button\" class=\"ref-jump\" title=\"打开原件（新标签）\" aria-label=\"打开原件\">↗</button></span>";
      });
    }
    (picked.unknown || []).forEach(function (id) {
      // 正文出现的编号在检索结果里找不到对应文档（多为模型自拟），标出来避免读者白找
      inner += "<span class=\"ref ref-unmatched\" title=\"正文里出现了这个引用编号，但本轮检索结果里没有对应文档\">[" +
        escapeHtml(id) + "] 未匹配到文档</span>";
    });
    refs.innerHTML = inner;
    // 渲染完立刻确认每份资料本机有没有原件（灰掉打不开的，双击/↗ 打开可用的）
    wireRefs(refs);
    view.refs = refs;
    view.content.appendChild(refs);
  }
  appendContextWarning(view);
  appendNoContextActions(view);
  appendErrorActions(view);
  appendBypassNote(view);
  appendMemoryNote(view);
  appendContentNote(view, view.__genMeta || null);
  appendUpstreamRetryNote(view);
  appendAnswerActions(view, shown);
  if (persist) {
    persistAiMessage(parts.answer, references, view.__genMeta || null, {
      retryUpstream: view.__retryUpstream === true,
      upstreamRetry: view.__upstreamRetry === true
    });
  }
  // 定稿：用最终内容高度收一次补白（回答够长就归零，不留幽灵空白），再贴到真实内容底
  ensurePinSpace();
  scrollBottom();
  updateJumpButton();
  // 定稿后会继续重排（思考框收起是 CSS 过渡、参考资料与操作条刚插入），内容一变矮
  // 浏览器就会把 scrollTop 往回夹，留下底部一段空隙 —— 这之后 0.5s 内补贴几次。
  let followTicks = 0;
  (function settleFollow() {
    if (followTicks >= 5 || !stickToBottom) { return; }
    followTicks += 1;
    scrollBottom();
    updateJumpButton();
    window.setTimeout(settleFollow, 100);
  })();
}

function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
  syncComposerState();
  positionIntroComposer();     // 开场态下输入框变高时，抬起量跟着重算
}

// 输入框状态：has-text 控制空输入禁用发送；busy 时同一个按钮切换为“停止生成”。
function syncComposerState() {
  const zone = $("dropZone");
  if (!zone || !zone.classList) { return; }
  const hasText = !!String(input.value || "").trim();
  zone.classList.toggle("has-text", hasText);
  zone.classList.toggle("busy", !!busy);
  sendBtn.disabled = !busy && !hasText;
}

// 发送按钮：空闲＝圆形发送箭头；生成中＝圆形停止（Gemini 风格）
function setSendState(mode) {
  const stop = (mode === "stop");
  sendBtn.classList.toggle("busy", stop);
  sendBtn.setAttribute("data-mode", stop ? "stop" : "send");
  sendBtn.setAttribute("aria-label", stop ? "停止生成" : "发送");
  sendBtn.title = stop ? "停止生成" : "发送（Enter）";
  sendBtn.innerHTML = stop
    ? '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor"><rect x="6.5" y="6.5" width="11" height="11" rx="2.5"/></svg>'
    : '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V6"/><path d="m5.5 12.5 6.5-6.5 6.5 6.5"/></svg>';
}

/* ---------- 开场编排（阶段 D / Batch 1）----------
   会话为空 → 居中问候块 + 一个 ChatGPT 入口 + 输入框，逐级上浮淡入；
   第一条消息发出 → 整块平滑收起，版面交给对话。
   注意：问候语不再作为一条消息写进对话记录（Gemini / ChatGPT 同样如此），
   它现在只属于开场态；对话记录里从用户的第一个问题开始。

   Batch 1：开场不再放三个示例问题，只保留「点击打开 ChatGPT」。
   同标签跳转（浏览器后退可返回）；这是外部网页入口，不经过本项目后端，
   也不读写记忆系统。离开前做两层保护：
     · 正在生成 / 正在准备发送 → 不跳转，提示先停止或等待；
     · 输入框有未发送内容 → 二次确认，避免静默丢失。 */
const CHATGPT_URL = "https://chatgpt.com/";
const mainEl = document.querySelector(".main");
let heroLeaveTimer = null;

function buildHeroChips() {
  const host = $("heroChips");
  if (!host || host.childElementCount) { return; }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "chip";
  button.textContent = "点击打开 ChatGPT";
  button.title = "在 ChatGPT 官网页面打开（当前标签；浏览器后退可返回）";
  button.style.animationDelay = "0.23s";
  button.addEventListener("click", openChatGPT);
  host.appendChild(button);
}

// 打开 ChatGPT 官网：同标签跳转，避免 iframe/弹窗与额外的跨域问题。
function openChatGPT() {
  if (busy || sendInFlight) {
    setToast("正在生成或准备发送，请先停止或等待完成");
    return;
  }
  const draft = String(input.value || "").trim();
  if (!draft) {
    window.location.assign(CHATGPT_URL);
    return;
  }
  const options = {
    title: "打开 ChatGPT？",
    body: "当前输入框里还有未发送的内容，打开 ChatGPT 会离开本页。",
    detail: "这段内容不会被发送，也不会自动保存。建议先复制或发送后再离开。",
    confirmText: "仍然打开",
    cancelText: "留在本页"
  };
  const ask = (window.RAGKB && typeof RAGKB.confirm === "function")
    ? RAGKB.confirm(options)
    : Promise.resolve(window.confirm(options.title + "\n\n" + options.body));
  ask.then(function (ok) {
    if (ok) { window.location.assign(CHATGPT_URL); }
  });
}

function introActive() {
  return !!(mainEl && mainEl.classList.contains("intro"));
}

// 会话里是否已经有内容（决定要不要显示开场块）
function sessionHasMessages() {
  const session = currentSession();
  return !!(session && session.messages && session.messages.length);
}

// 开场态布局：把「问候块 + 间距 + 输入框」整组在可用区域里垂直居中。
// 三个值都由实测得出，所以窗口尺寸 / 输入框高度变化后重算即可，换屏不用调参数。
function positionIntroComposer() {
  if (!mainEl || !mainEl.classList.contains("intro")) { return; }
  const hero = $("hero");
  const composer = $("composer");
  const wrap = hero && hero.querySelector(".hero-wrap");
  if (!hero || !composer || !wrap) { return; }
  const GAP = 22;
  const regionH = hero.getBoundingClientRect().height;   // 可用区域（开场态下对话区已让位）
  const wrapH = wrap.getBoundingClientRect().height;     // 问候块高度
  const comH = composer.getBoundingClientRect().height;  // 输入框高度
  // 整组居中；空间不够时贴顶（剩下的交给 .hero 的滚动，输入框不会被压住）
  const top = Math.max(12, Math.round((regionH - (wrapH + GAP + comH)) / 2));
  const lift = Math.max(0, Math.round(regionH - top - wrapH - GAP));
  mainEl.style.setProperty("--intro-top", top + "px");
  mainEl.style.setProperty("--intro-lift", lift + "px");
}

// 进入开场态（新建 / 清空 / 切到空会话 / 从图谱回来）
function enterIntro() {
  const hero = $("hero");
  if (!hero || !mainEl) { return; }
  if (heroLeaveTimer) { clearTimeout(heroLeaveTimer); heroLeaveTimer = null; }
  buildHeroChips();
  hero.hidden = false;
  // 入场这一帧先禁掉问候块的高度过渡，好让它的真实高度量得准（否则量到的是 0）
  mainEl.classList.add("hero-snap");
  // 先把"输入框还在底部"这一初始态落进样式，再挂 .intro
  mainEl.style.setProperty("--intro-lift", "0px");
  mainEl.style.setProperty("--intro-top", "20px");
  mainEl.classList.remove("intro");
  void mainEl.offsetWidth;
  mainEl.classList.add("intro");
  void mainEl.offsetWidth;        // 让问候块立刻展开，量得到真实高度；入场动画也从头播
  // 下一帧再抬高：这样 translate 的过渡才会从"底部"开始，看得见输入框升上来
  requestAnimationFrame(function () {
    mainEl.classList.remove("hero-snap");
    positionIntroComposer();
  });
}

// 离开开场态。immediate = true 时不播收起动画（切会话 / 打开图谱等场景）
function leaveIntro(immediate) {
  const hero = $("hero");
  if (!hero || !mainEl) { return; }
  if (heroLeaveTimer) { clearTimeout(heroLeaveTimer); heroLeaveTimer = null; }
  if (!mainEl.classList.contains("intro")) { hero.hidden = true; return; }
  mainEl.classList.remove("intro");
  if (immediate) { hero.hidden = true; return; }
  // 等收起过渡走完再真正隐藏：提前 display:none 会把动画打断
  heroLeaveTimer = window.setTimeout(function () {
    hero.hidden = true;
    heroLeaveTimer = null;
  }, 480);
}

function welcome() {
  messages.innerHTML = "";
  clearAllThinkTimers();
  clearCurrentQuestion();   // 消息都清了，本轮提问标记必须一起清（否则指向已脱离 DOM 的节点）
  resetPinGeometry();     // 顶部留白 / 底部补白 / 没走完的动画一起复位
  enterIntro();      // 问候语改由开场块承担，不再往对话记录里塞一条消息
}

// 窗口尺寸变化（含手机端横竖屏切换）后重新量一次输入框高度与锚点几何
let pinRelayoutTimer = 0;
window.addEventListener("resize", function () {
  positionIntroComposer();
  if (pinRelayoutTimer) { window.clearTimeout(pinRelayoutTimer); }
  pinRelayoutTimer = window.setTimeout(function () {   // 合并高频 resize（手机地址栏伸缩会狂发）
    pinRelayoutTimer = 0;
    relayoutPin();
  }, 160);
});
async function send() {
  return submitMessage(null, null);
}

async function submitMessage(forcedText, options) {
  const opts = options || {};
  const fromComposer = forcedText === null || forcedText === undefined;
  const text = String(fromComposer ? input.value : forcedText).trim();
  if (!text || busy || sendInFlight) { return; }
  // 任意发送入口（回车、按钮、政策检索）都先结束上一条朗读，避免旧音频压住新回答。
  if (typeof window.voiceCancelReading === "function") {
    try { window.voiceCancelReading(); } catch (err) { /* 朗读清理不影响发送 */ }
  }
  sendInFlight = true;
  syncComposerState();
  const workId = newWorkId("chat");
  WorkCompanion.begin(workId, {
    category: "chat",
    priority: 50,
    motion: "thinking",
    label: "正在理解你的要求…",
    detail: "正在读取当前会话与上下文"
  });
  try {
    closeGraph();   // 图谱视图占着屏幕时先让开；回答照常流式生成，对话内容不受影响
    leaveIntro();   // 开场块收起，版面交给对话（顺序不能反：closeGraph 可能刚把开场态放回来）
    if (fromComposer) {
      input.value = "";
      autoGrow();
      // 移动端：发送后主动收起输入法，避免键盘挡住刚出现的回答
      if (input.blur) { input.blur(); }
    }
    addMessage(text, "user");
    // 用户主动发问：无条件回到「跟随」状态，并把这条提问送到锚点。
    // 先量一次几何（此刻 sticky 还没夹持，量到的就是干净的布局位置），再逐帧滑过去；
    // 阅读栏高度还是 0 / scrollHeight 还没涨时，settlePin 会在下一帧自己重试（不会"看起来毫无反应"）。
    stickToBottom = true;
    pinPending = true;          // 几何还没量：滴流期间的补白维护先不动，等布局落定
    pinSettleRetries = 0;       // 本轮补救次数清零（避免跨轮累计把补救通道耗光）
    waitLayoutThenPin();        // 等阅读栏高度落定 → 量几何 → 滑到锚点（不会"看起来毫无反应"）

    // 先确保当前会话在服务端存在（旧本地会话会先导入），再发请求。
    const session = await ensureCurrentSession();
    const clientMessageId = newClientMessageId();
    persistUserMessage(text, clientMessageId);

    return await streamAnswer(text, {
      sessionId: session ? session.id : "",
      clientMessageId: clientMessageId,
      workId: workId
    });
  } catch (err) {
    WorkCompanion.finish(workId, {
      motion: "error",
      label: "请求没有完成，请检查服务",
      detail: String((err && err.message) || "")
    });
    setToast("请求失败：" + ((err && err.message) || "未知错误"), "bad");
    return null;
  } finally {
    sendInFlight = false;
    syncComposerState();
  }
}

// 手机横屏时视口可能超过移动布局断点，但主指针仍是粗指针；两者都按移动端处理。
function isMobileComposerContext() {
  if (MOBILE_LAYOUT && MOBILE_LAYOUT.matches) { return true; }
  try {
    return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
  } catch (err) {
    return false;
  }
}

// 生成结束后再决定输入焦点：桌面端保持“随时接着输入”，手机端不主动拉起软键盘。
// 手机端若生成期间已经有草稿，则保留用户当前焦点；只有空输入时才收起键盘。
function settleComposerFocusAfterStream() {
  if (!isMobileComposerContext()) {
    input.focus();
    return;
  }
  if (document.activeElement === input && !String(input.value || "").trim()) {
    input.blur();
  }
}

// 把「发请求 + 渲染回答」抽出来，正常发送与手动换上游重试共用同一套流式逻辑。
// options：{ forceUpstream, upstreamRetry, persist }
async function streamAnswer(question, options) {
  const opts = options || {};
  if (busy) { return null; }
  const text = String(question || "").trim();
  if (!text) { return null; }
  const persist = opts.persist !== false;
  const sessionId = String(opts.sessionId
    || (currentSession() && currentSession().id) || "");
  const clientMessageId = String(opts.clientMessageId || newClientMessageId());
  const workId = String(opts.workId || newWorkId("chat"));
  const payload = { message: text };
  if (sessionId) { payload.session_id = sessionId; }
  if (clientMessageId) { payload.client_message_id = clientMessageId; }
  if (opts.forceUpstream) { payload.force_upstream = true; }
  if (opts.forceBypass) { payload.force_bypass = true; }
  WorkCompanion.begin(workId, {
    category: "chat",
    priority: 50,
    motion: "thinking",
    label: "正在理解你的要求…",
    detail: payload.force_bypass ? "本轮继续对话，不查询知识库" : "正在读取当前会话与上下文"
  });

  busy = true;
  controller = new AbortController();
  requestStartedAt = Date.now();      // 思考框秒表 / 「用时 N 秒」从点击/重试算起
  setSendState("stop");
  syncComposerState();
  let references = [];
  let assembled = "";
  let started = false;
  // 点击发送/重试即出现「思考中」，并且**这个视图一直沿用到回答结束**：
  // 首个内容片段到达时不再换 DOM 节点，所以不会出现"闪一下换个框"。
  const view = addTyping(requestStartedAt);
  view.__upstreamRetry = opts.upstreamRetry === true;
  view.__bypass = opts.forceBypass === true;
  view.__retryQuestion = text;
  view.__sessionId = sessionId;
  view.__clientMessageId = clientMessageId;
  view.__workId = workId;
  view.__workError = false;
  view.__workStopped = false;

  try {
    const response = await fetch("/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal
    });
    const serverSessionId = response.headers.get("X-Session-Id");
    if (serverSessionId) {
      adoptServerSessionId(serverSessionId);
      view.__sessionId = serverSessionId;
    }
    if (response.headers.get("X-Context-Status") === "failed") {
      view.__contextWarning = {
        status: "failed",
        reason: response.headers.get("X-Context-Reason") || ""
      };
    }

    if (!response.ok || !response.body) {
      WorkCompanion.update(workId, {
        motion: "working",
        label: "正在改用兼容方式获取回答…",
        detail: "流式连接暂不可用"
      });
      const fallback = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const fallbackSessionId = fallback.headers.get("X-Session-Id");
      if (fallbackSessionId) {
        adoptServerSessionId(fallbackSessionId);
        view.__sessionId = fallbackSessionId;
      }
      if (fallback.headers.get("X-Context-Status") === "failed") {
        view.__contextWarning = {
          status: "failed",
          reason: fallback.headers.get("X-Context-Reason") || ""
        };
      }
      let data = {};
      try { data = await fallback.json(); } catch (err) { data = {}; }
      const fallbackText = data.response || data.answer || ("查询失败（HTTP " + fallback.status + "）");
      if (isNoContextText(fallbackText)) { view.__noContext = true; }
      stopWaitTimer(view);
      finalizeAnswer(view, fallbackText, data.references || [], persist);
      return view;
    }

    WorkCompanion.update(workId, {
      motion: "working",
      label: payload.force_bypass
        ? "正在继续对话（不查资料）…"
        : (GEN_MODE === "local" ? "正在准备回答…" : "正在检索相关资料并组织回答…"),
      detail: payload.force_bypass ? "本轮只使用当前会话上下文" : "正在等待资料与模型返回"
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let pending = "";

    while (true) {
      const step = await reader.read();
      if (step.done) { break; }
      pending += decoder.decode(step.value, { stream: true });
      const lines = pending.split("\n");
      pending = lines.pop() || "";
      for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) { continue; }
        let event = null;
        try { event = JSON.parse(line); } catch (err) { continue; }
        if (event.type === "context") {
          if (event.status === "failed") {
            view.__contextWarning = {
              status: "failed",
              reason: event.reason || ""
            };
            setToast("本轮未带入会话上下文，回答可能缺少前文", "bad");
            WorkCompanion.update(workId, {
              motion: "working",
              label: "仍在生成回答，但本轮上下文未带入",
              detail: "回答可能缺少前文"
            });
          }
          continue;
        }
        if (event.type === "no_context") {
          view.__noContext = true;
          WorkCompanion.update(workId, {
            motion: "waiting",
            label: "暂未检索到相关资料",
            detail: "你仍然可以选择继续对话"
          });
          continue;
        }
        if (event.type === "memory") {
          view.__memoryDirect = Array.isArray(event.items) ? event.items : [];
          continue;
        }
        if (event.type === "references") {
          references = event.items || [];
          WorkCompanion.update(workId, {
            motion: "working",
            label: "正在整理参考资料…",
            detail: references.length ? ("本轮候选资料 " + references.length + " 条") : ""
          });
          continue;
        }
        if (event.type === "done") {
          // 只有真正跑过内容校验的本机回答才带 bool verified；
          // 图片技能等整段结果不带，不能把它们误记成"本机模型生成"。
          if (typeof event.verified === "boolean") {
            view.__genMeta = {
              mode: "local",
              verified: event.verified,
              warnings: Array.isArray(event.warnings)
                ? event.warnings.filter(function (code) { return typeof code === "string"; }).slice(0, 10)
                : []
            };
          }
          continue;
        }
        if (event.type === "chunk") {
          assembled += event.text || "";
          if (!started) {
            started = true;
            stopWaitTimer(view);
            setToast("");
            WorkCompanion.update(workId, {
              motion: "working",
              label: "正在生成回答…",
              detail: references.length ? "正在组织资料与回答" : "正在组织回答"
            });
          }
          setStreamTarget(view, assembled);
          continue;
        }
        if (event.type === "error") {
          // 后端只在「本机已输出后失败」时带 retry_upstream；
          // 这里只是把入口记到视图上，绝不自动重发。
          view.__retryQuestion = text;
          if (event.retry_upstream === true) { view.__retryUpstream = true; }
          assembled += (assembled ? "\n\n" : "") + "⚠️ " + (event.message || "上游返回错误");
          if (event.no_context) {
            view.__noContext = true;
            view.__workError = false;
            WorkCompanion.update(workId, {
              motion: "waiting",
              label: "暂未检索到相关资料",
              detail: String(event.message || ""),
              detailAction: function () { scrollWorkTarget(view.wrap); }
            });
          } else {
            view.__workError = true;
            WorkCompanion.update(workId, {
              motion: "error",
              label: "检索或生成失败，请查看原因",
              detail: String(event.message || ""),
              detailAction: function () { scrollWorkTarget(view.wrap); }
            });
          }
          setStreamTarget(view, assembled);
          setToast(event.no_context ? "知识库没有找到相关资料" : "查询出错",
            event.no_context ? "" : "bad");
          continue;
        }
      }
    }

    stopWaitTimer(view);
    // 收流后不直接定稿：把剩余积压交给滴流放完，再在滴流里自动定稿（避免结尾突然整块出现）
    finishStream(view, references, persist);
    return view;
  } catch (err) {
    view.__workStopped = Boolean(err && err.name === "AbortError");
    if (view.__workStopped) {
      finishChatWork(view, {
        motion: "waiting",
        label: "已停止生成",
        detail: "保留已经显示的内容"
      });
    } else {
      finishChatWork(view, {
        motion: "error",
        label: "连接中断，请检查服务",
        detail: String((err && err.message) || ""),
        detailAction: function () { scrollWorkTarget(view.wrap); }
      });
    }
    if (!assembled && view.__waiting) {
      // 一个字都还没收到就失败：撤掉「思考中」占位，仍用原来的错误消息形态（信息量更大）
      stopWaitTimer(view);
      view.__finalized = true;
      stopDrip(view);
      view.wrap.remove();
      addMessage("请求失败：" + err.message + "（请确认服务仍在运行）", "ai");
    } else {
      stopWaitTimer(view);
      setStreamTarget(view, assembled + (assembled ? "\n\n" : "") + "（已停止生成）");
      finishStream(view, references, persist);
    }
    return view;
  } finally {
    busy = false;
    controller = null;
    sendBtn.disabled = false;
    setSendState("send");
    syncComposerState();
    settleComposerFocusAfterStream();
  }
}

// 用户手动点「换上游重试」才走这里；不是自动回退，也不会覆盖已输出内容。
async function retryWithUpstream(view, button) {
  if (busy) { setToast("当前还在生成，请稍后再试", "bad"); return; }
  const question = String((view && view.__retryQuestion) || "").trim();
  if (!question) { setToast("找不到原问题，无法重试", "bad"); return; }
  if (button) {
    button.disabled = true;
    button.textContent = "正在换上游重试…";
  }
  const retryView = await streamAnswer(question, {
    forceUpstream: true,
    upstreamRetry: true,
    sessionId: (view && view.__sessionId) || "",
    clientMessageId: (view && view.__clientMessageId) || "",
  });
  if (!button) { return; }
  if (retryView) {
    button.disabled = true;
    button.textContent = "已换上游重试";
    button.classList.add("done");
  } else {
    // 极端情况（例如另一轮刚好开始）：恢复入口，用户可再点。
    button.disabled = false;
    button.textContent = "换上游重试";
  }
}

const DOTS_HTML = '<span class="ragkb-dots" aria-hidden="true"><i></i><i></i><i></i></span>';
const BUSY_STATES_UI = {
  uploading: 1, queued: 1, parsing: 1, checking: 1, ocr: 1, ingesting: 1, processing: 1
};

// 徽章文字只在内容真的变了时才重绘：重绘会重启动画，三点会看起来"卡一下"。
function setBadge(card, label, dots) {
  const badge = card.querySelector(".badge");
  if (!badge) { return; }
  const want = Boolean(dots);
  if (badge.__label === label && badge.__dots === want) { return; }
  badge.__label = label;
  badge.__dots = want;
  badge.innerHTML = escapeHtml(label) + (want ? DOTS_HTML : "");
}

// 进行中的进度条：OCR / 入库是"不知道还要多久"的不确定进度；知识库建索引有 x/y 就显示真实比例。
function updateUploadProgress(card, data) {
  const bar = card.__progress;
  if (!bar) { return; }
  const state = String((data && data.state) || "");
  const progress = (data && data.progress) || {};
  const indexing = state === "done" && progress.state === "processing";
  const busy = Boolean(BUSY_STATES_UI[state]) || indexing;
  bar.hidden = !busy;
  const rail = bar.querySelector("i");
  if (rail) {
    const total = Number(progress.total || 0);
    const done = Number(progress.processed || 0);
    if (indexing && total > 0) {
      bar.classList.add("determinate");
      rail.style.width = Math.max(4, Math.min(100, Math.round(done / total * 100))) + "%";
    } else {
      bar.classList.remove("determinate");
      rail.style.width = "";
    }
  }
}

function createUploadCard(fileName, parent) {
  const card = document.createElement("div");
  card.className = "upload-card";
  card.__workId = newWorkId("upload");
  card.__fileName = String(fileName || "这份资料");
  const icon = document.createElement("div");
  icon.className = "upload-icon";
  icon.textContent = "📄";
  const main = document.createElement("div");
  main.className = "upload-main";
  const nameNode = document.createElement("div");
  nameNode.className = "upload-name";
  nameNode.textContent = fileName;
  const subNode = document.createElement("div");
  subNode.className = "upload-sub";
  const actionsNode = document.createElement("div");
  actionsNode.className = "upload-actions";
  // 进行中的细进度条：OCR / 入库 / 建索引时一直有东西在动，不用盯着徽章猜
  const progressNode = document.createElement("div");
  progressNode.className = "upload-progress";
  progressNode.hidden = true;
  progressNode.setAttribute("aria-hidden", "true");
  progressNode.innerHTML = "<i></i>";
  main.appendChild(nameNode);
  main.appendChild(subNode);
  main.appendChild(progressNode);
  main.appendChild(actionsNode);
  const badgeNode = document.createElement("span");
  badgeNode.className = "badge";
  card.appendChild(icon);
  card.appendChild(main);
  card.appendChild(badgeNode);
  card.__fileId = "";
  card.__actions = actionsNode;
  card.__progress = progressNode;
  (parent || messages).appendChild(card);
  if (!parent) { scrollBottom(true); }   // 用户刚选了文件：无条件回到底部
  return card;
}

// ---------- 批量上传：一个批次卡 + 一个聚合轮询器 ----------
// 大批量时不再为每个文件建一张长期轮询的卡片；批次卡只做汇总，明细按需展开，
// 展开后仍复用单文件卡片（确认 / 编辑 / 跳过等动作不变）。
const BATCH_THRESHOLD = 5;
const BATCH_POLL_MS = 4000;
const BATCH_MAX_POLLS = 450;
const UPLOAD_BATCHES = [];
const BATCH_ATTENTION = { review: 1, needs_ocr: 1, blocked: 1, failed: 1, skipped: 1 };
const BATCH_TERMINAL = { done: 1, failed: 1, skipped: 1 };
const BATCH_STATE_LABELS = {
  uploading: "上传中",
  queued: "排队中",
  processing: "解析中",
  parsing: "解析中",
  checking: "校验中",
  ocr: "OCR 识别中",
  review: "待确认入库",
  needs_ocr: "需要 OCR",
  blocked: "未通过校验",
  failed: "处理失败",
  skipped: "已跳过",
  cancelled: "已取消",
  duplicate: "已存在",
  ingesting: "入库中",
  done: "已入库",
  error: "上传失败",
  unknown: "已提交"
};

function newBatchId() {
  const d = new Date();
  const pad = function (value) { return String(value).padStart(2, "0"); };
  const stamp = String(d.getFullYear()) + pad(d.getMonth() + 1) + pad(d.getDate())
    + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
  let tail = "";
  if (window.crypto && window.crypto.getRandomValues) {
    const bytes = new Uint8Array(4);
    window.crypto.getRandomValues(bytes);
    tail = Array.prototype.map.call(bytes, function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  } else {
    tail = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, "0");
  }
  return "batch_" + stamp + "_" + tail;
}

function batchBadgeClass(state) {
  if (state === "review") { return "review"; }
  if (state === "needs_ocr" || state === "uploading" || state === "queued"
      || state === "processing" || state === "parsing" || state === "checking"
      || state === "ocr" || state === "ingesting") { return "processing"; }
  if (state === "done") { return "done"; }
  if (state === "skipped" || state === "cancelled" || state === "duplicate") { return "skipped"; }
  if (state === "blocked") { return "blocked"; }
  if (state === "failed" || state === "error") { return "failed"; }
  return "";
}

function createUploadBatch(batchId, expectedTotal) {
  const root = document.createElement("section");
  root.className = "upload-batch";
  root.dataset.batchId = batchId;
  const head = document.createElement("div");
  head.className = "upload-batch-head";
  const title = document.createElement("div");
  title.className = "upload-batch-title";
  title.textContent = "资料批次 · " + Math.max(0, Number(expectedTotal) || 0) + " 份";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "upload-batch-toggle";
  toggle.textContent = "展开明细";
  const link = document.createElement("a");
  link.className = "upload-batch-link";
  link.href = "/files?batch=" + encodeURIComponent(batchId);
  link.textContent = "文件管理";
  link.title = "在文件管理中按这个批次查看状态、失败原因并重试失败项";
  const summary = document.createElement("div");
  summary.className = "upload-batch-summary";
  summary.textContent = "准备上传…";
  const bar = document.createElement("div");
  bar.className = "upload-batch-bar";
  bar.innerHTML = "<i></i>";
  head.appendChild(title);
  head.appendChild(toggle);
  head.appendChild(link);
  head.appendChild(summary);
  head.appendChild(bar);
  const body = document.createElement("div");
  body.className = "upload-batch-list";
  body.hidden = true;
  const detail = document.createElement("div");
  detail.className = "upload-batch-detail";
  detail.hidden = true;
  root.appendChild(head);
  root.appendChild(body);
  root.appendChild(detail);
  messages.appendChild(root);
  scrollBottom(true);

  const batch = {
    id: batchId,
    root: root,
    body: body,
    detail: detail,
    title: title,
    summary: summary,
    toggle: toggle,
    bar: bar,
    workId: "batch-" + batchId,
    expectedTotal: Math.max(0, Number(expectedTotal) || 0),
    uploaded: 0,
    attempted: 0,
    localErrors: 0,
    duplicates: 0,
    items: new Map(),
    attempts: 0,
    polling: false,
    uploadDone: false,
    autoOpened: false,
    finished: false,
    lastData: null
  };
  toggle.addEventListener("click", function () {
    setBatchOpen(batch, batch.body.hidden);
  });
  UPLOAD_BATCHES.push(batch);
  if (window.WorkCompanion) {
    window.WorkCompanion.begin(batch.workId, {
      category: "upload",
      batch: true,
      fileCount: batch.expectedTotal,
      waitingCount: 0,
      activeCount: 0,
      doneCount: 0,
      motion: "working",
      label: "资料批次处理中",
      detail: "已提交 0/" + batch.expectedTotal,
      detailAction: function () { openUploadBatch(batch); }
    });
  }
  updateBatchUi(batch, null);
  return batch;
}

function setBatchOpen(batch, open) {
  batch.body.hidden = !open;
  if (!open) { batch.detail.hidden = true; }
  batch.toggle.textContent = open
    ? "收起明细"
    : ("展开明细" + (batch.items.size ? "（" + batch.items.size + "）" : ""));
}

function openUploadBatch(batch) {
  if (!batch) { return; }
  setBatchOpen(batch, true);
  try { batch.root.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (err) { /* 兼容 */ }
}

function ensureBatchItem(batch, item) {
  const key = String((item && (item.file_id || item.tempId)) || "");
  if (!key) { return null; }
  let entry = batch.items.get(key);
  if (!entry) {
    const row = document.createElement("div");
    row.className = "upload-batch-row";
    row.dataset.fileId = key;
    const name = document.createElement("div");
    name.className = "upload-batch-name";
    const state = document.createElement("span");
    state.className = "badge";
    const action = document.createElement("button");
    action.type = "button";
    action.className = "upload-batch-action";
    const reason = document.createElement("div");
    reason.className = "upload-batch-reason";
    row.appendChild(name);
    row.appendChild(state);
    row.appendChild(action);
    row.appendChild(reason);
    batch.body.appendChild(row);
    entry = {
      key: key,
      fileId: String(item.file_id || ""),
      name: String(item.name || item.filename || key),
      state: "",
      detail: "",
      knowledgeError: "",
      progress: {},
      row: row,
      nameNode: name,
      stateNode: state,
      reasonNode: reason,
      actionNode: action,
      card: null
    };
    batch.items.set(key, entry);
    action.addEventListener("click", function () {
      if (!entry.fileId) { return; }
      if (entry.state === "queued") { cancelQueuedItem(batch, entry); return; }
      openBatchItem(batch, entry);
    });
  }
  entry.fileId = String(item.file_id || entry.fileId || "");
  entry.name = String(item.name || item.filename || entry.name);
  entry.state = String(item.state || entry.state || "unknown");
  entry.detail = String(item.detail || "");
  entry.knowledgeError = String(item.knowledge_error || "");
  entry.progress = item.progress || {};
  entry.nameNode.textContent = entry.name;
  entry.stateNode.textContent = BATCH_STATE_LABELS[entry.state] || entry.state;
  entry.stateNode.className = "badge " + batchBadgeClass(entry.state);
  const reason = entry.knowledgeError || entry.detail;
  entry.reasonNode.textContent = reason;
  entry.reasonNode.hidden = !reason;
  entry.row.dataset.state = entry.state;
  entry.row.classList.toggle("attention", Boolean(BATCH_ATTENTION[entry.state]));
  entry.actionNode.hidden = !entry.fileId || entry.state === "cancelled" || entry.state === "duplicate";
  entry.actionNode.textContent = entry.state === "queued"
    ? "取消"
    : (BATCH_ATTENTION[entry.state] ? "处理" : "查看");
  entry.actionNode.title = entry.state === "queued"
    ? "取消排队；原件与上传留档保留"
    : (BATCH_ATTENTION[entry.state]
      ? "展开这份资料，执行入库 / 跳过 / 修改正文 / 重试"
      : "展开查看这份资料的状态");
  return entry;
}

function openBatchItem(batch, entry) {
  if (!batch || !entry || !entry.fileId) { return; }
  batch.detail.hidden = false;
  batch.detail.innerHTML = "";
  const card = createUploadCard(entry.name || entry.fileId, batch.detail);
  card.__fileId = entry.fileId;
  card.__batch = batch;
  card.__deferCompanion = true;
  card.__policy = "unified";
  card.__policyDone = true;
  entry.card = card;
  trackCard(entry.fileId, card);
  setBatchOpen(batch, true);
  fetchIngestStatus(card, entry.fileId);
  try { card.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (err) { /* 兼容 */ }
}

async function cancelQueuedItem(batch, entry) {
  if (!batch || !entry || !entry.fileId) { return; }
  const node = entry.actionNode;
  node.disabled = true;
  node.textContent = "取消中…";
  try {
    const response = await fetch("/ingest/action/" + encodeURIComponent(entry.fileId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "cancel" })
    });
    let data = {};
    try { data = await response.json(); } catch (err) { data = {}; }
    if (!response.ok) {
      setToast(errorText(data, response.status), "bad");
    } else {
      setToast("已取消排队；原件与上传留档保留", "ok");
    }
  } catch (err) {
    setToast("取消失败：" + err.message, "bad");
  }
  await refreshBatchOnce(batch);
  node.disabled = false;
}

function addBatchLocalError(batch, name, detail) {
  if (!batch) { return; }
  batch.localErrors += 1;
  ensureBatchItem(batch, {
    tempId: "localerr_" + batch.localErrors + "_" + Date.now(),
    name: name || "未命名文件",
    state: "error",
    detail: String(detail || "上传失败")
  });
  updateBatchUi(batch, batch.lastData);
}

function addBatchDuplicate(batch, name, detail) {
  if (!batch) { return; }
  batch.duplicates += 1;
  batch.uploaded += 1;
  ensureBatchItem(batch, {
    tempId: "dup_" + batch.duplicates + "_" + Date.now(),
    name: name || "未命名文件",
    state: "duplicate",
    detail: String(detail || "相同内容已存在，未重复入库")
  });
  updateBatchUi(batch, batch.lastData);
}

function batchCounts(batch, data) {
  const counts = (data && data.counts) || {};
  const queued = Number(counts.queued || 0);
  const processing = ["processing", "parsing", "checking", "ocr"]
    .reduce(function (sum, key) { return sum + Number(counts[key] || 0); }, 0);
  const review = Number(counts.review || 0) + Number(counts.needs_ocr || 0);
  const blocked = Number(counts.blocked || 0);
  const failed = Number(counts.failed || 0) + Number(batch.localErrors || 0);
  const done = Number(counts.done || 0);
  const skipped = Number(counts.skipped || 0);
  const cancelled = Number(counts.cancelled || 0);
  const duplicates = Number(batch.duplicates || 0);
  const ingesting = Number(counts.ingesting || 0);
  const uploaded = Math.max(
    data ? Number(data.total || 0) : 0,
    Number(batch.uploaded || 0)
  );
  const total = Math.max(Number(batch.expectedTotal || 0), uploaded);
  const active = queued + processing + ingesting;
  const terminal = done + skipped + failed + cancelled + duplicates;
  return {
    total: total,
    uploaded: uploaded,
    queued: queued,
    processing: processing,
    review: review,
    blocked: blocked,
    failed: failed,
    done: done,
    skipped: skipped,
    cancelled: cancelled,
    duplicates: duplicates,
    ingesting: ingesting,
    active: active,
    terminal: terminal,
    waiting: review + blocked + failed
  };
}

function batchSummaryParts(counts, uploadDone) {
  const parts = [];
  if (!uploadDone) {
    parts.push("已提交 " + counts.uploaded + "/" + counts.total);
  } else {
    parts.push("共 " + counts.total + " 份");
  }
  if (counts.queued) { parts.push("排队 " + counts.queued); }
  if (counts.processing) { parts.push("解析 " + counts.processing); }
  if (counts.review) { parts.push("待确认 " + counts.review); }
  if (counts.blocked) { parts.push("需修复 " + counts.blocked); }
  if (counts.ingesting) { parts.push("入库中 " + counts.ingesting); }
  if (counts.done) { parts.push("完成 " + counts.done); }
  if (counts.skipped) { parts.push("跳过 " + counts.skipped); }
  if (counts.cancelled) { parts.push("已取消 " + counts.cancelled); }
  if (counts.duplicates) { parts.push("已存在 " + counts.duplicates); }
  if (counts.failed) { parts.push("失败 " + counts.failed); }
  return parts;
}

function updateBatchCompanion(batch, counts) {
  if (!window.WorkCompanion || batch.finished) { return; }
  const detail = batchSummaryParts(counts, batch.uploadDone).join(" · ");
  if (batch.uploadDone && counts.total > 0 && counts.terminal >= counts.total
      && counts.active === 0 && counts.review === 0 && counts.blocked === 0) {
    batch.finished = true;
    window.WorkCompanion.finish(batch.workId, {
      category: "upload",
      batch: true,
      fileCount: counts.total,
      waitingCount: counts.review + counts.blocked + counts.failed,
      activeCount: counts.active,
      doneCount: counts.done + counts.skipped + counts.cancelled + counts.duplicates,
      motion: counts.failed ? "error" : "success",
      label: counts.failed ? "资料批次完成（含失败）" : "资料批次完成",
      detail: detail,
      detailAction: function () { openUploadBatch(batch); }
    });
    return;
  }
  if (counts.review || counts.blocked || counts.failed) {
    window.WorkCompanion.update(batch.workId, {
      category: "upload",
      batch: true,
      fileCount: counts.total,
      waitingCount: counts.review + counts.blocked + counts.failed,
      activeCount: counts.active,
      doneCount: counts.done + counts.skipped + counts.cancelled + counts.duplicates,
      motion: "waiting",
      label: "资料批次需要处理",
      detail: detail,
      detailAction: function () { openUploadBatch(batch); }
    });
  } else {
    window.WorkCompanion.update(batch.workId, {
      category: "upload",
      batch: true,
      fileCount: counts.total,
      waitingCount: counts.review + counts.blocked + counts.failed,
      activeCount: counts.active,
      doneCount: counts.done + counts.skipped + counts.cancelled + counts.duplicates,
      motion: "working",
      label: "资料批次处理中",
      detail: detail,
      detailAction: function () { openUploadBatch(batch); }
    });
  }
}

function updateBatchUi(batch, data) {
  if (!batch) { return; }
  const counts = batchCounts(batch, data);
  const parts = batchSummaryParts(counts, batch.uploadDone);
  batch.title.textContent = "资料批次 · " + counts.total + " 份" + (batch.uploadDone ? "" : "（上传中）");
  batch.summary.textContent = parts.join(" · ");
  batch.toggle.textContent = batch.body.hidden
    ? ("展开明细" + (batch.items.size ? "（" + batch.items.size + "）" : ""))
    : "收起明细";
  const pct = counts.total ? Math.min(100, Math.round(counts.terminal / counts.total * 100)) : 0;
  batch.bar.classList.toggle("determinate", counts.total > 0);
  const rail = batch.bar.querySelector("i");
  if (rail) { rail.style.width = pct + "%"; }
  if (data && Number(data.items_total || 0) > (data.items || []).length) {
    batch.summary.textContent += " · 另有 " + (Number(data.items_total) - (data.items || []).length)
      + " 份未列出，请在文件管理查看";
  }
  updateBatchCompanion(batch, counts);
  if (!batch.autoOpened && batch.uploadDone
      && (counts.review || counts.blocked || counts.failed) && batch.items.size) {
    batch.autoOpened = true;
    setBatchOpen(batch, true);
    setToast("资料批次中有 " + (counts.review + counts.blocked + counts.failed)
      + " 份需要你处理，点击「处理」逐份确认", "ok");
  }
}

function applyBatchData(batch, data) {
  if (!batch || !data) { return; }
  batch.lastData = data;
  (data.items || []).forEach(function (item) {
    const entry = ensureBatchItem(batch, item);
    if (entry && entry.card && !entry.card.__batchFetching
        && String(entry.card.__state || "") !== String(item.state || "")) {
      entry.card.__batchFetching = true;
      fetchIngestStatus(entry.card, entry.fileId).finally(function () {
        entry.card.__batchFetching = false;
      });
    }
  });
  updateBatchUi(batch, data);
}

function batchAfterAction(card) {
  const batch = card && card.__batch;
  if (!batch) { return; }
  refreshBatchOnce(batch).then(function (data) {
    if (data && data.groups && Number(data.groups.active || 0) > 0) {
      batchRefresh(batch, true);
    }
  });
}

async function refreshBatchOnce(batch) {
  if (!batch) { return null; }
  try {
    const response = await fetch("/ingest/batch/" + encodeURIComponent(batch.id), { cache: "no-store" });
    if (!response.ok) { return null; }
    const data = await response.json();
    applyBatchData(batch, data);
    return data;
  } catch (err) {
    return null;
  }
}

async function batchRefresh(batch, immediate) {
  if (!batch || batch.polling) { return; }
  batch.polling = true;
  let first = Boolean(immediate);
  try {
    for (let attempt = 0; attempt < BATCH_MAX_POLLS; attempt += 1) {
      await sleep(first ? 250 : BATCH_POLL_MS);
      first = false;
      const data = await refreshBatchOnce(batch);
      if (data) {
        const counts = batchCounts(batch, data);
        if (batch.uploadDone && data.complete) { break; }
        if (batch.uploadDone && counts.active === 0 && counts.total > 0
            && counts.terminal >= counts.total) {
          break;
        }
        if (batch.uploadDone && counts.active === 0
            && (Number(data.total || 0) > 0 || batch.localErrors >= batch.expectedTotal)) {
          break;
        }
      }
      if (batch.uploadDone && batch.items.size === 0 && batch.localErrors === 0 && attempt > 2) {
        break;
      }
    }
  } finally {
    batch.polling = false;
    if (!batch.finished) { updateBatchUi(batch, batch.lastData); }
  }
}

function finishBatchUploads(batch) {
  if (!batch) { return; }
  batch.uploadDone = true;
  updateBatchUi(batch, batch.lastData);
  batchRefresh(batch, true);
}

function setUploadState(card, state, detail, data) {
  const badge = card.querySelector(".badge");
  const sub = card.querySelector(".upload-sub");
  const table = {
    uploading: ["上传中", "uploading", "正在上传文件…"],
    queued: ["排队中", "processing", "已进入本地解析队列，等待后台 worker 处理"],
    parsing: ["本地解析中", "processing", "正在本地解析文档内容…"],
    checking: ["校验中", "processing", "正在做质量校验…"],
    needs_ocr: ["需要 OCR", "needs_ocr", "本地提取不到足够文字：可用「图片 / 扫描件」入口或点「OCR 解析」"],
    ocr: ["OCR 识别中", "processing", "正在调用 OCR 识别…"],
    ingesting: ["入库中", "processing", "已通过质量校验，正在提交知识库…"],
    processing: ["解析中", "processing", "已提交知识库，正在解析并构建索引"],
    done: ["✓ 已入库", "done", "已通过质量校验并入库，可以提问相关内容了"],
    // 质量门通过 ≠ 直接入库，停下来等你点头（确认门）
    review: ["待确认入库", "review", "已通过质量校验，等你确认后才会进知识库"],
    skipped: ["已跳过", "skipped", "已跳过：这个文件没有入库（点「仍要入库」可以反悔）"],
    cancelled: ["已取消", "skipped", "已取消排队；原件与上传留档保留"],
    duplicate: ["已存在", "skipped", "相同内容已存在，未重复入库"],
    blocked: ["✗ 未通过校验", "blocked", "未通过质量校验，已拦截：可先「修改正文」再重试，或点「仍要入库」自己担责放行"],
    failed: ["✗ 处理失败", "failed", "处理失败，可点击「重试」或查看 logs/app.log"],
    error: ["✗ 上传失败", "failed", "上传失败"],
    unknown: ["已提交", "unknown", "已提交（暂无法获取处理进度）"]
  };
  const item = table[state] || table.unknown;
  // 已入库但知识库还在建索引：徽章继续跳点，别让用户以为"已入库" = 已经能提问了
  const indexing = state === "done" && data && (data.progress || {}).state === "processing";
  setBadge(card, item[0], Boolean(BUSY_STATES_UI[state]) || Boolean(indexing));
  badge.className = "badge " + item[1];
  sub.textContent = detail || item[2];
  updateUploadProgress(card, data || { state: state });
  if (card.__workId && !card.__deferCompanion) {
    const reason = data && (data.knowledge_error
      || (data.lightrag && data.lightrag.error)
      || (data.knowledge && data.knowledge.message));
    WorkCompanion.ingest(card.__workId, {
      state: state,
      name: card.__fileName || "",
      detail: reason || detail || item[2],
      progress: (data && data.progress) || {}
    }, {
      detailAction: function () {
        if (card.__upstream && card.__upstream.parentNode) {
          card.__upstream.open = true;
        }
        revealIngestCard(card);
      }
    });
  }
  scrollBottom();
}

// ---------- 解析 / 质检 / 入库状态与人工动作 ----------

function sleep(ms) {
  return new Promise(function (resolve) { setTimeout(resolve, ms); });
}

function ocrScopeOf(data) {
  return ((data.ocr || {}).scope) || "";
}

function ocrButtonLabel(data) {
  const pending = data.unocr_images || 0;
  if (ocrScopeOf(data) === "images" && pending > 0) {
    return "识别图片（" + pending + " 张）";
  }
  return "OCR 解析";
}

function describeIngest(data) {
  // 确认门 / 已跳过的卡片，细节都在下面的建议面板里，副标题只保留一句话
  if (data.state === "review") {
    return data.detail || "已通过质量校验，等你确认后才会进知识库";
  }
  if (data.state === "skipped") {
    return data.detail || "已跳过：这个文件没有入库";
  }
  if (data.state === "queued" || data.state === "cancelled" || data.state === "duplicate") {
    return data.detail || "";
  }
  const parts = [];
  if (data.doc_name) {
    // 图片资料的文档名由用户定 —— 让他一眼看到「入库后叫什么」
    parts.push("入库为「" + data.doc_name + "」" + (data.manual_text ? "（你写的描述）" : ""));
  }
  if (data.detail) { parts.push(data.detail); }
  const quality = data.quality || {};
  if (quality.score) { parts.push("质量分 " + quality.score); }
  const reasons = quality.reasons || [];
  if (reasons.length) { parts.push(reasons[0]); }
  if (quality.metrics && quality.metrics.char_total) {
    parts.push("有效字符 " + quality.metrics.char_total);
  }
  if (data.image_total) {
    const done = data.image_total - (data.unocr_images || 0);
    parts.push("内嵌图片 " + done + "/" + data.image_total + " 已识别");
  }
  const progress = data.progress || {};
  if (progress.total > 0 && (data.state === "done" || data.state === "processing")) {
    parts.push("知识库解析 " + (progress.processed || 0) + "/" + progress.total);
  }
  if (data.state === "ingesting" && !(progress.total > 0)) {
    // 点了「入库」之后有一段没有进度的空窗（还在受理），说清楚免得用户以为卡住了
    parts.push("正在等知识库受理（通常几秒到半分钟）");
  }
  if (data.state === "needs_ocr") {
    if (data.is_image && (data.can || {}).edit) {
      // 图片没有文字可提取，写一段描述就能入库 —— 别让用户以为「必须先配 OCR」
      parts.push("图片可以直接点「写描述」入库（写清这是什么，比识别出的字更好找）");
    }
    if (!data.ocr_enabled) {
      parts.push("如需识别：请到「设置 → OCR 识别」开启并选择提供方（默认不外发；保存后需重启服务）");
    } else if (data.ocr_external && data.ocr_image_ocr !== false
        && ocrScopeOf(data) === "images" && (data.unocr_images || 0) > 0) {
      parts.push("注意：「识别图片」会把图片发送到云端 OCR 服务");
    }
  }
  return parts.join(" · ");
}

function renderIngestActions(card, data) {
  const box = card.__actions;
  if (!box) { return; }
  const can = data.can || {};
  if (card.__busy) {
    // 动作在途 —— 留一个禁用的按钮当场给反馈，绝不在这期间变回可点。
    // 以前点了之后按钮先消失、状态 2.5 秒后才回来，用户以为「没点上」继续点，
    // 每一次都会真的入库一次（日志里 2 秒内 11 次动作就是这么来的）。
    box.innerHTML = "";
    const pending = document.createElement("button");
    pending.textContent = (card.__busyLabel || "正在提交") + "…";
    pending.className = "ghost";
    pending.disabled = true;
    box.appendChild(pending);
    // 手动兜底：万一网络/轮询卡住，不用刷新整页，只查这一张卡片的最新状态
    const refresh = document.createElement("button");
    refresh.textContent = "刷新状态";
    refresh.className = "ghost";
    refresh.title = "立即向服务端查一次最新进度（不会重新提交）";
    refresh.addEventListener("click", function () { refreshIngestNow(card); });
    box.appendChild(refresh);
    box.style.display = "flex";
    return;
  }
  const definitions = [];
  // 进行中（包括 OCR / 入库 / 建索引）给一个手动刷新入口：
  // 网络抖动或轮询意外停住时，用户不用刷新整页、也不用去文件管理页绕一圈。
  const indexing = data.state === "done" && (data.progress || {}).state === "processing";
  if (BUSY_STATES_UI[data.state] || indexing) {
    definitions.push(["刷新状态", "refresh", "ghost",
      "立即向服务端查一次最新进度（不会重新提交）"]);
  }
  if (can.ocr) { definitions.push([ocrButtonLabel(data), "ocr", ""]); }
  // 确认门上的两个动作 —— 入库（放行）/ 跳过（这个文件不进知识库）
  if (can.ingest) {
    definitions.push(["✓ 入库", "ingest", "confirm",
      "把当前正文提交知识库；确认后这份内容会被知识库引用"]);
  }
  if (can.skip) {
    definitions.push(["跳过", "skip", "ghost",
      "这次不入库，但本机解析结果与原件保留；之后可点「仍要入库」反悔"]);
  }
  if (can.resume) {
    definitions.push(["仍要入库", "resume", "confirm",
      "把这份已跳过的内容重新提交知识库；本机原件与解析结果不会变动"]);
  }
  if (can.cancel) {
    definitions.push(["取消排队", "cancel", "ghost",
      "把这份文件从解析队列移除；原件与上传留档保留，可重新上传或删除"]);
  }
  // 「仍要入库」= 人工推翻质量校验的出口：质检会一直跑，只有它被拦下时这个按钮才出现。
  // 点了会在档案里留下 forced 记录（可事后复查这份是怎么进来的），所以把后果写在悬停提示里。
  if (can.force && data.state !== "done") {
    definitions.push(["仍要入库", "force", "ghost",
      "这份文件没通过质量校验，但你要按原样入库。\n\n"
      + "· 入库的是当前解析出的文本，乱码/空壳会一起进去，提问可能检索不到有效内容；\n"
      + "· 会留下一条「已强制入库」记录（含当时的校验原因），事后可复查；\n"
      + "· 更稳妥的做法：先在卡片上点「✎ 编辑正文」修好，再点「重试」。"]);
  }
  if (can.retry) { definitions.push(["重试", "retry", "ghost"]); }  // 正文可以改（只有停下来的作业能改 —— 忙 / 已入库由后端 can.edit 说了算）
  //  / OCR：图片资料分两种情况 ——
  //   · OCR 已经识别出文字：除了「写描述」，还要能改**完整正文**（OCR 结果本身）；
  //   · 纯图片（没有可提取文字）：只给「写描述」，描述就是入库内容。
  if (can.edit) {
    const imageJob = Boolean(data.is_image);
    const ocrBlock = data.ocr || {};
    const hasOcrText = Boolean(ocrBlock.used) && Number(data.content_chars || 0) > 0;
    if (card.__editor) {
      definitions.push([
        card.__editorMode === "caption" ? "收起描述" : "收起修改", "edit", "ghost"
      ]);
    } else if (imageJob && !hasOcrText) {
      definitions.push(["✎ 写描述", "edit-caption", "confirm"]);
    } else if (imageJob) {
      definitions.push(["✎ 修改正文", "edit-full", "ghost"]);
      definitions.push(["✎ 写描述", "edit-caption", "ghost"]);
    } else {
      definitions.push(["修改正文", "edit-full", "ghost"]);
    }
  }
  if (data.state === "done") {
    definitions.push(["继续提问", "focus-input", "confirm"]);
    definitions.push(["继续上传", "upload-more", "ghost"]);
  }
  if (data.knowledge_error || data.state === "failed" || data.state === "blocked") {
    definitions.push(["文件管理", "files", "ghost"]);
  }
  box.innerHTML = "";
  if (!definitions.length) { box.style.display = "none"; return; }
  definitions.forEach(function (item) {
    const button = document.createElement("button");
    button.textContent = item[0];
    if (item[2]) { button.className = item[2]; }
    if (item[3]) { button.title = item[3]; }        // 危险/不可逆的动作把后果写在悬停提示里
    button.addEventListener("click", function () {
      if (item[1] === "refresh") { refreshIngestNow(card); return; }
      if (item[1] === "edit") { toggleEditor(card, card.__fileId); return; }
      if (item[1] === "edit-full") { toggleEditor(card, card.__fileId, "full"); return; }
      if (item[1] === "edit-caption") { toggleEditor(card, card.__fileId, "caption"); return; }
      if (item[1] === "focus-input") {
        input.focus();
        scrollBottom(true);
        return;
      }
      if (item[1] === "upload-more") { pickFile("unified"); return; }
      if (item[1] === "files") {
        const batchId = card.__batch && card.__batch.id ? card.__batch.id : "";
        window.open("/files" + (batchId ? "?batch=" + encodeURIComponent(batchId) : ""),
          "_blank", "noopener");
        return;
      }
      runIngestAction(card, card.__fileId, item[1], item[0]);
    });
    box.appendChild(button);
  });
  box.style.display = "flex";
}

// 「仍要入库」是质检的唯一出口，也是这张卡片上最危险的动作 ——
// 上一版只有悬停提示（手机上等于没有），用户点完才看到后果。现在把后果**写在卡片上**，
// 点下去还会再确认一次（详见 runIngestAction 的 force 分支）。
const FORCE_NOTE_HINT = "点「仍要入库」会把现在这份正文直接写进知识库（不再经确认门），"
  + "档案里会记下「人工放行」；以后提问可能引用到这份内容。";

function renderForceNote(card, data) {
  const main = card.querySelector(".upload-main");
  if (!main) { return; }
  const can = data.can || {};
  const show = Boolean(can.force) && data.state !== "done" && !card.__busy;
  if (!show) {
    if (card.__forceNote && card.__forceNote.parentNode) {
      card.__forceNote.parentNode.removeChild(card.__forceNote);
    }
    card.__forceNote = null;
    return;
  }
  const reasons = (((data.quality || {}).reasons) || []).filter(Boolean);
  if (!card.__forceNote) {
    const panel = document.createElement("div");
    panel.className = "force-note";
    card.__forceNote = panel;
    main.insertBefore(panel, card.__actions);
  }
  card.__forceNote.innerHTML = "";
  const head = document.createElement("div");
  head.className = "force-head";
  head.textContent = "质检已跑完，结论是「没通过」" + (reasons.length ? "：" + reasons[0] : "");
  const body = document.createElement("div");
  body.className = "force-body";
  body.textContent = "想入库有两条路：① 先点「修改正文」把有问题的地方改好，再点「重试」（推荐）；"
    + "② " + FORCE_NOTE_HINT;
  card.__forceNote.appendChild(head);
  card.__forceNote.appendChild(body);
}

// 这些状态意味着"轮到用户做决定了"：卡片从识别中变成待确认时，
// 操作按钮可能在视口下方（卡片变高 + 聊天区没有跟随），看起来就像"没跳转、输入框挡住了"。
// 只滚这一次，把操作区带进视野；不改变 stickToBottom，用户仍可自由上滑。
const REVEAL_INGEST_STATES = { review: 1, needs_ocr: 1, blocked: 1, failed: 1 };

function revealIngestCard(card) {
  if (!card || !card.isConnected) { return; }
  const target = (card.__actions && card.__actions.offsetParent) ? card.__actions : card;
  const pane = chatArea;
  const paneRect = pane.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  const margin = 16;
  let delta = 0;
  if (rect.bottom > paneRect.bottom - margin) {
    delta = rect.bottom - (paneRect.bottom - margin);
  } else if (rect.top < paneRect.top + margin) {
    delta = rect.top - (paneRect.top + margin);
  }
  if (!delta) { return; }
  const reduce = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  pane.scrollTo({
    top: Math.max(0, pane.scrollTop + delta),
    behavior: reduce ? "auto" : "smooth"
  });
}

function renderOutcomeNote(card, data) {
  const main = card.querySelector(".upload-main");
  if (!main) { return; }
  const knowledgeError = String((data && data.knowledge_error) || "");
  let kind = "";
  let text = "";
  if (data && data.state === "done") {
    kind = "success";
    text = "已入库，可以继续提问。处理过程已收起，需要时点「处理过程」展开。";
  } else if (knowledgeError) {
    kind = "failure";
    text = "入库失败。原件与解析结果保留：可点「入库」重试，"
      + "或点「文件管理」查看记录与失败原因。";
  } else if (data && data.state === "failed") {
    kind = "failure";
    text = "解析失败。可点「重试」；仍失败时请修改原件后重新上传，"
      + "或点「文件管理」保留留档。";
  } else if (data && data.state === "blocked") {
    kind = "failure";
    text = "未通过质量校验。可先「修改正文」再「重试」，或「跳过」保留留档；"
      + "需要人工放行可点「仍要入库」。";
  }
  if (!text) {
    if (card.__outcomeNote && card.__outcomeNote.parentNode) {
      card.__outcomeNote.parentNode.removeChild(card.__outcomeNote);
    }
    card.__outcomeNote = null;
    return;
  }
  if (!card.__outcomeNote) {
    card.__outcomeNote = document.createElement("div");
  }
  const note = card.__outcomeNote;
  note.className = "outcome-note " + kind;
  note.textContent = text;
  const anchor = (card.__steps && card.__steps.parentNode === main)
    ? card.__steps
    : card.__actions;
  if (note.parentNode !== main || note.nextSibling !== anchor) {
    main.insertBefore(note, anchor);
  }
}

function renderIngest(card, data) {
  card.__lastStatusAt = Date.now();   // 自愈看门狗用：多久没拿到过新状态
  const previousState = String(card.__lastIngestState || "");
  card.__lastIngestState = data.state || "";
  card.__state = data.state || "";
  card.__data = data;          // 编辑面板要复用同一份状态（按钮列表 / 长度上限）
  setUploadState(card, data.state || "unknown", describeIngest(data), data);
  renderIngestActions(card, data);
  renderForceNote(card, data);
  renderReviewPanel(card, data);
  renderTimeline(card, data);
  renderOutcomeNote(card, data);
  renderUpstreamDetail(card, data);
  // 必须放在 renderTimeline 之后：详情开关的文案要报「处理过程 N 步」
  updateReviewToggle(card, data);
  // 第一次进入"需要你决定"的状态：等两帧让面板/按钮完成布局，再把操作区滚进视野 + 亮一下。
  if (REVEAL_INGEST_STATES[data.state] && previousState !== data.state) {
    requestAnimationFrame(function () {
      requestAnimationFrame(function () {
        revealIngestCard(card);
        card.classList.remove("attention");
        void card.offsetWidth;          // 重启动画
        card.classList.add("attention");
        window.setTimeout(function () { card.classList.remove("attention"); }, 1600);
      });
    });
  }
}

function renderUpstreamDetail(card, data) {
  // 处理失败时，除了「人话原因」（副标题里），再给一个可展开的知识库原文。
  // 排障时不用去翻 logs/app.log —— 用户看到的和日志里的一致（result.json 的 lightrag.error）。
  const main = card.querySelector(".upload-main");
  if (!main) { return; }
  const info = data.lightrag || {};
  const message = String(info.error || "").trim();
  const show = Boolean(message) && (data.state === "failed" || data.state === "blocked");
  if (!show) {
    if (card.__upstream && card.__upstream.parentNode) {
      card.__upstream.parentNode.removeChild(card.__upstream);
    }
    card.__upstream = null;
    return;
  }
  if (!card.__upstream) {
    const details = document.createElement("details");
    details.className = "review-upstream";
    const summary = document.createElement("summary");
    const pre = document.createElement("pre");
    details.appendChild(summary);
    details.appendChild(pre);
    card.__upstream = details;
    main.insertBefore(details, card.__actions);   // 放在按钮上方
  }
  const head = "查看知识库原始返回";
  card.__upstream.querySelector("summary").textContent =
    info.http_status ? head + "（HTTP " + info.http_status + "）" : head;
  card.__upstream.querySelector("pre").textContent = message;
}

// ---------- 入库确认门（停下来让你决定「这份资料要不要进知识库」）----------
// 三个概念别混在一起：
//   质量门（quality.py）→ 能不能入库，硬阈值，不通过根本到不了这里；
//   智能建议（advice.py）→ 值不值得入库，只给提示与理由；
//   确认门（本段）→ 最终由你点「入库 / 跳过」，页面只负责把「为什么建议你看一眼」摆出来。
const ADVICE_TAGS = { ok: "建议入库", attention: "再看一眼", risk: "谨慎入库" };
const SESSION_ALLOW_TEXT = "本次运行内全部允许（后续文件直接入库，不再逐个确认）";
// 本次运行的开闸状态：由 GET /ingest/pending 同步，POST /ingest/session 修改（只存在内存里）
let SESSION_CONFIRM = { allow_all: false, mode: "always", ttl_hours: 24 };
// 页面上的上传卡片（fileId → card）：打开「全部允许」后，停在确认门上的卡片要继续跑
const UPLOAD_CARDS = [];

function trackCard(fileId, card) {
  if (!fileId || !card) { return; }
  for (let i = 0; i < UPLOAD_CARDS.length; i++) {
    if (UPLOAD_CARDS[i].fileId === fileId) { UPLOAD_CARDS[i].card = card; return; }
  }
  UPLOAD_CARDS.push({ fileId: fileId, card: card });
}

function countText(value) {
  const number = Number(value);
  return isFinite(number) && number > 0 ? String(number) : "0";
}

function reviewStatsText(data) {
  const stats = (data.advice || {}).stats || {};
  const chars = (stats.chars === undefined || stats.chars === null) ? data.content_chars : stats.chars;
  const parts = [];
  if (data.doc_name) {
    // 入库后叫什么，比「有效字符多少」更该先看到（尤其图片是用户自己起的名字）
    parts.push("入库为「" + data.doc_name + "」");
  }
  parts.push("有效字符 " + countText(chars));
  if (stats.lines !== undefined) { parts.push(countText(stats.lines) + " 行"); }
  parts.push("来源 " + (stats.route_label || data.route_label || "未知"));
  if (stats.pages) { parts.push(stats.pages + " 页"); }
  if (stats.images_total) {
    const done = stats.images_total - (stats.images_pending || 0);
    parts.push("图片 " + done + "/" + stats.images_total + " 已识别");
  }
  return parts.join(" · ");
}

function renderReviewPanel(card, data) {
  const main = card.querySelector(".upload-main");
  if (!main) { return; }
  if (data.state !== "review") {
    // 只有「时间线在待确认面板里、随面板一起被移除」时才清缓存。
    // 处理中（解析 / OCR / 入库）时间线是直接挂在 main 上的 —— 这里如果无脑置 null，
    // renderTimeline() 会以为没有时间线、再追加一块，于是每次轮询都多一段"处理过程"，
    // 看起来就是卡片里在不停"刷屏打印"。这是本次实际问题的根因。
    const stepsInPanel = Boolean(card.__review && card.__steps
      && card.__review.contains(card.__steps));
    if (card.__review && card.__review.parentNode) { card.__review.parentNode.removeChild(card.__review); }
    card.__review = null;
    card.__reviewMain = null;
    card.__reviewMore = null;
    card.__reviewMoreMain = null;
    card.__reviewToggle = null;
    card.__reviewOpen = false;
    if (stepsInPanel || (card.__steps && !card.__steps.isConnected)) {
      card.__steps = null;
      card.__stepsSig = "";
    }
    return;
  }
  const advice = data.advice || {};
  if (!card.__review) {
    const panel = document.createElement("div");
    panel.className = "review-panel";
    // 三个子容器一次建好，之后每次重绘只动前两个：
    //   .rv-keep      = 留在外面、每次都要重算的部分（结论 / 未通过项 / 统计 / 复选框 / 详情开关）
    //   .rv-more-main = 折进详情的、每次重算的部分（其余 N 项通过 / 正文预览）
    //   .rv-more      = 折叠容器本身。**它的 innerHTML 永远不清空**，因为
    //                   renderTimeline() 会把「处理过程」追加进来，
    //                   整块重绘会把时间线冲掉（这是这套缓存最容易踩的坑）。
    panel.innerHTML = '<div class="rv-keep"></div>'
      + '<div class="rv-more" hidden><div class="rv-more-main"></div></div>';
    card.__reviewMain = panel.querySelector(".rv-keep");
    card.__reviewMore = panel.querySelector(".rv-more");
    card.__reviewMoreMain = panel.querySelector(".rv-more-main");
    card.__review = panel;
    main.insertBefore(panel, card.__actions);   // 面板在按钮上方：理由读完再点
  }
  const panel = card.__reviewMain;
  panel.innerHTML = "";
  const moreBox = card.__reviewMoreMain;
  moreBox.innerHTML = "";

  const level = advice.level || "ok";
  const head = document.createElement("div");
  head.className = "review-head " + level;
  const tag = document.createElement("span");
  tag.className = "lv";
  tag.textContent = ADVICE_TAGS[level] || "提示";
  const title = document.createElement("span");
  title.textContent = advice.headline || "已通过质量校验，等你确认后入库";
  head.appendChild(tag);
  head.appendChild(title);
  panel.appendChild(head);

  (advice.reasons || []).slice(0, 3).forEach(function (line) {
    const node = document.createElement("div");
    node.className = "review-reason";
    node.textContent = "· " + line;
    panel.appendChild(node);
  });

  // 检查项：只展开「没通过」的（通过的那几项合并成一行，免得面板比对话还长）
  const checks = advice.checks || [];
  const flagged = checks.filter(function (item) { return item && !item.ok; });
  flagged.forEach(function (item) {
    const node = document.createElement("div");
    node.className = "review-check";
    const name = document.createElement("b");
    name.textContent = (item.name || "检查项") + "：";
    node.appendChild(name);
    node.appendChild(document.createTextNode(item.detail || ""));
    panel.appendChild(node);
  });
  // 通过的那几项合并成一行、正文预览，都折进「详情」里（见 .rv-toggle 的注释）
  const passed = checks.length - flagged.length;
  card.__passedChecks = passed;
  if (passed > 0) {
    const node = document.createElement("div");
    node.className = "review-check";
    node.textContent = "其余 " + passed + " 项检查通过";
    node.title = "内容量 / 文本结构 / 超长单行 / 重复内容 / 乱码比例 / 内嵌图片 / 识别方式 / 个人信息";
    moreBox.appendChild(node);
  }

  if (advice.preview) {
    const preview = document.createElement("div");
    preview.className = "review-preview";
    preview.textContent = advice.preview;
    preview.title = "入库后的正文开头（取前 200 字，空白已压缩）";
    moreBox.appendChild(preview);
  }

  const statsNode = document.createElement("div");
  statsNode.className = "review-stats";
  statsNode.textContent = reviewStatsText(data);
  panel.appendChild(statsNode);

  // 「跳过」到底做了什么，直接写在面板上（不是删除）：
  // 用户不必为了"取消这一次"去猜会不会把原件删掉。
  const skipNote = document.createElement("div");
  skipNote.className = "review-reason";
  skipNote.textContent = "「跳过」= 这次不入库，但本机解析结果与原件保留；之后可点「仍要入库」反悔。";
  panel.appendChild(skipNote);

  const sessionRow = document.createElement("label");
  sessionRow.className = "review-session";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = Boolean(SESSION_CONFIRM.allow_all);
  box.addEventListener("change", function () { setSessionAllowAll(box.checked); });
  const boxText = document.createElement("span");
  boxText.textContent = SESSION_CONFIRM.allow_all ? "已打开「本次运行内全部允许」" : SESSION_ALLOW_TEXT;
  sessionRow.appendChild(box);
  sessionRow.appendChild(boxText);
  panel.appendChild(sessionRow);

  // 「详情 ▾」开关放在最后（紧随它要展开的那块），展开状态记在卡片上、跨重绘保留
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "rv-toggle";
  toggle.hidden = true;
  toggle.addEventListener("click", function () {
    card.__reviewOpen = !card.__reviewOpen;
    updateReviewToggle(card, card.__data || data);
  });
  panel.appendChild(toggle);
  card.__reviewToggle = toggle;
  updateReviewToggle(card, data);
}

// 刷新「详情 ▾」的文案与显隐。文案里带上被折起来的都是什么，
// 用户不展开也知道自己错过了什么（"其余 6 项检查通过 · 正文预览 · 处理过程 4 步"）。
// 由 renderIngest 在最后统一调用，这样 renderTimeline 已经跑过、步数是准的。
function updateReviewToggle(card, data) {
  const btn = card.__reviewToggle;
  const more = card.__reviewMore;
  if (!btn || !more) { return; }
  const advice = (data && data.advice) || {};
  const steps = Array.isArray(data && data.timeline) ? data.timeline.length : 0;
  const passed = Number(card.__passedChecks) || 0;
  const parts = [];
  if (passed > 0) { parts.push("其余 " + passed + " 项检查通过"); }
  if (advice.preview) { parts.push("正文预览"); }
  if (steps) { parts.push("处理过程 " + steps + " 步"); }
  const open = Boolean(card.__reviewOpen);
  btn.hidden = !parts.length;
  more.hidden = !parts.length || !open;
  if (!parts.length) { return; }
  btn.textContent = (open ? "收起详情" : "详情") + "（" + parts.join(" · ") + "）";
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.title = open ? "收起检查项明细 / 正文预览 / 处理过程" : "展开检查项明细 / 正文预览 / 处理过程";
}

// ---------- 处理过程可读化（一步一行）+ 先改正文再入库 ----------
// 两件事互相配合：
//   时间线（服务端给）→ 说清「这一步在做什么、花了多久、现在卡在哪」；
//   编辑面板（本地做）→ 解析结果「基本对但有点脏」时，改完再入库，不用整份放弃。
// 前端只负责画和提交，不做任何判定：能改不能改由后端 can.edit / editable 说了算。

const STEP_MARKS = { done: "✓", current: "…", failed: "✗", skipped: "–", pending: "○" };

function formatSeconds(value) {
  const seconds = Number(value);
  if (!isFinite(seconds) || seconds <= 0) { return ""; }
  if (seconds < 10) { return seconds.toFixed(1) + " 秒"; }
  if (seconds < 60) { return Math.round(seconds) + " 秒"; }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  if (minutes < 60) { return minutes + " 分 " + rest + " 秒"; }
  const hours = Math.floor(minutes / 60);
  return hours + " 小时 " + (minutes - hours * 60) + " 分";
}

const TIMELINE_STEP_LABELS = {
  upload: "上传文件",
  queue: "进入解析队列",
  worker: "后台开始处理",
  parse: "本地解析",
  ocr: "OCR 识别",
  quality: "质量校验",
  edit: "修改正文",
  knowledge: "提交知识库",
  index: "知识库索引",
  resume: "继续入库",
  force: "人工放行",
  retry: "重新排队",
  cancel: "取消排队",
  recover: "重启恢复"
};
const TIMELINE_STEP_HINTS = {
  upload: "文件已保存到本机",
  queue: "等待后台 worker 解析",
  worker: "worker 接手解析",
  parse: "读取并提取正文",
  ocr: "识别图片中的文字",
  quality: "检查内容是否适合入库",
  edit: "保存修改并重新质检",
  knowledge: "提交知识库并建立索引",
  index: "建立向量索引并完成",
  resume: "把跳过内容重新提交",
  force: "按人工选择跳过质检结论",
  retry: "重新排队解析",
  cancel: "已取消，原件与留档保留",
  recover: "服务重启后继续处理"
};

function timelineUiState(value) {
  const state = String(value || "").toLowerCase();
  if (state === "done" || state === "completed" || state === "success" || state === "review") {
    return "done";
  }
  if (state === "failed" || state === "blocked" || state === "needs_ocr" || state === "error") {
    return "failed";
  }
  if (state === "skipped" || state === "cancelled") { return "skipped"; }
  if (state === "current" || state === "processing" || state === "parsing"
      || state === "checking" || state === "ocr" || state === "ingesting"
      || state === "queued") {
    return "current";
  }
  return "pending";
}

function normalizeTimeline(data) {
  const rawRows = Array.isArray(data && data.timeline) ? data.timeline : [];
  return rawRows.map(function (raw, index) {
    const source = raw || {};
    const step = String(source.step || source.key || "step");
    let state = timelineUiState(source.state);
    // 后面还有步骤，说明这一步已经结束；只有最后一行可以保持“进行中”。
    if (index < rawRows.length - 1 && state === "current") { state = "done"; }
    let elapsed = Number(source.elapsed_s);
    if (!isFinite(elapsed) || elapsed <= 0) {
      const nextAt = rawRows[index + 1] && rawRows[index + 1].at;
      if (source.at && nextAt) {
        const delta = (Date.parse(nextAt) - Date.parse(source.at)) / 1000;
        if (isFinite(delta) && delta > 0) { elapsed = delta; }
      }
    }
    let label = String(source.label || TIMELINE_STEP_LABELS[step] || step);
    if (!source.label && step === "worker") {
      const rawState = String(source.state || "").toLowerCase();
      if (rawState === "queued") { label = "等待重试"; }
      else if (rawState === "failed") { label = "后台处理失败"; }
    }
    return {
      key: step + "#" + (index + 1),
      state: state,
      label: label,
      hint: String(source.hint || source.detail || TIMELINE_STEP_HINTS[step] || ""),
      at: String(source.at || ""),
      elapsed_s: elapsed
    };
  });
}

function timelineSpentSeconds(data, rows) {
  if (!rows.length) { return 0; }
  const start = Date.parse(rows[0].at || "");
  if (!isFinite(start)) { return 0; }
  const state = String((data && data.state) || "");
  const terminal = state === "done" || state === "failed" || state === "blocked"
    || state === "needs_ocr" || state === "skipped" || state === "cancelled"
    || state === "review";
  const end = terminal ? Date.parse(rows[rows.length - 1].at || "") : Date.now();
  if (!isFinite(end) || end <= start) { return 0; }
  return (end - start) / 1000;
}

function renderTimeline(card, data) {
  // 过程表全部来自 GET /ingest/status 的 timeline 段：刷新页面、重开浏览器看到的是同一份，
  // 不用盯着「正在解析…」猜。忙碌时每 2.5 秒轮询一次，所以「进行中」那一步的秒数是活的。
  const main = card.querySelector(".upload-main");
  if (!main) { return; }
  const rows = normalizeTimeline(data);
  if (!rows.length) {
    if (card.__steps && card.__steps.parentNode) { card.__steps.parentNode.removeChild(card.__steps); }
    card.__steps = null;
    card.__stepsSig = "";
    return;
  }
  if (!card.__steps) {
    const box = document.createElement("div");
    box.className = "steps";
    const head = document.createElement("div");
    head.className = "steps-head";
    head.setAttribute("role", "button");
    head.tabIndex = 0;
    head.title = "点击展开 / 收起处理过程";
    const title = document.createElement("b");
    title.textContent = "处理过程";
    const total = document.createElement("span");
    head.appendChild(title);
    head.appendChild(total);
    const body = document.createElement("div");
    body.className = "steps-body";
    box.appendChild(head);
    box.appendChild(body);
    card.__steps = box;
    card.__stepsSig = "";
    box.__sync = function () {
      const open = card.__stepsOpen !== false;
      body.hidden = !open;
      head.classList.toggle("closed", !open);
      head.setAttribute("aria-expanded", open ? "true" : "false");
    };
    const toggle = function () {
      card.__stepsOpen = card.__stepsOpen === false;
      box.__sync();
    };
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
    // 有待确认面板时挂进它的详情区（跟着「详情 ▾」一起折叠）；
    // 没有面板（解析中 / 失败 / 已入库）时时间线就是主内容，照旧直接放在按钮上方。
    if (card.__reviewMore) { card.__reviewMore.appendChild(box); }
    else { main.insertBefore(box, card.__actions); }   // 放在按钮上方：过程读完再决定点哪个
  }
  const panel = card.__steps;
  // 清理历史遗留的重复块（旧版本每次轮询多追加一块）；只保留当前这一块。
  Array.prototype.forEach.call(main.querySelectorAll(".steps"), function (node) {
    if (node !== panel && node.parentNode) { node.parentNode.removeChild(node); }
  });
  // 待确认面板出现后，时间线要跟着搬进折叠区；离开待确认时再搬回 main。
  const stepsHome = card.__reviewMore || main;
  if (panel.parentNode !== stepsHome) { stepsHome.appendChild(panel); }
  const isDone = String(data.state || "") === "done";
  if (isDone) {
    if (!card.__stepsAutoCollapsed) {
      card.__stepsOpen = false;
      card.__stepsAutoCollapsed = true;
    }
  } else {
    card.__stepsAutoCollapsed = false;
    if (card.__stepsOpen === undefined) { card.__stepsOpen = true; }
  }
  if (panel.__sync) { panel.__sync(); }
  const spent = formatSeconds(timelineSpentSeconds(data, rows));
  panel.querySelector(".steps-head span").textContent = spent
    ? ("已过 " + spent + (isDone ? "（含知识库建索引）" : ""))
    : (rows.length + " 步");
  // 轮询时只更新"用时"和状态发生变化的行，不整块 innerHTML 重绘：
  // 每 0.8 秒重建一次会让"进行中"的动画反复从头开始，看起来像页面在闪 / 在刷屏。
  const signature = rows.map(function (row) {
    return [row.key, row.state, row.label, row.hint, row.at].join("|");
  }).join("||");
  if (card.__stepsSig === signature) {
    const lines = panel.querySelectorAll(".steps-body .step");
    rows.forEach(function (row, index) {
      const time = lines[index] && lines[index].querySelector(".time");
      if (time) { time.textContent = stepTimeText(row); }
    });
    return;
  }
  card.__stepsSig = signature;
  const body = panel.querySelector(".steps-body");
  body.innerHTML = "";
  rows.forEach(function (row) {
    const state = String(row.state || "pending");
    const line = document.createElement("div");
    line.className = "step " + state;
    line.title = row.at ? ("开始于 " + row.at) : "";
    const mark = document.createElement("span");
    mark.className = "mark";
    if (state === "current") {
      // 进行中的那一步用跳动的三点：一眼看出"它在动"，而不是一个静态的省略号
      mark.innerHTML = DOTS_HTML;
    } else {
      mark.textContent = STEP_MARKS[state] || "○";
    }
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = row.label || row.key || "步骤";
    const hint = document.createElement("span");
    hint.className = "hint";
    hint.textContent = row.hint || "";
    const time = document.createElement("span");
    time.className = "time";
    time.textContent = stepTimeText(row);
    line.appendChild(mark);
    line.appendChild(label);
    line.appendChild(hint);
    line.appendChild(time);
    body.appendChild(line);
  });
}

function stepTimeText(row) {
  const took = formatSeconds(row && row.elapsed_s);
  if (took) { return (row.state === "current" ? "已 " : "") + took; }
  if (row && row.at) {
    return String(row.at).slice(11, 19);   // 还没开始的几步不写「0 秒」，留白更好读
  }
  return "";
}

const EDIT_PANEL_HINT = "改的是本地解析出的正文（content.md）：原件与上传留档一个字节都不动；"
  + "保存后会重新过一遍质量校验，通过之后仍要你点「入库」才会进知识库。";
// 图片资料走「写描述」——图片里没有可提取的文字，用户写的描述就是入库的正文。
// 名字由用户定（后面的短 ID 是这张图的指纹，服务端补、用户改不了），提问时看到的引用就是这个文档名。
// OCR 之后图片作业也能改「完整正文」：面板里看到的就是 content.md（OCR 全文 + 你写的描述块）。
const OCR_FULL_HINT = "下面是 OCR 识别出的完整正文（含你写的描述块）。"
  + "改的是本地 content.md，原件与上传留档一个字节都不动；"
  + "保存后会重新过一遍质量校验，通过之后仍要你点「入库」才会进知识库。";
const CAPTION_HINT = "这张图片里没有可提取的文字，所以下面这段描述就是进知识库的正文："
  + "写清「这是什么 / 什么时候 / 和谁或哪件事有关」，以后提问时才找得到它。"
  + "文档名可以改（短 ID 是这张图的指纹，由服务端补，改不了）。";
// 已经 OCR 出文字之后的「写描述」：描述是追加，不是覆盖识别结果。
const CAPTION_APPEND_HINT = "你写的描述会单独成段追加到正文里（重复保存是替换上一段描述），"
  + "不会覆盖 OCR 识别出的文字。写清「这是什么 / 什么时候 / 和哪件事有关」，以后提问更容易找到它。";

function captionNoteText(payload, hasText) {
  const info = payload || {};
  let text = hasText ? CAPTION_APPEND_HINT : CAPTION_HINT;
  if (info.manual_text) {
    text += " 你已经写过描述：再保存一次是替换上一段描述，不会越堆越多。";
  }
  if (info.doc_name) {
    text += " 入库后的文档名：" + info.doc_name + "。";
  }
  return text;
}

function editNoteText(edit) {
  const info = edit || {};
  let text = EDIT_PANEL_HINT;
  if (info.count) {
    text += " 已改过 " + info.count + " 次（最后一次 " + (info.edited_at || "") + "），上一版留在 "
      + (info.backup || "content.before-edit.md") + "。";
  }
  return text;
}

function errorText(data, status) {
  // 后端把「发生了什么 + 下一步」放在 detail.message 里；别把整段 JSON 甩到用户脸上
  const detail = data && data.detail;
  if (typeof detail === "string" && detail) { return detail; }
  if (detail && typeof detail === "object") { return detail.message || JSON.stringify(detail); }
  return "HTTP " + status;
}

async function toggleEditor(card, fileId, mode) {
  // 点「修改正文 / 写描述」：先取回正文看一眼（改之前总要能看见），能改才开面板，不能改就说清原因
  if (card.__editor) { closeEditor(card); return; }
  if (!fileId) {
    setToast("这个文件还没有处理编号，请重新上传", "bad");
    return;
  }
  let payload = null;
  try {
    const response = await fetch("/ingest/content/" + encodeURIComponent(fileId), { cache: "no-store" });
    let data = {};
    try { data = await response.json(); } catch (err) { data = {}; }
    if (!response.ok) {
      setToast(errorText(data, response.status), "bad");
      return;
    }
    payload = data;
  } catch (err) {
    setToast("取回正文失败：" + err.message, "bad");
    return;
  }
  if (!payload.editable) {
    // 正在处理 / 已经入库 / 还没有正文：原因说清楚，顺手把卡片状态拉回真实值
    setToast(payload.reason || "现在还不能修改正文", "bad");
    pollIngestStatus(card, fileId, { fast: true });
    return;
  }
  // 纯图片（没有可提取的正文）只能写描述；一旦 OCR 出了文字，就可以改完整正文。
  let actual = mode === "caption" ? "caption" : "full";
  if (payload.is_image && !(Number(payload.body_chars || 0) > 0)) { actual = "caption"; }
  openEditor(card, fileId, payload, actual);
}

function openEditor(card, fileId, payload, mode) {
  const main = card.querySelector(".upload-main");
  if (!main) { return; }
  const panel = document.createElement("div");
  panel.className = "edit-panel";
  card.__editor = panel;
  card.__editorMode = mode === "caption" ? "caption" : "full";

  const imageJob = Boolean(payload.is_image);
  const captionMode = card.__editorMode === "caption";
  const head = document.createElement("div");
  head.className = "edit-head";
  const tag = document.createElement("span");
  tag.className = "lv";
  tag.textContent = captionMode ? "写描述" : "改正文";
  const captionMarker = String(payload.content || "").indexOf("ragkb:manual-caption") >= 0;
  const captionHasText = Number(payload.body_chars || 0) > 0
    && (!payload.is_image || captionMarker || !payload.manual_text);
  const title = document.createElement("span");
  if (captionMode) {
    title.textContent = captionHasText
      ? (payload.name || "这份资料") + "：补充描述（追加到 OCR 正文里，不改动识别出的文字）"
      : (payload.name || "这张图片") + "：图片里没有可提取的文字，你写的描述就是入库内容";
  } else if (imageJob) {
    title.textContent = (payload.name || "这份资料") + "：修改 OCR 识别出的完整正文";
  } else {
    title.textContent = (payload.name || "这份资料") + "：改的是本地解析出的正文";
  }
  head.appendChild(tag);
  head.appendChild(title);
  panel.appendChild(head);

  const note = document.createElement("p");
  note.className = "edit-note";
  note.textContent = captionMode
    ? captionNoteText(payload, captionHasText)
    : (imageJob ? OCR_FULL_HINT : editNoteText(payload.edit));
  panel.appendChild(note);

  // 图片资料先定名字（入库后在知识库里显示的名字就是它）
  let nameInput = null;
  if (imageJob) {
    const row = document.createElement("div");
    row.className = "edit-name";
    nameInput = document.createElement("input");
    nameInput.type = "text";
    nameInput.value = payload.doc_name_stem || "";
    nameInput.spellcheck = false;
    nameInput.setAttribute("aria-label", "入库文档名");
    nameInput.title = "入库后在知识库里显示的名字；短 ID 是这张图的指纹，由服务端补，改不了";
    const suffix = document.createElement("span");
    suffix.className = "suffix";
    suffix.textContent = "_" + (payload.doc_id || "") + ".md";
    row.appendChild(nameInput);
    row.appendChild(suffix);
    panel.appendChild(row);
  }

  const area = document.createElement("textarea");
  area.className = "edit-area";
  // 写描述：只回填上一段描述（描述单独成块，不会把你之前写的堆在一起）；
  // 改正文：回填**完整 content.md**（图片作业里就是 OCR 全文 + 描述块）。
  area.value = captionMode ? (payload.caption_text || "") : (payload.content || "");
  area.spellcheck = false;
  area.setAttribute("aria-label", captionMode ? "图片描述" : "正文内容");
  if (captionMode) {
    area.rows = 7;
    area.placeholder = "例如：2026 年 10 月签的租房合同首页，出租方王某，月租 4200 元，房子在××小区 3 号楼。";
  }
  panel.appendChild(area);

  const bar = document.createElement("div");
  bar.className = "edit-bar";
  const saveBtn = document.createElement("button");
  saveBtn.textContent = captionMode ? "保存描述" : "保存";
  saveBtn.title = captionMode
    ? "保存这段描述并重新质检，仍停在确认门等你决定是否入库"
    : "保存后重新过质量校验，仍停在确认门等你决定是否入库";
  const ingestBtn = document.createElement("button");
  ingestBtn.textContent = "保存并入库";
  ingestBtn.className = "primary";
  ingestBtn.title = "保存 + 重新质检；质检通过就直接入库（等价于再点一次「入库」）";
  const cancelBtn = document.createElement("button");
  cancelBtn.textContent = "放弃修改";
  cancelBtn.title = "只关掉编辑框（已经保存过的改动不会回滚）";
  const count = document.createElement("span");
  count.className = "edit-count";
  bar.appendChild(saveBtn);
  bar.appendChild(ingestBtn);
  bar.appendChild(cancelBtn);
  bar.appendChild(count);
  panel.appendChild(bar);

  const maxChars = Number(payload.max_chars) || 0;
  let busy = false;
  const refreshCount = function () {
    const size = area.value.length;
    const over = maxChars > 0 && size > maxChars;
    count.textContent = (captionMode ? "描述 " : "共 ") + size + " 字"
      + (maxChars ? "（上限 " + maxChars + " 字）" : "");
    count.className = "edit-count" + (over ? " warn" : "");
    if (!busy) {
      const empty = !area.value.trim();
      saveBtn.disabled = empty || over;
      ingestBtn.disabled = empty || over;
    }
  };
  const saveLabel = captionMode ? "保存描述" : "保存";
  const ingestLabel = "保存并入库";
  const setBusy = function (flag, label, activeButton) {
    busy = flag;
    saveBtn.disabled = flag;
    ingestBtn.disabled = flag;
    cancelBtn.disabled = flag;
    saveBtn.textContent = saveLabel;
    ingestBtn.textContent = ingestLabel;
    if (flag && activeButton) {
      activeButton.textContent = (label || "正在保存") + "…";
    }
  };
  area.addEventListener("input", refreshCount);
  cancelBtn.addEventListener("click", function () { closeEditor(card); });

  const submit = async function (ingestNow) {
    if (busy) { return; }
    const text = area.value;
    if (!text.trim()) {
      setToast(captionMode ? "描述不能为空：不想入库请点「跳过」" : "正文不能为空：不想入库请点「跳过」", "bad");
      return;
    }
    if (maxChars > 0 && text.length > maxChars) {
      setToast("正文太长（上限 " + maxChars + " 字）：请分成几份资料分别上传", "bad");
      return;
    }
    // 图片资料提交的是「描述 + 文档名」——描述追加进正文（不覆盖），
    // 名字由后端收敛成 `<名字>_<短ID>.md`（非法字符、超长、重复提交都在后端处理）。
    const body = { content: text, ingest: Boolean(ingestNow) };
    if (imageJob) {
      // 写描述 = 追加（caption=true）；改正文 = 完整替换 content.md（caption 缺省 false）。
      if (captionMode) { body.caption = true; }
      body.doc_name = nameInput ? nameInput.value : "";
    }
    setBusy(true, ingestNow ? "正在保存并入库" : "正在保存", ingestNow ? ingestBtn : saveBtn);
    try {
      const response = await fetch("/ingest/content/" + encodeURIComponent(fileId), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      let data = {};
      try { data = await response.json(); } catch (err) { data = {}; }
      if (!response.ok) {
        setToast(errorText(data, response.status), "bad");
        if (response.status === 404 || response.status === 409) {
          // 状态已经不是「停着等你」了（比如后台已入库）：关掉编辑框，把真实状态拉回来
          closeEditor(card);
          pollIngestStatus(card, fileId, { fast: true });
        }
        return;
      }
      if (data.status) {
        renderIngest(card, data.status);            // 质检结论 / 建议 / 时间线一起刷新
        note.textContent = captionMode
          ? captionNoteText(data.status, captionHasText)
          : (imageJob ? OCR_FULL_HINT : editNoteText(data.status.edit));
        // 名字交给后端收敛（非法字符 / 超长 / 短 ID 都由 make_doc_name 归一），
        // 最终叫什么看上面那句「入库后的文档名：…」，输入框保留用户自己写的样子。
      }
      if (data.ingested) {
        setToast("正文已保存并提交知识库，正在建立索引", "ok");
        card.__busy = true;
        card.__busyLabel = "正在提交";
        card.__busyFrom = "review";
        card.__knowledgeAt = 0;
        card.__knowledgeFailures = 0;
        closeEditor(card);                          // 已经进知识库：没得再改了
        renderIngestActions(card, { can: {}, state: "ingesting" });
        pollIngestStatus(card, fileId, { fast: true, awaitAction: true, fromState: "review" });
      } else if (data.ingest_error) {
        setToast("正文已保存，但入库失败：" + data.ingest_error, "bad");
        if (data.state === "review") {
          closeEditor(card);
          pollIngestStatus(card, fileId, { fast: true });
        }
      } else {
        setToast(data.message || "已保存", "ok");
      }
    } catch (err) {
      setToast("保存失败：" + err.message, "bad");
    } finally {
      setBusy(false);
      refreshCount();
      if (card.__batch) { batchAfterAction(card); }
    }
  };
  saveBtn.addEventListener("click", function () { submit(false); });
  ingestBtn.addEventListener("click", function () { submit(true); });

  refreshCount();
  main.insertBefore(panel, card.__actions);   // 编辑框紧贴按钮：改完就在下面点「保存 / 入库」
  // 重画一次动作条：编辑框开着时按钮变成「收起修改 / 收起描述」，
  // 另一个模式的入口先收起来（关掉再点，避免"点写描述却把正文面板关了"）。
  renderIngestActions(card, card.__data || {});
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);   // 光标落在末尾，接着往下读着改
}

function closeEditor(card) {
  const panel = card.__editor;
  if (!panel) { return; }
  if (panel.parentNode) { panel.parentNode.removeChild(panel); }
  card.__editor = null;
  card.__editorMode = "";
  // 按钮文字要从「收起修改」变回「修改正文」；其余按钮列表用最近一次状态还原
  renderIngestActions(card, card.__data || {});
}

async function runIngestAction(card, fileId, action, label) {
  if (!fileId) {
    setToast("这个文件还没有处理编号，请重新上传", "bad");
    return;
  }
  if (card.__busy) {
    // 在途保护 —— 第二次点击直接忽略（后端也会幂等丢弃一次重复提交）
    setToast("上一次操作还在处理中，请稍候…");
    return;
  }

  if (action === "force") {
    // 人工放行是不可逆的用户裁量 —— 在真正提交前再说一遍"质检结论 + 后果"，
    // 并明确给出更稳妥的那条路。取消就什么都不做（卡片保持原状，随时可以再来）。
    const reasons = ((((card.__data || {}).quality || {}).reasons) || []).filter(Boolean);
    // 自绘确认框（不再是原生 window.confirm）：这里是全页后果最重的一次点击，
    // 需要把"质检结论"和"更稳妥的那条路"分开写清，原生弹窗没有放这些的位置。
    const ok = await RAGKB.confirm({
      title: "仍要入库（人工放行）？",
      body: "这会跳过质检结论，把当前解析出的正文直接写进知识库。",
      detail: "质检结论：" + (reasons[0] || "未通过质量校验")
        + "\n\n" + FORCE_NOTE_HINT
        + "\n\n更稳妥的做法：先点「修改正文」改好，再点「重试」。",
      confirmText: "仍要入库",
      danger: true
    });
    if (!ok) {
      setToast("已取消：没有入库，这份资料还留在卡片上");
      return;
    }
  }

  // 点一下立刻给反馈：徽章换成「入库中」、按钮变「正在提交…（禁用）」。
  // 以前是先把按钮全清掉、再等 2.5 秒才知道状态，用户以为没点上就继续点。
  // fromState 记下动作前的状态：单线程队列里，动作提交后状态可能还是它 ——
  // 轮询据此区分「还没轮到」与「已经处理完」。
  const fromState = String(card.__state || "");
  card.__busyFrom = fromState;
  card.__busyAction = action;
  card.__actionSeen = false;
  card.__knowledgeAt = 0;
  card.__knowledgeFailures = 0;
  if (action === "ocr") { card.__ocrOutcomeNotified = false; }
  card.__busy = true;
  card.__busyLabel = action === "skip" ? "正在跳过"
    : (action === "cancel" ? "正在取消"
      : ((action === "ingest" || action === "resume") ? "正在提交" : "正在处理"));
  if (action === "ingest" || action === "resume") {
    setUploadState(card, "ingesting", "已受理，正在提交知识库…（通常几秒内会有结果）");
  } else if (action === "cancel") {
    setUploadState(card, "processing", "正在取消排队…");
  } else if (action === "skip") {
    setUploadState(card, "skipped", "已按你的选择跳过，正在登记…");
  } else {
    setUploadState(card, "processing", "已受理：" + label + "…");
  }
  renderIngestActions(card, { can: {}, state: card.__state || "" });
  setToast("正在执行：" + label + " …");

  try {
    const response = await fetch("/ingest/action/" + encodeURIComponent(fileId), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: action })
    });
    let data = {};
    try { data = await response.json(); } catch (err) { data = {}; }
    if (!response.ok) {
      card.__busy = false;          // 失败：立刻把按钮还回来，让用户能重试
      setToast(errorText(data, response.status), "bad");
    } else if (data && data.duplicate_submit) {
      card.__busy = false;
      setToast(data.message || "这次点击已忽略：上一次同样的操作还在处理中", "ok");
      if (data.status) { renderIngest(card, data.status); }
    } else {
      setToast("已受理：" + label, "ok");
      if (data.status) { renderIngest(card, data.status); }
    }
  } catch (err) {
    card.__busy = false;
    setToast("操作失败：" + err.message, "bad");
  }
  // 刚提交完先用较密的间隔问几次，让「提交中」尽快变成真实进度（快路径在前端轮询里）
  pollIngestStatus(card, fileId, { fast: true, awaitAction: true, fromState: fromState });
  batchAfterAction(card);
}

// OCR 结束时的明确反馈（成功 / 失败都要说清楚）。
// 以前只在卡片徽章上悄悄变一下，用户会觉得"点完什么都没发生"。
function notifyOcrOutcome(card, data) {
  const ocr = data.ocr || {};
  const pages = Number(ocr.pages || ocr.total_pages || 0);
  const chars = Number(data.content_chars || 0);
  if (data.state === "review") {
    let text = "OCR 完成";
    if (pages > 0) { text += "：识别 " + pages + " 页"; }
    if (chars > 0) { text += (pages > 0 ? "、" : "：") + chars + " 个字符"; }
    text += "；已通过质量校验，等你确认入库";
    setToast(text, "ok");
    return;
  }
  if (data.state === "blocked") {
    setToast("OCR 完成，但质量校验未通过" + (data.detail ? "：" + data.detail : "") + "（可先改正文）", "bad");
    return;
  }
  if (data.state === "failed") {
    setToast("OCR 失败：" + (data.detail || "请查看日志或换一个提供方重试"), "bad");
    return;
  }
  if (data.state === "needs_ocr") {
    setToast("OCR 没有识别出可用文字：" + (data.detail || "可改正文或写描述"), "bad");
    return;
  }
  setToast("OCR 已结束，当前状态：" + (data.state || "未知"), "info");
}

const KNOWLEDGE_POLL_MS = 6000;

// 入库提交成功后，本地 result.json 只会一直写「ingesting」。
// 真正的索引进度在知识库侧，必须主动查 /ingest/knowledge-status，轮询才会走到 done / failed。
async function refreshKnowledgeStatus(card, fileId, data, force) {
  const knowledge = (data && data.knowledge) || {};
  const lightrag = (data && data.lightrag) || {};
  const trackId = String(knowledge.track_id || lightrag.track_id || "");
  const progress = (data && data.progress) || {};
  const indexing = data && (data.state === "ingesting"
    || (data.state === "done" && progress.state === "processing"));
  if (!fileId || !trackId || !indexing) { return data; }
  const now = Date.now();
  if (!force && now - (card.__knowledgeAt || 0) < KNOWLEDGE_POLL_MS) { return data; }
  card.__knowledgeAt = now;
  try {
    const response = await fetch("/ingest/knowledge-status/" + encodeURIComponent(fileId), {
      method: "POST",
      cache: "no-store"
    });
    if (!response.ok) {
      card.__knowledgeFailures = (card.__knowledgeFailures || 0) + 1;
      return data;
    }
    card.__knowledgeFailures = 0;
    const payload = await response.json();
    return payload.status || data;
  } catch (err) {
    card.__knowledgeFailures = (card.__knowledgeFailures || 0) + 1;
    return data;
  }
}

async function pollIngestStatus(card, fileId, options) {
  // 已入库后若知识库仍在构建索引，继续每 2.5 秒刷新一次进度
  // options.fast：点完「入库 / 跳过」后的头几次用 0.8 秒间隔，反馈更快
  // options.awaitAction / fromState：刚提交的动作（OCR / 入库 / 跳过…）。
  //   服务端是单线程队列，动作提交后状态**可能仍然是动作前的值**（比如 OCR 还在排队，
  //   仍是 needs_ocr）。这时不能把 needs_ocr 当终态直接 return —— 那正是"页面卡死、
  //   刷新后才看到入库按钮"的根因。必须等状态离开 fromState 再交给终态判断。
  const fast = Boolean(options && options.fast);
  const awaitAction = Boolean(options && options.awaitAction);
  const fromState = awaitAction ? String((options && options.fromState) || "") : "";
  // 队列是单线程的：前面还有作业时，刚提交的动作可能几分钟才轮到。
  // 这段时间里「状态没变」不代表提交失败，只是还没开始；等满 2 分钟再提示重试。
  const actionDeadline = awaitAction ? Date.now() + 120000 : 0;
  const maxAttempts = 720;   // 720 × 2.5 秒 ≈ 30 分钟；超时后明确提示手动续跟踪
  // 同一张卡片只保留一轮轮询：手动「刷新状态」/ 新动作会替换旧循环，
  // 避免两个循环互相覆盖，也避免旧循环在网络卡住后一直占着。
  const seq = (card.__pollSeq || 0) + 1;
  card.__pollSeq = seq;
  let lastData = null;
  for (let attempt = 0; attempt < maxAttempts; attempt++) {
    await sleep(fast && attempt < 6 ? 800 : 2500);
    if (card.__pollSeq !== seq) { return; }
    let data = null;
    try {
      const response = await fetch("/ingest/status/" + encodeURIComponent(fileId), { cache: "no-store" });
      if (!response.ok) { continue; }
      data = await response.json();
    } catch (err) { continue; }
    // 手动刷新 / 新动作可能在 fetch 途中替换轮询；旧响应不得再覆盖新状态。
    if (card.__pollSeq !== seq) { return; }
    data = await refreshKnowledgeStatus(card, fileId, data, Boolean(awaitAction && attempt === 0));
    if (card.__pollSeq !== seq) { return; }
    lastData = data;
    if (card.__knowledgeFailures === 3) {
      setToast("暂时查不到知识库索引状态，可稍后点「刷新状态」", "bad");
    }
    // 本轮动作是否已经真正开始过（状态离开过动作前的值）。
    // 用来区分「还在队列里」与「跑完又回到同一个状态」（例如 OCR 没识别出文字）。
    if (awaitAction && data.state !== fromState) { card.__actionSeen = true; }
    const queued = awaitAction && data.state === fromState
      && !card.__actionSeen && Date.now() < actionDeadline;
    if (card.__busy) {
      if (queued) {
        // 还在队列里 / 还没开始：保持"正在处理"，继续等 worker 接手
      } else {
        card.__busy = false;   // 状态已离开动作前的值（或等超时）：交回真实状态
        if (awaitAction && data.state === fromState && !card.__actionSeen) {
          setToast("这次操作迟迟没有开始，可能没有提交成功，请再点一次", "bad");
        }
      }
    }
    // OCR 动作的最终结果必须说出来：成功（review）、质检没过、失败、没识别出文字。
    // 不依赖"一定看到过一次 ocr 状态"——本地 OCR 可能快到第一次轮询就已是终态。
    if (card.__busyAction === "ocr" && !card.__ocrOutcomeNotified
        && data.state !== "ocr" && (card.__actionSeen || data.state !== fromState)) {
      card.__ocrOutcomeNotified = true;
      notifyOcrOutcome(card, data);
    }
    renderIngest(card, data);
    applyPolicy(card, data);
    if (data.state === "done") {
      const progress = data.progress || {};
      if (progress.state === "processing") { continue; }
      return;
    }
    if (data.state === "blocked" || data.state === "failed") {
      return;   // 终态：需要用户决定（仍要入库 / 重试 / 改正文）
    }
    if (data.state === "needs_ocr") {
      // 刚提交 OCR、但队列还没轮到：继续等，别在这里收摊
      if (awaitAction && data.state === fromState
          && !card.__actionSeen && Date.now() < actionDeadline) { continue; }
      return;
    }
    if (data.state === "review") {
      return;   // 确认门也是终态 —— 等用户点「入库 / 跳过」，别再空转会话
    }
    if (data.state === "skipped") {
      return;   // 已跳过：用户已经决定不入库（想反悔就点「仍要入库」）
    }
    if (data.state === "cancelled") {
      return;   // 已取消排队：终态，原件与留档保留
    }
  }
  if (lastData) {
    card.__busy = false;
    setUploadState(card, lastData.state || "unknown",
      "自动更新超时；点「刷新状态」继续跟踪最新进度", lastData);
    renderIngestActions(card, lastData);
    setToast("自动更新超时，可点卡片上的「刷新状态」继续跟踪", "bad");
  } else {
    setUploadState(card, "unknown", "暂时读不到状态；点「刷新状态」继续跟踪");
  }
}

// 上传入口策略：两个入口走**同一条**流水线（本地解析 → 质量校验 → 确认门 → 入库），
// 区别只在 accept 收敛与「识别不出文字时要不要自动提示 OCR」。
// 原来的「文档 / PDF」两条已删除：它们与「上传资料」完全等价，只是 accept 更窄，属于让用户
// 替系统做判断；「跳过质检直接入库」（bypass）也已删除 —— 质检恒定运行，未通过的文件只能在卡片上
// 事后点「仍要入库」（留痕），入口不该替用户做这个决定。
const POLICIES = {
  unified: {
    label: "上传资料",
    accept: "",
    hint: "自动判断类型：文档 / PDF / 图片都能传，可一次选多个"
  },
  scan: {
    label: "上传图片",
    accept: ".png,.jpg,.jpeg,.bmp,.tif,.tiff,.webp",
    hint: "图片 / 扫描件：可以走 OCR 识别文字，也可以写描述；可一次选多张"
  }
};
let pendingPolicy = "unified";

// 点击某个入口 → 带对应策略打开文件选择器（accept 也随之收敛）
function pickFile(policyId) {
  const policy = POLICIES[policyId] || POLICIES.unified;
  if (policyId === "scan") {
    setToast("图片 / 扫描件：识别不出文字时可以直接在卡片上点「✎ 写描述」入库");
  }
  closeGraph();   // 选文件即回到对话区，让上传卡片可见
  leaveIntro();
  pendingPolicy = policyId;
  const picker = $("file");
  picker.setAttribute("accept", policy.accept || "");
  picker.click();
}

async function upload(file, policyId, batch) {
  if (!file) { return; }
  const policy = POLICIES[policyId] || POLICIES.unified;
  const card = batch ? null : createUploadCard(file.name);
  if (card) {
    card.__policy = policyId || "unified";
    card.__policyDone = false;
    setUploadState(card, "uploading", policy.label + " · 正在上传文件…（" + policy.hint + "）");
  } else {
    batch.attempted = (batch.attempted || 0) + 1;
    updateBatchUi(batch, batch.lastData);
  }
  const form = new FormData();
  form.append("file", file);
  if (batch) { form.append("batch_id", batch.id); }
  setToast("正在上传 " + file.name + " …");
  // 上传接口会同步完成本地解析与质检；大文件期间不能让卡片一直写“上传中”。
  let answered = false;
  const parseHint = card ? window.setTimeout(function () {
    if (!answered) {
      setUploadState(card, "parsing", "文件已送达，服务端正在本地解析与质检…（完成后会自动更新）");
    }
  }, 1500) : 0;
  try {
    const response = await fetch("/upload", { method: "POST", body: form });
    answered = true;
    if (parseHint) { window.clearTimeout(parseHint); }
    let data = {};
    try { data = await response.json(); } catch (err) { data = {}; }
    if (response.ok && data.success) {
      const fileId = String(data.file_id || "").trim();
      if (data.duplicate) {
        if (batch) {
          addBatchDuplicate(batch, file.name, data.detail || "");
        } else {
          card.__fileId = fileId;
          setUploadState(card, "duplicate", data.detail || "相同内容已存在，未重复入库");
          setToast(data.detail || "相同内容已存在，未重复入库", "ok");
        }
        return;
      }
      if (batch) {
        if (!fileId) {
          addBatchLocalError(batch, file.name, "服务未返回进度编号");
        } else {
          batch.uploaded += 1;
          ensureBatchItem(batch, {
            file_id: fileId,
            name: file.name,
            state: data.state || "queued",
            detail: data.detail || "",
            knowledge_error: "",
            progress: {}
          });
          batchRefresh(batch);
          updateBatchUi(batch, batch.lastData);
        }
        return;
      }
      setToast("已受理，正在排队解析", "ok");
      card.__fileId = fileId;
      trackCard(fileId, card);   // 让「全部允许」开关能找回这张卡片
      if (fileId) {
        setUploadState(card, data.state || "queued", data.detail || "");
        pollIngestStatus(card, fileId, { fast: true, fromState: "queued" });
      } else {
        setUploadState(card, "unknown", "已受理（服务未返回进度编号）");
      }
    } else {
      const detail = (data && data.detail) ? ((typeof data.detail === "string") ? data.detail : (data.detail.message || JSON.stringify(data.detail))) : ("HTTP " + response.status);
      if (batch) {
        addBatchLocalError(batch, file.name, detail);
      } else {
        setUploadState(card, "error", "上传失败：" + detail);
      }
      setToast("上传失败：" + file.name, "bad");
    }
  } catch (err) {
    answered = true;
    if (parseHint) { window.clearTimeout(parseHint); }
    if (batch) {
      addBatchLocalError(batch, file.name, err.message);
    } else {
      setUploadState(card, "error", "上传失败：" + err.message);
    }
    setToast("上传失败：" + file.name, "bad");
  }
}

// ---------- 入口的自动动作：只有「上传图片」会替他多想一步（图片本来就该考虑 OCR） ----------
// 为什么只剩 scan：原文里的 pdf 之所以在这里，是因为 PDF 常有内嵌图片；
// 现在「上传资料」什么都收，PDF 的内嵌图片会照常停在 needs_ocr 并显示可点的「识别图片（N 张）」，
// 不再自动替他点那一下（少一次替你按按钮的机会，多一次你自己确认的机会）。
function policyWantsOcr(policyId) {
  return policyId === "scan";
}

async function applyPolicy(card, data) {
  if (!card || card.__policyDone) { return; }
  const policyId = card.__policy || "unified";
  const state = data.state;

  if (state === "needs_ocr" && policyWantsOcr(policyId)) {
    if (!data.ocr_enabled) {
      card.__policyDone = true;
      setToast(
        data.is_image
          ? "图片可以直接点「✎ 写描述」入库；想先识别文字请到「设置 → OCR 文字识别」开启"
          : "需要先在「设置 → OCR 文字识别」开启 OCR 才能自动识别",
        "bad"
      );
      return;
    }
    const external = data.ocr_external && data.ocr_image_ocr !== false
      && ocrScopeOf(data) === "images" && (data.unocr_images || 0) > 0;
    if (external) {
      // 这是全应用唯一会把资料**传出本机**的动作，所以确认框里要把这条说透：
      // 传什么、传到哪、有没有本机替代方案。
      const ok = await RAGKB.confirm({
        title: "「" + ocrButtonLabel(data) + "」会把图片发到云端",
        body: "本应用默认「资料不出本机」，这是唯一一次把内容传出本机的动作。",
        detail: "· 发送内容：这份资料里的图片（共 " + (data.unocr_images || 0) + " 张）"
          + "\n· 服务方：设置 → OCR 文字识别 里配置的提供方"
          + "\n· 想避免外发：在设置里把 OCR 改为「本机识别」（PaddleOCR 本地模型）",
        confirmText: "继续并外发",
        danger: true
      });
      if (!ok) { card.__policyDone = true; return; }
    }
    card.__policyDone = true;
    runIngestAction(card, card.__fileId, "ocr", ocrButtonLabel(data));
    return;
  }

}

// ---------- 侧栏「入库策略」现状：质量校验 / 入库确认 / OCR 当前生效值，让入口一眼可读 ----------
// 三种确认方式的「人话说法」（与 backend INGEST_CONFIRM_MODES 一致：always / auto / off）
const CONFIRM_MODE_ROWS = {
  always: ["逐个确认", "ok", "质检通过的文件会停下来，等你点「入库 / 跳过」"],
  auto: ["智能放行", "ok", "建议直接入库的自动入库；提示「再看一眼 / 谨慎入库」的会停下来问你"],
  off: ["已关闭 · 直接入库", "warn", "质检通过就直接入库，不再问你（不推荐：可能把错内容写进知识库）"]
};

// 摘要里每个 chip 只放「名字 + 一个语义点」：侧栏宽 268px（可用 240px），
// 一行要放下三个 chip + 「调整 ›」，写成句子必然溢出。完整现状与解释放 title（悬停可读），
// 设置页里是同样的信息、完整呈现 —— 这里只做"一眼看得到状态"。
function ingestChip(label, dotClass, tip) {
  const chip = document.createElement("span");
  chip.className = "ingest-chip";
  chip.title = tip || label;
  const dot = document.createElement("i");
  dot.className = "dot " + dotClass;
  const name = document.createElement("span");
  name.textContent = label;
  chip.appendChild(dot);
  chip.appendChild(name);
  return chip;
}

// 质检恒定运行（没有整体开关）—— 这句话在侧栏摘要、设置页、文档里是同一套说法
const QUALITY_ALWAYS_TIP = "质量校验恒定运行（没有整体开关）：上传后先在本地解析并做质量校验。"
  + "未通过的会停下来说清原因 —— 想放行只能在卡片上点「仍要入库」，且档案里会记下「人工放行」。";

/* 本机模式标识：只认「进程当前生效值」，与 /chat/stream 的实际行为一致；
   设置页保存了但还没重启时仍是旧模式，这里也如实显示旧模式。
   上游模式 / 读取失败时不渲染任何提示（宁可不提示，也不误报）。 */
function applyGenerationMode(app) {
  const mode = String((app && app.generation_mode) || "").toLowerCase();
  GEN_MODE = (mode === "local" || mode === "upstream") ? mode : "";
  GEN_MODEL = String((app && app.local_llm_model) || "");
  const badge = $("genModeBadge");
  if (!badge) { return; }
  if (GEN_MODE !== "local") {
    badge.hidden = true;
    badge.removeAttribute("title");
    return;
  }
  badge.hidden = false;
  const model = GEN_MODEL ? ("本机模型 " + GEN_MODEL) : "本机模型";
  badge.title = "回答由" + model + " 生成：首字通常需 60–90 秒（CPU 推理），答案可能比上游短；"
    + "第一个字之前失败会自动回退上游，已输出内容后失败会保留内容并提示。可在设置中切回上游。";
}

async function refreshIngestState() {
  const box = $("ingestState");
  if (!box) { return; }
  let data = null;
  try {
    const response = await fetch("/api/settings", { cache: "no-store" });
    if (response.ok) { data = await response.json(); }
  } catch (err) { data = null; }
  if (!data) { box.style.display = "none"; return; }
  applyGenerationMode(data.app_effective || {});
  const quality = data.quality_effective || {};
  const ocr = data.ocr_effective || {};
  const gate = data.ingest_confirm_effective || {};
  const gateRow = CONFIRM_MODE_ROWS[gate.mode] || CONFIRM_MODE_ROWS.always;
  const ttlHours = (gate.pending_review_ttl_hours === undefined || gate.pending_review_ttl_hours === null)
    ? 24 : gate.pending_review_ttl_hours;
  box.innerHTML = "";
  box.style.display = "flex";
  const chips = document.createElement("div");
  chips.className = "ingest-chips";
  // ① 质检：恒定开启 —— .env 里的 QUALITY_ENABLED=false 会被忽略并记一条 warning
  chips.appendChild(ingestChip(
    "质检",
    "ok",
    QUALITY_ALWAYS_TIP
      + " 当前预设：" + (quality.profile || "standard") + "。"
      + (quality.switch_ignored ? "（.env 里的 QUALITY_ENABLED=false 已被忽略：质检关不掉）" : "")
  ));
  // ② 确认门：always / auto / off，以及「本次运行全部允许」的临时态
  chips.appendChild(ingestChip(
    "确认",
    SESSION_CONFIRM.allow_all ? "warn" : gateRow[1],
    "入库确认（" + (gate.mode || "always") + "）：" + gateRow[2]
      + "；待确认作业会一直保留到处理；最近 " + ttlHours
      + " 小时跳过的文件会在对话页恢复，更早的可在「文件管理」继续入库（资料与解析结果不会删）。"
      + (SESSION_CONFIRM.allow_all ? " 本次运行已勾选「全部允许」：质检通过的文件不再逐个问。" : "")
  ));
  // ③ OCR：未启用时是灰点 —— 需要识别的文件只会被拦下，不会外发
  chips.appendChild(ingestChip(
    "OCR",
    ocr.external ? "warn" : (ocr.enabled ? "ok" : "gray"),
    ocr.enabled
      ? ("OCR 已启用（" + (ocr.mode || "") + " · " + (ocr.provider || "")
          + (ocr.external ? " · 会外发" : " · 不外发") + "）：扫描件与内嵌图片需要识别时使用；"
          + "外发型会把图片发送到云端 OCR 服务。")
      : "OCR 未启用：需要识别的文件会被拦下（不外发任何文件），可在设置中开启；"
          + "图片也可以直接「写描述」入库。"
  ));
  const link = document.createElement("a");
  link.className = "ingest-go";
  link.href = "/settings";
  link.title = "打开设置（也可从侧栏或更多菜单进入）：质量校验阈值 / 入库确认 / OCR";
  link.textContent = "调整 ›";
  chips.appendChild(link);
  box.appendChild(chips);
}

function setStatus(key, status) {
  const dot = $("st" + key);
  const label = $("tx" + key);
  const detail = status && typeof status === "object" ? status : {};
  const enabled = typeof status === "object" ? detail.enabled !== false : !!status;
  const ok = typeof status === "object" ? !!detail.ok : !!status;
  const text = !enabled ? "未启用" : (ok ? "正常" : "未连接");
  if (dot) { dot.className = "dot " + (!enabled ? "gray" : (ok ? "ok" : "bad")); }
  if (label) {
    label.textContent = text;
    label.title = detail.detail || "";
  }
}

async function refreshStatus() {
  try {
    const response = await fetch("/health", { cache: "no-store" });
    const data = await response.json();
    setStatus("Lightrag", data.lightrag || false);
    setStatus("Ollama", data.ollama || false);
    setStatus("Qdrant", data.qdrant || false);
  } catch (err) { /* 状态查询失败时保持原样 */ }
}

/* ---------- 知识图谱：自建只读视图（不再嵌 LightRAG 的 WebUI）----------
   为什么自建：LightRAG 自带 WebUI 里有上传（会绕过本项目的质量门）、删除文档、
   清空知识库、重建索引，Knowledge Graph 页甚至能删实体/关系 —— 个人用户误操之后
   无法自行恢复，只能运维介入。所以这里只画图：三个只读接口
   （/api/graph/popular、/api/graph/search、/api/graph/subgraph）+ 一块 canvas。
   节点与边自带 file_path / description，因此「从节点跳到相关文档 / 就它提问」
   在前端即可完成，不需要额外接口。
   布局用 BFS 分层圆环：确定、快，且天然是"从中心往外读"，比力导向更好读。 */
const GRAPH_NODE_BASE = 8;        // 基础半径（全局图节点多、会缩得小，所以基数调大一点）
const GRAPH_RING_GAP = 118;      // 自我图（下钻）每一跳的圆环半径增量
const GRAPH_LEVEL_GAP = 104;     // 三维：每一跳抬升的高度
const GRAPH_CLUSTER_SPACING = 62; // 星团内部向日葵盘的节点间距（世界单位）
const GRAPH_JITTER = 0.16;        // 离散随机扰动的幅度（相对星团间距）
const GRAPH_REGRESS = 0.8;        // 回归系数：1=完全按随机，0=完全回理想位置
const GRAPH_FOCAL = 1150;        // 三维焦距
const GRAPH_TYPE_COLORS = {
  person: "#2563eb", organization: "#7c3aed", location: "#0d9488",
  event: "#d97706", concept: "#0891b2", method: "#4f46e5",
  content: "#65a30d", data: "#0284c7", artifact: "#c026d3",
  naturalobject: "#15803d", activity: "#b45309", creature: "#be123c",
  other: "#6b7280", unknown: "#6b7280"
};
const GV = {
  nodes: [], edges: [], allNodes: [], allEdges: [], byId: {}, adj: {},
  pos2: {}, pos3: {}, level: {}, radius: {}, proj: {}, delay: {},
  ideal2: {}, clusterOf: {}, planned: {}, jitterAmp: 0,
  view: { scale: 1, tx: 0, ty: 0 },
  mode: "2d", rot: { yaw: -0.62, pitch: 0.36 },
  scope: "global",                 // global＝整张银河图；ego＝以某实体为中心的自我图
  center: "", clusters: 0,
  selected: null, selectedEdge: null, hover: null, hot: {}, focusId: "", focusing: false,
  drag: null, pan: null, rotate: null, moved: false, truncated: false,
  touchX: 0, touchY: 0, touchRot: null,
  hiddenTypes: {}, legendKey: "",
  born: 0, animSpan: 0, animDone: true, dpr: 1,
  bgCanvas: document.createElement("canvas")
};
let graphSearchTimer = null;
let graphCalm = false;
try { graphCalm = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (err) { graphCalm = false; }

function graphTypeColor(type) {
  return GRAPH_TYPE_COLORS[String(type || "").toLowerCase()] || "#6b7280";
}

// 颜色加深/提亮：amount>0 向白靠，amount<0 向黑靠（给节点做高光与暗部）
function gvShade(hex, amount) {
  const m = /^#([0-9a-f]{6})$/i.exec(String(hex || ""));
  if (!m) { return hex; }
  const num = parseInt(m[1], 16);
  const target = amount >= 0 ? 255 : 0;
  const k = Math.min(1, Math.abs(amount));
  const out = [(num >> 16) & 255, (num >> 8) & 255, num & 255].map(function (v) {
    return Math.round(v + (target - v) * k);
  });
  return "rgb(" + out[0] + "," + out[1] + "," + out[2] + ")";
}

function gvSplitFiles(raw) {
  if (!raw) { return []; }
  const seen = {};
  const out = [];
  String(raw).split("<SEP>").forEach(function (item) {
    const path = item.trim();
    if (path && !seen[path]) { seen[path] = true; out.push(path); }
  });
  return out;
}

function gvReset() {
  GV.nodes = []; GV.edges = []; GV.allNodes = []; GV.allEdges = []; GV.byId = {}; GV.adj = {};
  GV.pos2 = {}; GV.pos3 = {}; GV.level = {}; GV.radius = {}; GV.proj = {}; GV.delay = {};
  GV.ideal2 = {}; GV.clusterOf = {}; GV.planned = {}; GV.jitterAmp = 0;
  GV.selected = null; GV.selectedEdge = null; GV.hover = null; GV.hot = {}; GV.focusId = ""; GV.focusing = false;
  GV.drag = null; GV.pan = null; GV.rotate = null;
  GV.hiddenTypes = {}; GV.legendKey = "";
  GV.clusters = 0; GV.truncated = false; GV.animDone = true;
  $("graphTip").hidden = true;
}

function gvPoint(id) {
  return GV.mode === "3d" ? GV.pos3[id] : GV.pos2[id];
}

// 3D 透视投影（绕 Y 偏航、绕 X 俯仰）；2D 模式下原样返回
function gvProject(p) {
  if (!p) { return null; }
  if (GV.mode !== "3d") { return { x: p.x, y: p.y, s: 1, d: 0 }; }
  const cy = Math.cos(GV.rot.yaw), sy = Math.sin(GV.rot.yaw);
  const cp = Math.cos(GV.rot.pitch), sp = Math.sin(GV.rot.pitch);
  const x1 = p.x * cy + p.z * sy;
  const z1 = -p.x * sy + p.z * cy;
  const y1 = p.y * cp - z1 * sp;
  const z2 = p.y * sp + z1 * cp;
  const s = GRAPH_FOCAL / Math.max(GRAPH_FOCAL * 0.34, GRAPH_FOCAL + z2);
  return { x: x1 * s, y: y1 * s, s: s, d: z2 };
}

// ---------- 布局 ----------

// 自我图（下钻）：以中心实体为核，按跳数铺同心圆环
function gvLayoutEgo() {
  const adj = GV.adj;
  const level = {};
  const order = [];
  const start = GV.byId[GV.center] ? GV.center : (GV.nodes[0] ? GV.nodes[0].id : "");
  if (start) {
    level[start] = 0;
    order.push(start);
    const queue = [start];
    while (queue.length) {
      const cur = queue.shift();
      (adj[cur] || []).forEach(function (next) {
        if (level[next] === undefined) {
          level[next] = level[cur] + 1;
          order.push(next);
          queue.push(next);
        }
      });
    }
  }
  let maxLevel = 0;
  Object.keys(level).forEach(function (id) { if (level[id] > maxLevel) { maxLevel = level[id]; } });
  GV.nodes.forEach(function (n) {
    if (level[n.id] === undefined) { level[n.id] = maxLevel + 1; order.push(n.id); }
  });
  const byLevel = {};
  order.forEach(function (id) {
    const l = level[id];
    if (!byLevel[l]) { byLevel[l] = []; }
    if (byLevel[l].indexOf(id) < 0) { byLevel[l].push(id); }
  });
  GV.pos2 = {}; GV.pos3 = {}; GV.level = {}; GV.delay = {};
  Object.keys(byLevel).forEach(function (key) {
    const l = Number(key);
    const ids = byLevel[key];
    const radius = GRAPH_RING_GAP * l;
    ids.forEach(function (id, i) {
      const angle = (Math.PI * 2 * i) / ids.length - Math.PI / 2 + (l % 2 ? 0.4 : 0);
      const cx = Math.cos(angle), cy2 = Math.sin(angle);
      GV.pos2[id] = { x: cx * radius, y: cy2 * radius };
      GV.pos3[id] = { x: cx * radius, y: l * GRAPH_LEVEL_GAP, z: cy2 * radius };
      GV.level[id] = l;
      GV.delay[id] = l * 90 + i * 26;
    });
  });
  GV.clusters = 1;
}

// 星团内部：跳数越深半径越"压"，避免深链把星团撑爆
function gvRingRadius(level, compSize) {
  const gap = compSize > 24 ? 70 : compSize > 8 ? 58 : 48;
  const eff = level <= 2 ? level : 2 + (level - 2) * 0.55;
  return gap * eff;
}

// 确定性伪随机：同一个实体名 + 同一个用途，任何机器、任何刷新都得到同一个 [0,1) 数。
// 这就是"离散型随机变量"——随机，但有记忆（不用 Math.random，否则每次重排都跳）。
function gvHash01(str, salt) {
  let h = 2166136261 >>> 0;
  const text = String(str) + "|" + String(salt);
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  h ^= h >>> 15; h = Math.imul(h, 2246822507) >>> 0;
  h ^= h >>> 13; h = Math.imul(h, 3266489909) >>> 0;
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

// 随机之后只做一件事：把离得太近的一对轻轻推开（回归到"合法构型"），其余一律不动。
// 这样随机感保留，但永远不会重叠。
function gvRelax(map, ids, dMin) {
  for (let iter = 0; iter < 6; iter++) {
    let moved = false;
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = map[ids[i]], b = map[ids[j]];
        if (!a || !b) { continue; }
        const dx = b.x - a.x, dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.001;
        if (d >= dMin) { continue; }
        const push = ((dMin - d) / d) * 0.5;
        a.x -= dx * push; a.y -= dy * push;
        b.x += dx * push; b.y += dy * push;
        moved = true;
      }
    }
    if (!moved) { break; }
  }
}

// 星团摆位：向日葵盘（黄金角 137.5°）+ **按面积累加**决定半径。
// 重的星团先放、离中心近；后面每一个的半径按"已占面积"开平方外推 ——
// 长出来天然是一个**自然收拢的圆盘**，而不是一条螺旋或一团散沙。
// 整体密度系数 c 从 0.86 起步，若有重叠就按 5% 递增重试，直到完全不叠为止。
function gvPackClusters(radii) {
  const n = radii.length;
  if (!n) { return []; }
  if (n === 1) { return [{ x: 0, y: 0, r: radii[0] }]; }
  const GOLDEN = 2.39996323;
  const base = radii[0];
  const GAP = 8;
  for (let attempt = 0; attempt < 90; attempt++) {
    const k = 1.9 * Math.pow(1.03, attempt);
    const pts = [{ x: 0, y: 0, r: radii[0] }];
    let area = radii[0] * radii[0];
    for (let i = 1; i < n; i++) {
      const th = i * GOLDEN;
      // 面积法半径：让“已占面积”随半径平方增长 → 均匀铺满一个圆盘
      let dist = Math.sqrt(k * area);
      // 中心那颗是特例：第一圈还得让开它的自身半径（原来漏了这条，c 被顶到 2 倍）
      if (dist < radii[0] + radii[i] + GAP) { dist = radii[0] + radii[i] + GAP; }
      pts.push({ x: Math.cos(th) * dist, y: Math.sin(th) * dist, r: radii[i] });
      area += radii[i] * radii[i];
    }
    let ok = true;
    for (let i = 0; i < pts.length && ok; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        const need = pts[i].r + pts[j].r + 6;
        const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y;
        if (dx * dx + dy * dy < need * need) { ok = false; break; }
      }
    }
    if (ok) { return pts; }
  }
  // 兜底：沿一条渐开线排开（几乎不会走到这里）
  return radii.map(function (r, i) {
    return { x: (base + r + 24) * i * 0.9, y: 0, r: r };
  });
}

// 全局图：先按连通分量拆成"星系 / 星团 / 散星"，再排成银河
function gvLayoutGlobal() {
  const adj = GV.adj;
  const seen = {};
  const comps = [];
  GV.nodes.forEach(function (n) {
    if (seen[n.id]) { return; }
    seen[n.id] = true;
    const stack = [n.id];
    const members = [];
    while (stack.length) {
      const cur = stack.pop();
      members.push(cur);
      (adj[cur] || []).forEach(function (nb) {
        if (!seen[nb]) { seen[nb] = true; stack.push(nb); }
      });
    }
    comps.push(members);
  });
  // 大的先排（大的进中心）
  comps.sort(function (a, b) { return b.length - a.length; });
  GV.clusters = comps.length;

  // 先把每个分量内部排好、算出星团半径，再统一摆位（摆位需要先知道全部半径）
  const clusters = [];
  const local = {};
  const levels = {};
  comps.forEach(function (members) {
    const inComp = {};
    members.forEach(function (id) { inComp[id] = true; });
    // 星团核心＝分量内度数最高的节点
    let root = members[0];
    members.forEach(function (id) {
      if ((adj[id] || []).length > (adj[root] || []).length) { root = id; }
    });
    const level = {};
    level[root] = 0;
    const order = [root];
    const queue = [root];
    while (queue.length) {
      const cur = queue.shift();
      (adj[cur] || []).forEach(function (nb) {
        if (inComp[nb] && level[nb] === undefined) {
          level[nb] = level[cur] + 1;
          order.push(nb);
          queue.push(nb);
        }
      });
    }
    members.forEach(function (id) {
      if (level[id] === undefined) { level[id] = 1; order.push(id); }
    });
    // 星团内部铺「向日葵盘」：按 BFS 顺序（核心在前），半径按**已占面积**外推。
    // 用同心圆环时半径由“层数”决定（7 层就要 7 圈），而第 4~6 层可能只有 4~6 个节点，
    // 会白白占掉好几圈空白 —— 这正是全局图节点被迫变小的根因。
    const pts = {};
    let area = 0;
    order.forEach(function (id, i) {
      const th = i * 2.39996323;
      const radius = i === 0 ? 0 : GRAPH_CLUSTER_SPACING * Math.sqrt(area / 2.72);
      pts[id] = { x: Math.cos(th) * radius, y: Math.sin(th) * radius };
      area += 1;
    });

    // ---- 离散随机扰动 + 线性回归收敛 ----
    // 每个节点用自己的名字哈希出两个 [0,1) 的确定性随机数 → 得到 raw 扰动；
    // 再对**该星团内扰动的均值**做一次线性回归校正（减掉系统性偏移，星团重心不动）；
    // 最后乘回归系数 GRAPH_REGRESS 收缩回理想位置。效果：随机，但收敛。
    const rot = gvHash01(root, "rot") * Math.PI * 2;   // 每个星团整体转一个确定角度，避免千篇一律
    const amp = GRAPH_CLUSTER_SPACING * GRAPH_JITTER;
    const ideal = {};
    const jit = {};
    let sumX = 0, sumY = 0;
    members.forEach(function (id) {
      const p = pts[id] || { x: 0, y: 0 };
      const cs = Math.cos(rot), sn = Math.sin(rot);
      const ix = p.x * cs - p.y * sn;
      const iy = p.x * sn + p.y * cs;
      ideal[id] = { x: ix, y: iy };
      const jx = (gvHash01(id, "jx") - 0.5) * amp;
      const jy = (gvHash01(id, "jy") - 0.5) * amp;
      jit[id] = { x: ix + jx, y: iy + jy };
      sumX += jx; sumY += jy;
    });
    const meanX = members.length ? sumX / members.length : 0;
    const meanY = members.length ? sumY / members.length : 0;
    members.forEach(function (id) {
      const j = jit[id], i0 = ideal[id];
      jit[id] = {
        x: i0.x + (j.x - i0.x - meanX) * GRAPH_REGRESS,
        y: i0.y + (j.y - i0.y - meanY) * GRAPH_REGRESS
      };
    });
    // 只把"太近"的对推开：随机之后仍然互不重叠
    gvRelax(jit, members, GRAPH_CLUSTER_SPACING * 0.55);
    let maxR = 0;
    members.forEach(function (id) {
      const q = jit[id];
      const d = Math.sqrt(q.x * q.x + q.y * q.y);
      if (d > maxR) { maxR = d; }
    });
    clusters.push({
      members: members,
      pts: jit,
      ideal: ideal,
      root: root,
      levels: level,
      r: maxR + GRAPH_NODE_BASE + 4
    });
  });

  // 按星团半径从大到小摆位（大的＝重的，落在中心）
  const order = clusters.map(function (c, i) { return i; });
  order.sort(function (a, b) { return clusters[b].r - clusters[a].r; });
  const spots = gvPackClusters(order.map(function (i) { return clusters[i].r; }));
  order.forEach(function (ci, k) {
    const c = clusters[ci];
    const spot = spots[k];
    c.members.forEach(function (id) {
      const q = c.pts[id] || { x: 0, y: 0 };
      const i0 = c.ideal[id] || q;
      local[id] = { x: spot.x + q.x, y: spot.y + q.y };
      GV.ideal2[id] = { x: spot.x + i0.x, y: spot.y + i0.y };
      GV.clusterOf[id] = c.root || "";
      levels[id] = Math.min(c.levels[id], 3);
    });
  });

  GV.pos2 = {}; GV.pos3 = {}; GV.level = {}; GV.delay = {};
  GV.nodes.forEach(function (n) {
    const p = local[n.id] || { x: 0, y: 0 };
    const lv = levels[n.id] || 0;
    GV.pos2[n.id] = { x: p.x, y: p.y };
    // 三维：整张银河摊在水平面上（转动时就是一个星系盘），层次给一点点厚度
    GV.pos3[n.id] = { x: p.x, y: lv * 14, z: p.y };
    GV.level[n.id] = lv;
  });
}

// 布局尾巴（两种 scope 共用）：度数半径、平行边分组、入场节奏
function gvFinalizeLayout() {
  GV.radius = {};
  GV.nodes.forEach(function (n) {
    const deg = (GV.adj[n.id] || []).length;
    GV.radius[n.id] = GRAPH_NODE_BASE + Math.min(8, Math.sqrt(deg) * 2.4);
  });
  // 平行边扇开（同两点多条边时沿法向对称错开；当前数据 0 对，属防御性处理）
  const pairCount = {};
  GV.edges.forEach(function (e) {
    const key = e.source < e.target ? e.source + "\u0000" + e.target : e.target + "\u0000" + e.source;
    e.__key = key;
    pairCount[key] = (pairCount[key] || 0) + 1;
  });
  const pairIdx = {};
  GV.edges.forEach(function (e) {
    pairIdx[e.__key] = pairIdx[e.__key] === undefined ? 0 : pairIdx[e.__key] + 1;
    e.__idx = pairIdx[e.__key];
    e.__count = pairCount[e.__key];
  });
  GV.animSpan = 0;
  if (GV.nodes.length > 90) {
    // 节点多时不做逐个入场（166 个逐帧重绘会卡），整盘一次性淡入
    GV.nodes.forEach(function (n) { GV.delay[n.id] = 0; });
    GV.animSpan = 460;
  } else {
    let span = 0;
    Object.keys(GV.delay).forEach(function (id) { span = Math.max(span, GV.delay[id]); });
    GV.animSpan = span + 380;
  }
}

// 屏幕上"希望节点多大"：节点少就大、多了自然变小（对数式，尺度温和）
function gvNodeScreenSize(n) {
  return Math.max(3.2, Math.min(10, 9.5 * Math.pow(60 / Math.max(1, n), 0.32)));
}

// 球体大小的**线性**规则：关系数量越多球越大。
// 0 度 → 0.78×，最高度 → 1.87×（相差 2.4 倍，一眼能看出谁重要）。
function gvSizeFactor(deg, maxDeg) {
  const t = maxDeg > 0 ? deg / maxDeg : 0;
  return 0.78 + 1.09 * t;
}

// 把"希望的屏幕尺寸"换算成世界半径，再逐节点用**它自己的最近邻距离**兜底 ——
// 这一步是"自然适量放大缩小 + 关系多的球更大"的关键，同时**绝不重叠**。
// 为什么不沿用全局最小间距：那个值由全图最挤的一对决定，会把所有球一起压扁，
// 于是"关系多/少"在视觉上就没差别了（实测 min/med/max 只差 1.29 倍）。
// 换成逐节点约束后：只要某节点周围有空间，它就保持线性放大后的尺寸；
// 数学上仍然安全 —— r_i ≤ 0.45·d_i 且 d_i ≤ d_ij ⇒ r_i + r_j ≤ 0.9·d_ij < d_ij。
function gvApplyNodeScale(fitScale) {
  const n = GV.nodes.length;
  if (!n) { return; }
  const target = gvNodeScreenSize(n) / Math.max(0.05, fitScale);
  let maxDeg = 1;
  GV.nodes.forEach(function (node) {
    const deg = (GV.adj[node.id] || []).length;
    node.__deg = deg;
    if (deg > maxDeg) { maxDeg = deg; }
  });
  const nearest = {};
  if (n <= 2000) {
    const pts = GV.nodes.map(function (node) { return gvProject(gvPoint(node.id)); });
    for (let i = 0; i < n; i++) {
      if (!pts[i]) { continue; }
      let best = Infinity;
      for (let j = 0; j < n; j++) {
        if (i === j || !pts[j]) { continue; }
        const dx = pts[i].x - pts[j].x, dy = pts[i].y - pts[j].y;
        const d2 = dx * dx + dy * dy;
        if (d2 < best) { best = d2; }
      }
      nearest[GV.nodes[i].id] = Math.sqrt(best);
    }
  }
  GV.baseR = target;
  GV.maxDeg = maxDeg;
  GV.radius = {};
  GV.nodes.forEach(function (node) {
    const want = target * gvSizeFactor(node.__deg || 0, maxDeg);
    const local = nearest[node.id];
    const cap = isFinite(local) ? local * 0.45 : want;
    GV.radius[node.id] = Math.max(1.6, Math.min(want, cap));
  });
}

// 布局 → 拟合 → 按节点数自适应半径 → 再拟合（半径变了，画面占比也变）
function gvRelayoutView() {
  gvFit();
  gvApplyNodeScale(GV.view.scale);
  gvFit();
}

function gvLayout() {
  if (GV.scope === "global") { gvLayoutGlobal(); } else { gvLayoutEgo(); }
  gvFinalizeLayout();
}

function gvAnimTick() {
  if (GV.animDone) { return; }
  gvDraw();
  if (performance.now() - GV.born < GV.animSpan) {
    requestAnimationFrame(gvAnimTick);
  } else {
    GV.animDone = true;
    gvDraw();
  }
}

function gvStartAnim() {
  if (graphCalm) { GV.animDone = true; return; }
  GV.born = performance.now();
  GV.animDone = false;
  requestAnimationFrame(gvAnimTick);
}

function gvT(id, now) {
  if (GV.animDone || graphCalm) { return 1; }
  const raw = (now - GV.born - (GV.delay[id] || 0)) / 340;
  const t = Math.max(0, Math.min(1, raw));
  return 1 - Math.pow(1 - t, 3);
}

// ---------- 聚焦（悬停/选中）：关联凸显、非关联微下沉 ----------
function gvRebuildFocus() {
  if (GV.selectedEdge) {
    const edge = GV.edges.find(function (item) { return item.id === GV.selectedEdge; });
    if (edge) {
      GV.focusId = edge.source;
      GV.focusing = true;
      GV.hot = {};
      GV.hot[edge.source] = true;
      GV.hot[edge.target] = true;
      return;
    }
  }
  const focus = GV.hover || GV.selected || "";
  GV.focusId = focus;
  GV.focusing = !!focus;
  GV.hot = {};
  if (!focus) { return; }
  GV.hot[focus] = true;
  (GV.adj[focus] || []).forEach(function (nb) { GV.hot[nb] = true; });
}

function gvDepthAlpha(n, p) {
  if (GV.mode === "3d") { return Math.max(0.5, Math.min(1, 0.62 + (p.s - 0.72) * 1.35)); }
  return Math.max(0.62, 1 - (GV.level[n.id] || 0) * 0.09);
}

// ---------- 主题色（Canvas 读不到 CSS 变量）----------
// 每帧都 getComputedStyle 太贵：按当前 data-theme 缓存一份，主题切换时清空。
// 深色下画布底色、连线、标签、节点描边都跟着变；实体类型色仍是数据色，不随主题改。
let GV_THEME = null;
function gvColors() {
  const key = document.documentElement.getAttribute("data-theme") || "light";
  if (GV_THEME && GV_THEME.key === key) { return GV_THEME; }
  const cs = getComputedStyle(document.documentElement);
  const v = function (name, fallback) { return cs.getPropertyValue(name).trim() || fallback; };
  GV_THEME = {
    key: key,
    glow1: v("--graph-glow-1", "rgba(37,99,235,.085)"),
    glow2: v("--graph-glow-2", "rgba(37,99,235,.024)"),
    glow0: v("--graph-glow-0", "rgba(37,99,235,0)"),
    star: v("--graph-star", "rgba(148,163,184,.22)"),
    orbit: v("--graph-orbit", "rgba(37,99,235,.05)"),
    edge: v("--graph-edge", "rgba(148,163,184,.38)"),
    edgeArrow: v("--graph-edge-arrow", "rgba(148,163,184,.62)"),
    focus: v("--graph-focus", "rgba(37,99,235,.68)"),
    focusArrow: v("--graph-focus-arrow", "rgba(37,99,235,.8)"),
    focusShadow: v("--graph-focus-shadow", "rgba(37,99,235,.35)"),
    halo: v("--graph-halo", "rgba(37,99,235,.30)"),
    halo0: v("--graph-halo-0", "rgba(37,99,235,0)"),
    nodeStroke: v("--graph-node-stroke", "rgba(255,255,255,.92)"),
    nodeSelected: v("--graph-node-selected", "#1d4ed8"),
    nodeHover: v("--graph-node-hover", "#60a5fa"),
    nodeGloss: v("--graph-node-gloss", "rgba(255,255,255,.34)"),
    nodeShadow: v("--graph-node-shadow", "rgba(15,23,42,.20)"),
    labelBg: v("--graph-label-bg", "rgba(255,255,255,.94)"),
    labelStroke: v("--graph-label-stroke", "#cddcff"),
    labelHalo: v("--graph-label-halo", "rgba(255,255,255,.94)"),
    labelText: v("--graph-label-text", "#475569"),
    labelHot: v("--graph-label-hot", "#1d4ed8")
  };
  return GV_THEME;
}

// 主题切换：清缓存 + 让离屏背景失效 + 重画一帧
document.addEventListener("ragkb-theme-change", function () {
  GV_THEME = null;
  if (GV.bgCanvas) { GV.bgCanvas.width = 0; GV.bgCanvas.height = 0; }
  if (typeof gvDraw === "function") { requestAnimationFrame(gvDraw); }
});

// ---------- 背景（缓存成离屏画布，避免每帧重画上千个点）----------
function gvBackdrop(ctx, w, h) {
  const dpr = GV.dpr || 1;
  const C = gvColors();
  const bw = Math.round(w * dpr), bh = Math.round(h * dpr);
  if (GV.bgCanvas.width !== bw || GV.bgCanvas.height !== bh) {
    GV.bgCanvas.width = bw;
    GV.bgCanvas.height = bh;
    const b = GV.bgCanvas.getContext("2d");
    b.setTransform(dpr, 0, 0, dpr, 0, 0);
    b.clearRect(0, 0, w, h);
    // 银核光晕
    const glow = b.createRadialGradient(w / 2, h / 2, 8, w / 2, h / 2, Math.max(w, h) * 0.62);
    glow.addColorStop(0, C.glow1);
    glow.addColorStop(0.5, C.glow2);
    glow.addColorStop(1, C.glow0);
    b.fillStyle = glow;
    b.fillRect(0, 0, w, h);
    // 星空点阵
    const step = 26;
    b.fillStyle = C.star;
    for (let x = step / 2; x < w; x += step) {
      for (let y = step / 2; y < h; y += step) {
        b.beginPath();
        b.arc(x, y, 0.9, 0, Math.PI * 2);
        b.fill();
      }
    }
    // 几圈极淡的"轨道"，强化星系感
    b.strokeStyle = C.orbit;
    b.lineWidth = 1;
    const maxR = Math.max(w, h) * 0.46;
    for (let k = 1; k <= 3; k++) {
      b.beginPath();
      b.arc(w / 2, h / 2, (maxR * k) / 3, 0, Math.PI * 2);
      b.stroke();
    }
  }
  ctx.drawImage(GV.bgCanvas, 0, 0, w, h);
}

// ---------- 连线：长度成比例的弧线（像 LightRAG 的曲线边），平行边对称扇开 ----------
function gvDrawEdge(ctx, e) {
  const C = gvColors();
  const a = GV.proj[e.source];
  const b = GV.proj[e.target];
  if (!a || !b) { return; }
  const selectedEdge = GV.selectedEdge === e.id;
  const focusEdge = selectedEdge || (GV.focusing && GV.hot[e.source] && GV.hot[e.target]);
  const dimmed = GV.focusing && !focusEdge;
  // 远景 + 非关联的边干脆不画：166 个节点时画面立刻清爽很多
  if (dimmed && GV.view.scale < 0.42) { return; }
  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  if (len < 1) { return; }
  // 弧度随边长增长（远距离的边弯得更明显），方向统一 → 整张图有轻微的"旋臂"观感
  let bow = Math.min(46, Math.max(9, len * 0.11));
  if (e.__count > 1) { bow += (e.__idx - (e.__count - 1) / 2) * len * 0.16; }
  const mx = (a.x + b.x) / 2 - (dy / len) * bow;
  const my = (a.y + b.y) / 2 + (dx / len) * bow;

  const ra = (GV.radius[e.source] || GRAPH_NODE_BASE) * (a.s || 1);
  const rb = (GV.radius[e.target] || GRAPH_NODE_BASE) * (b.s || 1);
  // 屏幕上的固定尺寸：间隙（线不贴球面）、箭头长度
  const gap = 2.5 / GV.view.scale;
  const arrow = (focusEdge ? 7 : 5.2) / GV.view.scale;
  const drawArrow = !dimmed && (focusEdge || GV.view.scale > 0.62);
  // 沿曲线切线方向把两端从球心退到球面之外：线"停在球外"，不再插进球体里
  const t1 = Math.atan2(my - a.y, mx - a.x);
  const t2 = Math.atan2(my - b.y, mx - b.x);
  const sx = a.x + Math.cos(t1) * (ra + gap);
  const sy = a.y + Math.sin(t1) * (ra + gap);
  const stop = rb + gap + (drawArrow ? arrow : 0);
  const ex = b.x + Math.cos(t2) * stop;
  const ey = b.y + Math.sin(t2) * stop;

  ctx.save();
  ctx.globalAlpha = dimmed ? 0.22 : 1;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.lineWidth = (focusEdge ? 1.9 : 0.95) / GV.view.scale;
  ctx.strokeStyle = focusEdge ? C.focus : C.edge;
  if (focusEdge) { ctx.shadowColor = C.focusShadow; ctx.shadowBlur = 6 / GV.view.scale; }
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.quadraticCurveTo(mx, my, ex, ey);
  ctx.stroke();
  ctx.restore();

  if (!drawArrow) { return; }
  // 箭头尖正好落在"球面 + 间隙"上，底边与线端重合 —— 接口干净
  const size = (focusEdge ? 6.8 : 5.4) / GV.view.scale;
  const ux = Math.cos(t2), uy = Math.sin(t2);
  const tipX = b.x + ux * (rb + gap);
  const tipY = b.y + uy * (rb + gap);
  const ang = Math.atan2(-uy, -ux);
  ctx.save();
  ctx.globalAlpha = dimmed ? 0.22 : 1;
  ctx.fillStyle = focusEdge ? C.focusArrow : C.edgeArrow;
  ctx.beginPath();
  ctx.moveTo(tipX, tipY);
  ctx.lineTo(tipX - Math.cos(ang - 0.4) * size, tipY - Math.sin(ang - 0.4) * size);
  ctx.lineTo(tipX - Math.cos(ang + 0.4) * size, tipY - Math.sin(ang + 0.4) * size);
  ctx.closePath();
  ctx.fill();
  ctx.restore();
}

function gvDrawNode(ctx, n, now) {
  const C = gvColors();
  const p = GV.proj[n.id];
  if (!p) { return; }
  const t = gvT(n.id, now);
  if (t <= 0) { return; }
  const sel = GV.selected === n.id;
  const hov = GV.hover === n.id;
  const related = !!GV.hot[n.id];
  const isFocus = GV.focusId === n.id;
  let base = GV.radius[n.id] || GRAPH_NODE_BASE;
  if (GV.focusing && !related) { base *= 0.86; }          // 非关联：微下沉
  else if (GV.focusing && related && !isFocus) { base *= 1.1; }
  if (isFocus) { base *= 1.16; }
  const r = Math.max(1, base * (p.s || 1) * (0.45 + 0.55 * t));
  const color = graphTypeColor(n.type);
  let alpha = t * gvDepthAlpha(n, p);
  if (GV.focusing && !related) { alpha *= 0.32; }         // 非关联：压暗、下沉
  const rich = r > 3.4 && p.s > 0.45;                     // 小节点/远景不做渐变与投影，省性能

  ctx.save();
  ctx.globalAlpha = alpha;
  if (rich && (sel || isFocus)) {
    const halo = ctx.createRadialGradient(p.x, p.y, r * 0.6, p.x, p.y, r * 2.7);
    halo.addColorStop(0, C.halo);
    halo.addColorStop(1, C.halo0);
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(p.x, p.y, r * 2.7, 0, Math.PI * 2);
    ctx.fill();
  }
  if (rich) {
    ctx.shadowColor = C.nodeShadow;
    ctx.shadowBlur = (sel || isFocus ? 9 : 5.5) * (p.s || 1);
    ctx.shadowOffsetY = 1.6 * (p.s || 1);
    const g = ctx.createRadialGradient(p.x - r * 0.34, p.y - r * 0.38, r * 0.12, p.x, p.y, r * 1.05);
    g.addColorStop(0, gvShade(color, 0.52));
    g.addColorStop(0.55, color);
    g.addColorStop(1, gvShade(color, -0.22));
    ctx.fillStyle = g;
  } else {
    ctx.fillStyle = color;
  }
  ctx.beginPath();
  ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.shadowBlur = 0;
  ctx.shadowOffsetY = 0;
  ctx.lineWidth = (sel || isFocus ? 2.2 : hov ? 1.8 : 1) / GV.view.scale;
  ctx.strokeStyle = sel ? C.nodeSelected : (isFocus || hov) ? C.nodeHover : C.nodeStroke;
  ctx.stroke();
  if (rich && r > 6) {
    ctx.beginPath();
    ctx.arc(p.x - r * 0.3, p.y - r * 0.34, r * 0.3, 0, Math.PI * 2);
    ctx.fillStyle = C.nodeGloss;
    ctx.fill();
  }
  ctx.restore();
}

// 两个标签框是否相交
function gvBoxHit(a, b) {
  return !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y);
}

// 给一个常驻标签找"放得下"的锚点：先朝**背离银河中心**的方向往外放（那里正是星团之间的
// 空白区，比一律挂在球下方更容易放得下，也更像"星球带标注"），放不下就绕一圈换方向再试。
// 为什么必须能重试：大球周围往往被邻居围住，只试一个方向时，**关系最多的那个球**一旦
// 撞上邻居就彻底没有名字（verify_texture 的「最大的球一定有名字」就是这么红的）。
// 两层避让：①不与已排定的标签框重叠（可读性的硬底线）；②不把名字正中压在别的球心上
// （名字与球体边缘的轻微相碰由白描边化解，不必一律否决）。
function gvPlaceLabel(nodeId, p, r, vs, w, h, boxes) {
  const base = r + 5 / vs;
  const dl = Math.sqrt(p.x * p.x + p.y * p.y);
  const ux = dl > 1 ? p.x / dl : 0;
  const uy = dl > 1 ? p.y / dl : 1;
  const dirs = [[ux, uy], [0, 1], [0, -1], [1, 0], [-1, 0],
                [0.7071, 0.7071], [0.7071, -0.7071], [-0.7071, 0.7071], [-0.7071, -0.7071]];
  // 距离也要能退：密集星团里的 Hub 四周被邻居围住，只在"贴着球"的距离上换方向仍然一个都放不下。
  const offs = [base, base * 1.6, base * 2.4];
  for (let o = 0; o < offs.length; o++) {
    for (let d = 0; d < dirs.length; d++) {
      const ax = p.x + dirs[d][0] * offs[o];
      const ay = p.y + dirs[d][1] * offs[o];
      const box = { x: ax - w / 2, y: ay - h / 2, w: w, h: h };
      let ok = true;
      for (let i = 0; i < boxes.length && ok; i++) { ok = !gvBoxHit(box, boxes[i]); }
      for (let i = 0; i < GV.nodes.length && ok; i++) {
        const other = GV.nodes[i];
        if (other.id === nodeId) { continue; }
        const q = GV.proj[other.id];
        if (!q) { continue; }
        const rr = (GV.radius[other.id] || GRAPH_NODE_BASE) * (q.s || 1) * 0.9;
        if ((ax - q.x) * (ax - q.x) + (ay - q.y) * (ay - q.y) < rr * rr) { ok = false; }
      }
      if (ok) { return { ax: ax, ay: ay, box: box }; }
    }
  }
  return null;
}

// 全局图"开局就有文字"：按度数从高到低，给枢纽节点挂**常驻**名字。
// 摆放与避让交给 gvPlaceLabel（它会在多个方向上挑一个放得下的位置）。
// 数量上限随当前缩放自适应（缩得越小留的标签越少），手机上自然更少 —— 美感优先。
function gvPlanLabels(ctx) {
  const out = [];
  if (GV.scope !== "global" || GV.hover || GV.selected) { return out; }
  const vs = GV.view.scale;
  // 标几个：综合"留白多少（缩放）"与"有多少值得标的（节点规模）"，再夹在 3~16 之间。
  // 真正能放下几个由后面的避让决定 —— 这里只给一个合理的上限。
  const maxLabels = Math.max(
    3,
    Math.min(16, Math.min(Math.round(vs * 26), Math.round(Math.sqrt(GV.nodes.length) * 1.15)))
  );
  // 标谁：**先比关系多少（度数），再比球体大小**。
  // 为什么不只按球大小排：球半径还要受"最近邻距离"兜底压制（见 gvApplyNodeScale），
  // 密集星团里关系最多的 Hub 会被压得比稀疏区的中等球还小 —— 只按球排就会把它挤掉，
  // 于是"关系最多的那个一定有名字"这条承诺就守不住了（verify_texture 的用例）。
  const ranked = GV.nodes.slice().sort(function (a, b) {
    const da = (GV.adj[a.id] || []).length, db = (GV.adj[b.id] || []).length;
    if (db !== da) { return db - da; }
    return (GV.radius[b.id] || 0) - (GV.radius[a.id] || 0);
  });
  ctx.save();
  ctx.font = "600 " + (12.5 / vs) + "px -apple-system,'Microsoft YaHei',sans-serif";
  const boxes = [];
  for (let k = 0; k < ranked.length; k++) {
    if (out.length >= maxLabels) { break; }
    const n = ranked[k];
    const deg = (GV.adj[n.id] || []).length;
    if (deg < 3) { continue; }              // 关系太少的点不配常驻名字（顺序按球大小，不能 break）
    const p = GV.proj[n.id];
    if (!p) { continue; }
    const text = n.id.length > 12 ? n.id.slice(0, 11) + "…" : n.id;
    const r = (GV.radius[n.id] || GRAPH_NODE_BASE) * (p.s || 1);
    const w = ctx.measureText(text).width + 8 / vs;
    const h = 16 / vs;
    // 标签朝**背离银河中心**的方向往外放：那里正是星团之间的空白区，
    // 比一律挂在球下方（正好压着邻居）更容易放得下，也更像"星球带标注"。
    const spot = gvPlaceLabel(n.id, p, r, vs, w, h, boxes);
    if (!spot) { continue; }
    boxes.push(spot.box);
    // bw/bh 是**规划器实际使用的框**（含 ctx.measureText 实测宽度）：验收脚本据此复核
    // "标签之间不重叠"这条硬底线，不必再另写一份近似公式（那样只会假红／假绿）。
    out.push({ id: n.id, text: text, deg: deg, ax: spot.ax, ay: spot.ay, bw: w, bh: h });
  }
  ctx.restore();
  return out;
}

function gvLabelVisible(n) {
  if (GV.hover === n.id || GV.selected === n.id) { return true; }
  if (GV.focusing) { return !!GV.hot[n.id]; }
  if (GV.scope === "global") {
    // 全局图：常驻标签由 gvPlanLabels 按度数 + 避让挑出来（保证开局就有文字，
    // 又不会 166 个名字糊成一片）；其余节点靠悬停。
    return !!(GV.planned && GV.planned[n.id]);
  }
  if (n.id === GV.center) { return true; }
  if (GV.mode === "3d") {
    if (!GV.selected || !GV.adj[GV.selected]) { return false; }
    return GV.adj[GV.selected].indexOf(n.id) >= 0;
  }
  return GV.view.scale > 0.78;
}

function gvDrawLabel(ctx, n) {
  const C = gvColors();
  const p = GV.proj[n.id];
  if (!p || !gvLabelVisible(n)) { return; }
  const r = (GV.radius[n.id] || GRAPH_NODE_BASE) * (p.s || 1);
  const planned = GV.planned && GV.planned[n.id];
  const label = planned ? planned.text : (n.id.length > 16 ? n.id.slice(0, 15) + "…" : n.id);
  const hot = GV.hover === n.id || GV.selected === n.id || (GV.focusing && GV.hot[n.id]);
  // 关键：字号固定在"屏幕"上 —— 画布已被 scale 放大/缩小，所以这里反除 scale。
  // 于是图是矢量缩放的，文字永远保持同一个合适大小（不会在全局图里缩成看不清的小字）。
  const vs = GV.view.scale;
  ctx.save();
  ctx.font = (hot ? "600 " + (13 / vs) + "px " : (12.5 / vs) + "px ") + "-apple-system,'Microsoft YaHei',sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  const padX = 6 / vs, padY = 2.5 / vs;
  const tw = ctx.measureText(label).width;
  // 常驻标签用它自己规划出来的锚点（朝外放），其余仍挂在球下方
  const lx = planned && planned.ax !== undefined ? planned.ax : p.x;
  const y = planned && planned.ay !== undefined ? planned.ay - 8 / vs : p.y + r + 4 / vs;
  if (hot) {
    ctx.fillStyle = C.labelBg;
    ctx.strokeStyle = C.labelStroke;
    ctx.lineWidth = 1 / vs;
    const bx = lx - tw / 2 - padX, by = y - padY;
    const bw = tw + padX * 2, bh = 15 / vs + padY * 2;
    const rr = 6 / vs;
    ctx.beginPath();
    ctx.moveTo(bx + rr, by);
    ctx.lineTo(bx + bw - rr, by);
    ctx.quadraticCurveTo(bx + bw, by, bx + bw, by + rr);
    ctx.lineTo(bx + bw, by + bh - rr);
    ctx.quadraticCurveTo(bx + bw, by + bh, bx + bw - rr, by + bh);
    ctx.lineTo(bx + rr, by + bh);
    ctx.quadraticCurveTo(bx, by + bh, bx, by + bh - rr);
    ctx.lineTo(bx, by + rr);
    ctx.quadraticCurveTo(bx, by, bx + rr, by);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  } else {
    ctx.lineWidth = 3 / GV.view.scale;
    ctx.strokeStyle = C.labelHalo;
    ctx.strokeText(label, lx, y);
  }
  ctx.fillStyle = hot ? C.labelHot : C.labelText;
  ctx.fillText(label, lx, y);
  ctx.restore();
}

function gvDraw() {
  const cv = $("graphCanvas");
  if (!cv) { return; }
  const wrap = cv.parentElement;
  const w = Math.max(1, wrap.clientWidth);
  const h = Math.max(1, wrap.clientHeight);
  const dpr = window.devicePixelRatio || 1;
  GV.dpr = dpr;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  if (!GV.nodes.length) { gvRenderLegend(); return; }

  gvBackdrop(ctx, w, h);

  const now = performance.now();
  gvRebuildFocus();
  GV.proj = {};
  GV.nodes.forEach(function (n) { GV.proj[n.id] = gvProject(gvPoint(n.id)); });

  // 常驻标签先规划（按度数 + 两层避让），再进入缩放坐标系绘制
  GV.planned = {};
  gvPlanLabels(ctx).forEach(function (item) { GV.planned[item.id] = item; });

  ctx.save();
  ctx.translate(w / 2 + GV.view.tx, h / 2 + GV.view.ty);
  ctx.scale(GV.view.scale, GV.view.scale);

  // 画家算法：节点与边一起按深度排序，远的先画
  const items = [];
  GV.nodes.forEach(function (n) {
    const p = GV.proj[n.id];
    if (p) { items.push({ kind: "node", depth: p.d, node: n }); }
  });
  GV.edges.forEach(function (e) {
    const a = GV.proj[e.source], b = GV.proj[e.target];
    if (a && b) { items.push({ kind: "edge", depth: (a.d + b.d) / 2, edge: e }); }
  });
  // 深度相同时**必须**先画边再画节点：2D 下所有元素深度都是 0，否则排序退化成
  // "节点在前、边在后"，连线就会全部压在星球上面（看起来就是线插进了球里）。
  items.sort(function (m, n2) {
    if (m.depth !== n2.depth) { return m.depth - n2.depth; }
    if (m.kind === n2.kind) { return 0; }
    return m.kind === "edge" ? -1 : 1;
  });
  items.forEach(function (item) {
    if (item.kind === "edge") { gvDrawEdge(ctx, item.edge); } else { gvDrawNode(ctx, item.node, now); }
  });
  GV.nodes.forEach(function (n) { gvDrawLabel(ctx, n); });
  ctx.restore();
  gvRenderLegend();
}

function gvRenderLegend() {
  const host = $("graphLegend");
  if (!host) { return; }
  const types = {};
  (GV.allNodes || []).forEach(function (n) { types[n.type || ""] = true; });
  const names = Object.keys(types);
  if (names.length < 2) {
    host.hidden = true;
    host.innerHTML = "";
    host.dataset.key = "";
    return;
  }
  const key = names.map(function (type) {
    return type + ":" + (GV.hiddenTypes[type] ? "0" : "1");
  }).join("|");
  if (host.dataset.key === key) {
    host.hidden = false;
    return;
  }
  host.dataset.key = key;
  host.innerHTML = "";
  names.slice(0, 8).forEach(function (type) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.graphType = type;
    button.className = GV.hiddenTypes[type] ? "off" : "";
    button.title = GV.hiddenTypes[type] ? "显示此类型" : "隐藏此类型";
    const dot = document.createElement("i");
    dot.style.background = graphTypeColor(type);
    button.appendChild(dot);
    button.appendChild(document.createTextNode(type || "未标注"));
    host.appendChild(button);
  });
  host.hidden = false;
}

function gvToggleType(type) {
  const hidden = GV.hiddenTypes || {};
  if (!hidden[type]) {
    const remaining = Object.keys((GV.allNodes || []).reduce(function (out, node) {
      if (node.type !== type) { out[node.type || ""] = true; }
      return out;
    }, {})).filter(function (name) { return !hidden[name]; });
    if (!remaining.length) {
      setToast("至少保留一种实体类型", "");
      return;
    }
  }
  hidden[type] = !hidden[type];
  GV.hiddenTypes = hidden;
  const hadPanel = !!(GV.selected || GV.selectedEdge);
  gvApplyTypeFilter();
  if (hadPanel && !GV.selected && !GV.selectedEdge) {
    gvPanelPlaceholder();
  }
}

function gvHitEdge(x, y) {
  let best = null;
  let bestDistance = Infinity;
  const threshold = 8 / GV.view.scale;
  GV.edges.forEach(function (edge) {
    const a = GV.proj[edge.source];
    const b = GV.proj[edge.target];
    if (!a || !b) { return; }
    if (GV.focusing && GV.view.scale < 0.42
      && GV.selectedEdge !== edge.id
      && !(GV.hot[edge.source] && GV.hot[edge.target])) { return; }
    const dx = b.x - a.x, dy = b.y - a.y;
    const length = Math.sqrt(dx * dx + dy * dy) || 1;
    let bow = Math.min(46, Math.max(9, length * 0.11));
    if (edge.__count > 1) {
      bow += (edge.__idx - (edge.__count - 1) / 2) * length * 0.16;
    }
    const cx = (a.x + b.x) / 2 - (dy / length) * bow;
    const cy = (a.y + b.y) / 2 + (dx / length) * bow;
    let lastX = a.x, lastY = a.y;
    for (let step = 1; step <= 16; step++) {
      const t = step / 16;
      const inv = 1 - t;
      const px = inv * inv * a.x + 2 * inv * t * cx + t * t * b.x;
      const py = inv * inv * a.y + 2 * inv * t * cy + t * t * b.y;
      const sx = px - lastX, sy = py - lastY;
      const len2 = sx * sx + sy * sy;
      const q = len2 > 0 ? Math.max(0, Math.min(1, ((x - lastX) * sx + (y - lastY) * sy) / len2)) : 0;
      const dx2 = x - (lastX + q * sx);
      const dy2 = y - (lastY + q * sy);
      const distance = Math.sqrt(dx2 * dx2 + dy2 * dy2);
      if (distance < bestDistance) {
        bestDistance = distance;
        best = edge.id;
      }
      lastX = px;
      lastY = py;
    }
  });
  return bestDistance <= threshold ? best : null;
}

function gvHit(clientX, clientY) {
  const cv = $("graphCanvas");
  if (!cv) { return { id: null, edgeId: null, wx: 0, wy: 0 }; }
  const rect = cv.getBoundingClientRect();
  const x = (clientX - rect.left - rect.width / 2 - GV.view.tx) / GV.view.scale;
  const y = (clientY - rect.top - rect.height / 2 - GV.view.ty) / GV.view.scale;
  let hit = null;
  for (let i = GV.nodes.length - 1; i >= 0; i--) {
    const n = GV.nodes[i];
    const p = GV.proj[n.id];
    if (!p) { continue; }
    const r = (GV.radius[n.id] || GRAPH_NODE_BASE) * (p.s || 1) + 5 / GV.view.scale;
    if ((x - p.x) * (x - p.x) + (y - p.y) * (y - p.y) <= r * r) { hit = n.id; break; }
  }
  return { id: hit, edgeId: hit ? null : gvHitEdge(x, y), wx: x, wy: y };
}

function gvFit() {
  const cv = $("graphCanvas");
  if (!cv || !GV.nodes.length) { GV.view = { scale: 1, tx: 0, ty: 0 }; return; }
  const wrap = cv.parentElement;
  const w = Math.max(200, wrap.clientWidth);
  const h = Math.max(200, wrap.clientHeight);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  GV.nodes.forEach(function (n) {
    const p = gvProject(gvPoint(n.id));
    if (!p) { return; }
    const r = (GV.radius[n.id] || GRAPH_NODE_BASE) * (p.s || 1);
    minX = Math.min(minX, p.x - r);
    maxX = Math.max(maxX, p.x + r);
    minY = Math.min(minY, p.y - r);
    maxY = Math.max(maxY, p.y + r);
  });
  if (!isFinite(minX)) { return; }
  const pad = GV.scope === "global" ? 34 : 64;
  const scale = Math.min(
    1.7,
    Math.max(0.18, Math.min((w - pad * 2) / Math.max(1, maxX - minX), (h - pad * 2) / Math.max(1, maxY - minY)))
  );
  GV.view.scale = scale;
  GV.view.tx = -((minX + maxX) / 2) * scale;
  GV.view.ty = -((minY + maxY) / 2) * scale;
}

function gvShowOverlay(kind, text) {
  $("graphEmpty").hidden = kind !== "empty";
  $("graphBusy").hidden = kind !== "busy";
  $("graphError").hidden = kind !== "error";
  if (kind === "busy" && text) { $("graphBusyText").textContent = text; }
  if (kind === "error") { $("graphErrorText").textContent = text || "读不到知识图谱。"; }
}

function gvUpdateSub() {
  const mode = GV.mode === "3d" ? "三维" : "平面";
  if (!GV.nodes.length) { $("graphSub").textContent = "正在读取图谱…"; return; }
  if (GV.scope === "global") {
    let text = "全局知识图谱 · " + GV.nodes.length + " 个实体 / " + GV.edges.length + " 条关系";
    if (GV.clusters > 1) { text += " · " + GV.clusters + " 个连通星团"; }
    text += " · " + mode;
    if (GV.truncated) { text += " · 已截断"; }
    $("graphSub").textContent = text;
    return;
  }
  let text = "以「" + GV.center + "」为中心 · " + GV.nodes.length + " 个实体 / " + GV.edges.length
    + " 条关系 · " + mode;
  if (GV.truncated) { text += " · 图较大，已截断"; }
  $("graphSub").textContent = text;
}

function gvUpdateScopeUI() {
  const btn = $("graphGlobalBtn");
  if (btn) { btn.classList.toggle("on", GV.scope === "global"); }
  const back = $("graphBackGlobal");
  if (back) { back.hidden = GV.scope === "global"; }
}

function gvSetMode(mode) {
  GV.mode = mode === "3d" ? "3d" : "2d";
  document.querySelectorAll("#graphModes button").forEach(function (btn) {
    btn.classList.toggle("on", btn.getAttribute("data-mode") === GV.mode);
  });
  const hint = $("graph3dHint");
  if (hint) { hint.hidden = GV.mode !== "3d"; }
  GV.hover = null;
  $("graphTip").hidden = true;
  if (GV.nodes.length) { gvRelayoutView(); gvStartAnim(); }
  gvUpdateSub();
  gvDraw();
}

function gvShowTip(id, clientX, clientY) {
  const tip = $("graphTip");
  const node = GV.byId[id];
  if (!node) { tip.hidden = true; return; }
  const wrap = $("graphCanvas").parentElement.getBoundingClientRect();
  tip.innerHTML = "";
  const name = document.createElement("b");
  name.textContent = node.id;
  tip.appendChild(name);
  const deg = (GV.adj[id] || []).length;
  const meta = document.createElement("span");
  meta.textContent = (node.type ? node.type + " · " : "") + deg + " 个直接关联";
  tip.appendChild(meta);
  tip.hidden = false;
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = clientX - wrap.left + 14;
  let top = clientY - wrap.top + 14;
  if (left + tw > wrap.width - 8) { left = wrap.width - tw - 8; }
  if (top + th > wrap.height - 8) { top = clientY - wrap.top - th - 12; }
  tip.style.left = Math.max(8, left) + "px";
  tip.style.top = Math.max(8, top) + "px";
}

function gvSelect(id) {
  GV.selectedEdge = null;
  GV.selected = id || null;
  const node = GV.byId[GV.selected];
  const panel = $("graphPanel");
  if (!node) { gvPanelPlaceholder(); return; }
  panel.classList.remove("empty");
  panel.hidden = false;
  $("gpName").textContent = node.id;
  const deg = (GV.adj[node.id] || []).length;
  $("gpType").textContent = (node.type ? "类型：" + node.type : "类型：未标注") + " · 直接关联 " + deg;
  $("gpDesc").textContent = node.desc || "（这个实体没有描述）";
  const files = $("gpFiles");
  files.innerHTML = "";
  if (!node.files.length) {
    const none = document.createElement("div");
    none.className = "gp-kind";
    none.textContent = "（未记录来源文档）";
    files.appendChild(none);
  }
  node.files.forEach(function (path) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = path;
    btn.title = "就这份资料提问";
    btn.addEventListener("click", function () { graphAskFile(path); });
    files.appendChild(btn);
  });
  const centerBtn = $("gpCenter");
  centerBtn.textContent = GV.scope === "global" ? "展开它的关系图" : "以它为中心";
  $("gpAsk").onclick = function () { graphAskEntity(node.id); };
  $("gpAsk").textContent = "就这个实体提问";
  centerBtn.onclick = function () { loadGraphSubgraph(node.id); };
  const nearTitle = $("gpNearTitle");
  if (nearTitle) { nearTitle.textContent = "直接关联"; }
  // 关联关系可点：点邻居直接打开这条边的关系详情。
  const near = $("gpNear");
  if (near) {
    near.innerHTML = "";
    const related = GV.edges.filter(function (edge) {
      return edge.source === node.id || edge.target === node.id;
    }).slice(0, 12);
    if (!related.length) {
      const none = document.createElement("div");
      none.className = "gp-kind";
      none.textContent = "（没有直接关联）";
      near.appendChild(none);
    }
    related.forEach(function (edge) {
      const otherId = edge.source === node.id ? edge.target : edge.source;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = otherId;
      btn.title = edge.desc || edge.keywords || "查看关系详情";
      btn.addEventListener("click", function () { gvSelectEdge(edge.id); });
      near.appendChild(btn);
    });
  }
  gvDraw();
}

function gvSelectEdge(id) {
  const edge = GV.edges.find(function (item) { return item.id === id; });
  if (!edge) { gvPanelPlaceholder(); return; }
  GV.selected = null;
  GV.selectedEdge = edge.id;
  GV.hover = null;
  $("graphTip").hidden = true;
  const panel = $("graphPanel");
  panel.hidden = false;
  panel.classList.remove("empty");
  $("gpName").textContent = edge.source + " — " + edge.target;
  const weight = edge.weight === undefined || edge.weight === null || edge.weight === ""
    ? ""
    : " · 权重 " + edge.weight;
  $("gpType").textContent = "关系" + (edge.type ? "：" + edge.type : "") + weight;
  $("gpDesc").textContent = edge.desc || edge.keywords
    || "这条关系没有描述。";
  const files = $("gpFiles");
  files.innerHTML = "";
  if (!edge.files.length) {
    const none = document.createElement("div");
    none.className = "gp-kind";
    none.textContent = "（未记录来源文档）";
    files.appendChild(none);
  }
  edge.files.forEach(function (path) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = path;
    btn.title = "就这份资料提问";
    btn.addEventListener("click", function () { graphAskFile(path); });
    files.appendChild(btn);
  });
  const nearTitle = $("gpNearTitle");
  if (nearTitle) { nearTitle.textContent = "关系两端"; }
  const near = $("gpNear");
  near.innerHTML = "";
  [edge.source, edge.target].forEach(function (name, index) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = (index === 0 ? "起点 · " : "终点 · ") + name;
    btn.title = "选中实体 " + name;
    btn.addEventListener("click", function () { gvSelect(name); });
    near.appendChild(btn);
  });
  const ask = $("gpAsk");
  ask.textContent = "就这条关系提问";
  ask.onclick = function () {
    gvGoAsk("「" + edge.source + "」和「" + edge.target + "」是什么关系？请结合我的资料说明。");
  };
  const centerBtn = $("gpCenter");
  centerBtn.textContent = "以起点为中心";
  centerBtn.onclick = function () { loadGraphSubgraph(edge.source); };
  gvDraw();
}

// 没选中实体时：把预留出来的一栏变成"怎么用"的引导，而不是留白
function gvPanelPlaceholder() {
  GV.selected = null;
  GV.selectedEdge = null;
  const panel = $("graphPanel");
  panel.hidden = false;
  panel.classList.add("empty");
  $("gpName").textContent = "还没有选中实体";
  $("gpType").textContent = "";
  $("gpDesc").textContent = "点画布上的任意节点（或从左边搜索、点热门实体），"
    + "这里会显示它的类型、描述、来源文档与「直接关联」，还能就它提问。";
  $("gpFiles").innerHTML = "";
  const nearTitle = $("gpNearTitle");
  if (nearTitle) { nearTitle.textContent = "直接关联"; }
  const near = $("gpNear");
  if (near) { near.innerHTML = ""; }
  $("gpAsk").textContent = "就这个实体提问";
  $("graphTip").hidden = true;
  gvDraw();
}

function gvGoAsk(question) {
  closeGraph();
  input.value = question;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.focus();
  setToast("已填入问题，按 Enter 发送", "ok");
}

function graphAskEntity(name) {
  gvGoAsk("关于「" + name + "」，我的资料里是怎么说的？");
}

function graphAskFile(path) {
  const base = String(path).split("/").pop();
  gvGoAsk("《" + base + "》里写了什么？请概括要点。");
}

// 上游数据 → 画布（全局图与自我图共用）
function gvApplyData(data) {
  const nodes = (data.nodes || []).map(function (raw) {
    const props = raw.properties || {};
    const rawType = props.entity_type;
    const type = Array.isArray(rawType)
      ? rawType.filter(Boolean).join(" / ")
      : String(rawType || "").trim();
    return {
      id: raw.id,
      type: type,
      desc: props.description || "",
      files: gvSplitFiles(props.file_path)
    };
  });
  const edges = (data.edges || []).map(function (raw) {
    const props = raw.properties || {};
    return {
      id: raw.id || (raw.source + "-" + raw.target),
      source: raw.source,
      target: raw.target,
      type: raw.type || "",
      desc: props.description || "",
      keywords: props.keywords || "",
      weight: props.weight,
      files: gvSplitFiles(props.file_path)
    };
  });
  const truncated = !!data.is_truncated;
  gvReset();
  GV.allNodes = nodes;
  GV.allEdges = edges;
  GV.truncated = truncated;
  if (!nodes.length) {
    gvPanelPlaceholder();
    gvShowOverlay("empty");
    $("graphSub").textContent = GV.scope === "global" ? "知识库里还没有实体" : "「" + GV.center + "」没有关联的实体";
    return false;
  }
  gvApplyTypeFilter();
  return GV.nodes.length > 0;
}

// 类型筛选只改“当前可见集合”，原始图数据保留，切回类型时无需重新请求后端。
function gvApplyTypeFilter() {
  const hidden = GV.hiddenTypes || {};
  const nodes = (GV.allNodes || []).filter(function (node) {
    return !hidden[node.type || ""];
  });
  const visible = {};
  nodes.forEach(function (node) { visible[node.id] = true; });
  const edges = (GV.allEdges || []).filter(function (edge) {
    return visible[edge.source] && visible[edge.target];
  });
  GV.nodes = nodes;
  GV.edges = edges;
  GV.byId = {};
  GV.adj = {};
  nodes.forEach(function (node) {
    GV.byId[node.id] = node;
    GV.adj[node.id] = [];
  });
  edges.forEach(function (edge) {
    if (GV.adj[edge.source] && GV.adj[edge.target]) {
      GV.adj[edge.source].push(edge.target);
      GV.adj[edge.target].push(edge.source);
    }
  });
  if (GV.selected && !GV.byId[GV.selected]) { GV.selected = null; }
  if (GV.selectedEdge && !edges.some(function (edge) { return edge.id === GV.selectedEdge; })) {
    GV.selectedEdge = null;
  }
  if (!nodes.length) {
    GV.animDone = true;
    gvShowOverlay("empty");
    $("graphSub").textContent = "当前筛选没有可显示的实体";
    gvRenderLegend();
    gvDraw();
    return false;
  }
  gvLayout();
  gvRelayoutView();
  gvShowOverlay(null);
  gvUpdateSub();
  gvUpdateScopeUI();
  const meta = $("graphGlobalMeta");
  if (meta && GV.scope === "global") {
    meta.textContent = GV.nodes.length + " 个实体 · " + GV.edges.length + " 条关系"
      + (GV.clusters > 1 ? " · " + GV.clusters + " 个星团" : "");
  }
  gvStartAnim();
  gvRenderLegend();
  gvDraw();
  return true;
}

async function graphResponseError(response) {
  try {
    const data = await response.json();
    const error = data && data.error;
    if (error && typeof error === "object" && error.message) { return String(error.message); }
    if (typeof error === "string") { return error; }
    if (data && typeof data.detail === "string") { return data.detail; }
  } catch (err) { /* 非 JSON 错误响应只保留状态码 */ }
  return "HTTP " + response.status;
}

// 默认视图：整张知识图谱
async function loadGraphGlobal() {
  GV.scope = "global";
  GV.center = "";
  gvShowOverlay("busy", "正在读取全局知识图谱…");
  try {
    // 参数名必须是 max_nodes：后端只认这个名字，写 ?max= 会被忽略、
    // 页面拿到的是**服务端默认值**（默认也是 400，所以以前"看起来能用"）。
    const resp = await fetch("/api/graph/global?max_nodes=400", { cache: "no-store" });
    if (!resp.ok) { throw new Error(await graphResponseError(resp)); }
    const data = await resp.json();
    if (!gvApplyData(data)) { return; }
    const meta = $("graphGlobalMeta");
    if (meta) {
      meta.textContent = GV.nodes.length + " 个实体 · " + GV.edges.length + " 条关系"
        + (GV.clusters > 1 ? " · " + GV.clusters + " 个星团" : "");
    }
    gvSelect(null);
  } catch (err) {
    gvShowOverlay("error", "读不到图谱：" + (err && err.message ? err.message : err));
  }
}

// 下钻：以某实体为中心的自我图
async function loadGraphSubgraph(label) {
  const name = String(label || "").trim();
  if (!name) { return; }
  GV.scope = "ego";
  GV.center = name;
  gvShowOverlay("busy", "正在读取「" + name + "」的关系图…");
  const suggest = $("graphSuggest");
  if (suggest) { suggest.hidden = true; }
  try {
    const resp = await fetch(
      "/api/graph/subgraph?label=" + encodeURIComponent(name) + "&depth=2&max_nodes=60",
      { cache: "no-store" }
    );
    if (!resp.ok) { throw new Error(await graphResponseError(resp)); }
    const data = await resp.json();
    if (!gvApplyData(data)) { return; }
    gvSelect(GV.byId[name] ? name : GV.nodes[0].id);
    document.querySelectorAll("#graphPopular button").forEach(function (btn) {
      btn.classList.toggle("on", btn.getAttribute("data-label") === name);
    });
  } catch (err) {
    gvShowOverlay("error", "读不到图谱：" + (err && err.message ? err.message : err));
  }
}

function graphBackToGlobal() {
  document.querySelectorAll("#graphPopular button").forEach(function (btn) { btn.classList.remove("on"); });
  loadGraphGlobal();
}

async function loadGraphPopular() {
  const host = $("graphPopular");
  if (!host) { return; }
  try {
    const resp = await fetch("/api/graph/popular?limit=16", { cache: "no-store" });
    if (!resp.ok) { throw new Error(await graphResponseError(resp)); }
    const data = await resp.json();
    const items = data.items || [];
    host.innerHTML = "";
    items.forEach(function (name) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = name;
      btn.setAttribute("data-label", name);
      btn.title = "展开「" + name + "」的关系图";
      btn.addEventListener("click", function () { loadGraphSubgraph(name); });
      host.appendChild(btn);
    });
    if (!items.length) { host.textContent = "知识库里还没有实体"; }
  } catch (err) {
    host.textContent = "热门实体读取失败";
  }
}

function graphBindSearch() {
  const box = $("graphSearchInput");
  const list = $("graphSuggest");
  if (!box || !list) { return; }
  box.addEventListener("input", function () {
    const q = box.value.trim();
    if (graphSearchTimer) { clearTimeout(graphSearchTimer); }
    if (!q) { list.hidden = true; list.innerHTML = ""; return; }
    graphSearchTimer = setTimeout(async function () {
      try {
        const resp = await fetch("/api/graph/search?q=" + encodeURIComponent(q) + "&limit=20", { cache: "no-store" });
        if (!resp.ok) { throw new Error(await graphResponseError(resp)); }
        const data = await resp.json();
        const items = data.items || [];
        list.innerHTML = "";
        if (!items.length) {
          const none = document.createElement("div");
          none.className = "gp-kind";
          none.style.padding = "6px 9px";
          none.textContent = "没有匹配的实体";
          list.appendChild(none);
        }
        items.forEach(function (name) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = name;
          btn.addEventListener("click", function () {
            list.hidden = true;
            box.value = name;
            loadGraphSubgraph(name);
          });
          list.appendChild(btn);
        });
        list.hidden = false;
      } catch (err) {
        list.innerHTML = "";
        const failed = document.createElement("div");
        failed.className = "gp-kind";
        failed.style.padding = "6px 9px";
        failed.textContent = "搜索失败：" + (err && err.message ? err.message : err);
        list.appendChild(failed);
        list.hidden = false;
      }
    }, 200);
  });
  box.addEventListener("keydown", function (event) {
    if (event.key === "Enter") {
      const q = box.value.trim();
      if (q) { list.hidden = true; loadGraphSubgraph(q); }
    } else if (event.key === "Escape") {
      list.hidden = true;
    }
  });
  document.addEventListener("click", function (event) {
    if (event.target && event.target.closest && !event.target.closest(".gr-search")) {
      list.hidden = true;
    }
  });
}

function graphBindCanvas() {
  const cv = $("graphCanvas");
  if (!cv || cv.dataset.bound === "1") { return; }
  cv.dataset.bound = "1";
  cv.addEventListener("wheel", function (event) {
    event.preventDefault();
    const rect = cv.getBoundingClientRect();
    const px = event.clientX - rect.left;          // 光标在画布内的位置
    const py = event.clientY - rect.top;
    const before = GV.view.scale;
    const factor = event.deltaY < 0 ? 1.14 : 1 / 1.14;
    const after = Math.max(0.1, Math.min(8, before * factor));
    if (after === before) { return; }
    // **以鼠标位置为中心**缩放：让光标下的那个世界坐标点在缩放前后停在同一个屏幕位置。
    // 屏幕坐标 = 画布中心 + tx + 世界坐标 × scale，令光标处映射不变，即得下式。
    const k = after / before;
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    GV.view.tx = px - cx - (px - cx - GV.view.tx) * k;
    GV.view.ty = py - cy - (py - cy - GV.view.ty) * k;
    GV.view.scale = after;
    gvDraw();
  }, { passive: false });
  cv.addEventListener("mousedown", function (event) {
    if (event.button !== 0) { return; }
    const hit = gvHit(event.clientX, event.clientY);
    GV.moved = false;
    if (GV.mode === "3d") {
      if (event.shiftKey) {
        GV.pan = { x: event.clientX, y: event.clientY, tx: GV.view.tx, ty: GV.view.ty };
      } else {
        GV.rotate = { x: event.clientX, y: event.clientY, yaw: GV.rot.yaw, pitch: GV.rot.pitch };
      }
    } else if (hit.id) {
      GV.drag = { id: hit.id };
    } else if (hit.edgeId) {
      GV.hover = null;
    } else {
      GV.pan = { x: event.clientX, y: event.clientY, tx: GV.view.tx, ty: GV.view.ty };
    }
    cv.classList.add("dragging");
    event.preventDefault();
  });
  cv.addEventListener("mousemove", function (event) {
    if (GV.rotate) {
      GV.rot.yaw = GV.rotate.yaw + (event.clientX - GV.rotate.x) * 0.008;
      GV.rot.pitch = Math.max(-1.32, Math.min(1.32, GV.rotate.pitch - (event.clientY - GV.rotate.y) * 0.006));
      GV.moved = true;
      gvDraw();
      return;
    }
    if (GV.drag) {
      const hit = gvHit(event.clientX, event.clientY);
      GV.pos2[GV.drag.id] = { x: hit.wx, y: hit.wy };
      GV.moved = true;
      gvDraw();
      return;
    }
    if (GV.pan) {
      GV.view.tx = GV.pan.tx + (event.clientX - GV.pan.x);
      GV.view.ty = GV.pan.ty + (event.clientY - GV.pan.y);
      GV.moved = true;
      gvDraw();
      return;
    }
    const hit = gvHit(event.clientX, event.clientY);
    if (hit.id !== GV.hover) {
      GV.hover = hit.id;
      cv.style.cursor = (hit.id || hit.edgeId) ? "pointer" : (GV.mode === "3d" ? "move" : "grab");
      gvShowTip(hit.id, event.clientX, event.clientY);
      gvDraw();
    } else if (hit.id) {
      gvShowTip(hit.id, event.clientX, event.clientY);
    }
  });
  cv.addEventListener("dblclick", function (event) {
    const hit = gvHit(event.clientX, event.clientY);
    if (hit.id) { loadGraphSubgraph(hit.id); }
  });
  window.addEventListener("mouseup", function (event) {
    if (!GV.drag && !GV.pan && !GV.rotate) { return; }
    const moved = GV.moved;
    const wasDrag = !!GV.drag;
    GV.drag = null;
    GV.pan = null;
    GV.rotate = null;
    cv.classList.remove("dragging");
    if (moved) { return; }
    const hit = gvHit(event.clientX, event.clientY);
    if (wasDrag) { gvSelect(GV.hover || GV.selected); return; }
    if (hit.id) {
      gvSelect(hit.id);
    } else if (hit.edgeId) {
      gvSelectEdge(hit.edgeId);
    } else {
      gvSelect(null);
    }
  });
  cv.addEventListener("mouseleave", function () {
    GV.hover = null;
    $("graphTip").hidden = true;
    gvDraw();
  });
  cv.addEventListener("touchstart", function (event) {
    if (event.touches.length !== 1) { return; }
    const t = event.touches[0];
    GV.touchX = t.clientX;
    GV.touchY = t.clientY;
    const hit = gvHit(t.clientX, t.clientY);
    GV.moved = false;
    if (GV.mode === "3d") {
      GV.touchRot = { x: t.clientX, y: t.clientY, yaw: GV.rot.yaw, pitch: GV.rot.pitch };
    } else if (hit.id) {
      GV.drag = { id: hit.id };
    } else if (hit.edgeId) {
      GV.hover = null;
    } else {
      GV.pan = { x: t.clientX, y: t.clientY, tx: GV.view.tx, ty: GV.view.ty };
    }
  }, { passive: true });
  cv.addEventListener("touchmove", function (event) {
    if (event.touches.length !== 1) { return; }
    const t = event.touches[0];
    GV.touchX = t.clientX;
    GV.touchY = t.clientY;
    if (GV.touchRot) {
      GV.rot.yaw = GV.touchRot.yaw + (t.clientX - GV.touchRot.x) * 0.01;
      GV.rot.pitch = Math.max(-1.32, Math.min(1.32, GV.touchRot.pitch - (t.clientY - GV.touchRot.y) * 0.007));
    } else if (GV.drag) {
      const hit = gvHit(t.clientX, t.clientY);
      GV.pos2[GV.drag.id] = { x: hit.wx, y: hit.wy };
    } else if (GV.pan) {
      GV.view.tx = GV.pan.tx + (t.clientX - GV.pan.x);
      GV.view.ty = GV.pan.ty + (t.clientY - GV.pan.y);
    } else {
      return;
    }
    GV.moved = true;
    gvDraw();
    event.preventDefault();
  }, { passive: false });
  cv.addEventListener("touchend", function () {
    GV.drag = null;
    GV.pan = null;
    GV.touchRot = null;
    if (GV.moved) { return; }
    const hit = gvHit(GV.touchX, GV.touchY);
    if (hit.id) {
      gvSelect(hit.id);
    } else if (hit.edgeId) {
      gvSelectEdge(hit.edgeId);
    } else {
      gvSelect(null);
    }
  });
}

// 打开图谱视图：收起对话区与输入区，把屏幕让给图谱
function showGraphView() {
  leaveIntro(true);            // 开场块与图谱不能同屏
  document.body.classList.add("graph-open");
  $("graphView").hidden = false;
  chatArea.hidden = true;
  $("composer").hidden = true;
  // .main.graph 让顶栏收起只对"对话"有意义的控件（清空当前对话 / 自动朗读）——
  // 正在看一张只读图谱，旁边却摆着"删掉这条对话"，属于上下文错位。
  const pane = document.querySelector(".main");
  if (pane) { pane.classList.add("graph"); }
  closeMoreMenu();
}

function setTopbarTitle(title, sub) {
  const titleEl = document.querySelector(".topbar .title");
  const subEl = document.querySelector(".topbar .sub");
  if (titleEl) {
    const badge = titleEl.querySelector(".gen-badge");
    titleEl.textContent = title;
    if (badge) { titleEl.appendChild(badge); }
  }
  if (subEl) {
    const prompt = subEl.querySelector(".sub-prompt");
    const caret = subEl.querySelector(".sub-caret");
    subEl.textContent = sub;
    if (prompt) { subEl.insertBefore(prompt, subEl.firstChild); }
    if (caret) { subEl.appendChild(caret); }
  }
}

// 关掉图谱视图：清掉选中并停止一切后台动作（旧实现是清 iframe，现在没有 iframe 了）
function closeGraph() {
  const view = $("graphView");
  if (!view || view.hidden) { return; }
  gvSelect(null);
  view.hidden = true;
  chatArea.hidden = false;
  $("composer").hidden = false;
  const pane = document.querySelector(".main");
  if (pane) { pane.classList.remove("graph"); }
  document.body.classList.remove("graph-open");
  setTopbarTitle("智能问答", "基于你的个人资料库，支持上传文档并自动构建");
  if (!sessionHasMessages()) { enterIntro(); }   // 回到一个还空着的会话：把开场态放回来
  input.focus();
}

async function setSessionAllowAll(enabled) {
  // 打开后：本运行内上传的文件质检通过就直接入库（服务重启自动恢复逐个确认）
  try {
    const response = await fetch("/ingest/session", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ allow_all: Boolean(enabled) })
    });
    let data = {};
    try { data = await response.json(); } catch (err) { data = {}; }
    if (!response.ok) {
      setToast("操作失败：HTTP " + response.status, "bad");
      return;
    }
    SESSION_CONFIRM.allow_all = Boolean(data.allow_all);
    const released = Number(data.released || 0);
    setToast(data.message || (SESSION_CONFIRM.allow_all
      ? (released ? ("已打开：正在放行 " + released + " 个待确认文件，后续文件直接入库") : "已打开：后续文件直接入库")
      : "已关闭：继续逐个确认"), "ok");
    if (SESSION_CONFIRM.allow_all) {
      // 服务端会把已经停在确认门上的作业一起放行：让这些卡片继续轮询，别停在原地
      UPLOAD_CARDS.forEach(function (item) {
        const panel = item.card && item.card.__review;
        if (panel) {
          const input = panel.querySelector(".review-session input");
          const label = panel.querySelector(".review-session span");
          if (input) { input.checked = true; }
          if (label) { label.textContent = "已打开「本次运行内全部允许」"; }
        }
        if (item.card && item.card.__state === "review") { pollIngestStatus(item.card, item.fileId); }
      });
      UPLOAD_BATCHES.forEach(function (batch) { batchRefresh(batch, true); });
      return;
    }
    // 关闭：各卡片状态没变，只把开关的样子改回来
    UPLOAD_CARDS.forEach(function (item) {
      const panel = item.card && item.card.__review;
      if (!panel) { return; }
      const input = panel.querySelector(".review-session input");
      const label = panel.querySelector(".review-session span");
      if (input) { input.checked = false; }
      if (label) { label.textContent = SESSION_ALLOW_TEXT; }
    });
  } catch (err) {
    setToast("操作失败：" + err.message, "bad");
  }
}

async function fetchIngestStatus(card, fileId) {
  // 拉一次完整状态（建议 / 预览 / 按钮都在里面）；失败返回 null，由调用方决定怎么说
  try {
    const response = await fetch("/ingest/status/" + encodeURIComponent(fileId), { cache: "no-store" });
    if (!response.ok) { return null; }
    const data = await response.json();
    renderIngest(card, data);
    return data;
  } catch (err) {
    return null;
  }
}

// 卡片上的「刷新状态」：只查这一张卡片，不刷新整页；还在进行中就重新起一轮轮询。
async function refreshIngestNow(card) {
  const fileId = card && card.__fileId;
  if (!fileId) { return; }
  let data = await fetchIngestStatus(card, fileId);
  if (!data) {
    setToast("暂时读不到这个文件的状态，请稍后再试", "bad");
    return;
  }
  data = await refreshKnowledgeStatus(card, fileId, data, true);
  renderIngest(card, data);
  // 手动刷新绕过动作轮询的解锁逻辑：状态已经离开动作前的值 → 立刻解除"正在提交"
  if (card.__busy && String(data.state || "") !== String(card.__busyFrom || "")) {
    card.__busy = false;
    renderIngestActions(card, data);
  }
  if (BUSY_STATES_UI[data.state]) {
    pollIngestStatus(card, fileId, { fast: true });
  }
  if (card.__batch) { refreshBatchOnce(card.__batch); }
}

async function restorePendingReviews() {
  // 打开页面时把服务端仍在等待、失败或进行中的文件卡片恢复出来：
  // 用户可能昨天上传完就关了电脑，今天回来必须一眼看到真实状态，而不是卡片凭空消失。
  let data = null;
  try {
    const response = await fetch("/ingest/pending", { cache: "no-store" });
    if (response.ok) { data = await response.json(); }
  } catch (err) { data = null; }
  if (!data) { return; }
  const confirm = data.confirm || {};
  const restoreHours = (confirm.restore_hours === undefined || confirm.restore_hours === null)
    ? 24 : Number(confirm.restore_hours);
  SESSION_CONFIRM = {
    allow_all: Boolean(confirm.session_allow_all),
    mode: confirm.mode || "always",
    ttl_hours: restoreHours
  };
  const groups = [
    { items: data.pending || [], fallback: "review" },
    { items: data.actionable || [], fallback: "blocked" },
    { items: data.skipped || [], fallback: "skipped" },
    { items: data.active || [], fallback: "processing" }
  ];
  const total = groups.reduce(function (sum, group) { return sum + group.items.length; }, 0);
  if (!total) { return; }
  leaveIntro(true);           // 有待处理或仍在处理的文件：直接进对话区

  const seen = {};
  const counts = { pending: 0, actionable: 0, skipped: 0, active: 0 };
  const buckets = ["pending", "actionable", "skipped", "active"];

  // 同一批次的大批量作业恢复成一张批次卡，不再散落成 N 张卡片。
  const entries = [];
  groups.forEach(function (group, groupIndex) {
    group.items.slice(0, 20).forEach(function (item) {
      entries.push({ item: item, fallback: group.fallback, groupIndex: groupIndex });
    });
  });
  const byBatch = new Map();
  entries.forEach(function (entry) {
    const batchId = String(entry.item.batch_id || "");
    if (!batchId) { return; }
    if (!byBatch.has(batchId)) { byBatch.set(batchId, []); }
    byBatch.get(batchId).push(entry);
  });
  let batchGroups = 0;
  let batchItems = 0;
  let batchedSkipped = 0;
  byBatch.forEach(function (batchEntries, batchId) {
    if (batchEntries.length <= BATCH_THRESHOLD) { return; }
    const batch = createUploadBatch(batchId, batchEntries.length);
    batch.uploadDone = true;
    batchEntries.forEach(function (entry) {
      const item = entry.item;
      const fileId = String(item.file_id || "");
      if (!fileId) { return; }
      seen[fileId] = true;
      const state = String(item.state || entry.fallback);
      if (state === "skipped") { batchedSkipped += 1; }
      ensureBatchItem(batch, {
        file_id: fileId,
        name: item.name || item.filename || fileId,
        state: state,
        detail: item.detail || "",
        knowledge_error: item.knowledge_error || "",
        progress: item.progress || {}
      });
    });
    batchGroups += 1;
    batchItems += batchEntries.length;
    updateBatchUi(batch, null);
    batchRefresh(batch, true);
  });

  groups.forEach(function (group, groupIndex) {
    group.items.slice(0, 20).forEach(function (item) {
      const fileId = String(item.file_id || "");
      if (!fileId || seen[fileId]) { return; }
      seen[fileId] = true;
      const state = String(item.state || group.fallback);
      const card = createUploadCard(item.name || item.filename || fileId);
      card.__fileId = fileId;
      card.__policy = "unified";
      card.__policyDone = true;   // 恢复出来的卡片不替用户做任何自动动作
      card.__state = state;
      card.__data = item;
      trackCard(fileId, card);
      setUploadState(card, state, item.knowledge_error || item.detail || "", item);
      counts[buckets[groupIndex]] += 1;
      if (BUSY_STATES_UI[state] || state === "done") {
        pollIngestStatus(card, fileId, { fast: true });
      } else {
        fetchIngestStatus(card, fileId);   // 补全建议面板 / 按钮 / 失败原因
      }
    });
  });

  const parts = [];
  if (counts.pending) { parts.push(counts.pending + " 个待确认入库"); }
  if (counts.actionable) { parts.push(counts.actionable + " 个需要处理"); }
  if (counts.skipped) { parts.push(counts.skipped + " 个最近跳过"); }
  if (counts.active) { parts.push(counts.active + " 个正在处理"); }
  if (batchItems) { parts.push(batchItems + " 份在 " + batchGroups + " 个批次中"); }
  let text = parts.length ? ("已恢复：" + parts.join(" · ")) : "";
  const skippedTotal = Number(data.skipped_total || 0);
  if (skippedTotal > counts.skipped + batchedSkipped) {
    text += (text ? "；" : "") + "另有 " + (skippedTotal - counts.skipped - batchedSkipped)
      + " 个更早跳过的文件，可在「文件管理」继续入库";
  }
  if (text) { setToast(text, counts.pending || batchItems ? "ok" : ""); }
}

// ---------- 知识图谱（只读浏览）----------
async function openGraph() {
  if (busy) { setToast("正在生成回答，稍后再打开知识图谱", "bad"); return; }
  showGraphView();
  setTopbarTitle("知识图谱", "实体与关系浏览（只读）");
  // 默认进「全局图」：一进来就是整张知识图谱（银河视图），不用先搜一个实体
  if (GV.nodes.length) { gvRelayoutView(); gvDraw(); } else { gvShowOverlay("busy", "正在读取全局知识图谱…"); }
  // 先确认知识库服务在（没起来就直接说清楚，别给一块白画布）
  try {
    const health = await fetch("/health", { cache: "no-store" });
    const status = await health.json();
    if (status && status.lightrag && status.lightrag.ok === false) {
      gvShowOverlay("error", "知识库服务没有响应，图谱暂时打不开。请先确认知识库已启动，再点「重试」。");
      return;
    }
  } catch (err) { /* 探活失败不拦着，真正的请求会给出错误 */ }
  if (!$("graphPopular").childElementCount) { loadGraphPopular(); }
  if (GV.nodes.length) { gvRelayoutView(); gvDraw(); } else { loadGraphGlobal(); }
}

function initGraphView() {
  const close = $("graphClose");
  if (!close) { return; }
  close.addEventListener("click", closeGraph);
  $("graphBackGlobal").addEventListener("click", graphBackToGlobal);
  $("graphGlobalBtn").addEventListener("click", graphBackToGlobal);
  $("graphRefresh").addEventListener("click", function () {
    if (GV.center) {
      loadGraphSubgraph(GV.center);
    } else {
      loadGraphGlobal();
    }
  });
  $("graphLegend").addEventListener("click", function (event) {
    const button = event.target && event.target.closest
      ? event.target.closest("button[data-graph-type]")
      : null;
    if (button) { gvToggleType(button.getAttribute("data-graph-type") || ""); }
  });
  $("graphModes").addEventListener("click", function (event) {
    const btn = event.target && event.target.closest ? event.target.closest("button[data-mode]") : null;
    if (btn) { gvSetMode(btn.getAttribute("data-mode")); }
  });
  // 「复位视角」：重算布局、把 3D 转回默认角度、再适应窗口
  $("graphRelayout").addEventListener("click", function () {
    if (!GV.nodes.length) { return; }
    GV.rot = { yaw: -0.62, pitch: 0.36 };
    gvLayout();
    gvRelayoutView();
    gvStartAnim();
    gvDraw();
  });
  $("graphRetry").addEventListener("click", function () {
    if (GV.center) { loadGraphSubgraph(GV.center); } else { openGraph(); }
  });
  $("graphZoomIn").addEventListener("click", function () {
    GV.view.scale = Math.min(8, GV.view.scale * 1.2);
    gvDraw();
  });
  $("graphZoomOut").addEventListener("click", function () {
    GV.view.scale = Math.max(0.1, GV.view.scale / 1.2);
    gvDraw();
  });
  $("graphFit").addEventListener("click", function () { gvRelayoutView(); gvDraw(); });
  $("graphPanelClose").addEventListener("click", function () { gvSelect(null); });
  graphBindSearch();
  graphBindCanvas();
  window.addEventListener("resize", function () {
    if (!$("graphView").hidden) { gvDraw(); }
  });
}

/* ---------- 会话历史（本地保存，刷新不丢） ---------- */
const SESSION_STORE = "chat_sessions_v1";
const SESSION_CURRENT = "chat_current_v1";
const SESSION_MIGRATED = "chat_sessions_migrated_v2";
const SESSION_ORIGIN = "chat_origin_v1";
const MAX_SESSIONS = 60;
let sessions = [];
let currentId = "";
// 后端会话可用时优先使用；off/不可达时回退到 localStorage。
const SERVER_SESSIONS = { enabled: false, ready: false };

function loadStore() {
  try {
    const raw = localStorage.getItem(SESSION_STORE);
    sessions = raw ? (JSON.parse(raw) || []) : [];
  } catch (err) { sessions = []; }
  if (!Array.isArray(sessions)) { sessions = []; }
  currentId = localStorage.getItem(SESSION_CURRENT) || "";
}

function saveStore() {
  try {
    localStorage.setItem(SESSION_STORE, JSON.stringify(sessions));
    localStorage.setItem(SESSION_CURRENT, currentId);
  } catch (err) {
    sessions = sessions.slice(0, Math.max(1, Math.floor(sessions.length / 2)));
    try {
      localStorage.setItem(SESSION_STORE, JSON.stringify(sessions));
      localStorage.setItem(SESSION_CURRENT, currentId);
    } catch (err2) { setToast("本地存储已满，较早的对话未保存", "bad"); }
  }
}

// 后端会话库关闭时，历史只存在**当前浏览器地址**的 localStorage 里：
// 127.0.0.1、192.168.x.x、电脑名是三个不同的存储空间。换地址打开会"历史全没了"，
// 但旧地址里的记录并没有删。这里在地址变化时明确说出来，别让用户以为数据丢了。
function noteSessionOrigin() {
  try {
    const previous = String(localStorage.getItem(SESSION_ORIGIN) || "");
    const current = String(location.host || "");
    if (previous && current && previous !== current) {
      setToast(
        "这次是用 " + current + " 打开的；历史对话按浏览器地址分别保存，旧记录还在 "
        + previous + " 下。用同一个地址打开才能看到它们。",
        "bad"
      );
    }
    if (current) { localStorage.setItem(SESSION_ORIGIN, current); }
  } catch (err) {
    /* 隐私模式 / 存储被禁用：不影响聊天 */
  }
}

function currentSession() {
  for (let i = 0; i < sessions.length; i++) {
    if (sessions[i].id === currentId) { return sessions[i]; }
  }
  return null;
}

function newSessionId() {
  return "c" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function newClientMessageId() {
  return "cmsg_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function newWorkId(prefix) {
  return String(prefix || "work") + ":" + Date.now().toString(36)
    + ":" + Math.random().toString(36).slice(2, 7);
}

function scrollWorkTarget(node) {
  if (!node || !node.isConnected || !node.scrollIntoView) { return; }
  const reduce = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  node.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
}

function finishChatWork(view, info) {
  if (!view || !view.__workId || view.__workFinished) { return; }
  view.__workFinished = true;
  WorkCompanion.finish(view.__workId, info || {});
}

/* ---------- 后端会话 API（失败一律返回 null，由调用方回退） ----------
   局域网设备通过六位数字授权码完成设备授权，令牌在 HttpOnly Cookie 中自动携带；
   未授权时不弹口令框，直接进入授权说明页。 */
let deviceAuthRedirecting = false;

function redirectToDeviceAuth(data) {
  if (deviceAuthRedirecting) { return true; }
  const detail = data && data.detail;
  if (!detail || (detail.error !== "device_auth_required"
      && detail.error !== "chat_token_required")) { return false; }
  deviceAuthRedirecting = true;
  location.replace(detail.pair_url || "/pair");
  return true;
}

function apiJson(path, options) {
  const init = Object.assign({ cache: "no-store" }, options || {});
  return fetch(path, init).then(function (response) {
    if (response.status === 401) {
      return response.clone().json().catch(function () { return null; }).then(function (data) {
        redirectToDeviceAuth(data);
        return null;
      });
    }
    if (!response.ok) { return null; }
    return response.json().catch(function () { return null; });
  }).catch(function () { return null; });
}

function apiListSessions() {
  return apiJson("/api/sessions?limit=200");
}

function apiCreateSession(title, topicId) {
  return apiJson("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title || "", topic_id: topicId || "" })
  });
}

function apiUpdateSession(id, patch) {
  return apiJson("/api/sessions/" + encodeURIComponent(id), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch || {})
  });
}

function apiGetMessages(id) {
  return apiJson("/api/sessions/" + encodeURIComponent(id) + "/messages?limit=1000");
}

function apiDeleteSession(id) {
  return apiJson("/api/sessions/" + encodeURIComponent(id), { method: "DELETE" });
}

function apiClearMessages(id) {
  return apiJson("/api/sessions/" + encodeURIComponent(id) + "/messages", { method: "DELETE" });
}

function apiEndSession(id) {
  return apiJson("/api/sessions/" + encodeURIComponent(id) + "/end", { method: "POST" });
}

function apiImportSessions(list) {
  return apiJson("/api/sessions/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sessions: list || [] })
  });
}

function serverSessionToLocal(item) {
  const created = Number(item && item.created_ts) || 0;
  const updated = Number(item && item.updated_ts) || created;
  return {
    id: String(item.id || ""),
    title: item.title || "新对话",
    messages: [],
    message_count: Number(item.message_count) || 0,
    topic_id: item.topic_id || null,
    created_at: created || Date.now(),
    updated_at: updated || Date.now(),
    _server: true
  };
}

function serverMessageToLocal(item) {
  return {
    role: item.role === "user" ? "user" : "ai",
    text: item.text || "",
    ts: Number(item.ts) || Date.now(),
    status: item.status || "complete",
    references: Array.isArray(item.references) ? item.references : [],
    generation: item.generation || null,
    retry_upstream: item.retry_upstream === true,
    upstream_retry: item.upstream_retry === true
  };
}

async function loadSessionMessages(session, force) {
  if (!session || !session._server) { return false; }
  if (!force && session.messages && session.messages.length) { return true; }
  const data = await apiGetMessages(session.id);
  if (!data || !Array.isArray(data.messages)) { return false; }
  session.messages = data.messages.map(serverMessageToLocal);
  session.message_count = session.messages.length;
  if (session.messages.length) {
    session.updated_at = session.messages[session.messages.length - 1].ts;
  }
  noteRecoveredSessionWork(session);
  saveStore();
  renderSidebar();
  return true;
}

function noteRecoveredSessionWork(session) {
  const messages = (session && session.messages) || [];
  let lastAi = null;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i] && messages[i].role === "ai") { lastAi = messages[i]; break; }
  }
  if (!lastAi || lastAi.status !== "streaming") { return; }
  const id = "chat-recovered:" + String(session.id || "") + ":" + String(lastAi.ts || "");
  WorkCompanion.finish(id, {
    motion: "waiting",
    label: "暂时无法确认上一轮是否仍在生成",
    detail: "刷新已断开原来的流式连接；请稍候或重新提问确认",
    holdMs: 12000
  });
}

function formatSessionTime(ts) {
  if (!ts) { return ""; }
  const date = new Date(ts);
  const now = new Date();
  const pad = function (value) { return (value < 10 ? "0" : "") + value; };
  const hhmm = pad(date.getHours()) + ":" + pad(date.getMinutes());
  if (date.toDateString() === now.toDateString()) { return hhmm; }
  const yesterday = new Date(now.getTime() - 86400000);
  if (date.toDateString() === yesterday.toDateString()) { return "昨天 " + hhmm; }
  return (date.getMonth() + 1) + "-" + date.getDate();
}

function titleFromText(text) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  if (!clean) { return "新对话"; }
  return clean.length > 18 ? (clean.slice(0, 18) + "…") : clean;
}

function persistUserMessage(text, clientMessageId) {
  const session = currentSession();
  if (!session) { return; }
  const isFirst = session.messages.length === 0;
  session.messages.push({
    role: "user",
    text: text,
    ts: Date.now(),
    client_message_id: clientMessageId || ""
  });
  session.message_count = (session.message_count || 0) + 1;
  if (isFirst) { session.title = titleFromText(text); }
  session.updated_at = Date.now();
  saveStore();
  renderSidebar();
}

function persistAiMessage(text, references, generation, flags) {
  const session = currentSession();
  if (!session) { return; }
  const item = { role: "ai", text: text || "", references: references || [], ts: Date.now() };
  // 只存布尔标记；问题原文仍在前面那条用户消息里，不重复保存。
  if (flags && flags.retryUpstream) { item.retry_upstream = true; }
  if (flags && flags.upstreamRetry) { item.upstream_retry = true; }
  // 只持久化规则码与是否通过，不存正文之外的任何模型信息；刷新会话后仍能看到同样的提示。
  if (generation && generation.mode === "local") {
    item.generation = {
      mode: "local",
      verified: generation.verified !== false,
      warnings: (generation.warnings || [])
        .filter(function (code) { return typeof code === "string"; })
        .slice(0, 10)
    };
  }
  session.messages.push(item);
  session.message_count = (session.message_count || 0) + 1;
  session.updated_at = Date.now();
  saveStore();
  renderSidebar();
}

function renderSidebar() {
  const box = document.getElementById("sessionList");
  if (!box) { return; }
  box.innerHTML = "";
  // 一次话都没说过的空会话不进记录列表：它没有内容可回看，列出来只会和上面的「新对话」按钮重名，
  // 又因为"当前会话"而带高亮，看起来像被按下的第二个按钮（真实反馈过这一点）。
  // 发出第一条消息后 persistUserMessage() 会再次渲染，那时它才出现在记录里。
  const visible = sessions.filter(function (session) {
    return (session.messages && session.messages.length) || (session.message_count || 0) > 0;
  });
  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "session-empty";
    empty.textContent = "还没有对话，点上面「新对话」开始";
    box.appendChild(empty);
    return;
  }
  visible.forEach(function (session) {
    const row = document.createElement("div");
    row.className = "session" + (session.id === currentId ? " active" : "");
    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = session.title || "新对话";
    title.title = session.title || "";
    const time = document.createElement("span");
    time.className = "session-time";
    time.textContent = formatSessionTime(session.updated_at);
    const del = document.createElement("button");
    del.className = "session-del";
    del.textContent = "×";
    del.title = "删除该对话";
    del.addEventListener("click", function (event) {
      event.stopPropagation();
      //  服务端是“软删除 + 删除墓碑”：列表与后续检索不再返回。
      const count = (session.messages || []).length || (session.message_count || 0);
      RAGKB.confirm({
        title: "删除这条对话记录？",
        body: "「" + (session.title || "新对话") + "」"
          + (count ? "（" + count + " 条消息）" : "")
          + " 将从对话记录中移除，后续不再出现在列表与检索中。",
        confirmText: "删除",
        danger: true
      }).then(function (ok) { if (ok) { deleteSession(session.id); } });
    });
    row.appendChild(title);
    row.appendChild(time);
    row.appendChild(del);
    row.addEventListener("click", function () { selectSession(session.id); });
    box.appendChild(row);
  });
}

function startNewSession() {
  if (sessionCreatePromise) { return sessionCreatePromise; }
  sessionCreatePromise = _startNewSession().finally(function () {
    sessionCreatePromise = null;
  });
  return sessionCreatePromise;
}

async function _startNewSession() {
  if (busy && controller) { controller.abort(); }
  closeGraph();
  // 已经站在一条还没说过话的会话上：复用它，不再新建 ——
  // 否则空会话会越堆越多，而且"空会话"不会再进记录列表（见 renderSidebar），
  // 建了也回不去，只会白占存储。
  const existing = currentSession();
  if (existing && (!existing.messages || !existing.messages.length)
      && !(existing.message_count > 0)) {
    const topicSelect = $("topicSelect");
    const wanted = topicSelect && topicSelect.value && topicSelect.value !== "__new__"
      ? topicSelect.value : "";
    if (wanted !== (existing.topic_id || "")) {
      existing.topic_id = wanted || null;
      if (existing._server) { apiUpdateSession(existing.id, { topic_id: wanted }); }
      saveStore();
    }
    renderSidebar();
    renderSession(existing);
    input.focus();
    return existing;
  }
  // 切走一个有内容的会话时通知服务端结束，触发补充提取（失败静默）。
  if (existing && (existing.messages && existing.messages.length)
      && SERVER_SESSIONS.enabled && existing._server) {
    apiEndSession(existing.id);
  }

  let session = null;
  const topicSelect = $("topicSelect");
  const topicId = topicSelect && topicSelect.value && topicSelect.value !== "__new__"
    ? topicSelect.value : "";
  if (SERVER_SESSIONS.enabled) {
    const created = await apiCreateSession("", topicId);
    if (created && created.session) {
      session = serverSessionToLocal(created.session);
    }
  }
  if (!session) {
    session = {
      id: newSessionId(), title: "新对话", messages: [], message_count: 0,
      topic_id: topicId || null,
      created_at: Date.now(), updated_at: Date.now(), _server: false
    };
  }
  sessions.unshift(session);
  if (sessions.length > MAX_SESSIONS) { sessions = sessions.slice(0, MAX_SESSIONS); }
  currentId = session.id;
  saveStore();
  renderSidebar();
  renderSession(session);
  input.focus();
  return session;
}

async function selectSession(id) {
  if (busy && controller) { controller.abort(); }
  closeGraph();
  const previous = currentSession();
  if (previous && previous.id !== id && SERVER_SESSIONS.enabled && previous._server
      && ((previous.messages && previous.messages.length) || (previous.message_count || 0) > 0)) {
    // 切走会话时通知服务端结束，触发补充提取（失败静默）。
    apiEndSession(previous.id);
  }
  currentId = id;
  saveStore();
  renderSidebar();
  const session = currentSession();
  if (!session) { await startNewSession(); return; }
  if (session._server && (!session.messages || !session.messages.length)
      && (session.message_count || 0) > 0) {
    await loadSessionMessages(session);
  }
  renderSession(session);
}

async function deleteSession(id) {
  if (busy && controller) { controller.abort(); }
  const target = sessions.find(function (item) { return item.id === id; }) || null;
  if (SERVER_SESSIONS.enabled && target && target._server) {
    const result = await apiDeleteSession(id);
    if (!result || !result.ok) {
      setToast("删除失败：服务端未确认", "bad");
      return;
    }
  }
  sessions = sessions.filter(function (item) { return item.id !== id; });
  if (currentId === id) {
    currentId = sessions.length ? sessions[0].id : "";
  }
  saveStore();
  if (!sessions.length) { await startNewSession(); return; }
  renderSidebar();
  if (!currentSession()) { currentId = sessions[0].id; }
  const session = currentSession();
  if (session && session._server && (!session.messages || !session.messages.length)
      && (session.message_count || 0) > 0) {
    await loadSessionMessages(session);
  }
  renderSession(session);
}

async function clearCurrentSession() {
  closeGraph();
  const session = currentSession();
  if (!session) { await startNewSession(); return; }
  if (SERVER_SESSIONS.enabled && session._server) {
    const result = await apiClearMessages(session.id);
    if (!result || !result.ok) {
      setToast("清空失败：服务端未确认", "bad");
      return;
    }
  }
  session.messages = [];
  session.message_count = 0;
  session.title = "新对话";
  session.updated_at = Date.now();
  saveStore();
  renderSidebar();
  renderSession(session);
}

function adoptServerSessionId(newId) {
  if (!newId || newId === currentId) { return; }
  const session = currentSession();
  if (session) {
    session.id = newId;
    session._server = true;
  }
  currentId = newId;
  saveStore();
  renderSidebar();
}

/* 发送前确保当前会话在服务端存在；旧 localStorage 会话会先尝试导入。 */
async function ensureCurrentSession() {
  let session = currentSession();
  if (!session) {
    session = await startNewSession();
  }
  if (!session) { return null; }

  if (SERVER_SESSIONS.enabled && !session._server) {
    const imported = await apiImportSessions([{
      id: session.id,
      title: session.title,
      created_at: session.created_at,
      updated_at: session.updated_at,
      messages: session.messages || []
    }]);
    if (imported && imported.enabled && imported.mapping
        && imported.mapping[session.id]) {
      session.id = imported.mapping[session.id];
      session._server = true;
      currentId = session.id;
      saveStore();
      renderSidebar();
    } else {
      const created = await apiCreateSession(session.title, session.topic_id || "");
      if (created && created.session) {
        session.id = created.session.id;
        session._server = true;
        session.message_count = (session.messages || []).length;
        currentId = session.id;
        saveStore();
        renderSidebar();
      }
    }
  }

  if (session._server && (session.message_count || 0) > 0
      && (!session.messages || !session.messages.length)) {
    await loadSessionMessages(session);
  }
  return session;
}

function syncTopicSelect(session) {
  const select = $("topicSelect");
  if (!select || !session) { return; }
  const value = session.topic_id || "";
  const exists = Array.prototype.some.call(select.options, function (option) {
    return option.value === value;
  });
  select.value = exists ? value : "";
}

function renderSession(session) {
  // 切换 / 重建对话时先收掉旧消息上的朗读与画布脉冲，避免动画留在已卸载的节点上。
  if (typeof window.voiceCancelReading === "function") {
    try { window.voiceCancelReading(); } catch (err) { /* 不影响会话渲染 */ }
  }
  if (session) { syncTopicSelect(session); }
  if (!session || !session.messages.length) {
    welcome();
    return;
  }
  leaveIntro(true);            // 已有内容的会话：直接跳过开场编排
  clearCurrentQuestion();      // 旧会话的本轮提问标记先清掉，再重建消息
  resetPinGeometry();          // 脚手架（顶部留白 / 底部补白 / 动画）也不跨会话残留
  messages.innerHTML = "";
  clearAllThinkTimers();
  let lastUserText = "";
  session.messages.forEach(function (item) {
    if (item.role === "user") {
      addMessage(item.text, "user");
      lastUserText = item.text;
    } else {
      const view = createAnswer();
      // 历史消息若来自本机生成，保留当时的校验提示（旧会话没有该字段，正常降级为不提示）。
      view.__genMeta = item.generation || null;
      // 失败消息的「换上游重试」入口在刷新后仍可用；问题取自前面那条用户消息。
      view.__retryUpstream = item.retry_upstream === true;
      view.__retryQuestion = lastUserText;
      view.__upstreamRetry = item.upstream_retry === true;
      view.__sessionId = session.id;
      view.__restored = true;      // 刷新 / 切会话恢复历史：默认不触发自动朗读
      finalizeAnswer(view, item.text, item.references || []);
    }
  });
  scrollBottom(true);          // 新建 / 切换会话：回到最新一条
  // 再把「最后一条提问」标成本轮提问 —— 纯语义标记、不再滚动任何东西，
  // 所以不会把刚回好的底部位置顶走。
  const users = messages.querySelectorAll(".msg.user");
  if (users.length) { markCurrentQuestion(users[users.length - 1]); }
}

/* ---------- 主题作用域 ---------- */
async function loadTopics() {
  const select = $("topicSelect");
  if (!select) { return; }
  const data = await apiJson("/api/topics?status=active&limit=200");
  select.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "主题：不绑定（使用全局偏好）";
  select.appendChild(blank);
  if (!data || !data.enabled) {
    select.disabled = true;
    return;
  }
  select.disabled = false;
  (data.topics || []).forEach(function (topic) {
    const option = document.createElement("option");
    option.value = topic.id;
    option.textContent = topic.name || "未命名主题";
    select.appendChild(option);
  });
  const add = document.createElement("option");
  add.value = "__new__";
  add.textContent = "＋ 新建主题";
  select.appendChild(add);
  syncTopicSelect(currentSession());
}

async function bindTopicToCurrent(topicId) {
  const session = currentSession();
  if (!session) { return; }
  const value = String(topicId || "");
  session.topic_id = value || null;
  if (session._server) {
    const updated = await apiUpdateSession(session.id, { topic_id: value });
    if (!updated || !updated.session) {
      setToast("绑定主题失败：请刷新后重试", "bad");
      return;
    }
    session.topic_id = updated.session.topic_id || null;
  }
  saveStore();
  syncTopicSelect(session);
}

async function createTopicFromPrompt() {
  const name = window.prompt(
    "新建主题名称（例如：项目 A、客户资料、个人学习）：",
    ""
  );
  const clean = String(name || "").trim();
  if (!clean) {
    syncTopicSelect(currentSession());
    return;
  }
  const data = await apiJson("/api/topics", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: clean, kind: "topic" })
  });
  if (!data || !data.topic) {
    setToast("新建主题失败：请稍后重试", "bad");
    syncTopicSelect(currentSession());
    return;
  }
  await loadTopics();
  const select = $("topicSelect");
  if (select) { select.value = data.topic.id; }
  await bindTopicToCurrent(data.topic.id);
  setToast("已新建并绑定主题：" + data.topic.name, "ok");
}

(function bindTopicSelect() {
  const select = $("topicSelect");
  if (!select) { return; }
  select.addEventListener("change", function () {
    if (select.value === "__new__") {
      createTopicFromPrompt();
    } else {
      bindTopicToCurrent(select.value);
    }
  });
})();

async function initSessions() {
  loadStore();
  const localSessions = sessions.slice();
  const initial = await apiListSessions();

  if (initial && initial.enabled) {
    SERVER_SESSIONS.enabled = true;

    // 首次升级：把旧 localStorage 导入服务端；失败时保留本地只读记录。
    let importedOk = false;
    if (localStorage.getItem(SESSION_MIGRATED) !== "1" && sessions.length) {
      const payload = sessions.map(function (item) {
        return {
          id: item.id,
          title: item.title,
          created_at: item.created_at,
          updated_at: item.updated_at,
          messages: item.messages || []
        };
      });
      const imported = await apiImportSessions(payload);
      if (imported && imported.enabled) {
        importedOk = true;
        localStorage.setItem(SESSION_MIGRATED, "1");
      }
    }

    const listed = await apiListSessions();
    if (listed && listed.enabled) {
      const serverList = (Array.isArray(listed.sessions) ? listed.sessions : [])
        .map(serverSessionToLocal)
        .filter(function (item) { return item.id; });

      if (serverList.length) {
        if (!importedOk) {
          // 导入失败时不要把本地旧记录藏起来：并入列表，标记为非服务端会话，
          // 用户下次发送时会再次尝试导入。
          const known = {};
          serverList.forEach(function (item) { known[item.id] = true; });
          localSessions.forEach(function (item) {
            if (!known[item.id] && item.messages && item.messages.length) {
              item._server = false;
              serverList.push(item);
            }
          });
          serverList.sort(function (a, b) {
            return (b.updated_at || 0) - (a.updated_at || 0);
          });
        }
        sessions = serverList;
        if (!currentSession()) { currentId = sessions[0].id; }
        SERVER_SESSIONS.ready = true;
        saveStore();
        renderSidebar();
        const session = currentSession();
        if (session) {
          await loadSessionMessages(session);
          renderSession(session);
        }
        input.focus();
        return;
      }

      if (!importedOk && sessions.length) {
        // 服务端可用但导入失败：先显示本地记录，避免旧对话“消失”。
        SERVER_SESSIONS.ready = true;
        if (!currentSession()) { currentId = sessions[0].id; }
        saveStore();
        renderSidebar();
        renderSession(currentSession());
        input.focus();
        return;
      }
    } else if (sessions.length) {
      // 列表接口临时失败：回退本地只读，聊天仍可继续（新消息走本地）。
      SERVER_SESSIONS.ready = true;
      if (!currentSession()) { currentId = sessions[0].id; }
      saveStore();
      renderSidebar();
      renderSession(currentSession());
      input.focus();
      return;
    }

    await startNewSession();
    SERVER_SESSIONS.ready = true;
    return;
  }

  // 后端未启用 / 不可达：沿用 localStorage。
  if (!sessions.length) { await startNewSession(); return; }
  if (!currentSession()) { currentId = sessions[0].id; }
  saveStore();
  renderSidebar();
  renderSession(currentSession());
  input.focus();
}

sendBtn.addEventListener("click", function () {
  if (busy) { if (controller) { controller.abort(); } return; }
  send();
});
input.addEventListener("input", autoGrow);
/* 中文/日文等输入法：回车是「选字/上屏」，不是「发送」。
   此前没有这层守卫，拼音打字的回车会把没上屏的半成品直接发出去。
   （并存的 event.isComposing 是双保险：keydown 早于 compositionend 时它已为 true。） */
let composing = false;
input.addEventListener("compositionstart", function () { composing = true; });
input.addEventListener("compositionend", function () { composing = false; });
input.addEventListener("keydown", function (event) {
  if (event.key === "Enter" && !event.shiftKey) {
    if (composing || event.isComposing) { return; }   // 交给输入法自己处理，不拦截、不发送
    event.preventDefault();
    if (!busy) { send(); }
  }
});
$("newChat").addEventListener("click", function () {
  if (busy && controller) { controller.abort(); }
  startNewSession();
  // 桌面端：从侧栏点「新对话」后自动收起侧栏，把屏幕还给新对话
  if (window.matchMedia && window.matchMedia("(min-width:900.1px)").matches) {
    applySideCollapsed(true);
  }
});
// 「清空当前对话」会把这条对话的消息全部删掉且不可恢复，所以先确认一次。
// （它原来是个一点就没的按钮，还长期占据顶栏 —— 桌面上在标题右边、手机上是首屏第二行。）
$("clearBtn").addEventListener("click", function () {
  const session = currentSession();
  const count = (session && session.messages.length) || 0;
  if (!count) { return; }        // 本来就是空的：不弹一个没有意义的确认框
  RAGKB.confirm({
    title: "清空当前对话？",
    body: "这条对话里的 " + count + " 条消息会被删除，且无法恢复。",
    detail: "左侧「对话记录」里的其它对话不受影响。",
    confirmText: "清空",
    danger: true
  }).then(function (ok) {
    if (!ok) { return; }
    if (busy && controller) { controller.abort(); }
    clearCurrentSession();
  });
});
$("graphBtn").addEventListener("click", openGraph);
// 资料入库入口：由 data-policy 决定 accept 与后续动作（上传资料 / 上传图片）
document.querySelectorAll("[data-policy]").forEach(function (node) {
  node.addEventListener("click", function () { pickFile(node.getAttribute("data-policy")); });
});
$("attachBtn").addEventListener("click", function () { pickFile("unified"); });
$("file").addEventListener("change", function (event) {
  // 多选：一次选中的每个文件各建一张卡片。后端入库队列是单并发的，
  // 它们会依次解析 / 质检，但前端可以同时看到各自的进度，不用等前一个做完再选下一个。
  const files = Array.prototype.slice.call(event.target.files || []);
  const policy = pendingPolicy;
  event.target.value = "";
  if (!files.length) { return; }
  const batch = (files.length > BATCH_THRESHOLD && policy === "unified")
    ? createUploadBatch(newBatchId(), files.length)
    : null;
  if (files.length > 1) {
    setToast(batch
      ? ("已选择 " + files.length + " 个文件，按批次上传处理")
      : ("已选择 " + files.length + " 个文件，逐个上传处理"), "info");
  }
  // 顺序上传（不是 Promise.all）：上传接口本身很快，并发上传对单线程入库队列没有好处，
  // 反而更容易把网络/浏览器压出问题。少量文件仍逐张卡片显示；大批量收进一个批次卡。
  (async function () {
    for (let i = 0; i < files.length; i++) {
      await upload(files[i], policy, batch);
    }
    if (batch) { finishBatchUploads(batch); }
  })();
});

const dropZone = $("dropZone");
["dragenter", "dragover"].forEach(function (name) {
  dropZone.addEventListener(name, function (event) { event.preventDefault(); dropZone.classList.add("dragover"); });
});
["dragleave", "drop"].forEach(function (name) {
  dropZone.addEventListener(name, function (event) { event.preventDefault(); dropZone.classList.remove("dragover"); });
});
dropZone.addEventListener("drop", function (event) {
  const files = Array.prototype.slice.call((event.dataTransfer && event.dataTransfer.files) || []);
  if (!files.length) { return; }
  const batch = files.length > BATCH_THRESHOLD
    ? createUploadBatch(newBatchId(), files.length)
    : null;
  if (files.length > 1) {
    setToast(batch
      ? ("已拖入 " + files.length + " 个文件，按批次上传处理")
      : ("已拖入 " + files.length + " 个文件，逐个上传处理"), "info");
  }
  (async function () {
    for (let i = 0; i < files.length; i++) {
      await upload(files[i], "unified", batch);
    }
    if (batch) { finishBatchUploads(batch); }
  })();
});

// ---------- 关闭窗口即停服 + 手机端适配：界面只负责「显示状态 + 手动停止」 ----------
// 判断逻辑全在 static/session.js 与后端 app/routers/control.py：
// 本机页面在时每 15 秒续命，关窗口 25 秒后自动停服；手机页面续命只会把停止推后 180 秒，
// 但手机不能停止服务 —— 所以「停止全部服务」按钮只在电脑本机页面出现。
const stopBtn = $("stopBtn");
const stoppingBox = $("stopping");
const stoppingText = $("stoppingText");
const svcBar = $("svcBar");
const svcDot = $("svcDot");
const svcBarText = $("svcBarText");
const offlineBox = $("offline");
const offlineText = $("offlineText");
const offlineHint = $("offlineHint");
const offlineRetry = $("offlineRetry");

function renderControl(state) {
  if (!state || !state.ready) { return; }
  const lan = state.mode === "lan";

  // 手机 / 局域网页面拿不到控制令牌：这个按钮不显示（硬点也只会拿到 403）
  stopBtn.hidden = !state.canStop;

  if (state.stopping) {
    stoppingBox.hidden = false;
    const left = Number(state.pending);
    const busyNote = state.busy ? "（有文件正在处理，已延长等待）" : "";
    stoppingText.textContent = (left > 0)
      ? "服务将在 " + left + " 秒后停止" + busyNote + "。"
      : "服务已停止，本页面已不再连接后端。";
  }

  // 手机端提示条：电脑上的窗口都关了，但手机页面上还有事要说明
  const lanPending = lan && !state.offline && Number(state.pending) > 0;
  svcBar.hidden = !lanPending;
  if (lanPending) {
    if (state.keepalive) {
      svcDot.className = "dot ok";
      svcBarText.textContent = "电脑上的窗口已关闭：你还在用手机，服务会保持运行"
        + "（空闲 " + state.pending + " 秒后自动停止；手机切后台时间太久也会停）。";
    } else {
      svcDot.className = "dot warn";
      svcBarText.textContent = "电脑上的窗口已关闭：服务将在 " + state.pending
        + " 秒后停止（当前手机不参与保活：LAN_KEEPALIVE=false）。";
    }
  }

  // 服务真的停了：本机点了停止、电脑上关了窗口、或者根本没启动过
  if (state.offline && !state.stopping) {
    offlineBox.hidden = false;
    offlineText.textContent = lan
      ? "电脑上的这个服务已经停止，手机暂时用不了。"
      : "本应用与知识库服务已在这台电脑上停止。";
    offlineHint.textContent = lan
      ? "请在电脑上从项目根目录重新启动服务重新启动；启动完成后这个页面会自动恢复，也可以点「重新检测」。"
      : "想再次使用：从项目根目录重新启动服务（通常需要数十秒）。服务起来后这个页面会自动恢复。";
  } else {
    offlineBox.hidden = true;
  }
}

// 遮罩上的「重新检测」：电脑上重新启动服务后，点一下即可接着用（不用重新扫码/输地址）
function retryServices() {
  const session = window.RAGKB_SESSION;
  if (!session) { window.location.reload(); return; }
  offlineText.textContent = "正在检测…";
  session.refresh().then(function () {
    renderControl(session.state);
    refreshStatus();
  });
}
offlineRetry.addEventListener("click", retryServices);

stopBtn.addEventListener("click", function () {
  const session = window.RAGKB_SESSION;
  if (!session || session.state.stopping) { return; }
  RAGKB.confirm({
    title: "停止全部服务？",
    body: "会停止「本地 RAG 知识库」，以及本次启动时拉起的知识库服务。",
    detail: (session.state.busy ? "注意：现在有文件正在解析 / 入库，停止会打断它们。\n\n" : "")
      + "· 共享的 Qdrant / Ollama 不会被停止"
      + "\n· 再次使用：从项目根目录重新启动服务（通常需要数十秒）",
    confirmText: "停止",
    danger: true
  }).then(function (ok) { if (ok) { session.stopNow(); } });
});

window.addEventListener("ragkb-session", function (event) { renderControl(event.detail); });
if (window.RAGKB_SESSION) { renderControl(window.RAGKB_SESSION.state); }

/* ---------- 手机端「⋯」溢出菜单----------
   它不是第二套控制逻辑，只是一层**外观**：每个菜单项都去点顶栏上那个被 CSS 隐藏的
   真按钮（.click() 对 display:none 的元素同样有效），所以
     · 「停止全部服务」仍然走 renderControl() 的 canStop 判断与那段确认文案；
     · 「清空当前对话」仍然走 clearBtn 上的确认框；
     · 「自动朗读」的状态仍由 voice.js 维护（这里只读它的文案与 aria-pressed 来显示开/关）。
   每次打开菜单重新渲染一遍状态，因此不存在"菜单里显示的和实际不一致"的窗口。 */
const moreBtn = $("moreBtn");
const moreMenu = $("moreMenu");
const moreMask = $("moreMask");

function closeMoreMenu() {
  if (!moreMenu || moreMenu.hidden) { return; }
  moreMenu.hidden = true;
  if (moreMask) { moreMask.hidden = true; }
  if (moreBtn) { moreBtn.setAttribute("aria-expanded", "false"); }
}

function renderMoreMenu() {
  // 「自动朗读」：voice.js 在可用时才创建这个按钮；它不存在就不显示这一项
  const speak = $("speakBtn");
  const mmSpeak = $("mmSpeak");
  if (mmSpeak) {
    mmSpeak.hidden = !speak;
    if (speak) {
      const on = speak.getAttribute("aria-pressed") === "true";
      mmSpeak.classList.toggle("on", on);
      $("mmSpeakState").textContent = on ? "开" : "关";
    }
  }
  // 「停止全部服务」：只有本机页面（canStop）才有这个按钮，并且正在停止时不该再点
  const stop = $("stopBtn");
  const mmStop = $("mmStop");
  const showStop = Boolean(stop) && !stop.hidden;
  if (mmStop) { mmStop.hidden = !showStop; }
  // 「清空当前对话」：空对话没什么可清，图谱态下更不该出现（图谱态没有对话可清）
  const clear = $("clearBtn");
  const inGraph = document.querySelector(".main.graph") !== null;
  const hasMessages = sessionHasMessages();
  const showClear = Boolean(clear) && hasMessages && !inGraph;
  const mmClear = $("mmClear");
  if (mmClear) { mmClear.hidden = !showClear; }
  const sep = $("mmSep");
  if (sep) { sep.hidden = !(showStop && showClear); }
}

function openMoreMenu() {
  if (!moreMenu) { return; }
  closeSidebar();                 // 抽屉与菜单不同屏
  renderMoreMenu();
  moreMenu.hidden = false;
  if (moreMask) { moreMask.hidden = false; }
  if (moreBtn) { moreBtn.setAttribute("aria-expanded", "true"); }
}

if (moreBtn) {
  moreBtn.addEventListener("click", function () {
    if (moreMenu && !moreMenu.hidden) { closeMoreMenu(); } else { openMoreMenu(); }
  });
}
if (moreMask) { moreMask.addEventListener("click", closeMoreMenu); }
// 菜单项：委托到真按钮；<a> 项（文件管理 / 设置）走自己的 href，不用管
if (moreMenu) {
  moreMenu.addEventListener("click", function (event) {
    const node = event.target && event.target.closest ? event.target.closest(".mm-item") : null;
    if (!node) { return; }
    closeMoreMenu();
    if (node.tagName === "A") { return; }        // 让浏览器按 href 跳转
    if (node.id === "mmSpeak") { const b = $("speakBtn"); if (b) { b.click(); } return; }
    if (node.id === "mmStop") { const b = $("stopBtn"); if (b) { b.click(); } return; }
    if (node.id === "mmClear") { const b = $("clearBtn"); if (b) { b.click(); } return; }
  });
}

// ---------- 手机端侧栏抽屉（纯 UI 适配，不改任何功能） ----------
// 背景：≤900px 时侧栏原本被 display:none 隐藏，手机上再也点不到「新对话 / 对话记录 /
// 各种上传 / 知识图谱 / 系统状态」。这里给侧栏补一个开关（左上「≡」），里面还是原来
// 那些按钮与事件，功能、接口、后端判断都不变；>900px 时开关与遮罩都不渲染，桌面端零影响。
const MOBILE_LAYOUT = window.matchMedia ? window.matchMedia("(max-width: 900px)") : null;
const appShell = document.querySelector(".app");
const sidebar = $("sidebar");
const sideMask = $("sideMask");
const menuBtn = $("menuBtn");

function closeSidebar() {
  if (appShell) { appShell.classList.remove("nav-open"); }
  if (sideMask) { sideMask.hidden = true; }
  if (menuBtn) { menuBtn.setAttribute("aria-expanded", "false"); }
}

function openSidebar() {
  closeMoreMenu();                 // 抽屉与溢出菜单不同屏
  if (appShell) { appShell.classList.add("nav-open"); }
  if (sideMask) { sideMask.hidden = false; }
  if (menuBtn) { menuBtn.setAttribute("aria-expanded", "true"); }
}

if (menuBtn && sidebar) {
  menuBtn.addEventListener("click", function () {
    if (appShell && appShell.classList.contains("nav-open")) { closeSidebar(); } else { openSidebar(); }
  });
}
if (sideMask) { sideMask.addEventListener("click", closeSidebar); }
// 桌面端：侧栏显示 / 隐藏开关（>900px 生效），状态本地记忆
const sideToggle = $("sideToggle");
function applySideCollapsed(collapsed) {
  if (appShell) { appShell.classList.toggle("side-collapsed", collapsed); }
  if (sideToggle) { sideToggle.setAttribute("aria-expanded", collapsed ? "false" : "true"); }
  try { localStorage.setItem("ragkb.sideCollapsed", collapsed ? "1" : "0"); } catch (err) { /* 私密模式 */ }
}
if (sideToggle) {
  let savedSide = "0";
  try { savedSide = localStorage.getItem("ragkb.sideCollapsed") || "0"; } catch (err) { /* 私密模式 */ }
  if (savedSide === "1") { applySideCollapsed(true); }
  sideToggle.addEventListener("click", function () {
    const collapsed = !(appShell && appShell.classList.contains("side-collapsed"));
    applySideCollapsed(collapsed);
  });
}
// 收起态图标栏：气泡=展开侧栏并新建对话；回形针/上传=展开并触发上传；展开=恢复完整侧栏
const railChat = $("railChat"), railUpload = $("railUpload"), railExpand = $("railExpand");
if (railChat) {
  railChat.addEventListener("click", function () {
    // 收起态气泡 = 「回到对话」：图谱视图先退回聊天；否则滚到底部最新消息
    const view = $("graphView");
    if (view && !view.hidden) { closeGraph(); return; }
    const jb = $("jumpBottom");
    if (jb && !jb.hidden) { jb.click(); }
  });
}
if (railUpload) {
  railUpload.addEventListener("click", function () {
    applySideCollapsed(false);
    const up = $("uploadBtn");
    if (up) { up.click(); }
  });
}
if (railExpand) {
  railExpand.addEventListener("click", function () { applySideCollapsed(false); });
}
// Esc：先收侧栏抽屉，再收知识图谱（两层都开着时，一次只关一层）
document.addEventListener("keydown", function (event) {
  if (event.key !== "Escape") { return; }
  // 一次只关一层：溢出菜单 → 侧栏抽屉 → 知识图谱
  if (moreMenu && !moreMenu.hidden) { closeMoreMenu(); return; }
  if (appShell && appShell.classList.contains("nav-open")) { closeSidebar(); return; }
  closeGraph();
});

// 抽屉里点完任一个入口（新对话 / 会话 / 上传 / 图谱 …）就自动收起，把屏幕让回给对话
if (sidebar) {
  sidebar.addEventListener("click", function (event) {
    const target = event.target;
    if (!target || !target.closest || !target.closest("button, a, .session, select")) { return; }
    if (target.closest("select") || target.tagName === "SELECT" || target.closest("#topicSelect")) { return; }
    // 移动端抽屉：关闭遮罩抽屉；桌面端：自动收起，把屏幕还给主区
    if (MOBILE_LAYOUT && MOBILE_LAYOUT.matches) { closeSidebar(); }
    else { applySideCollapsed(true); }
  });
}

// 点击侧栏以外任意区域：自动收起侧栏（桌面端收成图标栏，移动端关抽屉）。
// 侧栏内部点击、收起态图标栏点击、侧栏开关本身不触发，避免与原有入口冲突。
document.addEventListener("click", function (event) {
  const target = event.target;
  if (!target || !target.closest) { return; }
  if (target.closest("#sidebar, #sideRail, #sideToggle, #menuBtn")) { return; }
  if (appShell && appShell.classList.contains("nav-open")) { closeSidebar(); return; }
  if (appShell && !appShell.classList.contains("side-collapsed")) { applySideCollapsed(true); }
});
// 旋屏 / 把窗口拉宽到桌面尺寸时复位，避免遮罩留在页面上
if (MOBILE_LAYOUT) {
  const onLayoutChange = function (event) {
    if (!event.matches) { closeSidebar(); closeMoreMenu(); }   // 拉到桌面宽度：抽屉与菜单一并收起
  };
  if (MOBILE_LAYOUT.addEventListener) { MOBILE_LAYOUT.addEventListener("change", onLayoutChange); }
  else if (MOBILE_LAYOUT.addListener) { MOBILE_LAYOUT.addListener(onLayoutChange); }
}

// 手机上没法把文件「拖到输入框」：把提示换成本机能用的操作（只在手机宽度下替换文案）
if (MOBILE_LAYOUT && MOBILE_LAYOUT.matches) {
  input.placeholder = "输入消息…";
  const hintNode = $("hint");
  if (hintNode) {
    hintNode.textContent = "输入消息…";
  }
}

/* ---------- Batch 2：本地 AI 桥接回程 ----------
   AHK 把用户在 ChatGPT 里复制的文本 POST 到 /api/ai-bridge/ingest；
   本页轮询 /api/ai-bridge/pending，取到后只填入输入框，不自动发送。
   边界：不读写记忆；失败静默，不阻断聊天。默认关闭时 status.enabled=false，不启动轮询。 */
const AI_BRIDGE_POLL_MS = 1500;
const aiBridge = { enabled: false, token: "", timer: null, inFlight: false, autoSend: false };

async function initAIBridge() {
  try {
    const response = await fetch("/api/ai-bridge/status", { cache: "no-store" });
    if (!response.ok) { return; }
    const data = await response.json();
    aiBridge.enabled = !!data.enabled;
    aiBridge.token = String(data.token || "");
    aiBridge.autoSend = !!data.auto_send;
    if (aiBridge.timer) { clearInterval(aiBridge.timer); aiBridge.timer = null; }
    if (aiBridge.enabled && aiBridge.token) {
      aiBridge.timer = setInterval(aiBridgePoll, AI_BRIDGE_POLL_MS);
    }
  } catch (error) {
    // 新能力失败不得阻断聊天
  }
}

async function aiBridgePoll() {
  if (!aiBridge.enabled || aiBridge.inFlight) { return; }
  if (document.visibilityState && document.visibilityState !== "visible") { return; }
  if (busy || sendInFlight) { return; }
  // 输入框已有内容时不取走：后端的条目会保留到 TTL，避免覆盖用户草稿。
  if (String(input.value || "").trim()) { return; }
  aiBridge.inFlight = true;
  try {
    const response = await fetch("/api/ai-bridge/pending", {
      cache: "no-store",
      headers: { "X-AI-Bridge-Token": aiBridge.token }
    });
    if (response.status === 204) { return; }
    if (response.status === 401 || response.status === 403) {
      // token 随进程重启失效：停掉轮询，重新读一次 status；失败就不再来回打。
      aiBridge.enabled = false;
      if (aiBridge.timer) { clearInterval(aiBridge.timer); aiBridge.timer = null; }
      initAIBridge();
      return;
    }
    if (!response.ok) { return; }
    const data = await response.json();
    const text = String(data.text || "").trim();
    if (!text) { return; }
    input.value = text;
    autoGrow();
    syncComposerState();
    input.focus();
    if (aiBridge.autoSend) {
      // 自动发送没有二次确认：仍走现有 send()，会话/流式/错误处理与手动发送一致。
      if (busy || sendInFlight) {
        setToast("已接收 ChatGPT 回复，已填入输入框；当前正忙，请稍后按 Enter", "info");
        return;
      }
      setToast("已接收 ChatGPT 回复，正在发送…", "info");
      send();
    } else {
      setToast("已接收 ChatGPT 回复，已填入输入框；确认后按 Enter 发送", "ok");
    }
  } catch (error) {
    // 静默；不阻断聊天
  } finally {
    aiBridge.inFlight = false;
  }
}

document.addEventListener("visibilitychange", function () {
  if (document.visibilityState === "visible") { aiBridgePoll(); }
});

// 自愈看门狗：进行中的卡片如果 12 秒都没有拿到新状态（网络抖动、轮询意外断掉），
// 自动重新起一轮轮询；不改变用户操作，也不需要刷新整页。
setInterval(function () {
  const now = Date.now();
  UPLOAD_CARDS.forEach(function (item) {
    const card = item.card;
    if (!card || !card.isConnected || !item.fileId) { return; }
    const indexing = card.__state === "done"
      && card.__data && (card.__data.progress || {}).state === "processing";
    if (!BUSY_STATES_UI[card.__state] && !indexing) { return; }
    if (now - (card.__lastStatusAt || 0) < 12000) { return; }
    pollIngestStatus(card, item.fileId, { fast: true });
  });
}, 6000);

/* ---------- -mini：只读历史检索 ---------- */
const historyModal = $("historyModal");
const historyResultsNode = $("historyResults");

function historyOpen() {
  if (!historyModal) { return; }
  historyModal.hidden = false;
  const input = $("historyQuery");
  if (input) { input.focus(); }
}

function historyClose() {
  if (historyModal) { historyModal.hidden = true; }
}

function historyStatus(text, kind) {
  const node = $("historyStatus");
  if (!node) { return; }
  node.textContent = text || "";
  node.className = "material-status" + (kind ? " " + kind : "");
}

function historyRender(items) {
  if (!historyResultsNode) { return; }
  historyResultsNode.textContent = "";
  if (!items || !items.length) {
    historyStatus("没有找到匹配的历史消息；可以换一个更短的关键词。", "");
    return;
  }
  historyStatus("找到 " + items.length + " 条；点「打开会话」查看上下文。", "ok");
  items.forEach(function (item) {
    const box = document.createElement("div");
    box.className = "history-item";
    const title = document.createElement("div");
    title.className = "history-title";
    title.textContent = item.session_title || "未命名会话";
    const meta = document.createElement("div");
    meta.className = "history-meta";
    meta.textContent = (item.role === "user" ? "我" : "助手")
      + " · " + (item.topic_name || "未绑定主题")
      + " · " + (formatSessionTime(item.created_at) || "");
    const snippet = document.createElement("div");
    snippet.className = "history-snippet";
    snippet.textContent = item.snippet || "";
    const open = document.createElement("button");
    open.type = "button";
    open.className = "history-open";
    open.textContent = "打开会话";
    open.addEventListener("click", function () {
      historyClose();
      selectSession(item.session_id);
    });
    box.appendChild(title);
    box.appendChild(meta);
    box.appendChild(snippet);
    box.appendChild(open);
    historyResultsNode.appendChild(box);
  });
}

async function historySearch() {
  const input = $("historyQuery");
  const query = String((input && input.value) || "").trim();
  if (query.length < 2) {
    historyStatus("请至少输入 2 个字。", "bad");
    return;
  }
  historyStatus("检索中…", "");
  const data = await apiJson("/api/history/search?q=" + encodeURIComponent(query) + "&limit=10");
  if (!data) {
    historyStatus("检索失败：请检查访问口令或服务状态。", "bad");
    return;
  }
  if (!data.enabled) {
    historyStatus("会话持久化未启用，暂时无法检索历史。", "bad");
    return;
  }
  historyRender(data.items || []);
}

if (historyModal) {
  const historyBtn = $("historyBtn");
  if (historyBtn) { historyBtn.addEventListener("click", historyOpen); }
  const historyCloseBtn = $("historyClose");
  if (historyCloseBtn) { historyCloseBtn.addEventListener("click", historyClose); }
  const historySearchBtn = $("historySearchBtn");
  if (historySearchBtn) { historySearchBtn.addEventListener("click", historySearch); }
  const historyQuery = $("historyQuery");
  if (historyQuery) {
    historyQuery.addEventListener("keydown", function (event) {
      if (event.key === "Enter") { event.preventDefault(); historySearch(); }
    });
  }
  historyModal.addEventListener("click", function (event) {
    if (event.target === historyModal) { historyClose(); }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && historyModal && !historyModal.hidden) { historyClose(); }
  });
}

initGraphView();
// 会话先落定（恢复历史 / 新建会话），再把待确认卡片摆进来。
// 反过来会撞上 initSessions 后面的 renderSession()：卡片刚插进去就被清掉，
// 或者表现为"刷新后先看到空首页，过一会儿才蹦出 OCR 卡片"。
initSessions().finally(function () { restorePendingReviews(); loadTopics(); });
initAIBridge();
refreshStatus();
refreshIngestState();
setTimeout(noteSessionOrigin, 900);   // 地址变了导致历史看不到时，至少说清楚原因
setInterval(refreshStatus, 20000);
setInterval(refreshIngestState, 60000);
syncComposerState();
setSendState("send");
input.focus();
