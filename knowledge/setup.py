"""
Knowledge Base Setup Module

Initializes ChromaDB vector store with Agno Knowledge Module.
"""
import logging
import os
from typing import Optional

from agno.knowledge.knowledge import Knowledge
from agno.vectordb.chroma import ChromaDb
from agno.vectordb.search import SearchType

from knowledge.config import KnowledgeConfig

logger = logging.getLogger(__name__)


def create_embedder(config: KnowledgeConfig):
    """
    Create embedder using OpenRouter (OPENROUTER_API_KEY required).

    Args:
        config: KnowledgeConfig instance.

    Returns:
        OpenAIEmbedder instance or None if creation fails.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning(
            "OPENROUTER_API_KEY not set; embeddings will fail until it is set."
        )
        return None

    try:
        from agno.knowledge.embedder.openai import OpenAIEmbedder

        model_id = config.openrouter_model or "text-embedding-3-small"
        if "/" in model_id:
            model_id = model_id.split("/")[-1]

        embedder = OpenAIEmbedder(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            id=model_id,
            dimensions=1536,
        )
        logger.info("Using OpenRouter embedder: %s (dimensions=1536)", model_id)
        return embedder
    except Exception as e:
        logger.warning("Failed to create OpenRouter embedder: %s", e)
        return None


def _get_reranker():
    """
    Attempt to create a CohereReranker. Falls back gracefully if
    the COHERE_API_KEY is not set or the library is unavailable.

    Returns:
        CohereReranker instance or None.
    """
    try:
        from agno.knowledge.reranker.cohere import CohereReranker

        cohere_key = os.getenv("COHERE_API_KEY")
        if cohere_key:
            reranker = CohereReranker()
            logger.info("CohereReranker enabled.")
            return reranker
        else:
            logger.info(
                "COHERE_API_KEY not set; CohereReranker disabled (optional)."
            )
            return None
    except ImportError:
        logger.info("CohereReranker not available; skipping (optional).")
        return None
    except Exception as e:
        logger.warning("CohereReranker initialization failed: %s", e)
        return None


def create_knowledge_base(config: Optional[KnowledgeConfig] = None) -> Knowledge:
    """
    Create and initialize Knowledge Base with ChromaDB.

    Args:
        config: KnowledgeConfig instance. Uses defaults if None.

    Returns:
        Initialized Knowledge instance.
    """
    if config is None:
        config = KnowledgeConfig()

    config.ensure_directories()

    embedder = create_embedder(config)
    reranker = _get_reranker()

    vector_db_params = {
        "collection": config.collection,
        "path": config.path,
        "persistent_client": True,
        "search_type": SearchType.vector,
    }

    if reranker:
        vector_db_params["reranker"] = reranker

    if embedder:
        vector_db_params["embedder"] = embedder

    vector_db = ChromaDb(**vector_db_params)
    logger.info(
        "ChromaDB initialized: collection=%s, path=%s, reranker=%s",
        config.collection,
        config.path,
        "cohere" if reranker else "none",
    )

    knowledge_base = Knowledge(vector_db=vector_db)
    logger.info(
        "Knowledge base initialized: collection=%s, path=%s",
        config.collection,
        config.path,
    )

    return knowledge_base
