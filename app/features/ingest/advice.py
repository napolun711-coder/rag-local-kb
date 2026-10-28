"""入库智能建议（）：用**可解释的检查项**回答「这份资料该不该入库」。

为什么单独一个模块：
- **纯函数、零依赖、不联网、不读磁盘**（与 `quality.py` 同一套路），因此可以离线单测；
- 结论必须**可解释**：每条建议都对应一个具名检查（名字 + 结论 + 人话说明），
  前端把检查项原样列出来。用户看到的是"为什么"，而不是一个黑箱分数 ——
  质量门（quality.py）负责"能不能入库"的硬判定，本模块只负责"值不值得入库"的提示。

与质量门的分工（重要，别把两件事混在一起）：
- quality.py：硬阈值 → pass / needs_ocr / blocked，决定文件**能不能**继续；
- advice.py ：启发式 → ok / attention / risk，只影响「确认门」要不要多问一句
  （INGEST_CONFIRM=auto 时，只有 ok 才会自动入库；always / off 不受影响）。

结论只用于提示，**任何情况下都不会替用户做"入库"这个决定**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.features.ingest import quality as quality_service

LEVEL_OK = "ok"
LEVEL_ATTENTION = "attention"
LEVEL_RISK = "risk"

#: 结论严重度顺序（合并多条检查结论时按这个顺序取最严重的）
LEVEL_ORDER = (LEVEL_OK, LEVEL_ATTENTION, LEVEL_RISK)

HEADLINES: Dict[str, str] = {
    LEVEL_OK: "建议直接入库",
    LEVEL_ATTENTION: "建议先看一眼再入库",
    LEVEL_RISK: "建议纠正内容或跳过",
}

#: 这些路由是"图片 → 文字"，数字 / 表格容易被识错，值得提醒对照原件
OCR_ROUTES = ("pdf", "image")

#: 超长单行阈值：整段没有换行的文本（典型是聊天记录、截图 OCR、表格导出）
LONG_LINE_CHARS = 800
#: 结构过于单薄的行数阈值
THIN_LINE_COUNT = 3

# 个人信息（上传者，用户很可能把自己的敏感资料放进来）
SENSITIVE_PATTERNS = (
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("银行卡号", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    ("邮箱地址", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
)

PREVIEW_CHARS = 200


def _level_of(value: str) -> str:
    return value if value in LEVEL_ORDER else LEVEL_OK


def _worst(levels: List[str]) -> str:
    """取最严重的结论（risk > attention > ok）。"""
    worst = LEVEL_OK
    for level in levels:
        if LEVEL_ORDER.index(_level_of(level)) > LEVEL_ORDER.index(worst):
            worst = _level_of(level)
    return worst


def _check(name: str, level: str, detail: str) -> Dict[str, Any]:
    resolved = _level_of(level)
    return {"name": name, "level": resolved, "ok": resolved == LEVEL_OK, "detail": detail}


@dataclass
class Advice:
    """智能建议结论（可直接 json 化后写进 result.json）。"""

    level: str = LEVEL_OK
    headline: str = HEADLINES[LEVEL_OK]
    reasons: List[str] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)
    preview: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)

    @property
    def auto_ok(self) -> bool:
        """是否"可以直接入库"（INGEST_CONFIRM=auto 时据此自动放行）。"""
        return self.level == LEVEL_OK

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "headline": self.headline,
            "reasons": list(self.reasons),
            "checks": [dict(item) for item in self.checks],
            "preview": self.preview,
            "stats": dict(self.stats),
        }


def _preview_of(text: str) -> str:
    """快速预览：把连续空白压成一个空格，避免预览框里出现大片空行。"""
    flat = re.sub(r"\s+", " ", text or "").strip()
    if len(flat) <= PREVIEW_CHARS:
        return flat
    return flat[:PREVIEW_CHARS] + "…"


def _sensitive_hits(text: str) -> List[str]:
    hits: List[str] = []
    for label, pattern in SENSITIVE_PATTERNS:
        found = len(pattern.findall(text or ""))
        if found:
            hits.append(f"{label} × {found}")
    return hits


def advise(
    text: str,
    *,
    quality: Optional[Dict[str, Any]] = None,
    route: str = "text",
    route_label: str = "",
    pages: int = 1,
    file_name: str = "",
    images_total: int = 0,
    images_pending: int = 0,
    ocr_used: bool = False,
    min_chars: int = 50,
    max_unocr_images: int = 0,
) -> Advice:
    """给一份「已经通过质检、等你决定要不要入库」的文本出建议。

    参数说明：
    · quality        质量门的结论字典（含 metrics）；给了就直接用，省一次重复计算；
    · images_pending 还没识别的内嵌图片数（> 0 表示图片里的文字不会进知识库）。
    """
    metrics: Dict[str, Any] = dict((quality or {}).get("metrics") or {})
    if not metrics:
        metrics = quality_service.compute_metrics(text, pages)

    char_total = int(metrics.get("char_total") or 0)
    line_total = int(metrics.get("line_total") or 0)
    garbled_ratio = float(metrics.get("garbled_ratio") or 0.0)
    repeat_ratio = float(metrics.get("repeat_line_ratio") or 0.0)

    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    longest_line = max((len(line) for line in lines), default=0)

    checks: List[Dict[str, Any]] = []

    # 1) 内容量：太少的内容进了知识库也检索不出什么
    # 删掉了"质检关掉了就不做阈值判断"的分支 —— 质检恒定运行，建议永远用同一套标准
    if char_total >= max(min_chars * 4, 200):
        checks.append(_check("内容量", LEVEL_OK, f"有效字符 {char_total} 个，足够构建索引"))
    elif char_total >= min_chars:
        checks.append(_check("内容量", LEVEL_ATTENTION, f"有效字符只有 {char_total} 个（下限 {min_chars}）：内容偏少，提问时可能命中率不高"))
    else:
        checks.append(_check("内容量", LEVEL_RISK, f"有效字符只有 {char_total} 个（下限 {min_chars}）：内容太少，建议纠正或跳过"))

    # 2) 结构：只有一两行，往往意味着"这段摘录/截图被当成文档"
    if line_total >= THIN_LINE_COUNT * 3:
        checks.append(_check("文本结构", LEVEL_OK, f"{line_total} 行，结构正常"))
    elif line_total >= THIN_LINE_COUNT:
        checks.append(_check("文本结构", LEVEL_ATTENTION, f"只有 {line_total} 行：段落结构很弱，提问时可能抓不到上下文"))
    else:
        checks.append(_check("文本结构", LEVEL_RISK, f"只有 {line_total} 行：更像一段摘录，建议核对后再入库"))

    # 3) 超长单行：整段没有换行（表格导出 / OCR 一整页）
    if longest_line > LONG_LINE_CHARS and line_total <= THIN_LINE_COUNT * 3:
        checks.append(_check("超长单行", LEVEL_ATTENTION, f"存在 {longest_line} 个字符的单行：切分后语义容易断裂，检索效果可能一般"))

    # 4) 重复内容
    if repeat_ratio <= 0.4:
        checks.append(_check("重复内容", LEVEL_OK, f"重复行占比 {repeat_ratio:.0%}，正常"))
    elif repeat_ratio <= 0.6:
        checks.append(_check("重复内容", LEVEL_ATTENTION, f"重复行占比 {repeat_ratio:.0%}：文档里有大段重复"))
    else:
        checks.append(_check("重复内容", LEVEL_RISK, f"重复行占比 {repeat_ratio:.0%}：几乎在同一段话里反复重复，建议跳过"))

    # 5) 乱码
    if garbled_ratio <= 0.05:
        checks.append(_check("乱码比例", LEVEL_OK, f"乱码字符占比 {garbled_ratio:.1%}，正常"))
    elif garbled_ratio <= 0.2:
        checks.append(_check("乱码比例", LEVEL_ATTENTION, f"乱码字符占比 {garbled_ratio:.1%}：可能有部分字符没解析出来"))
    else:
        checks.append(_check("乱码比例", LEVEL_RISK, f"乱码字符占比 {garbled_ratio:.1%}：解析结果不可靠，建议纠正或跳过"))

    # 6) 未识别的内嵌图片：这些内容不会进知识库，必须说清楚
    if images_pending > 0:
        heavy = images_pending >= 3 or (
            images_total > 0 and images_pending / max(images_total, 1) > 0.5
        )
        checks.append(_check(
            "内嵌图片",
            LEVEL_RISK if heavy else LEVEL_ATTENTION,
            f"{images_pending}/{images_total} 张图片还没识别：只入库正文，图片里的文字提问时检索不到"
            + ("（可先点「识别图片」）" if max_unocr_images <= 0 else ""),
        ))
    elif images_total > 0:
        checks.append(_check("内嵌图片", LEVEL_OK, f"{images_total} 张内嵌图片都已识别并合进正文"))

    # 7) 识别方式：OCR 出来的数字 / 表格容易错，提醒对照原件
    if ocr_used or route in OCR_ROUTES:
        checks.append(_check("识别方式", LEVEL_ATTENTION, "本文件走的是图片 / PDF 识别：数字、表格可能识别不准，关键数字建议对照原件"))

    # 8) 个人信息：上传者最常见的"入库后才发现"的问题
    hits = _sensitive_hits(text)
    if hits:
        checks.append(_check("个人信息", LEVEL_ATTENTION, "检测到" + "、".join(hits) + "：入库后本机与局域网页面都能检索到，注意访问范围"))

    reasons = [item["detail"] for item in checks if not item["ok"]][:3]
    level = _worst([item["level"] for item in checks])

    return Advice(
        level=level,
        headline=HEADLINES[level],
        reasons=reasons,
        checks=checks,
        preview=_preview_of(text),
        stats={
            "chars": char_total,
            "lines": line_total,
            "longest_line": longest_line,
            "pages": int(pages or 0),
            "images_total": int(images_total or 0),
            "images_pending": int(images_pending or 0),
            "file_name": file_name,
            "route": route,
            "route_label": route_label,
        },
    )

