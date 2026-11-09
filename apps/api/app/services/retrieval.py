"""Hybrid retrieval, strictly scoped to one tenant and one assistant.

Vector similarity and PostgreSQL full-text ranking are combined in a single
query. Both the ``org_id`` predicate and the forced RLS policy apply, so even a
bug in this SQL cannot return another client's chunks. Weak matches are dropped
rather than passed to the model, which is what makes an honest refusal possible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.ai import AIProvider


@dataclass(slots=True)
class RetrievedChunk:
    id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    chunk_index: int
    page_number: int | None
    content: str
    score: float
    vector_score: float
    keyword_score: float


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


async def search(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    assistant_id: uuid.UUID,
    query: str,
    ai: AIProvider,
    top_k: int | None = None,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    settings = get_settings()
    limit = min(max(int(top_k or settings.retrieval_top_k), 1), 50)
    threshold = settings.retrieval_min_score if min_score is None else min_score
    threshold = min(max(float(threshold), 0.0), 1.0)

    query = query.strip()
    if not query:
        return []

    embeddings = await ai.embed([query])
    if not embeddings:
        return []
    embedding = vector_literal(embeddings[0])

    rows = (
        await session.execute(
            text(
                """
                WITH candidates AS (
                    SELECT
                        c.id,
                        c.document_id,
                        c.chunk_index,
                        c.content,
                        c.page_number,
                        d.title AS document_title,
                        (1 - (c.embedding <=> CAST(:embedding AS vector))) AS vector_score,
                        ts_rank_cd(c.tsv, plainto_tsquery('simple', :query)) AS keyword_score
                    FROM document_chunks c
                    JOIN documents d ON d.id = c.document_id
                    WHERE c.org_id = :org_id
                      AND c.assistant_id = :assistant_id
                      AND d.status = 'ready'
                      AND d.embedding_model = :embedding_model
                      AND d.embedding_dimensions = :embedding_dimensions
                )
                SELECT
                    id,
                    document_id,
                    document_title,
                    chunk_index,
                    page_number,
                    content,
                    vector_score,
                    keyword_score,
                    (0.75 * vector_score + 0.25 * LEAST(keyword_score, 1.0)) AS score
                FROM candidates
                WHERE vector_score >= :min_score OR keyword_score > 0
                ORDER BY score DESC, vector_score DESC, chunk_index ASC
                LIMIT :limit
                """
            ),
            {
                "embedding": embedding,
                "query": query,
                "org_id": str(org_id),
                "assistant_id": str(assistant_id),
                "embedding_model": ai.embedding_model,
                "embedding_dimensions": ai.dimensions,
                "min_score": threshold,
                "limit": limit,
            },
        )
    ).mappings()

    return [
        RetrievedChunk(
            id=row["id"],
            document_id=row["document_id"],
            document_title=row["document_title"],
            chunk_index=row["chunk_index"],
            page_number=row["page_number"],
            content=row["content"],
            score=float(row["score"]),
            vector_score=float(row["vector_score"]),
            keyword_score=float(row["keyword_score"]),
        )
        for row in rows
    ]
