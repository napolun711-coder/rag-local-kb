"""Tier 1 同步快记忆（规则，不调用模型）。

目标：在用户消息落盘后、上下文装配前，用保守规则识别“值得长期保存”的
稳定陈述/明确记忆，并立即写入；保证“会话 A 说完 → 立刻新建会话 B”能召回。

边界：
- 只处理用户消息；助手消息永不进入这里；
- 明确证据（“记住/以后都/我的偏好是/我们决定”）→ active；
- 一般稳定陈述（“我的项目代号是 A3”）→ 高置信 active，弱规则 → pending；
- `secret` 不落库；`sensitive` 只能 pending，不能自动 active；
- 纠正（“不是 A3，是 B7”）优先于普通提取，唯一命中时原子 supersede；
- 任何异常只记类型，不阻断聊天。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.features.memory.memories import MemoryRepository
from app.features.memory.redact import is_secret_like

logger = logging.getLogger(__name__)

MAX_QUOTE = 500
MAX_CONTENT = 300

_EXPLICIT_RE = re.compile(r"^(?:请|帮我)?记住\s*[:：]?\s*(.+)$")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;，,\n]+")
#: 问句不只以问号结尾：中文里“我的项目代号是什么”“这个版本怎么理解”
#: 都不带问号，不能写成长久事实。
_QUESTION_RE = re.compile(
    r"[?？]|(?:吗|呢|么)\s*$|什么|怎么|怎样|如何|为什么|哪(?:个|些|里|儿)?|"
    r"谁|何时|多少|几[个号]|是否|能不能|可不可以|有没有|要不要|是不是"
)
_HYPOTHETICAL_RE = re.compile(r"如果|假设|假如|要是|也许|可能|或许|考虑一下|要不要")
#: 指令/提示词注入：不能被当作“偏好”永久保存。
_INJECTION_RE = re.compile(
    r"系统提示词|内部指令|开发者模式|忽略(?:之前|以上|先前)|越狱|"
    r"先输出.{0,30}再回答|以.{0,12}身份(?:回答|说话)|扮演|prompt\s*注入"
)
#: 否定句不能写成肯定事实；“我不喜欢/我习惯不要”这类偏好除外。
_NEGATION_RE = re.compile(r"不是|并非|不再|没有|别(?:用|给|说)|不要|不能|不允许|未|无需")
_TRIVIAL_RE = re.compile(
    r"^(?:你好|您好|谢谢|多谢|好的|好|嗯|哦|在吗|继续|开始|测试|收到|明白|ok|hi|hello)[。.!！?？\s]*$",
    re.IGNORECASE,
)
_TEMPORARY_RE = re.compile(
    r"今天|明天|后天|刚才|这次|待会|一会儿|下午|上午|晚上|提醒我|临时|"
    r"本周|下周|这个周末|先帮我"
)

_SENSITIVE_RE = re.compile(
    r"身份证|护照号|银行卡|信用卡|工资|薪水|薪酬|病史|病例|诊断|家庭住址|"
    r"手机号|电话号码|邮箱|生日|年龄|密码|口令"
)

_LANGUAGE_RE = re.compile(
    r"中文|英文|英语|日语|语言|回答用|用中文|用英文|说中文|说英文|称呼|叫我"
)
_PROJECT_CODE_RE = re.compile(r"项目代号|项目编号|代号")
_PROJECT_NAME_RE = re.compile(
    r"项目名称|项目名|项目叫|(?:我的|我们的)项目(?:是|叫|名称|名)"
)
_PORT_RE = re.compile(r"端口")
_VERSION_RE = re.compile(r"版本")
_TECH_RE = re.compile(r"技术栈|框架|架构|数据库|后端|前端")
_PREFERENCE_RE = re.compile(
    r"我喜欢|我不喜欢|我讨厌|我习惯|我的偏好|默认(?:用|要|给)|"
    r"以后都|以后不要|别再(?:用|给|说)"
)
_GOAL_RE = re.compile(r"我的目标|我计划|我打算|我正在做|我在做|长期目标|长期计划")
_DECISION_RE = re.compile(r"我们决定|我决定|已确定|确定用|采用|选型|定下来")
_PROFILE_RE = re.compile(
    r"我叫|我的名字|名字是|称呼我|我在.*工作|我住|我来自|职业|"
    r"我是做[^，,。；;!?！？]{1,16}|"
    r"我是(?:一名|一个)?(?:工程师|老师|学生|医生|设计师|程序员|产品经理|自由职业|退休)"
)
_CONSTRAINT_RE = re.compile(r"必须|不能|只能|不允许|禁止|限制")

_CORRECTION_RE = re.compile(
    r"不是\s*([^，,。；;！!？?\s]{1,40})\s*[，,；;]?\s*(?:而是|是)\s*(.{1,%d})"
    % MAX_CONTENT
)


@dataclass
class FastCandidate:
    content: str
    subject: str
    kind: str
    confidence: float
    source_type: str
    sensitivity: str
    quote: str
    scope: str = "global"
    topic_id: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FastResult:
    created: List[Dict[str, Any]] = field(default_factory=list)
    correction: bool = False
    ambiguous: bool = False
    skipped_secret: bool = False
    candidates: int = 0


def classify_sensitivity(text: str) -> str:
    if is_secret_like(text):
        return "secret"
    if _SENSITIVE_RE.search(str(text or "")):
        return "sensitive"
    return "normal"


def _clean(text: str) -> str:
    return " ".join(str(text or "").split()).strip()


def _sentences(text: str) -> List[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(str(text or ""))]
    return [part for part in parts if part][:6]


def _is_trivial(text: str) -> bool:
    clean = _clean(text)
    return len(clean) < 4 or bool(_TRIVIAL_RE.match(clean))


def _is_question(text: str) -> bool:
    return bool(_QUESTION_RE.search(str(text or "").strip()))


def _is_hypothetical(text: str) -> bool:
    return bool(_HYPOTHETICAL_RE.search(str(text or "")))


def _subject_and_kind(text: str, *, explicit: bool) -> Tuple[str, str, float]:
    """返回 (subject, kind, confidence)。"""
    if _LANGUAGE_RE.search(text):
        if "称呼" in text or "叫我" in text:
            return "称呼", "preference", 0.9
        return "回答语言", "preference", 0.9
    if _PROJECT_CODE_RE.search(text):
        return "项目代号", "fact", 0.9
    if _PROJECT_NAME_RE.search(text):
        return "项目名称", "fact", 0.9
    if _PORT_RE.search(text):
        return "端口", "fact", 0.9
    if _VERSION_RE.search(text):
        return "版本", "fact", 0.9
    if _TECH_RE.search(text):
        return "技术栈", "fact", 0.85
    if _GOAL_RE.search(text):
        return "长期目标", "goal", 0.85
    if _DECISION_RE.search(text):
        return "项目决定", "decision", 0.85
    if _CONSTRAINT_RE.search(text):
        return "约束", "constraint", 0.85
    if _PROFILE_RE.search(text):
        return "个人背景", "profile", 0.85
    if _PREFERENCE_RE.search(text):
        return "偏好", "preference", 0.85

    match = re.match(r"(?:我的?|我们的?)([^，,。；;：:]{1,20}?)(?:是|为|用|在|有)", text)
    subject = match.group(1).strip() if match else "对话事实"
    confidence = 0.6 if not explicit else 0.95
    return subject, "fact", confidence


def _candidate_from_sentence(
    sentence: str, *, explicit: bool, topic_id: Optional[str]
) -> Optional[FastCandidate]:
    raw_sentence = str(sentence or "").strip()
    text = _clean(raw_sentence)
    if not text or _is_trivial(text) or _is_question(text) or _is_hypothetical(text):
        return None
    if _INJECTION_RE.search(text):
        return None
    # 否定句（“项目代号不是 A3”）不是肯定事实；明确偏好（“我不喜欢…”）仍然保留。
    if _NEGATION_RE.search(text) and not _PREFERENCE_RE.search(text):
        return None
    # 临时任务/一次性提醒不是长期记忆；明确稳定线索（以后/默认/偏好/目标）除外。
    if _TEMPORARY_RE.search(text) and not re.search(
        r"以后|长期|默认|偏好|目标|计划", text
    ):
        return None

    marker = _EXPLICIT_RE.match(text)
    if marker:
        explicit = True
        content = _clean(marker.group(1))
    else:
        content = text
    if not content:
        return None

    sensitivity = classify_sensitivity(content)
    if sensitivity == "secret":
        return None
    if sensitivity != "normal" and not explicit:
        # 非明确的敏感信息不自动收集；明确“记住”时也只进待确认。
        return None

    subject, kind, confidence = _subject_and_kind(content, explicit=explicit)
    if explicit:
        confidence = 1.0
        source_type = "user_explicit"
    else:
        source_type = "user_stated"

    # 没有稳定线索的普通对话不落成记忆；弱规则进 pending。
    if not explicit and confidence < 0.75:
        return None
    if sensitivity != "normal":
        confidence = min(confidence, 0.6)
    scope = "global"
    topic = None
    if topic_id and kind in ("fact", "decision", "background", "constraint"):
        scope = "topic"
        topic = topic_id

    return FastCandidate(
        content=content[:MAX_CONTENT],
        subject=subject,
        kind=kind,
        confidence=confidence,
        source_type=source_type,
        sensitivity=sensitivity,
        quote=raw_sentence[:MAX_QUOTE],
        scope=scope,
        topic_id=topic,
    )


def has_explicit_memory_marker(text: str) -> bool:
    """供任务入队分级：明确记忆/偏好/目标/决定 → 高优先级立即提取。"""
    clean = _clean(text)
    if not clean:
        return False
    return bool(
        _EXPLICIT_RE.match(clean)
        or _PREFERENCE_RE.search(clean)
        or _GOAL_RE.search(clean)
        or _DECISION_RE.search(clean)
    )


def extract_candidates(
    text: str,
    *,
    topic_id: Optional[str] = None,
) -> List[FastCandidate]:
    found: List[FastCandidate] = []
    seen: set[str] = set()
    explicit = bool(_EXPLICIT_RE.match(_clean(text)))
    for sentence in _sentences(text):
        candidate = _candidate_from_sentence(
            sentence, explicit=explicit, topic_id=topic_id
        )
        if candidate is None:
            continue
        key = candidate.content.lower()
        if key in seen:
            continue
        seen.add(key)
        found.append(candidate)
    return found


def _scope_keys(topic_id: Optional[str]) -> List[str]:
    keys = ["global"]
    if topic_id:
        keys.append(f"topic:{topic_id}")
    return keys


def _apply_correction(
    store: MemoryRepository,
    text: str,
    *,
    message_id: str,
    session_id: str,
    topic_id: Optional[str],
    result: FastResult,
    source_text: Optional[str] = None,
) -> bool:
    match = _CORRECTION_RE.search(str(text or ""))
    if not match:
        return False
    old_value = match.group(1).strip()
    new_value = match.group(2).strip().rstrip("。！？!?；;，,")
    if not old_value or not new_value:
        return False
    if classify_sensitivity(text) != "normal":
        return False
    quote_text = str(source_text if source_text is not None else text or "")

    matches = store.find_active_containing(
        old_value, scope_keys=_scope_keys(topic_id), limit=10
    )
    if len(matches) == 1:
        old = matches[0]
        new_content = (
            old["content"].replace(old_value, new_value)
            if old_value in old["content"]
            else f"{old['subject']}：{new_value}"
        )
        created = store.replace_active(
            old["id"],
            content=new_content,
            subject=old["subject"],
            kind=old["kind"],
            scope=old["scope"],
            topic_id=old["topic_id"],
            source={
                "message_id": message_id,
                "session_id": session_id,
                "quote": quote_text[:MAX_QUOTE],
            },
            reason="用户明确纠正",
            actor="user",
        )
        result.created.append(created)
        result.correction = True
        return True
    if len(matches) > 1:
        result.ambiguous = True
        pending = store.create(
            content=str(text or "")[:MAX_CONTENT],
            subject="用户纠正",
            kind="fact",
            scope="global",
            source_type="user_stated",
            sensitivity="normal",
            confidence=0.5,
            importance=3,
            status="pending",
            meta={"correction_ambiguous": [item["id"] for item in matches]},
            source={
                "message_id": message_id,
                "session_id": session_id,
                "quote": quote_text[:MAX_QUOTE],
            },
            actor="system",
        )
        result.created.append(pending)
        return True
    return False


def apply(
    store: MemoryRepository,
    text: str,
    *,
    message_id: str,
    session_id: str,
    topic_id: Optional[str] = None,
) -> FastResult:
    """对一条用户消息执行 Tier 1；失败只记日志，不抛异常。"""
    result = FastResult()
    clean = _clean(text)
    if not clean or _is_trivial(clean) or _is_question(clean) or _is_hypothetical(clean):
        return result
    if _INJECTION_RE.search(clean):
        return result
    sensitivity = classify_sensitivity(clean)
    if sensitivity == "secret":
        result.skipped_secret = True
        return result

    try:
        if _apply_correction(
            store,
            clean,
            message_id=message_id,
            session_id=session_id,
            topic_id=topic_id,
            result=result,
            source_text=text,
        ):
            return result
    except Exception as exc:  # noqa: BLE001 - 纠正失败不能阻断聊天
        logger.warning("Tier 1 纠正失败（忽略）：%s", type(exc).__name__)

    candidates = extract_candidates(text, topic_id=topic_id)
    result.candidates = len(candidates)
    for candidate in candidates:
        try:
            status = (
                "active"
                if candidate.confidence >= 0.8 and candidate.sensitivity == "normal"
                else "pending"
            )
            created = store.create(
                content=candidate.content,
                subject=candidate.subject,
                kind=candidate.kind,
                scope=candidate.scope,
                topic_id=candidate.topic_id,
                source_type=candidate.source_type,
                sensitivity=candidate.sensitivity,
                confidence=candidate.confidence,
                importance=4 if candidate.source_type == "user_explicit" else 3,
                status=status,
                meta=candidate.meta,
                source={
                    "message_id": message_id,
                    "session_id": session_id,
                    "quote": candidate.quote,
                },
                actor="user" if candidate.source_type == "user_explicit" else "system",
            )
            if created.get("status") in ("active", "pending", "conflicted"):
                result.created.append(created)
        except Exception as exc:  # noqa: BLE001 - 单条候选失败不影响其它候选
            logger.warning("Tier 1 候选写入失败（忽略）：%s", type(exc).__name__)
    return result


__all__ = [
    "FastCandidate",
    "FastResult",
    "apply",
    "classify_sensitivity",
    "extract_candidates",
    "has_explicit_memory_marker",
]
