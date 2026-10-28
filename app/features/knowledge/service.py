"""Knowledge gateway factory."""

from __future__ import annotations

from app.adapters.lightrag.client import LightRagHttpClient
from app.features.knowledge.contracts import KnowledgeGateway


def get_knowledge_gateway() -> KnowledgeGateway:
    return LightRagHttpClient()


__all__ = ["get_knowledge_gateway"]