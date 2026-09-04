"""Ensure WIKI_DIR is set before wiki_mcp.storage is first imported at
collection time — each test then reloads storage against its own tmp_path
via the `wiki_root` fixture in test_storage.py."""

import os
import tempfile

os.environ.setdefault("WIKI_DIR", tempfile.mkdtemp())
