"""Knowledge-base helpers for design-document RAG."""

from .pdf_rag import (
    ClipTextEmbedderAdapter,
    KnowledgeBuildArtifacts,
    KnowledgeChunk,
    KnowledgeSearchHit,
    PdfKnowledgeBaseBuilder,
    PdfKnowledgeBaseRetriever,
    SentenceTransformerEmbedder,
    build_pdf_knowledge_base,
)

__all__ = [
    "KnowledgeBuildArtifacts",
    "KnowledgeChunk",
    "KnowledgeSearchHit",
    "ClipTextEmbedderAdapter",
    "PdfKnowledgeBaseBuilder",
    "PdfKnowledgeBaseRetriever",
    "SentenceTransformerEmbedder",
    "build_pdf_knowledge_base",
]
