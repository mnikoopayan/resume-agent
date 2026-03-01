"""
Knowledge Module — Configuration and Setup for LanceDB Knowledge Base
"""
from knowledge.config import KnowledgeConfig
from knowledge.setup import create_knowledge_base, create_embedder

__all__ = ["KnowledgeConfig", "create_knowledge_base", "create_embedder"]
