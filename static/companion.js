/* 虚拟工作伙伴：只展示调用方明确上报的真实任务状态。
 *
 * 约束：
 * - 不自行推断阶段，不生成百分比；
 * - 每个任务用稳定 id 隔离，旧任务的迟到更新不会盖住新任务；
 * - 只保留少量活跃任务；完成后短暂展示结果，然后回待机；
 * - 上传任务由 /ingest/status 的真实 state 驱动。
 */
(function () {
  "use strict";

  const HOLD_MS = 6000;
  const ERROR_HOLD_MS = 10000;
  const MAX_TASKS = 30;
  const tasks = new Map();
  let sequence = 0;
  let timer = 0;
  let initialized = false;
  let root = null;
  let dockNode = null;
  let copyNode = null;
  let labelNode = null;
  let subNode = null;
  let detailsBtn = null;
  let cubeStage = null;
  let cubeModel = null;
  let lastSignature = "";
  let cubeRX = -18;
  let cubeRY = 28;
  let cubeRZ = 0;
  let cubeBiasX = 0;
  let cubeBiasZ = 0;
  let cubeAutoX = 0;
  let cubeAutoY = 0;
  let cubeAutoZ = 0;
  let cubeActivity = "idle";
  let cubeActivitySince = 0;
  let cubeDragging = false;
  let cubePointerX = 0;
  let cubePointerY = 0;
  let cubeMomentumX = 0;
  let cubeMomentumY = 0;
  let cubeRaf = 0;
  let cubeLastFrame = 0;
  let dockDragging = false;
  let dockPointerX = 0;
  let dockPointerY = 0;
  let dockStartLeft = 0;
  let dockStartTop = 0;

  const DOCK_POSITION_KEY = "work_companion_position_v1";

  const BUSY = new Set([
    "uploading", "queued", "parsing", "checking", "ocr", "ingesting", "indexing",
    "thinking", "working", "generating"
  ]);
  const PRIORITY = { chat: 50, material: 40, upload: 30, system: 20 };

  function now() { return Date.now(); }

  function init() {
    if (initialized) { return; }
    root = document.getElementById("workCompanion");
    if (!root) { return; }
    dockNode = document.getElementById("workDock");
    copyNode = root.querySelector(".wc-copy");
    labelNode = document.getElementById("workLabel");
    subNode = document.getElementById("workSub");
    detailsBtn = document.getElementById("workDetails");
    cubeStage = document.getElementById("workCubeStage");
    cubeModel = document.getElementById("workCubeModel");
    if (detailsBtn) {
      detailsBtn.addEventListener("click", function () {
        const current = currentDisplay();
        const action = current && current.detailAction;
        if (typeof action === "function") {
          try { action(); } catch (err) { /* 展示失败不能反向影响任务 */ }
        }
      });
    }
    bindCubeInteraction();
  bindCubeDockDrag();
    bindDockDrag();
    restoreDockPosition();
    initialized = true;
    render();
  }

  function reducedMotion() {
    return Boolean(
      window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  }

  function applyCubeRotation() {
    if (!cubeModel) { return; }
    cubeModel.style.transform = "rotateX(" + (cubeRX + cubeBiasX + cubeAutoX).toFixed(2)
      + "deg) rotateY(" + (cubeRY + cubeAutoY).toFixed(2)
      + "deg) rotateZ(" + (cubeRZ + cubeBiasZ + cubeAutoZ).toFixed(2) + "deg)";
  }

  function activityMode(task, motion) {
    if (motion === "idle" || !task) { return "idle"; }
    if (motion === "error") { return "error"; }
    if (motion === "success") { return "success"; }
    if (motion === "waiting") { return "waiting"; }
    if (motion === "reading") { return "reading"; }
    const text = String(task.label || "") + " " + String(task.detail || "");
    if (/OCR|文字识别|识别图片/i.test(text)) { return "precision"; }
    if (/知识库索引|写入知识库|提交知识库|建索引/i.test(text)) { return "orbit"; }
    if (/召回|读取.*上下文|会话上下文|资料上下文/i.test(text)) { return "recall"; }
    if (/检索|参考资料|召回/i.test(text)) { return "search"; }
    if (/生成|修改材料|整理最终|材料/i.test(text)) { return "build"; }
    if (/上传|解析文件|校验/i.test(text)) { return "scan"; }
    if (/理解你的要求|准备发送|输入已提交/i.test(text)) { return "input"; }
    if (/思考|正在分析/i.test(text)) { return "thinking"; }
    return "working";
  }

  function syncCubeActivity(task, motion) {
    const next = activityMode(task, motion);
    if (next !== cubeActivity) {
      cubeActivity = next;
      cubeActivitySince = performance.now();
    }
    if (root) { root.setAttribute("data-activity", cubeActivity); }
  }

  function cubeBiasTargets() {
    return {
      idle: { x: 0, z: 0 },
      thinking: { x: -5, z: 2 },
      search: { x: 6, z: -3 },
      scan: { x: -3, z: 4 },
      precision: { x: 15, z: -2 },
      orbit: { x: -8, z: 7 },
      build: { x: -4, z: -5 },
      working: { x: 0, z: 0 },
      success: { x: -10, z: 9 },
      error: { x: 4, z: -3 },
      waiting: { x: 1, z: 2 },
      reading: { x: -2, z: 3 },
      recall: { x: 7, z: -5 },
      input: { x: -4, z: 2 }
    }[cubeActivity] || { x: 0, z: 0 };
  }

  function cubeFrame(timestamp) {
    if (!cubeModel) { return; }
    const delta = cubeLastFrame ? Math.min(48, timestamp - cubeLastFrame) : 16;
    cubeLastFrame = timestamp;
    if (!reducedMotion() && !cubeDragging) {
      const seconds = Math.max(0, (timestamp - cubeActivitySince) / 1000);
      const target = cubeBiasTargets();
      cubeBiasX += (target.x - cubeBiasX) * 0.08;
      cubeBiasZ += (target.z - cubeBiasZ) * 0.08;
      cubeAutoX = 0;
      cubeAutoY = 0;
      cubeAutoZ = 0;

      if (cubeActivity === "idle") {
        cubeRY += 0.012 * delta;
        cubeAutoX = Math.sin(seconds * 0.55) * 1.8;
        cubeAutoZ = Math.sin(seconds * 0.37) * 0.7;
      } else if (cubeActivity === "thinking") {
        cubeRY += 0.024 * delta;
        cubeAutoX = Math.sin(seconds * 1.1) * 4.2;
        cubeAutoY = Math.sin(seconds * 0.45) * 6.5;
        cubeAutoZ = Math.sin(seconds * 0.7) * 1.8;
      } else if (cubeActivity === "search") {
        cubeRY += 0.036 * delta;
        cubeAutoY = Math.sin(seconds * 0.9) * 28;
        cubeAutoX = Math.sin(seconds * 0.42) * 8;
      } else if (cubeActivity === "scan") {
        cubeRY += 0.052 * delta;
        cubeAutoX = Math.sin(seconds * 1.25) * 4;
      } else if (cubeActivity === "precision") {
        cubeRY += 0.018 * delta;
        cubeAutoX = Math.sin(seconds * 1.55) * 19;
        cubeAutoY = Math.sin(seconds * 0.72) * 7;
      } else if (cubeActivity === "orbit") {
        cubeRY += 0.060 * delta;
        cubeAutoX = Math.sin(seconds * 0.48) * 5;
        cubeAutoZ = Math.sin(seconds * 0.82) * 8;
      } else if (cubeActivity === "build") {
        cubeRY += 0.072 * delta;
        cubeAutoX = Math.sin(seconds * 0.7) * 3;
        cubeAutoZ = Math.sin(seconds * 1.3) * 5;
      } else if (cubeActivity === "success") {
        cubeRY += (seconds < 1.25 ? 0.16 : 0.025) * delta;
        cubeAutoZ = Math.sin(seconds * 2.8) * 3;
      } else if (cubeActivity === "error") {
        cubeAutoX = Math.sin(seconds * 27) * 1.5;
        cubeAutoY = Math.sin(seconds * 23) * 2.2;
      } else if (cubeActivity === "waiting") {
        cubeRY += 0.006 * delta;
        cubeAutoY = Math.sin(seconds * 0.62) * 9;
        cubeAutoX = Math.sin(seconds * 0.9) * 2.5;
      } else if (cubeActivity === "reading") {
        cubeRY += 0.032 * delta;
        cubeAutoY = Math.sin(seconds * 1.15) * 12;
        cubeAutoX = Math.sin(seconds * 0.78) * 3.5;
      } else if (cubeActivity === "recall") {
        cubeRY += 0.046 * delta;
        cubeAutoY = Math.sin(seconds * 1.4) * 22;
        cubeAutoX = Math.sin(seconds * 0.86) * 6;
        cubeAutoZ = Math.sin(seconds * 1.1) * 4;
      } else if (cubeActivity === "input") {
        cubeRY += 0.030 * delta;
        cubeAutoY = Math.sin(seconds * 1.7) * 14;
        cubeAutoX = Math.sin(seconds * 1.05) * 5;
      } else {
        cubeRY += 0.024 * delta;
      }
      if (Math.abs(cubeMomentumY) > 0.01 || Math.abs(cubeMomentumX) > 0.01) {
        cubeRY += cubeMomentumY * delta;
        cubeRX += cubeMomentumX * delta;
        cubeMomentumY *= 0.92;
        cubeMomentumX *= 0.92;
      }
      cubeRX = Math.max(-82, Math.min(82, cubeRX));
      applyCubeRotation();
    }
    cubeRaf = window.requestAnimationFrame(cubeFrame);
  }

  function clampDock(left, top) {
    const width = dockNode ? dockNode.offsetWidth : 220;
    const height = dockNode ? dockNode.offsetHeight : 120;
    return {
      left: Math.max(8, Math.min(window.innerWidth - width - 8, left)),
      top: Math.max(8, Math.min(window.innerHeight - height - 8, top))
    };
  }

  function saveDockPosition() { /* 位置不持久化：刷新回归默认位 */ }

  function applyDockPosition(left, top) {
    if (!dockNode) { return; }
    const pos = clampDock(left, top);
    dockNode.style.left = pos.left + "px";
    dockNode.style.top = pos.top + "px";
    dockNode.style.right = "auto";
    dockNode.style.bottom = "auto";
  }

  function restoreDockPosition() {
    if (!dockNode) { return; }
    /* 位置不持久化：刷新回到默认位（右下角，视觉最优） */
    window.addEventListener("resize", function () {
      if (!dockNode.style.left) { return; }
      const rect = dockNode.getBoundingClientRect();
      applyDockPosition(rect.left, rect.top);
    });
  }

  function bindDockDrag() {
    if (!copyNode || !dockNode) { return; }
    copyNode.title = "拖动这里移动工作伙伴位置";
    copyNode.addEventListener("pointerdown", function (event) {
      if (event.button !== undefined && event.button !== 0) { return; }
      if (event.target && event.target.closest && event.target.closest("#workDetails")) { return; }
      const rect = dockNode.getBoundingClientRect();
      dockDragging = true;
      dockPointerX = event.clientX;
      dockPointerY = event.clientY;
      dockStartLeft = rect.left;
      dockStartTop = rect.top;
      dockNode.classList.add("is-moving");
      try { copyNode.setPointerCapture(event.pointerId); } catch (err) { /* 兼容 */ }
      event.preventDefault();
    });
    let dockRaf = 0;
    let dockNextX = 0, dockNextY = 0;
    copyNode.addEventListener("pointermove", function (event) {
      if (!dockDragging) { return; }
      dockNextX = dockStartLeft + event.clientX - dockPointerX;
      dockNextY = dockStartTop + event.clientY - dockPointerY;
      if (!dockRaf) {
        dockRaf = window.requestAnimationFrame(function () {
          dockRaf = 0;
          if (!dockDragging) { return; }
          applyDockPosition(dockNextX, dockNextY);
        });
      }
      event.preventDefault();
    });
    function endMove(event) {
      if (!dockDragging) { return; }
      if (dockRaf) { window.cancelAnimationFrame(dockRaf); dockRaf = 0; }
      dockDragging = false;
      dockNode.classList.remove("is-moving");
      saveDockPosition();
      try { copyNode.releasePointerCapture(event.pointerId); } catch (err) { /* 已释放 */ }
    }
    copyNode.addEventListener("pointerup", endMove);
    copyNode.addEventListener("pointercancel", endMove);
  }

  let cubeRotatingDisabled = false;
  function bindCubeDockDrag() {
    if (!cubeStage || !dockNode) { return; }
    let dragRaf = 0, nextX = 0, nextY = 0;
    let sx = 0, sy = 0, sl = 0, st = 0, moved = false;
    cubeStage.addEventListener("pointerdown", function (event) {
      if (event.button !== undefined && event.button !== 0) { return; }
      const rect = dockNode.getBoundingClientRect();
      sx = event.clientX; sy = event.clientY;
      sl = rect.left; st = rect.top;
      moved = false;
      try { cubeStage.setPointerCapture(event.pointerId); } catch (err) { /* 兼容 */ }
      event.preventDefault();
    });
    cubeStage.addEventListener("pointermove", function (event) {
      if (event.buttons !== 1 && event.buttons !== undefined) { return; }
      const dx = event.clientX - sx, dy = event.clientY - sy;
      if (!moved && Math.abs(dx) < 4 && Math.abs(dy) < 4) { return; }
      if (!moved) { moved = true; cubeDragging = true; cubeRotatingDisabled = true; dockNode.classList.add("is-moving"); }
      nextX = sl + dx; nextY = st + dy;
      if (!dragRaf) {
        dragRaf = window.requestAnimationFrame(function () {
          dragRaf = 0;
          if (!moved) { return; }
          applyDockPosition(nextX, nextY);
        });
      }
      event.preventDefault();
    });
    function end(event) {
      if (!moved) { return; }
      if (dragRaf) { window.cancelAnimationFrame(dragRaf); dragRaf = 0; }
      moved = false; cubeDragging = false; cubeRotatingDisabled = false;
      dockNode.classList.remove("is-moving");
      saveDockPosition();
      try { cubeStage.releasePointerCapture(event.pointerId); } catch (err) { /* 已释放 */ }
    }
    cubeStage.addEventListener("pointerup", end);
    cubeStage.addEventListener("pointercancel", end);
  }

  function bindCubeInteraction() {
    if (!cubeStage || !cubeModel) { return; }
    applyCubeRotation();
    cubeRaf = window.requestAnimationFrame(cubeFrame);

    cubeStage.addEventListener("pointerdown", function (event) {
      if (event.button !== undefined && event.button !== 0) { return; }
      if (cubeRotatingDisabled) { return; }
      cubeDragging = true;
      cubePointerX = event.clientX;
      cubePointerY = event.clientY;
      cubeMomentumX = 0;
      cubeMomentumY = 0;
      cubeStage.classList.add("is-dragging");
      try { cubeStage.setPointerCapture(event.pointerId); } catch (err) { /* 不支持捕获时仍可拖动 */ }
      event.preventDefault();
    });

    cubeStage.addEventListener("pointermove", function (event) {
      if (!cubeDragging) { return; }
      const dx = event.clientX - cubePointerX;
      const dy = event.clientY - cubePointerY;
      cubePointerX = event.clientX;
      cubePointerY = event.clientY;
      cubeRY += dx * 0.55;
      cubeRX = Math.max(-82, Math.min(82, cubeRX - dy * 0.45));
      cubeMomentumY = dx * 0.020;
      cubeMomentumX = -dy * 0.016;
      if (!cubeRafDrag) {
        cubeRafDrag = window.requestAnimationFrame(function () {
          cubeRafDrag = 0;
          if (cubeDragging) { applyCubeRotation(); }
        });
      }
      event.preventDefault();
    });

    let cubeRafDrag = 0;
    function endDrag(event) {
      if (!cubeDragging) { return; }
      if (cubeRafDrag) { window.cancelAnimationFrame(cubeRafDrag); cubeRafDrag = 0; }
      cubeDragging = false;
      applyCubeRotation();
      cubeStage.classList.remove("is-dragging");
      try { cubeStage.releasePointerCapture(event.pointerId); } catch (err) { /* 已释放 */ }
    }
    cubeStage.addEventListener("pointerup", endDrag);
    cubeStage.addEventListener("pointercancel", endDrag);

    cubeStage.addEventListener("keydown", function (event) {
      const step = event.shiftKey ? 20 : 8;
      if (event.key === "ArrowLeft") { cubeRY -= step; }
      else if (event.key === "ArrowRight") { cubeRY += step; }
      else if (event.key === "ArrowUp") { cubeRX = Math.max(-82, cubeRX - step); }
      else if (event.key === "ArrowDown") { cubeRX = Math.min(82, cubeRX + step); }
      else { return; }
      event.preventDefault();
      cubeMomentumX = 0;
      cubeMomentumY = 0;
      applyCubeRotation();
    });

    cubeStage.addEventListener("dblclick", function () {
      cubeRX = -18;
      cubeRY = 28;
      cubeMomentumX = 0;
      cubeMomentumY = 0;
      applyCubeRotation();
    });

    if (window.matchMedia) {
      const media = window.matchMedia("(prefers-reduced-motion: reduce)");
      const onChange = function () {
        if (media.matches) {
          cubeMomentumX = 0;
          cubeMomentumY = 0;
        }
      };
      if (media.addEventListener) { media.addEventListener("change", onChange); }
    }
  }

  function prune() {
    if (tasks.size <= MAX_TASKS) { return; }
    const ordered = Array.from(tasks.values()).sort(function (a, b) {
      return (a.updatedAt || 0) - (b.updatedAt || 0);
    });
    while (ordered.length > MAX_TASKS) {
      tasks.delete(ordered.shift().id);
    }
  }

  function normalize(id, info, terminal) {
    const old = tasks.get(id) || {};
    const at = now();
    const task = Object.assign(
      {
        id: String(id),
        category: "system",
        priority: PRIORITY.system,
        motion: "thinking",
        label: "正在处理…",
        detail: "",
        object: "",
        active: true,
        startedAt: at,
        updatedAt: at,
        completedAt: 0,
        detailAction: null
      },
      old,
      info || {}
    );
    task.id = String(id);
    task.category = String(task.category || "system");
    task.priority = Number(task.priority || PRIORITY[task.category] || PRIORITY.system);
    if (!task.order) { task.order = ++sequence; }
    task.updatedAt = at;
    if (terminal) {
      task.active = false;
      task.completedAt = at;
    }
    tasks.set(task.id, task);
    prune();
    return task;
  }

  function begin(id, info) {
    init();
    const isNew = !tasks.has(id);
    const task = normalize(id, Object.assign({ active: true }, info || {}), false);
    if (isNew) { task.order = ++sequence; }
    if (!task.startedAt || task.startedAt === task.updatedAt) {
      task.startedAt = now();
    }
    render();
    return task;
  }

  function update(id, info) {
    init();
    if (!tasks.has(id)) { return begin(id, info); }
    const task = normalize(id, info || {}, false);
    task.active = info && info.active === false ? false : true;
    render();
    return task;
  }

  function finish(id, info) {
    init();
    const payload = Object.assign(
      {
        motion: "success",
        label: "已完成",
        detail: "",
        detailAction: null
      },
      info || {}
    );
    const task = normalize(id, payload, true);
    task.holdMs = payload.holdMs || (payload.motion === "error" ? ERROR_HOLD_MS : HOLD_MS);
    render();
    return task;
  }

  function idle(label) {
    init();
    tasks.clear();
    lastSignature = "";
    render(label ? {
      id: "idle",
      category: "system",
      motion: "idle",
      label: String(label),
      detail: "",
      active: false,
      completedAt: 0
    } : null);
  }

  function ingestLabel(state) {
    return {
      uploading: "正在上传文件…",
      queued: "正在排队等待解析…",
      parsing: "正在解析文件…",
      checking: "正在校验内容…",
      ocr: "正在进行 OCR 文字识别…",
      ingesting: "正在写入知识库…",
      indexing: "正在建立知识库索引…",
      needs_ocr: "需要你确认是否进行 OCR",
      review: "已通过校验，等待你确认入库",
      blocked: "文件未通过校验，需要你处理",
      failed: "文件处理失败，请查看原因",
      error: "文件处理失败，请查看原因",
      skipped: "已跳过这份文件",
      removed: "这份文件已从知识库删除",
      unknown: "已提交，暂无法确认进度",
      done: "文件处理完成"
    }[state] || "正在处理文件…";
  }

  function ingestMotion(state) {
    if (BUSY.has(state) || state === "indexing") { return "working"; }
    if (state === "done") { return "success"; }
    if (state === "failed" || state === "error" || state === "blocked") { return "error"; }
    return "waiting";
  }

  function ingest(id, data, options) {
    init();
    const info = data || {};
    const opts = options || {};
    let state = String(info.state || opts.state || "unknown");
    const progress = info.progress || {};
    const indexing = state === "done" && progress.state === "processing";
    if (indexing) { state = "indexing"; }

    const total = Number(progress.total || 0);
    const processed = Number(progress.processed || 0);
    let detail = String(info.detail || opts.detail || "");
    if (indexing && total > 0) {
      detail = "知识库解析 " + processed + "/" + total;
    }

    const payload = {
      category: "upload",
      priority: PRIORITY.upload,
      motion: ingestMotion(state),
      label: ingestLabel(state),
      object: String(info.name || opts.name || ""),
      detail: detail,
      detailAction: opts.detailAction || null
    };

    if (BUSY.has(state) || state === "indexing") {
      return update(id, payload);
    }
    return finish(id, payload);
  }

  function taskSignature(task) {
    return [
      task && task.id,
      task && task.motion,
      task && task.label,
      task && task.object,
      task && task.detail,
      task && task.active
    ].join("|");
  }

  function activeUploads() {
    return Array.from(tasks.values()).filter(function (task) {
      return task.active && task.category === "upload";
    });
  }

  function pickActive() {
    const active = Array.from(tasks.values()).filter(function (task) { return task.active; });
    if (!active.length) { return null; }
    active.sort(function (a, b) {
      return (b.priority - a.priority) || (b.order - a.order);
    });
    const winner = active[0];
    if (winner.category !== "upload") { return winner; }
    const uploads = activeUploads();
    if (uploads.length <= 1) { return winner; }
    const batches = uploads.filter(function (task) { return task.batch; });
    if (batches.length) {
      const individual = uploads.filter(function (task) { return !task.batch; }).length;
      const totalFiles = batches.reduce(function (sum, task) {
        return sum + Number(task.fileCount || 0);
      }, individual);
      const waiting = batches.reduce(function (sum, task) {
        return sum + Number(task.waitingCount || 0);
      }, 0);
      const activeCount = batches.reduce(function (sum, task) {
        return sum + Number(task.activeCount || 0);
      }, 0) + individual;
      const done = batches.reduce(function (sum, task) {
        return sum + Number(task.doneCount || 0);
      }, 0);
      const waitingTask = batches.filter(function (task) {
        return task.motion === "waiting" || task.motion === "error";
      })[0] || winner;
      const parts = ["共 " + totalFiles + " 份"];
      if (waiting) { parts.push("待处理 " + waiting); }
      if (activeCount) { parts.push("处理中 " + activeCount); }
      if (done) { parts.push("已完成 " + done); }
      const motion = waiting
        ? (batches.some(function (task) { return task.motion === "error"; }) ? "error" : "waiting")
        : (activeCount ? "working" : "success");
      return Object.assign({}, waitingTask, {
        motion: motion,
        label: waiting ? "资料队列需要处理"
          : (activeCount ? "资料队列处理中" : "资料队列即将完成"),
        detail: parts.join(" · "),
        object: "",
        detailAction: waitingTask.detailAction || null
      });
    }
    return Object.assign({}, winner, {
      label: "正在处理资料队列…",
      detail: "共 " + uploads.length + " 份资料在队列中",
      object: "",
      detailAction: null
    });
  }

  function pickTerminal() {
    const at = now();
    const recent = Array.from(tasks.values()).filter(function (task) {
      return !task.active && task.completedAt
        && at - task.completedAt <= (task.holdMs || HOLD_MS);
    });
    if (!recent.length) { return null; }
    recent.sort(function (a, b) {
      return (b.completedAt - a.completedAt) || (b.order - a.order);
    });
    return recent[0];
  }

  function currentDisplay() {
    return pickActive() || pickTerminal() || null;
  }

  function render(forced) {
    if (!root) { return; }
    const task = forced || currentDisplay();
    const signature = taskSignature(task);
    if (signature === lastSignature) { schedule(); return; }
    lastSignature = signature;

    const motion = task ? String(task.motion || "thinking") : "idle";
    root.setAttribute("data-state", motion);
    syncCubeActivity(task, motion);
    root.classList.toggle("is-active", Boolean(task && task.active));
    root.classList.toggle(
      "is-passive",
      !task || !task.active || motion === "reading"
    );
    root.setAttribute("aria-busy", task && task.active ? "true" : "false");

    if (labelNode) {
      labelNode.textContent = task ? String(task.label || "正在处理…") : "随时可以帮你整理资料";
    }
    const parts = [];
    if (task && task.object) { parts.push(String(task.object)); }
    if (task && task.detail) { parts.push(String(task.detail)); }
    if (subNode) {
      subNode.textContent = parts.join(" · ");
      subNode.hidden = !parts.length;
    }
    if (detailsBtn) {
      const show = Boolean(task && task.detailAction && (motion === "error" || motion === "waiting"));
      detailsBtn.hidden = !show;
      detailsBtn.textContent = motion === "error" ? "查看原因" : "去确认";
    }
    schedule();
  }

  function schedule() {
    if (timer) {
      window.clearTimeout(timer);
      timer = 0;
    }
    const task = pickTerminal();
    if (!task) { return; }
    const remaining = Math.max(
      40,
      (task.holdMs || HOLD_MS) - (now() - task.completedAt) + 20
    );
    timer = window.setTimeout(function () {
      timer = 0;
      render();
    }, remaining);
  }

  function activeCount() {
    return Array.from(tasks.values()).filter(function (task) { return task.active; }).length;
  }

  window.WorkCompanion = {
    begin: begin,
    update: update,
    finish: finish,
    idle: idle,
    ingest: ingest,
    activeCount: activeCount,
    current: currentDisplay,
    cubeAngles: function () {
      return {
        x: cubeRX + cubeBiasX + cubeAutoX,
        y: cubeRY + cubeAutoY,
        z: cubeRZ + cubeBiasZ + cubeAutoZ
      };
    },
    activityMode: function () { return cubeActivity; }
  };

  init();
})();










