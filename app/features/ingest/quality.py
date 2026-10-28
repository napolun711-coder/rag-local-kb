"""质量门（）：用 5 项客观指标判断解析结果是否值得入库。

判定原则（重要）：
- **硬阈值决定结论**（pass / needs_ocr / blocked），score 只用于展示与边缘提示，
  避免"分数看着还行、其实是乱码"的漏网；
- 纯函数、零依赖、不联网：同样的输入永远得到同样的结论，便于单测；
- 阈值来自配置（QualityOptions），单测可显式传值，不依赖 .env；
- **没有"质检总开关"**（）：常量、配置与界面里都不存在"关闭质检"的入口，
  未通过的文件只能由用户在卡片上点「仍要入库」事后放行（`ingest` 会写 forced 痕迹），
  质检本身永远会跑完并把结论留在 `result.json` 里。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.features.ingest.config import get_ingest_settings

LEVEL_PASS = "pass"
LEVEL_NEEDS_OCR = "needs_ocr"
LEVEL_BLOCKED = "blocked"

# 需要 OCR 的路由（PDF 可能是扫描件；图片本来就没有文字层）
OCR_ROUTES = ("pdf", "image")

# 可读字符：中日韩文字 + 英文字母 + 数字
READABLE_PATTERN = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7afA-Za-z0-9]"
)
# 乱码字符：替换符 + 私用区 + 控制字符（不含 \n \t）
GARBLED_PATTERN = re.compile(
    "[\ufffd\ue000-\uf8ff\U000f0000-\U000ffffd\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
)


@dataclass(frozen=True)
class QualityOptions:
    """阈值配置（默认值与 .env.example 保持一致）。"""

    # 这里**没有**「质检总开关」—— 质检恒定运行（未通过的文件只能在卡片上
    # 点「仍要入库」事后放行，且留痕）。原 QUALITY_ENABLED 只为兼容旧 .env 保留，读进来不生效。
    min_chars: int = 50
    min_readable_ratio: float = 0.60
    max_garbled_ratio: float = 0.05
    max_repeat_ratio: float = 0.40
    min_chars_per_page: int = 100
    pass_score: int = 70


# 质量门预设（）：standard 与  默认值完全一致（零行为变化）
PROFILES: Dict[str, QualityOptions] = {
    "standard": QualityOptions(),
    "strict": QualityOptions(
        min_chars=200,
        min_readable_ratio=0.75,
        max_garbled_ratio=0.01,
        max_repeat_ratio=0.25,
        min_chars_per_page=200,
        pass_score=80,
    ),
    "loose": QualityOptions(
        min_chars=20,
        min_readable_ratio=0.45,
        max_garbled_ratio=0.10,
        max_repeat_ratio=0.60,
        min_chars_per_page=50,
        pass_score=50,
    ),
}

PROFILE_NAMES = ("standard", "strict", "loose", "custom")


def ocr_routes(settings=None) -> tuple:
    """允许整篇 OCR 的路由清单（来自 QUALITY_OCR_ROUTES，默认 pdf/image）。"""
    settings = settings or get_ingest_settings()
    routes = getattr(settings, "quality_ocr_routes", None)
    if not routes:
        return OCR_ROUTES
    return tuple(str(item).strip().lower() for item in routes if str(item).strip())


def options_from_settings(settings=None) -> QualityOptions:
    """从运行配置生成阈值；settings=None 时读取当前 .env。

    质量门预设（QUALITY_PROFILE）：
    - standard（默认）= 与  完全相同的阈值；
    - strict / loose  = 内置预设，忽略 QUALITY_* 各项（便于一键切换松紧）；
    - custom          = 逐项使用 QUALITY_* 自定义阈值。
    """
    settings = settings or get_ingest_settings()
    profile = str(getattr(settings, "quality_profile", "standard") or "standard").lower()
    if profile in PROFILES:
        # 质检恒定运行 —— 预设直接返回，不再把 QUALITY_ENABLED 拼进来
        return PROFILES[profile]
    return QualityOptions(
        min_chars=settings.quality_min_chars,
        min_readable_ratio=settings.quality_min_readable_ratio,
        max_garbled_ratio=settings.quality_max_garbled_ratio,
        max_repeat_ratio=settings.quality_max_repeat_ratio,
        min_chars_per_page=settings.quality_min_chars_per_page,
        pass_score=settings.quality_pass_score,
    )


@dataclass
class QualityReport:
    """质量门结论。"""

    level: str = LEVEL_PASS
    score: int = 0
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.level == LEVEL_PASS

    @property
    def needs_ocr(self) -> bool:
        return self.level == LEVEL_NEEDS_OCR

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "score": self.score,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


def compute_metrics(text: str, pages: int) -> Dict[str, Any]:
    """计算 5 项指标（pages<=0 时按 1 页处理）。"""
    raw_total = len(text)
    char_total = sum(1 for ch in text if not ch.isspace())

    if char_total:
        readable = len(READABLE_PATTERN.findall(text))
        readable_ratio = round(readable / char_total, 4)
    else:
        readable = 0
        readable_ratio = 0.0

    garbled = len(GARBLED_PATTERN.findall(text))
    garbled_ratio = round(garbled / raw_total, 4) if raw_total else 0.0

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    repeat_ratio = round(1 - len(set(lines)) / len(lines), 4) if lines else 0.0

    safe_pages = pages if pages and pages > 0 else 1

    return {
        "char_total": char_total,
        "char_raw": raw_total,
        "line_total": len(lines),
        "readable_chars": readable,
        "readable_ratio": readable_ratio,
        "garbled_ratio": garbled_ratio,
        "repeat_line_ratio": repeat_ratio,
        "chars_per_page": round(char_total / safe_pages, 1),
        "pages": pages,
    }


def score_of(metrics: Dict[str, Any], options: QualityOptions) -> int:
    """综合评分（0-100，仅用于展示；结论由硬阈值决定）。"""
    score = 100.0
    char_total = metrics["char_total"]

    if char_total < options.min_chars:
        score -= 40.0 * (1 - char_total / max(options.min_chars, 1))

    if options.min_readable_ratio > 0 and metrics["readable_ratio"] < options.min_readable_ratio:
        gap = 1 - metrics["readable_ratio"] / options.min_readable_ratio
        score -= min(30.0, 30.0 * gap)

    if options.max_garbled_ratio > 0 and metrics["garbled_ratio"] > options.max_garbled_ratio:
        over = metrics["garbled_ratio"] / options.max_garbled_ratio
        score -= min(40.0, 20.0 * over)

    if options.max_repeat_ratio > 0 and metrics["repeat_line_ratio"] > options.max_repeat_ratio:
        over = metrics["repeat_line_ratio"] / options.max_repeat_ratio
        score -= min(30.0, 15.0 * over)

    if (
        metrics["pages"] > 0
        and options.min_chars_per_page > 0
        and metrics["chars_per_page"] < options.min_chars_per_page
    ):
        score -= 25.0

    if char_total >= 2000:
        score += 5.0  # 信息量充足，轻微加分

    return int(max(0.0, min(100.0, score)))


def evaluate(
    text: str,
    *,
    route: str = "text",
    pages: int = 1,
    options: Optional[QualityOptions] = None,
    routes: Optional[Sequence[str]] = None,
) -> QualityReport:
    """对一个文档的解析文本做质检，返回 pass / needs_ocr / blocked。

    routes：允许「建议 OCR」的解析路由（默认 pdf/image，可由 QUALITY_OCR_ROUTES 配置）。
    """
    options = options or options_from_settings()
    metrics = compute_metrics(text, pages)
    score = score_of(metrics, options)

    # 删掉了「总开关关闭 → 直接 pass」的逃生分支 —— 质检是硬门，
    # 任何配置都改变不了"先把指标算出来、再按阈值判定"这件事。
    reasons: List[str] = []
    ocr_targets = tuple(routes) if routes is not None else OCR_ROUTES
    ocr_ok = route in ocr_targets

    def block(reason: str) -> QualityReport:
        return QualityReport(
            level=LEVEL_BLOCKED, score=score, reasons=reasons + [reason], metrics=metrics
        )

    def need_ocr(reason: str) -> QualityReport:
        return QualityReport(
            level=LEVEL_NEEDS_OCR, score=score, reasons=reasons + [reason], metrics=metrics
        )

    # 1/2：没有有效文字
    if metrics["char_total"] == 0:
        if ocr_ok:
            return need_ocr("没有可提取的文字：疑似扫描件或图片，需要 OCR 识别")
        return block("文档没有可提取的文字（可能是空文件或纯图片文档）")

    # 3：PDF/图片每页字符数过低 → 疑似扫描件
    if (
        ocr_ok
        and options.min_chars_per_page > 0
        and metrics["chars_per_page"] < options.min_chars_per_page
    ):
        return need_ocr(
            f"每页平均仅 {metrics['chars_per_page']} 个字符（低于 {options.min_chars_per_page}）："
            "疑似扫描件或图片型文档，建议 OCR 识别"
        )

    # 4：有效文字过少
    if metrics["char_total"] < options.min_chars:
        reasons.append(
            f"有效文字过少（{metrics['char_total']} 个字符，低于 {options.min_chars}）"
        )
        if ocr_ok:
            return need_ocr("文字过少，可能是扫描件：建议 OCR 重新识别")
        return block("内容过少，不足以构建知识库（请确认文件是否是空壳文档）")

    # 5：可读字符占比过低 → 乱码或非文本内容
    if metrics["readable_ratio"] < options.min_readable_ratio:
        return block(
            f"可读字符占比过低（{metrics['readable_ratio']:.0%}，"
            f"低于 {options.min_readable_ratio:.0%}）：疑似乱码或非文本内容"
        )

    # 6：乱码比例偏高
    if metrics["garbled_ratio"] > options.max_garbled_ratio:
        return block(
            f"乱码字符比例偏高（{metrics['garbled_ratio']:.1%}，"
            f"上限 {options.max_garbled_ratio:.0%}）：解析结果不可靠"
        )

    # 7：重复行过多
    if metrics["repeat_line_ratio"] > options.max_repeat_ratio:
        return block(
            f"重复行比例偏高（{metrics['repeat_line_ratio']:.0%}，"
            f"上限 {options.max_repeat_ratio:.0%}）：文档内容异常重复"
        )

    # 8：综合分兜底
    if score < options.pass_score:
        return block(f"综合质量分过低（{score} < {options.pass_score}），不建议入库")

    return QualityReport(level=LEVEL_PASS, score=score, reasons=[], metrics=metrics)


def describe(report: QualityReport) -> str:
    """把结论压成一行中文说明（用于日志与前端副标题）。"""
    label = {LEVEL_PASS: "通过质检", LEVEL_NEEDS_OCR: "需要 OCR", LEVEL_BLOCKED: "未通过质检"}
    text = f"{label.get(report.level, report.level)}（质量分 {report.score}）"
    if report.reasons:
        text += "：" + "；".join(report.reasons[:2])
    return text

