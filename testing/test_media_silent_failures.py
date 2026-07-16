"""T-255: media silent-failure audit.

analyze_document_with_vision's PDF vision-analysis block used to swallow
failures with a bare pass, silently returning vision_analysis="" while still
reporting overall success — the caller never learns the vision step failed.
"""
import os
import sys
import sqlite3
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.tools_media as media


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    import agent.observability as obs
    test_db = tmp_path / "silent_failures.db"
    with (
        patch.object(obs, "_DB_PATH", test_db),
        patch.object(obs, "_conn", None),
        patch.object(obs, "_insert_count", 0),
    ):
        yield test_db
    obs._conn = None


def test_pdf_vision_failure_is_tracked(tmp_path, fresh_db):
    pdf_path = tmp_path / "doc.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    with patch.object(media, "_PIL_OK", True), \
         patch.object(media, "_PDF_OK", True), \
         patch.object(media.MediaTools, "read_document",
                       return_value={"success": True, "text": "hello"}), \
         patch("pdfplumber.open", side_effect=RuntimeError("corrupt pdf")):
        result = media.MediaTools.analyze_document_with_vision(str(pdf_path))

    # Must not raise, and vision_analysis degrades to empty as before.
    assert result.get("vision_analysis", "") == ""

    conn = sqlite3.connect(str(fresh_db))
    rows = conn.execute(
        "SELECT category, exception_type FROM silent_failures"
    ).fetchall()
    conn.close()
    assert rows == [("media.analyze_document_with_vision_pdf", "RuntimeError")]


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    import agent.observability as obs
    with tempfile.TemporaryDirectory() as d:
        test_db = Path(d) / "silent_failures.db"
        obs._DB_PATH = test_db
        obs._conn = None
        obs._insert_count = 0
        test_pdf_vision_failure_is_tracked(Path(d), test_db)
    print("OK")
