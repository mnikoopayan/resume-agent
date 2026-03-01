"""
Enhanced Knowledge Base Setup Module

Initializes LanceDB vector store with Agno Knowledge Module.
Preserves the monkey-patch workaround for LanceDB compatibility
and adds graceful CohereReranker fallback.
"""
import logging
import os
import shutil
from typing import Optional

from agno.knowledge.knowledge import Knowledge
from agno.vectordb.lancedb import LanceDb
from agno.vectordb.search import SearchType

from knowledge.config import KnowledgeConfig

logger = logging.getLogger(__name__)


def create_embedder(config: KnowledgeConfig):
    """
    Create embedder using OpenRouter (OPENROUTER_API_KEY required).

    Ensure embedder output dimensions match what the vector DB expects; LanceDB
    creates the vector column from the embedder when the table is first created.
    Changing dimensions later requires a new table or migration.

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


def _cleanup_stale_table(config: KnowledgeConfig) -> None:
    """
    Delete an existing LanceDB table if its schema is incompatible
    with what Agno expects (e.g., missing 'payload' field).

    Args:
        config: KnowledgeConfig instance.
    """
    import lancedb

    lance_table_path = os.path.join(config.uri, f"{config.table_name}.lance")
    if not os.path.exists(lance_table_path):
        return

    try:
        db_temp = lancedb.connect(config.uri)
        table_temp = db_temp.open_table(config.table_name)
        schema = table_temp.schema
        field_names = [field.name for field in schema]
        if "payload" not in field_names:
            logger.warning(
                "Existing table '%s' has incorrect schema (missing 'payload'). "
                "Deleting to let Agno recreate with correct schema.",
                config.table_name,
            )
            del table_temp
            del db_temp
            if os.path.isdir(lance_table_path):
                shutil.rmtree(lance_table_path)
            elif os.path.exists(lance_table_path):
                os.remove(lance_table_path)
            logger.info("Deleted old table '%s'.", config.table_name)
    except Exception as e:
        logger.debug("Could not check table schema: %s", e)


def _patched_lancedb_connect(original_connect, uri):
    """
    Create a patched lancedb.connect that adds list_tables() method
    if the underlying connection does not have one.

    This is a workaround for Agno's LanceDB wrapper which expects
    list_tables() on the connection object.

    Args:
        original_connect: The original lancedb.connect function.
        uri: Database URI string.

    Returns:
        A wrapper function for lancedb.connect.
    """

    def patched_connect(connect_uri=None, api_key=None, uri=None, **kwargs):
        """Patched lancedb.connect compatible with both positional and keyword URI.

        Agno calls lancedb.connect(uri=...), while other code may call
        lancedb.connect(path). This wrapper accepts either style and then
        adds a list_tables() method when missing.
        """
        # Accept uri passed as keyword (Agno uses `uri=`) or legacy `connect_uri=`
        if connect_uri is None:
            connect_uri = uri or kwargs.pop("connect_uri", None) or kwargs.pop("uri", None)
        if connect_uri is None:
            raise TypeError("patched_connect() missing required argument: 'uri' or 'connect_uri'")
        if api_key:
            conn = original_connect(connect_uri, api_key=api_key, **kwargs)
        else:
            conn = original_connect(connect_uri, **kwargs)

        if not hasattr(conn, "list_tables"):

            def list_tables():
                """Mock list_tables method that returns table names."""

                class TableList:
                    def __init__(self, tables):
                        self.tables = tables

                try:
                    if os.path.exists(connect_uri):
                        tables = [
                            f.replace(".lance", "")
                            for f in os.listdir(connect_uri)
                            if f.endswith(".lance")
                        ]
                        return TableList(tables)
                except Exception:
                    pass
                return TableList([])

            conn.list_tables = list_tables

        return conn

    return patched_connect


def create_knowledge_base(config: Optional[KnowledgeConfig] = None) -> Knowledge:
    """
    Create and initialize Knowledge Base with LanceDB.

    Uses the monkey-patch workaround from the sample project for LanceDB
    compatibility, with enhanced error handling and optional CohereReranker.

    Args:
        config: KnowledgeConfig instance. Uses defaults if None.

    Returns:
        Initialized Knowledge instance.
    """
    if config is None:
        config = KnowledgeConfig()

    config.ensure_directories()

    # Create embedder
    embedder = create_embedder(config)

    # Clean up stale tables with wrong schema
    import lancedb

    _cleanup_stale_table(config)

    # Store original connect and apply monkey-patch
    original_connect = lancedb.connect
    lancedb.connect = _patched_lancedb_connect(original_connect, config.uri)

    try:
        # Build vector DB parameters
        vector_db_params = {
            "table_name": config.table_name,
            "uri": config.uri,
            "search_type": SearchType.vector,
        }

        # Add optional reranker
        reranker = _get_reranker()
        if reranker:
            vector_db_params["reranker"] = reranker

        # Add embedder
        if embedder:
            vector_db_params["embedder"] = embedder

        vector_db = LanceDb(**vector_db_params)
        logger.info(
            "LanceDB initialized: table=%s, uri=%s, reranker=%s",
            config.table_name,
            config.uri,
            "cohere" if reranker else "none",
        )
    finally:
        # Restore original connect function
        lancedb.connect = original_connect

    # Create Knowledge base
    knowledge_base = Knowledge(vector_db=vector_db)
    logger.info(
        "Knowledge base initialized: table=%s, uri=%s",
        config.table_name,
        config.uri,
    )

    return knowledge_base
