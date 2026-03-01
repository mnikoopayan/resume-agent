"""
Ingestion Module — Enhanced Dropbox Folder Monitor

Watches a local directory for new resume files and auto-ingests them
into the knowledge base with optional candidate profiling.
"""
from ingestion.dropbox_monitor import DropboxMonitor, DropboxFileHandler

__all__ = ["DropboxMonitor", "DropboxFileHandler"]
