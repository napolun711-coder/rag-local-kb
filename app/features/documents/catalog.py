"""Merge LightRAG document records with local ingestion jobs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.features.documents.contracts import DocumentView, LocalJobSummary
from app.features.ingest import storage


def _name(value: Any) -> str:
    return Path(str(value or "").strip()).name.casefold()


def _knowledge(result: Dict[str, Any]) -> Dict[str, Any]:
    value = result.get("knowledge")
    return value if isinstance(value, dict) else {}


def _track_id(result: Dict[str, Any]) -> str:
    knowledge = _knowledge(result)
    response = knowledge.get("response") if isinstance(knowledge.get("response"), dict) else {}
    return str(
        knowledge.get("track_id")
        or (result.get("lightrag") or {}).get("track_id")
        or response.get("track_id")
        or ""
    )


def _document_ids(result: Dict[str, Any]) -> Tuple[str, ...]:
    knowledge = _knowledge(result)
    last = knowledge.get("last_status") if isinstance(knowledge.get("last_status"), dict) else {}
    documents = last.get("documents") if isinstance(last.get("documents"), list) else []
    values = []
    for item in documents:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            values.append(str(item["id"]).strip())
    return tuple(values)


def _local_job(result: Dict[str, Any]) -> LocalJobSummary:
    knowledge = _knowledge(result)
    lightrag = result.get("lightrag") if isinstance(result.get("lightrag"), dict) else {}
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    source_path = str(result.get("source_path") or "")
    source_available = False
    if source_path:
        try:
            source_available = storage.resolve_display_path(source_path).is_file()
        except Exception:
            source_available = False
    score = quality.get("score")
    return LocalJobSummary(
        file_id=str(result.get("file_id") or ""),
        filename=str(result.get("filename") or ""),
        doc_name=str(result.get("doc_name") or knowledge.get("document_name") or ""),
        state=str(result.get("state") or ""),
        quality_level=str(quality.get("level") or ""),
        quality_score=int(score) if isinstance(score, (int, float)) else None,
        source_path=source_path,
        saved_path=str(result.get("saved_path") or ""),
        track_id=_track_id(result),
        knowledge_state=str(knowledge.get("state") or ""),
        updated_at=str(result.get("updated_at") or ""),
        source_available=source_available,
        document_ids=_document_ids(result),
        detail=str(result.get("detail") or ""),
        knowledge_message=str(knowledge.get("message") or lightrag.get("error") or ""),
        batch_id=str(result.get("batch_id") or ""),
        document_name=str(knowledge.get("document_name") or ""),
    )


class LocalJobIndex:
    def __init__(self, jobs: Iterable[LocalJobSummary]) -> None:
        self.jobs = tuple(jobs)
        self.by_track: Dict[str, LocalJobSummary] = {}
        self.by_document_id: Dict[str, LocalJobSummary] = {}
        self.by_name: Dict[str, List[LocalJobSummary]] = {}
        for job in self.jobs:
            if job.track_id and job.track_id not in self.by_track:
                self.by_track[job.track_id] = job
            for document_id in job.document_ids:
                if document_id and document_id not in self.by_document_id:
                    self.by_document_id[document_id] = job
            names = [job.doc_name, job.document_name, job.filename, job.saved_path, job.source_path]
            if job.doc_name and not job.document_name:
                names.append(job.doc_name + ".md")
            for value in names:
                key = _name(value)
                if key:
                    self.by_name.setdefault(key, []).append(job)
        self.matched_file_ids: set[str] = set()

    def match(self, document: Dict[str, Any]) -> Tuple[Optional[LocalJobSummary], str]:
        track_id = str(document.get("track_id") or "").strip()
        if track_id and track_id in self.by_track:
            job = self.by_track[track_id]
            self.matched_file_ids.add(job.file_id)
            return job, "track_id"
        doc_id = str(document.get("id") or "").strip()
        if doc_id and doc_id in self.by_document_id:
            job = self.by_document_id[doc_id]
            self.matched_file_ids.add(job.file_id)
            return job, "document_id"
        metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
        for value in (
            document.get("file_path"),
            document.get("name"),
            metadata.get("source_file"),
        ):
            key = _name(value)
            candidates = self.by_name.get(key, []) if key else []
            unique = {job.file_id: job for job in candidates}
            if len(unique) == 1:
                job = next(iter(unique.values()))
                self.matched_file_ids.add(job.file_id)
                return job, "name"
        return None, ""

    def local_only(self) -> Tuple[LocalJobSummary, ...]:
        return tuple(job for job in self.jobs if job.file_id not in self.matched_file_ids)


def build_local_index(results: Iterable[Dict[str, Any]]) -> LocalJobIndex:
    return LocalJobIndex(_local_job(result) for result in results if isinstance(result, dict))


def merge_documents(
    documents: Sequence[Dict[str, Any]],
    index: LocalJobIndex,
) -> Tuple[DocumentView, ...]:
    merged: List[DocumentView] = []
    for document in documents:
        if not isinstance(document, dict):
            continue
        job, reason = index.match(document)
        metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
        file_path = str(document.get("file_path") or metadata.get("source_file") or "")
        name = Path(file_path).name if file_path else str(document.get("id") or "")
        chunks = document.get("chunks_count")
        merged.append(
            DocumentView(
                doc_id=str(document.get("id") or ""),
                name=name,
                file_path=file_path,
                status=str(document.get("status") or ""),
                chunks_count=int(chunks) if isinstance(chunks, (int, float)) else None,
                track_id=str(document.get("track_id") or ""),
                created_at=str(document.get("created_at") or ""),
                updated_at=str(document.get("updated_at") or ""),
                content_summary=str(document.get("content_summary") or ""),
                error=str(document.get("error_msg") or document.get("error") or ""),
                local_job=job,
                local_source_available=bool(job and job.source_available),
                match_reason=reason,
            )
        )
    return tuple(merged)


__all__ = ["LocalJobIndex", "build_local_index", "merge_documents"]