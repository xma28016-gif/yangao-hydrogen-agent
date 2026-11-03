"""Isolated parser. The child stdout is private IPC, never a request log."""

import json
import sys
import zipfile
from pathlib import Path


def validate_container(path, extension):
    with path.open("rb") as f:
        prefix = f.read(1024)
    if extension == ".pdf":
        if not prefix.startswith(b"%PDF-"):
            raise ValueError("INVALID_FILE")
    elif extension == ".docx":
        if not prefix.startswith(b"PK"):
            raise ValueError("INVALID_FILE")
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if not {"[Content_Types].xml", "word/document.xml"} <= names:
                raise ValueError("INVALID_FILE")
            if len(names) > 5000 or any(i.flag_bits & 1 for i in z.infolist()):
                raise ValueError("DOCUMENT_LIMIT")
            if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                raise ValueError("DOCUMENT_LIMIT")
            if any(i.file_size > 1024 * 1024 and i.file_size / max(i.compress_size, 1) > 200 for i in z.infolist()):
                raise ValueError("DOCUMENT_LIMIT")
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                raise ValueError("INVALID_FILE")
    elif extension in {".md", ".txt"}:
        if b"\x00" in prefix or prefix.startswith((b"%PDF-", b"PK", b"\x89PNG")):
            raise ValueError("INVALID_FILE")
    else:
        raise ValueError("INVALID_FILE")


def parse(path, extension):
    validate_container(path, extension)
    if extension == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted or len(reader.pages) > 1000:
            raise ValueError("DOCUMENT_LIMIT")
        pieces, size = [], 0
        for page in reader.pages:
            part = page.extract_text() or ""
            size += len(part)
            if size > 2_000_000:
                raise ValueError("DOCUMENT_LIMIT")
            pieces.append(part)
        content = "\n".join(pieces)
        no_text = len("".join(content.split())) / max(len(reader.pages), 1) < 50
    elif extension == ".docx":
        from docx import Document

        document = Document(path)
        pieces = [p.text for p in document.paragraphs]
        pieces.extend(" ".join(cell.text for cell in row.cells) for table in document.tables for row in table.rows)
        content = "\n".join(pieces)
        no_text = not content.strip()
    else:
        content = path.read_text(encoding="utf-8-sig")
        if "\x00" in content or any(ord(c) < 32 and c not in "\n\r\t" for c in content):
            raise ValueError("INVALID_FILE")
        no_text = not content.strip()
    if len(content) > 2_000_000:
        raise ValueError("DOCUMENT_LIMIT")
    return {"text": content, "no_text": no_text}


if __name__ == "__main__":
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        print(json.dumps(parse(Path(sys.argv[1]), sys.argv[2]), ensure_ascii=False))
    except Exception:
        # Never echo document content, parser exception text or filesystem paths.
        print(json.dumps({"error": "PARSE_FAILED"}))
        sys.exit(1)
