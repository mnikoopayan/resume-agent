# Dropbox Folder Monitor — Usage Guide

The Enhanced Resume Agent includes a file system monitor that watches a local "dropbox" directory for new resume files. When a file is added, it is automatically ingested into the LanceDB knowledge base and optionally creates a candidate profile in the pipeline database.

## How It Works

The monitor uses the `watchdog` library to observe file system events in real time. When a new file is detected (or an existing file is modified), the system performs the following steps:

1. **Extension Check** — Verifies the file has a supported extension (.pdf, .txt, .docx, .md).
2. **Deduplication** — Computes a SHA-256 hash of the file and checks against a SQLite state database to avoid re-ingesting identical content.
3. **Knowledge Ingestion** — Extracts text content from the file and inserts it as document chunks into the LanceDB vector store.
4. **Candidate Creation** — If enabled, creates a new candidate profile in the pipeline database with the filename as the candidate name.
5. **State Recording** — Records the file hash, path, timestamp, and status in the state database for future deduplication.

## Supported File Formats

| Format | Extension | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Extracted via PyPDF2; supports multi-page documents |
| Plain Text | `.txt` | Direct text reading with UTF-8 encoding |
| Word Document | `.docx` | Extracted via python-docx; preserves paragraph structure |
| Markdown | `.md` | Direct text reading |

## Starting the Monitor

```bash
# Start the dropbox monitor
python main.py monitor
```

The monitor will first scan and ingest any existing files in the dropbox directory, then begin watching for new files. Press Ctrl+C to stop.

## Configuration

The dropbox path and behavior can be configured via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DROPBOX_PATH` | `./dropbox` | Directory to watch for new files |

The monitor also supports recursive subdirectory watching, so you can organize resumes into subfolders (e.g., `dropbox/engineering/`, `dropbox/marketing/`).

## Batch Upload

For uploading a large number of existing files at once, use the batch upload script instead of the monitor:

```bash
# Upload all files from a directory
python batch_upload.py ./resumes/

# With candidate auto-creation and recursive scanning
python batch_upload.py ./resumes/ --create-candidates --recursive
```

## State Database

The monitor maintains a SQLite database at `./knowledge/dropbox_state.db` that tracks all ingested files. This ensures that files are not re-ingested if the monitor is restarted. The database stores the file hash, path, filename, ingestion timestamp, associated candidate ID, and processing status.

## Integration with Gmail

Resume attachments received via Gmail can also be automatically ingested. The Gmail sync service downloads attachments to the `./dropbox/gmail/` subdirectory, where the monitor (if running) will pick them up. Alternatively, run `python main.py gmail_sync` to process Gmail attachments directly.
