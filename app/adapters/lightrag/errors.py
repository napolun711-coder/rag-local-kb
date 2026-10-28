"""Errors raised by the LightRAG adapter."""

from __future__ import annotations


class KnowledgeError(Exception):
    pass


class KnowledgeUnavailable(KnowledgeError):
    pass


class KnowledgeEngineNotInstalled(KnowledgeError):
    pass


class KnowledgeHTTPError(KnowledgeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


__all__ = [
    "KnowledgeEngineNotInstalled",
    "KnowledgeError",
    "KnowledgeHTTPError",
    "KnowledgeUnavailable",
]