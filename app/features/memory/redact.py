"""持久化前的保守凭据脱敏。

原则：
- 只处理“看起来像凭据”的片段，不删除正常正文；
- 识别到就替换为固定占位符，并返回规则名（供 meta_json 记录）；
- **不改发给模型的内容**：用户已经输入的内容仍在本轮发送；这里只影响落盘副本；
- 不记录原文到日志。
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

REPLACEMENT = "[已隐藏的凭据]"

#: 顺序有意义：先处理私钥块，再处理独立 token/赋值形式。
_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
    ),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|"
            r"password|passwd|pwd)\s*[:=：]\s*([^\s,，;；]{6,})"
        ),
    ),
    # 中文 secret：密码/验证码不落库（不能只靠“敏感”留在 pending）。
    (
        "cn_secret",
        re.compile(
            r"(密码|口令|验证码|初始密码|登录密码)"
            r"\s*(?:是|为|：|:)\s*([^\s,，。；;]{4,})"
        ),
    ),
    # 中文 PII：身份证/银行卡/手机号先脱敏；它们仍是 sensitive（可 pending），不是 secret。
    (
        "cn_pii",
        re.compile(
            r"(身份证号?|银行卡号?|手机号|手机号码)"
            r"\s*(?:是|为|：|:)\s*([^\s,，。；;]{4,})"
        ),
    ),
)


def redact_text(text: str) -> Tuple[str, Dict[str, object]]:
    """返回 `(脱敏后的文本, 标记)`；标记形如 `{"redacted": True, "types": ["api_key"]}`。"""
    value = str(text or "")
    found: List[str] = []

    for name, pattern in _PATTERNS:
        def _replace(match: re.Match[str], _name: str = name) -> str:
            found.append(_name)
            if _name in ("assignment", "cn_secret", "cn_pii"):
                return f"{match.group(1)}={REPLACEMENT}"
            return REPLACEMENT

        value = pattern.sub(_replace, value)

    return value, {
        "redacted": bool(found),
        "types": sorted(set(found)),
    }


def is_secret_like(text: str) -> bool:
    """供记忆提取模块复用的快速判断。

    只有真正的凭据（密码/Token/私钥）算 secret；身份证/银行卡/手机号只是 PII，
    归为 sensitive（可 pending），不能因为脱敏就一律丢弃。
    """
    _, mark = redact_text(text)
    types = [str(item) for item in (mark.get("types") or [])]
    return any(item != "cn_pii" for item in types)


__all__ = ["REPLACEMENT", "is_secret_like", "redact_text"]
