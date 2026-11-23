#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sk_entry.py - deterministic cross-file passage retrieval and locator.

Standard library only. No network. No random numbers, no timestamps.

CLI:
    python3 scripts/sk_entry.py --input /input --work /app/work --out /app/output

`--input`  may be a directory (case.json + materials) or a case.json file.
`--out`    may be a directory (result.json is written inside) or a .json file.

The program always writes a contract-complete result.json and exits 0, even
when the input is empty, unreadable or malformed.
"""

import argparse
import bisect
import codecs
import fnmatch
import hashlib
import html
import json
import math
import os
import re
import sys
import zlib
from collections import Counter
from pathlib import Path

VERSION = "1.0.0"

TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".csv", ".tsv",
    ".xml", ".html", ".htm", ".tex", ".yaml", ".yml", ".rst", ".log",
}
PDF_EXTS = {".pdf"}
SKIP_DIRS = {
    "__pycache__", ".git", ".svn", ".hg", "node_modules", ".venv",
    "venv", ".cache", ".idea", ".vscode", ".mypy_cache", ".pytest_cache",
}

DEFAULT_POLICY = {
    "version": "1.0.0",
    "scoring": {
        "k1": 1.2,
        "b": 0.75,
        "min_score": 0.45,
        "top_k": 8,
        "cjk_bigram_weight": 1.8,
        "cjk_unigram_weight": 0.45,
        "ascii_weight": 1.0,
        "numeric_weight": 0.8,
        "phrase_boost": 1.5,
        "section_boost": 0.8,
        "filename_boost": 0.3,
        "max_section_boost_terms": 3,
    },
    "caps": {
        "max_files": 2000,
        "max_file_bytes": 4000000,
        "max_text_chars": 2000000,
        "max_total_chars": 20000000,
        "max_passages": 200000,
        "max_queries": 20,
        "passage_chars": 900,
        "quote_chars": 320,
        "context_chars": 80,
    },
}

QUERY_TEXT_KEYS = (
    "query", "text", "question", "prompt", "search", "query_text",
    "question_text", "q",
)

EN_STOP = set(
    "a an the and or but if then else of in on at to for from by with as is "
    "are was were be been being this that these those it its we you they he "
    "she i not no do does did what which who whom when where why how".split()
)
CJK_STOP = set("的了在是和与及中对为等请找出说明什么哪些如何是否以及这个那个我们可以把被从向于其该此这那")

ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*")
CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
CAPTION_RE = re.compile(r"(?i)^(table|figure|fig\.?|图|表)\s*\d+")
AUTHOR_RE = re.compile(r"(?im)^\s*(?:authors?|作者|撰写|written by)\s*[:：]\s*(.{2,200})$")

HEADING_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
HEADING_NUM = re.compile(r"^(?:section\s+)?(\d+(?:\.\d+){0,3})[.)]?\s+(\S.{0,90})$", re.I)
HEADING_CN = re.compile(r"^([一二三四五六七八九十]+)[、.．]\s*(\S.{0,90})$")
HEADING_WORD = re.compile(
    r"^(abstract|introduction|background|related work|materials? and methods|"
    r"methods?|experimental(?: section)?|results?(?: and discussion)?|discussion|"
    r"conclusions?|references|acknowledg(?:e)?ments?|appendix|supplementary|"
    r"摘要|引言|背景|方法|材料与方法|结果|讨论|结论|参考文献|附录|补充材料)\s*$",
    re.I,
)

SECTION_KEYS = {"section", "sections", "heading", "headings", "chapter", "章节"}

SUPPORTED_FILTER_KEYS = {
    "ext", "extension", "file_type", "format", "type", "year", "years", "date",
    "author", "authors", "project", "project_id", "batch", "version", "path",
    "glob", "file", "filename", "name",
}

EXT_ALIASES = {
    "text": {".txt", ".md", ".markdown", ".rst", ".log", ".tex"},
    "document": {".pdf", ".txt", ".md", ".markdown", ".html", ".htm", ".xml", ".rst"},
    "pdf": {".pdf"},
    "markdown": {".md", ".markdown"},
    "md": {".md", ".markdown"},
    "html": {".html", ".htm"},
    "htm": {".html", ".htm"},
    "data": {".csv", ".tsv", ".json", ".jsonl", ".xml"},
    "json": {".json", ".jsonl"},
    "csv": {".csv", ".tsv"},
    "xml": {".xml"},
    "txt": {".txt"},
    "tex": {".tex"},
    "latex": {".tex"},
    "log": {".log"},
}

# Contract-key alias table: canonical output key -> accepted contract key names.
CANONICAL_ALIASES = {
    "hits": ("hits", "results", "matches", "citations", "retrievals", "passages",
             "passages_found", "search_results", "locators", "result_items",
             "retrieved", "retrieval_results"),
    "evidence": ("evidence", "sources", "source", "refs", "references",
                 "provenance", "evidence_links", "backlinks", "source_evidence"),
    "misses": ("misses", "missed", "uncovered", "gaps", "not_found", "missing",
               "missed_queries", "empty_queries"),
    "index_stats": ("index_stats", "index", "state", "inventory", "corpus",
                    "file_index", "files_indexed", "index_summary", "index_state"),
    "uncertainty": ("uncertainty", "notes", "note", "limitations", "caveats",
                    "risks", "assumptions"),
    "coverage": ("coverage", "query_coverage", "completeness", "query_coverage_detail"),
    "queries": ("queries", "questions", "question_list", "query_list", "search_queries"),
    "run_log": ("run_log", "log", "logs", "changes", "change_log", "operations",
                "steps", "changelog"),
    "status": ("status", "run_status", "run_state"),
    "summary": ("summary", "answer", "conclusion", "brief", "digest", "short_summary"),
    "case_id": ("case_id", "caseid", "id"),
    "skipped": ("skipped", "skipped_files", "unreadable", "skipped_items"),
    "duplicates": ("duplicates", "duplicate_files", "copies"),
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def merge_dict(base, extra):
    out = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def load_policy():
    path = Path(__file__).resolve().parent.parent / "assets" / "policy.json"
    data = {}
    try:
        with open(str(path), "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            data = loaded
    except (OSError, ValueError):
        data = {}
    return merge_dict(DEFAULT_POLICY, data), bool(data)


def read_text_any(path):
    data = Path(path).read_bytes()
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def read_json_file(path):
    text, _enc = read_text_any(path)
    return json.loads(text)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def resolve_out(raw):
    path = Path(str(raw))
    if path.suffix.lower() == ".json":
        return path, path.parent
    if path.exists() and path.is_file():
        return path, path.parent
    return path / "result.json", path


def resolve_input(raw):
    path = Path(str(raw))
    if path.is_file():
        if path.name.lower().endswith(".json"):
            return path, path.parent
        return path.parent / "case.json", path.parent
    return path / "case.json", path


def contract_fields(case):
    oc = case.get("output_contract")
    out = []
    if isinstance(oc, dict):
        if isinstance(oc.get("properties"), dict):
            out = list(oc["properties"].keys())
        elif isinstance(oc.get("fields"), dict):
            out = list(oc["fields"].keys())
        elif isinstance(oc.get("fields"), list):
            out = [x for x in oc["fields"] if isinstance(x, str)]
        elif isinstance(oc.get("required"), list):
            out = [x for x in oc["required"] if isinstance(x, str)]
        else:
            out = list(oc.keys())
    elif isinstance(oc, list):
        out = [x for x in oc if isinstance(x, str)]
    seen, uniq = set(), []
    for field in out:
        if field not in seen:
            seen.add(field)
            uniq.append(field)
    return uniq


# --------------------------------------------------------------------------
# text analysis
# --------------------------------------------------------------------------

def token_weight(token, scoring):
    if CJK_RUN_RE.fullmatch(token):
        if len(token) >= 2:
            return float(scoring.get("cjk_bigram_weight", 1.8))
        return float(scoring.get("cjk_unigram_weight", 0.45))
    if token.isdigit():
        return float(scoring.get("numeric_weight", 0.8))
    return float(scoring.get("ascii_weight", 1.0))


def analyze(text):
    counts = Counter()
    low = text.lower()
    for match in ASCII_TOKEN_RE.finditer(low):
        token = match.group(0)
        if len(token) >= 2 and token not in EN_STOP:
            counts[token] += 1
    for run in CJK_RUN_RE.findall(low):
        for ch in run:
            if ch not in CJK_STOP:
                counts[ch] += 1
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram[0] in CJK_STOP or bigram[1] in CJK_STOP:
                continue
            counts[bigram] += 1
    return counts


def compact(text):
    return re.sub(r"[\s\W_]+", "", text.lower(), flags=re.UNICODE)


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def looks_binary(data):
    if not data:
        return False
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        return False
    sample = data[:4096]
    if b"\x00" in sample:
        return True
    control = sum(1 for b in sample if b < 9 or 13 < b < 32)
    return (control / float(max(1, len(sample)))) > 0.10


def html_to_text(raw):
    text = str(raw)
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(
        r"(?is)<h([1-6])[^>]*>(.*?)</h\1>",
        lambda m: "\n" + ("#" * int(m.group(1))) + " " +
        re.sub(r"(?s)<[^>]+>", " ", m.group(2)).strip() + "\n",
        text,
    )
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|section|article|table|ul|ol|blockquote)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def xml_to_text(raw):
    text = str(raw)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    text = re.sub(r"(?i)</(p|div|item|entry|section|title|abstract|chapter)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


PDF_STREAM_RE = re.compile(rb"stream\r?\n", re.I)
PDF_STRING_TOKEN_RE = re.compile(
    rb"\((?:\\.|[^\\()])*\)|<[0-9A-Fa-f\s]+>|T\*|Td|TD|ET", re.S
)


def pdf_unescape(token):
    out = bytearray()
    i, n = 0, len(token)
    simple = {0x6e: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
              0x28: 40, 0x29: 41, 0x5c: 92}
    while i < n:
        byte = token[i]
        if byte == 0x5c:
            i += 1
            if i >= n:
                break
            nxt = token[i]
            if nxt in simple:
                out.append(simple[nxt])
                i += 1
            elif 0x30 <= nxt <= 0x37:
                digits = ""
                while i < n and len(digits) < 3 and 0x30 <= token[i] <= 0x37:
                    digits += chr(token[i])
                    i += 1
                out.append(int(digits, 8) & 0xFF)
            elif nxt in (0x0A, 0x0D):
                i += 1
                if nxt == 0x0D and i < n and token[i] == 0x0A:
                    i += 1
            else:
                out.append(nxt)
                i += 1
        else:
            out.append(byte)
            i += 1
    return bytes(out)


def decode_pdf_bytes(raw):
    if raw.startswith(b"\xfe\xff"):
        try:
            return raw[2:].decode("utf-16-be")
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def decode_pdf_hex(token):
    cleaned = re.sub(rb"\s+", b"", token)
    if len(cleaned) % 2 == 1:
        cleaned += b"0"
    try:
        raw = bytes.fromhex(cleaned.decode("ascii"))
    except (ValueError, UnicodeDecodeError):
        return ""
    return decode_pdf_bytes(raw)


def pdf_stream_text(raw):
    pieces = []
    for match in PDF_STRING_TOKEN_RE.finditer(raw):
        token = match.group(0)
        if token in (b"T*", b"Td", b"TD", b"ET"):
            pieces.append("\n")
        elif token.startswith(b"("):
            pieces.append(decode_pdf_bytes(pdf_unescape(token[1:-1])))
        elif token.startswith(b"<"):
            pieces.append(decode_pdf_hex(token[1:-1]))
    text = "".join(pieces)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def pdf_text(data):
    """Heuristic text extraction: FlateDecode streams + Tj/TJ strings only.

    Returns text with form-feed page separators. Never invents text: a PDF
    without a usable text layer returns an empty string.
    """
    page_positions = [m.start() for m in re.finditer(rb"/Type\s*/Page\b", data)]
    pages = {}
    order = []
    for match in PDF_STREAM_RE.finditer(data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end]
        while raw[:1] in (b"\r", b"\n"):
            raw = raw[1:]
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            try:
                raw = zlib.decompressobj().decompress(raw)
            except zlib.error:
                pass
        if b"Tj" not in raw and b"TJ" not in raw:
            continue
        page = bisect.bisect_right(page_positions, match.start())
        if page <= 0:
            page = 1
        chunk = pdf_stream_text(raw)
        if not chunk.strip():
            continue
        if page not in pages:
            pages[page] = []
            order.append(page)
        pages[page].append(chunk)
    if not pages:
        return ""
    parts = []
    for page in range(1, max(order) + 1):
        parts.append(" ".join(pages.get(page, [])))
    return "\n\f\n".join(parts)


def extract_text(data, ext):
    """Return (text, extractor, encoding, confidence)."""
    if ext in PDF_EXTS:
        return pdf_text(data), "pdf-flate-tj-heuristic", "pdf-latin1/utf16", "low"
    text, encoding = decode_text_bytes(data)
    if ext in (".html", ".htm"):
        return html_to_text(text), "html-text", encoding, (
            "low" if encoding in ("latin-1", "utf-8-replace") else "high")
    if ext == ".xml":
        return xml_to_text(text), "xml-text", encoding, (
            "low" if encoding in ("latin-1", "utf-8-replace") else "high")
    return text, "text/" + encoding, encoding, (
        "low" if encoding in ("latin-1", "utf-8-replace") else "high")


def decode_text_bytes(data):
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            pass
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


# --------------------------------------------------------------------------
# indexing
# --------------------------------------------------------------------------

def walk_files(root, policy):
    root = Path(root)
    files, skipped = [], []
    if root.is_file():
        return [(root, root.name)], skipped
    cap = int(policy["caps"].get("max_files", 2000))

    def onerror(exc):
        skipped.append({
            "path": str(getattr(exc, "filename", root)),
            "reason": "walk_error",
            "detail": exc.strerror if getattr(exc, "strerror", None) else str(exc),
        })

    for dirpath, dirnames, filenames in os.walk(str(root), topdown=True, onerror=onerror):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = name
            if path.is_symlink():
                skipped.append({"path": rel, "reason": "symlink_not_followed"})
                continue
            files.append((path, rel))
    files.sort(key=lambda x: x[1])
    if len(files) > cap:
        for _path, rel in files[cap:]:
            skipped.append({"path": rel, "reason": "file_cap_reached"})
        files = files[:cap]
    return files, skipped


def build_metadata_map(case):
    mapping = {}

    def add(key, fields):
        if not isinstance(fields, dict):
            return
        normalized = str(key).replace("\\", "/").lstrip("./").lower()
        mapping[normalized] = dict(fields)
        mapping.setdefault(Path(normalized).name, dict(fields))

    raw = case.get("metadata")
    if isinstance(raw, dict):
        for key, value in raw.items():
            add(key, value)
    raw = case.get("file_metadata")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("path") or item.get("file") or item.get("name")
            if key:
                add(key, {k: v for k, v in item.items()
                          if k not in ("path", "file", "name")})
    return mapping


def lookup_metadata(mapping, rel):
    merged = {}
    low = rel.lower()
    if low in mapping:
        merged.update(mapping[low])
    base = Path(low).name
    if base in mapping:
        merged.update(mapping[base])
    return merged


def extract_authors(text):
    authors = []
    for match in AUTHOR_RE.finditer(text[:20000]):
        value = match.group(1).strip()
        if value:
            authors.append(value[:200])
    return authors[:5]


def load_documents(files, exclude_keys, metadata_map, policy):
    docs, skipped, duplicates = [], [], []
    caps = policy["caps"]
    max_bytes = int(caps.get("max_file_bytes", 4000000))
    max_chars = int(caps.get("max_text_chars", 2000000))
    for path, rel in files:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in exclude_keys:
            continue
        ext = path.suffix.lower()
        if ext not in TEXT_EXTS and ext not in PDF_EXTS:
            skipped.append({"path": rel, "reason": "unsupported_extension",
                            "ext": ext or "none"})
            continue
        try:
            stat = path.stat()
        except OSError as exc:
            skipped.append({"path": rel, "reason": "stat_error", "detail": str(exc)})
            continue
        if stat.st_size == 0:
            skipped.append({"path": rel, "reason": "empty_file"})
            continue
        try:
            with open(str(path), "rb") as fh:
                data = fh.read(max_bytes + 1)
        except OSError as exc:
            skipped.append({"path": rel, "reason": "unreadable", "detail": str(exc)})
            continue
        truncated = len(data) > max_bytes
        data = data[:max_bytes]
        if looks_binary(data):
            skipped.append({"path": rel, "reason": "binary_content"})
            continue
        try:
            text, extractor, encoding, confidence = extract_text(data, ext)
        except Exception as exc:  # extraction must never abort the run
            skipped.append({"path": rel, "reason": "extract_error",
                            "detail": "%s: %s" % (type(exc).__name__, exc)})
            continue
        if not text or not text.strip():
            reason = "no_text_layer" if ext in PDF_EXTS else "no_extractable_text"
            skipped.append({"path": rel, "reason": reason})
            continue
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = text[:max_chars]
        years = sorted(set(YEAR_RE.findall(text)) | set(YEAR_RE.findall(rel)))
        doc = {
            "file": rel,
            "ext": ext,
            "bytes": stat.st_size,
            "read_bytes": len(data),
            "sha256": sha256_bytes(data),
            "hash_scope": "prefix" if truncated else "full",
            "truncated": truncated,
            "text": text,
            "chars": len(text),
            "extractor": extractor,
            "encoding": encoding,
            "confidence": confidence,
            "years": years,
            "authors": extract_authors(text),
            "metadata": lookup_metadata(metadata_map, rel),
            "sections": [],
            "passages": [],
        }
        docs.append(doc)
    docs.sort(key=lambda d: d["file"])
    seen_hash, unique = {}, []
    for doc in docs:
        digest = doc["sha256"]
        if doc["hash_scope"] == "full" and digest in seen_hash:
            doc["duplicate_of"] = seen_hash[digest]
            duplicates.append({
                "file": doc["file"],
                "duplicate_of": seen_hash[digest],
                "sha256": digest,
            })
        else:
            if doc["hash_scope"] == "full":
                seen_hash[digest] = doc["file"]
            unique.append(doc)
    return docs, unique, skipped, duplicates


def detect_heading(line):
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    match = HEADING_MD.match(stripped)
    if match:
        return int(len(match.group(1))), match.group(2).strip()
    if stripped.endswith(("。", "；", ";", ",")):
        return None
    match = HEADING_NUM.match(stripped)
    if match and len(stripped) <= 100:
        return 2, stripped
    match = HEADING_CN.match(stripped)
    if match:
        return 2, stripped
    if HEADING_WORD.match(stripped):
        return 1, stripped
    return None


def split_long_line(line, limit):
    pieces = [p for p in re.split(r"(?<=[。．！？!?；;])\s*", line) if p]
    chunks, current = [], ""
    for piece in pieces:
        if len(current) + len(piece) <= limit:
            current += piece
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(piece) > limit:
            chunks.append(piece[:limit])
            piece = piece[limit:]
        current = piece
    if current:
        chunks.append(current)
    return chunks


def build_passages(doc, policy):
    limit = int(policy["caps"].get("passage_chars", 900))
    is_pdf = doc["ext"] in PDF_EXTS
    passages = []
    stack = []
    buffer = []
    state = {"line": None, "end": None, "char": 0, "page": None}

    def current_section():
        return stack[-1][1] if stack else None

    def current_path():
        return [text for _level, text in stack]

    def flush():
        if not buffer:
            return
        body = "\n".join(buffer)
        leading = len(body) - len(body.lstrip())
        body = body.strip()
        if body:
            passages.append({
                "file": doc["file"],
                "section": current_section(),
                "section_path": current_path(),
                "text": body,
                "line_start": state["line"],
                "line_end": state["end"],
                "char_start": state["char"] + leading,
                "char_end": state["char"] + leading + len(body),
                "page": state["page"],
                "kind": "caption" if CAPTION_RE.match(body) else "text",
            })
        buffer[:] = []

    offset = 0
    line_no = 0
    pages = doc["text"].split("\f")
    for page_index, page_text in enumerate(pages, start=1):
        page = page_index if is_pdf else None
        for raw_line in page_text.splitlines():
            line_no += 1
            line = raw_line.rstrip()
            if not line.strip():
                flush()
                offset += len(raw_line) + 1
                continue
            heading = detect_heading(line) if not is_pdf else None
            if heading:
                flush()
                level, heading_text = heading
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, heading_text))
                doc["sections"].append({
                    "heading": heading_text,
                    "line": line_no,
                    "level": level,
                })
                offset += len(raw_line) + 1
                continue
            if is_pdf and len(line) > limit:
                flush()
                cursor = 0
                for piece in split_long_line(line, limit):
                    index = line.find(piece, cursor)
                    if index < 0:
                        index = cursor
                    start_char = offset + index
                    if piece.strip():
                        passages.append({
                            "file": doc["file"],
                            "section": current_section(),
                            "section_path": current_path(),
                            "text": piece.strip(),
                            "line_start": None,
                            "line_end": None,
                            "char_start": start_char,
                            "char_end": start_char + len(piece),
                            "page": page,
                            "kind": "caption" if CAPTION_RE.match(piece) else "text",
                        })
                    cursor = index + len(piece)
                offset += len(raw_line) + 1
                continue
            if not buffer:
                state["line"] = line_no
                state["char"] = offset + (len(raw_line) - len(raw_line.lstrip()))
                state["page"] = page
            buffer.append(line.strip())
            state["end"] = line_no
            if sum(len(part) + 1 for part in buffer) >= limit:
                flush()
            offset += len(raw_line) + 1
        flush()
        offset += 1
    return passages


def build_index(unique_docs, policy):
    passages = []
    cap = int(policy["caps"].get("max_passages", 200000))
    for doc in unique_docs:
        doc["passages"] = build_passages(doc, policy)
        for passage in doc["passages"]:
            if len(passages) >= cap:
                break
            passage["p_id"] = "p%05d" % (len(passages) + 1)
            passage["confidence"] = doc["confidence"]
            passage["extractor"] = doc["extractor"]
            passage["encoding"] = doc["encoding"]
            passage["sha256"] = doc["sha256"]
            tokens = analyze(passage["text"])
            passage["tokens"] = tokens
            passage["dl"] = sum(tokens.values())
            passages.append(passage)
        if len(passages) >= cap:
            break
    return passages


# --------------------------------------------------------------------------
# queries, filters, ranking
# --------------------------------------------------------------------------

def strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end >= 0:
            text = text[end + 4:]
    return text


def parse_task_queries(text):
    text = strip_frontmatter(text)
    found = []
    for match in re.finditer(
            r"(?im)^\s*(?:查询|检索问题|问题|query|question)\s*[:：]\s*(.+)$", text):
        value = match.group(1).strip()
        if value:
            found.append(value)
    lines = text.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            in_section = bool(re.search(
                r"(检索|查询|问题|query|questions?|search)", heading.group(1), re.I))
            continue
        if in_section and re.match(r"^(?:[-*]|\d+[.)])\s+", stripped):
            value = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", stripped).strip()
            if len(value) >= 3:
                found.append(value)
    if not found:
        for line in lines:
            if "？" in line or "?" in line:
                value = line.strip(" -*\t")
                if 3 <= len(value) <= 300:
                    found.append(value)
    return found


def first_text(obj, keys):
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def extract_queries(case, task_text, policy):
    queries = []
    limit = int(policy["caps"].get("max_queries", 20))

    def add(text, source, filters=None):
        text = re.sub(r"\s+", " ", (text or "").strip())
        if not text:
            return
        if any(item["text"] == text for item in queries):
            return
        queries.append({
            "id": "q%d" % (len(queries) + 1),
            "text": text,
            "source": source,
            "filters": dict(filters) if isinstance(filters, dict) else {},
        })

    if isinstance(case, dict):
        for key in ("queries", "questions", "search_queries", "tasks"):
            raw = case.get(key)
            if isinstance(raw, list):
                for index, item in enumerate(raw):
                    source = "case.json:%s[%d]" % (key, index)
                    if isinstance(item, str):
                        add(item, source)
                    elif isinstance(item, dict):
                        add(first_text(item, QUERY_TEXT_KEYS), source,
                            item.get("filters"))
        for key in ("query", "question", "prompt", "search"):
            raw = case.get(key)
            if isinstance(raw, str):
                add(raw, "case.json:" + key)
            elif isinstance(raw, dict):
                add(first_text(raw, QUERY_TEXT_KEYS), "case.json:" + key,
                    raw.get("filters"))
        if not queries:
            raw = case.get("queries")
            if isinstance(raw, str):
                add(raw, "case.json:queries")
    if not queries and task_text:
        for index, text in enumerate(parse_task_queries(task_text)):
            add(text, "task.md:%d" % (index + 1))
    return queries[:limit]


def extract_required_fields(case, task_text, contract):
    fields = []

    def add_many(values):
        for value in values:
            value = str(value).strip()
            if value and value not in fields:
                fields.append(value)

    if isinstance(case, dict):
        raw = case.get("required_fields")
        if isinstance(raw, list):
            add_many(raw)
        elif isinstance(raw, str):
            add_many(re.split(r"[,，;；、\s]+", raw))
    add_many(contract)
    if task_text:
        for match in re.finditer(
                r"(?im)^\s*(?:必需字段|必填字段|required\s+fields?|required\s+outputs?)\s*[:：]\s*(.+)$",
                task_text):
            add_many(re.split(r"[,，;；、]+", match.group(1)))
    return fields


def filter_values(value):
    if isinstance(value, (list, tuple, set)):
        out = []
        for item in value:
            out.extend(filter_values(item))
        return out
    if isinstance(value, dict):
        return [str(v) for v in value.values() if isinstance(v, (str, int, float))]
    if value is None:
        return []
    return [str(value)]


def expand_ext(value):
    text = str(value).strip().lower()
    if text in EXT_ALIASES:
        return set(EXT_ALIASES[text])
    if text.startswith("."):
        return {text}
    return {"." + text}


def doc_matches(doc, key, value):
    k = norm_key(key)
    if k in ("ext", "extension", "file_type", "format", "type"):
        wanted = set()
        for item in filter_values(value):
            wanted |= expand_ext(item)
        return doc["ext"] in wanted
    if k in ("year", "years", "date"):
        if isinstance(value, dict):
            low = value.get("gte", value.get("min"))
            high = value.get("lte", value.get("max"))
            equals = value.get("eq")
            for year in doc["years"]:
                try:
                    number = int(year)
                except ValueError:
                    continue
                if equals is not None and str(equals) != year:
                    continue
                if low is not None and number < int(low):
                    continue
                if high is not None and number > int(high):
                    continue
                return True
            return False
        wanted = filter_values(value)
        return any(item in doc["years"] for item in wanted)
    if k in ("author", "authors"):
        haystack = " ".join(doc["authors"]).lower()
        if not haystack:
            haystack = doc["text"][:5000].lower()
        return any(item.lower() in haystack for item in filter_values(value))
    if k in SECTION_KEYS:
        return True
    if k in ("path", "glob", "file", "filename", "name"):
        for item in filter_values(value):
            pattern = item.lower()
            if fnmatch.fnmatch(doc["file"].lower(), pattern):
                return True
            if pattern in doc["file"].lower():
                return True
        return False
    values = filter_values(value)
    haystack = [doc["file"].lower(), doc["text"][:20000].lower()]
    for meta_key, meta_value in doc["metadata"].items():
        haystack.append(str(meta_key).lower())
        haystack.append(str(meta_value).lower())
    blob = "\n".join(haystack)
    return any(item.lower() in blob for item in values)


def apply_doc_filters(docs, filters):
    applied, unmatched = [], []
    if not isinstance(filters, dict):
        return docs, applied, unmatched
    kept = docs
    for key in sorted(filters):
        value = filters[key]
        if norm_key(key) in SECTION_KEYS:
            continue
        before = len(kept)
        kept = [doc for doc in kept if doc_matches(doc, key, value)]
        mode = "exact_field" if norm_key(key) in SUPPORTED_FILTER_KEYS else "best_effort_substring"
        applied.append({
            "filter": key,
            "value": value,
            "mode": mode,
            "before": before,
            "after": len(kept),
        })
        if not kept:
            unmatched.append({
                "filter": key,
                "value": value,
                "reason": "no document satisfies filter",
                "stage": "document",
            })
    return kept, applied, unmatched


def section_allowed(passage, value):
    section = " / ".join(passage.get("section_path") or [])
    if not section:
        section = passage.get("section") or ""
    if not section:
        return False
    low = section.lower()
    for item in filter_values(value):
        candidate = item.lower()
        if candidate in low or fnmatch.fnmatch(low, candidate):
            return True
    return False


def rank_passages(passages, query_text, policy):
    scoring = policy["scoring"]
    weights = {}
    for token in analyze(query_text):
        weights[token] = token_weight(token, scoring)
    if not weights or not passages:
        return []
    total = len(passages)
    average_length = sum(p["dl"] for p in passages) / float(max(1, total)) or 1.0
    document_freq = Counter()
    for passage in passages:
        tokens = passage["tokens"]
        for token in weights:
            if tokens.get(token):
                document_freq[token] += 1
    k1 = float(scoring.get("k1", 1.2))
    b = float(scoring.get("b", 0.75))
    min_score = float(scoring.get("min_score", 0.45))
    section_boost = float(scoring.get("section_boost", 0.8))
    filename_boost = float(scoring.get("filename_boost", 0.3))
    phrase_boost = float(scoring.get("phrase_boost", 1.5))
    max_section_terms = int(scoring.get("max_section_boost_terms", 3))
    compact_query = compact(query_text)
    results = []
    for passage in passages:
        tokens = passage["tokens"]
        length = passage["dl"] or 1
        score = 0.0
        matched = []
        for token, weight in weights.items():
            tf = tokens.get(token, 0)
            if not tf:
                continue
            df = document_freq[token]
            idf = math.log(1.0 + (total - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1.0 - b + b * length / average_length)
            score += idf * (tf * (k1 + 1.0) / denom) * weight
            matched.append(token)
        if not matched:
            continue
        section_match = False
        if passage.get("section"):
            section_tokens = analyze(passage["section"])
            section_hits = sum(1 for token in weights if section_tokens.get(token))
            if section_hits:
                section_match = True
                score += section_boost * min(section_hits, max_section_terms)
        filename_match = False
        filename_tokens = analyze(passage["file"])
        filename_hits = sum(1 for token in weights if filename_tokens.get(token))
        if filename_hits:
            filename_match = True
            score += filename_boost * min(filename_hits, 3)
        phrase_match = False
        if len(compact_query) >= 3 and compact_query in compact(passage["text"]):
            phrase_match = True
            score += phrase_boost
        if score < min_score:
            continue
        results.append({
            "score": score,
            "passage": passage,
            "matched": sorted(set(matched)),
            "phrase": phrase_match,
            "section": section_match,
            "filename": filename_match,
        })
    results.sort(key=lambda item: (
        -item["score"],
        item["passage"]["file"],
        item["passage"]["line_start"] if item["passage"]["line_start"] is not None else 0,
        item["passage"]["char_start"],
        item["passage"]["p_id"],
    ))
    return results


def make_quote(text, matched, policy):
    width = int(policy["caps"].get("quote_chars", 320))
    context = int(policy["caps"].get("context_chars", 80))
    low = text.lower()
    position = -1
    for token in sorted(matched, key=lambda t: (-len(t), t)):
        found = low.find(token.lower())
        if found >= 0:
            position = found
            break
    if position < 0:
        position = 0
    start = max(0, position - context)
    end = min(len(text), start + width)
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    quote = raw.strip()
    if not quote:
        quote = text[:width].strip()
        leading = 0
    return quote, start + leading


def locator_text(locator):
    if not isinstance(locator, dict):
        return "?"
    if locator.get("kind") == "page" and locator.get("page"):
        return "page %s" % locator["page"]
    if locator.get("line_start"):
        if locator.get("line_end") and locator["line_end"] != locator["line_start"]:
            return "lines %s-%s" % (locator["line_start"], locator["line_end"])
        return "line %s" % locator["line_start"]
    return "chars %s-%s" % (locator.get("char_start"), locator.get("char_end"))


# --------------------------------------------------------------------------
# result assembly
# --------------------------------------------------------------------------

def resolve_contract_field(field, payload):
    key = norm_key(field)
    for canonical, names in CANONICAL_ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical), "exact"
    best = (None, None, None)
    best_len = 0
    for canonical, names in CANONICAL_ALIASES.items():
        for name in (canonical,) + tuple(names):
            if len(name) < 3:
                continue
            if name in key and len(name) > best_len and (key.startswith(name) or key.endswith(name)):
                best = (canonical, payload.get(canonical), "substring")
                best_len = len(name)
    if best[0]:
        return best
    if "quer" in key:
        return "queries", payload.get("queries"), "heuristic"
    if "miss" in key or "empty" in key or "uncover" in key:
        return "misses", payload.get("misses"), "heuristic"
    if "file" in key or "doc" in key or "corpus" in key:
        return "index_stats", payload.get("index_stats"), "heuristic"
    if "score" in key or "rank" in key:
        return "hits", payload.get("hits"), "heuristic"
    return None, None, None


def unknown_value(field, payload):
    key = norm_key(field)
    if any(part in key for part in ("summary", "answer", "conclusion", "note",
                                    "comment", "remark", "description", "brief")):
        return payload.get("summary")
    if any(part in key for part in ("time", "date", "timestamp", "duration")):
        return "not_applicable"
    return {
        "note": "no dedicated producer for this contract field; best-effort projection",
        "query_count": len(payload.get("queries") or []),
        "hit_count": len(payload.get("hits") or []),
        "status": payload.get("status"),
    }


def build_summary(status, queries, hits, misses):
    if hits:
        top = hits[0]
        return "%d queries -> %d hits; top: %s [%s] score %.3f (%s)" % (
            len(queries), len(hits), top["file"],
            (top.get("section") or "unsectioned"), top["score"], top["hit_id"])
    if not queries:
        return "No retrievable query was found in case.json or task.md; only an index was produced."
    reasons = sorted(set(item.get("reason", "unknown") for item in misses))
    return "No supporting passage was found for %d queries; reasons: %s." % (
        len(queries), ", ".join(reasons) if reasons else "no_hit")


def build_review(case_id, status, queries, hits, misses, stats, uncertainty):
    lines = [
        "# Run review (draft)",
        "",
        "> 本文件由 `scripts/sk_entry.py` 自动生成，是草稿而不是最终结论。"
        "执行 Agent 必须逐条打开命中项的 locator 核对 quote，"
        "并把核对结果诚实写入 `/app/output/run-review.md`。",
        "",
        "## 1. 运行摘要",
        "",
        "| 字段 | 值 |",
        "|-|-|",
        "| case_id | `%s` |" % case_id,
        "| status | `%s` |" % status,
        "| files_indexed | %s |" % stats.get("files_indexed", 0),
        "| files_skipped | %s |" % stats.get("files_skipped", 0),
        "| passages | %s |" % stats.get("passages", 0),
        "| queries | %s |" % stats.get("queries", 0),
        "| hits | %s |" % stats.get("hits", 0),
        "",
        "## 2. 查询与命中",
        "",
    ]
    if not queries:
        lines.append("无可用查询。")
    for query in queries:
        lines.append("### %s: %s" % (query["id"], query["text"]))
        lines.append("")
        query_hits = [hit for hit in hits if hit["query_id"] == query["id"]]
        if not query_hits:
            lines.append("无命中。")
        else:
            lines.append("| hit | file | section | locator | score | quote |")
            lines.append("|-|-|-|-|-|-|")
            for hit in query_hits:
                quote = (hit.get("quote") or "").replace("|", "\\|").replace("\n", " ")
                if len(quote) > 80:
                    quote = quote[:80] + "..."
                lines.append("| %s | %s | %s | %s | %.3f | %s |" % (
                    hit["hit_id"], hit["file"], hit.get("section") or "-",
                    locator_text(hit.get("locator")), hit["score"], quote))
        lines.append("")
    lines.append("## 3. 未命中与限制")
    lines.append("")
    if misses:
        for miss in misses:
            lines.append("- `%s` (%s): %s" % (
                miss.get("query_id") or "global", miss.get("reason"),
                miss.get("detail", "")))
    else:
        lines.append("- 无未命中记录。")
    for item in uncertainty:
        lines.append("- %s" % item)
    lines.extend([
        "",
        "## 4. 执行 Agent 必做核对",
        "",
        "- [ ] 已运行入口命令，确认退出码为 0。",
        "- [ ] 已读取 `/app/output/result.json`，确认 case_id 与输入一致。",
        "- [ ] 已逐条打开下列命中项的 file + locator，核对 quote 与原文一致。",
        "- [ ] 无命中或过滤无匹配时，未编造任何命中。",
        "- [ ] 已把核对结论写入 `/app/output/run-review.md`，而不是只保留本草稿。",
        "",
    ])
    for hit in hits:
        lines.append("- [ ] `%s` %s %s 核对通过" % (
            hit["hit_id"], hit["file"], locator_text(hit.get("locator"))))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main run
# --------------------------------------------------------------------------

def run(args):
    policy, policy_loaded = load_policy()
    out_file, out_dir = resolve_out(args.out)
    work_dir = Path(str(args.work))
    input_case_path, input_dir = resolve_input(args.input)

    errors, skipped, duplicates = [], [], []
    uncertainty, run_log = [], []
    run_log.append("entry: sk_entry.py v%s" % VERSION)
    run_log.append("policy: %s" % ("assets/policy.json" if policy_loaded else "built-in defaults"))

    case, case_ok, case_state = {}, False, "missing"
    if input_case_path and input_case_path.is_file():
        try:
            loaded = read_json_file(input_case_path)
            if isinstance(loaded, dict):
                case = loaded
                case_ok = True
                case_state = "ok"
            else:
                case_state = "invalid"
                errors.append("case.json root is not a JSON object")
        except (OSError, ValueError) as exc:
            case_state = "invalid"
            errors.append("cannot read case.json: %s" % exc)
    run_log.append("case: %s (%s)" % (
        input_case_path.name if input_case_path else "case.json", case_state))

    case_id = "unknown"
    if isinstance(case.get("case_id"), str) and case["case_id"].strip():
        case_id = case["case_id"].strip()
    elif isinstance(case.get("id"), str) and case["id"].strip():
        case_id = case["id"].strip()

    contract = contract_fields(case) if case_ok else []
    task_text = ""
    task_path = input_dir / "task.md"
    if task_path.is_file():
        try:
            task_text, _enc = read_text_any(task_path)
        except OSError:
            task_text = ""
    queries = extract_queries(case if case_ok else {}, task_text, policy)
    required_fields = extract_required_fields(case if case_ok else {}, task_text, contract)
    run_log.append("queries: %d" % len(queries))

    metadata_map = build_metadata_map(case) if case_ok else {}
    all_docs, unique_docs, passages = [], [], []
    files_seen = 0
    if input_dir.exists() and input_dir.is_dir():
        files, walk_skips = walk_files(input_dir, policy)
        skipped.extend(walk_skips)
        files_seen = len(files)
        exclude_keys = set()
        for exclude in (input_case_path, task_path):
            try:
                exclude_keys.add(str(exclude.resolve()))
            except OSError:
                pass
        all_docs, unique_docs, load_skips, duplicates = load_documents(
            files, exclude_keys, metadata_map, policy)
        skipped.extend(load_skips)
        passages = build_index(unique_docs, policy)
        run_log.append("index: %d files seen, %d indexed, %d ranked, %d skipped, %d duplicates" % (
            files_seen, len(all_docs), len(unique_docs), len(skipped), len(duplicates)))
    else:
        errors.append("input path not found: %s" % input_dir)
        run_log.append("index: input path missing")

    global_filters = case.get("filters") if isinstance(case.get("filters"), dict) else {}
    base_docs, global_applied, global_unmatched = apply_doc_filters(unique_docs, global_filters)
    run_log.append("filters: %d global applied, %d unmatched" %
                   (len(global_applied), len(global_unmatched)))

    hits, evidence, misses = [], [], []
    coverage_queries = []
    evidence_counter = 0
    for query in queries:
        query_id = query["id"]
        merged_filters = dict(global_filters)
        merged_filters.update(query.get("filters") or {})
        docs_q, applied, unmatched = apply_doc_filters(unique_docs, merged_filters)
        allowed_files = set(doc["file"] for doc in docs_q)
        candidates = [p for p in passages if p["file"] in allowed_files]
        section_value = None
        for key in merged_filters:
            if norm_key(key) in SECTION_KEYS:
                section_value = merged_filters[key]
                break

        query_hits = []
        reason = None
        detail = ""
        if not unique_docs:
            reason = "no_indexable_files"
            detail = "no readable text file was found under the input path"
        elif not docs_q:
            reason = "filters_excluded_all_files"
            detail = "document-level filters removed every indexed file"
        else:
            ranked = rank_passages(candidates, query["text"], policy)
            top_k = int(policy["scoring"].get("top_k", 8))
            if section_value is not None:
                ranked = [item for item in ranked if section_allowed(item["passage"], section_value)]
            if not ranked:
                reason = "no_passage_above_threshold"
                if section_value is not None:
                    reason = "section_filter_excluded_all_hits"
                    detail = "no passage under the requested section passed the score threshold"
                else:
                    detail = "no passage scored above %.2f" % float(
                        policy["scoring"].get("min_score", 0.45))
            else:
                for index, item in enumerate(ranked[:top_k], start=1):
                    passage = item["passage"]
                    quote, quote_offset = make_quote(
                        passage["text"], item["matched"], policy)
                    page = passage.get("page")
                    if page:
                        locator = {
                            "kind": "page",
                            "page": page,
                            "char_start": passage["char_start"] + quote_offset,
                            "char_end": passage["char_start"] + quote_offset + len(quote),
                        }
                    else:
                        locator = {
                            "kind": "line",
                            "line_start": passage.get("line_start"),
                            "line_end": passage.get("line_end"),
                            "char_start": passage["char_start"] + quote_offset,
                            "char_end": passage["char_start"] + quote_offset + len(quote),
                        }
                    why = ["terms: " + ", ".join(item["matched"][:6])]
                    if item["phrase"]:
                        why.append("exact phrase")
                    if item["section"]:
                        why.append("section heading match")
                    if item["filename"]:
                        why.append("filename match")
                    why.append("source: " + passage.get("extractor", "text"))
                    hit_id = "%s-h%d" % (query_id, index)
                    evidence_counter += 1
                    evidence_id = "e%04d" % evidence_counter
                    confidence = passage.get("confidence", "medium")
                    if confidence != "low" and len(item["matched"]) < 2:
                        confidence = "medium"
                    hit = {
                        "hit_id": hit_id,
                        "query_id": query_id,
                        "file": passage["file"],
                        "section": passage.get("section"),
                        "section_path": passage.get("section_path") or [],
                        "locator": locator,
                        "page": page,
                        "quote": quote,
                        "quote_sha256": sha256_bytes(quote.encode("utf-8")),
                        "score": round(item["score"], 3),
                        "matched_terms": item["matched"][:12],
                        "why": "; ".join(why),
                        "confidence": confidence,
                        "evidence_id": evidence_id,
                    }
                    hits.append(hit)
                    query_hits.append(hit)
                    evidence.append({
                        "evidence_id": evidence_id,
                        "hit_id": hit_id,
                        "query_id": query_id,
                        "file": passage["file"],
                        "sha256": passage.get("sha256"),
                        "extractor": passage.get("extractor"),
                        "encoding": passage.get("encoding"),
                        "section": passage.get("section"),
                        "locator": locator,
                        "quote": quote,
                        "quote_sha256": sha256_bytes(quote.encode("utf-8")),
                        "confidence": confidence,
                    })

        for item in unmatched:
            misses.append({
                "query_id": query_id,
                "reason": "filter_unmatched",
                "filter": item.get("filter"),
                "value": item.get("value"),
                "detail": item.get("reason"),
            })
        if reason:
            misses.append({
                "query_id": query_id,
                "reason": reason,
                "detail": detail,
            })
        coverage_queries.append({
            "query_id": query_id,
            "query": query["text"],
            "source": query["source"],
            "filters": merged_filters,
            "documents_considered": len(docs_q),
            "hits": len(query_hits),
            "distinct_files": len(set(hit["file"] for hit in query_hits)),
            "top_score": query_hits[0]["score"] if query_hits else None,
            "covered": bool(query_hits),
            "applied_filters": applied,
        })

    for item in global_unmatched:
        misses.append({
            "query_id": None,
            "reason": "global_filter_unmatched",
            "filter": item.get("filter"),
            "value": item.get("value"),
            "detail": item.get("reason"),
        })
    if not unique_docs and (input_dir.exists() or case_state == "ok"):
        misses.append({
            "query_id": None,
            "reason": "no_indexable_files",
            "detail": "no readable text content was indexed from the input",
        })
    if case_state == "missing":
        misses.append({
            "query_id": None,
            "reason": "case_file_missing",
            "detail": "case.json was not found under the input path",
        })
    if not queries and case_ok:
        misses.append({
            "query_id": None,
            "reason": "no_query",
            "detail": "case.json and task.md did not contain a retrievable query",
        })

    # status
    if case_state == "invalid":
        status = "failed"
        status_reason = "case.json unreadable or invalid"
    elif case_state == "missing":
        status = "insufficient_input"
        status_reason = "case.json missing"
    elif not input_dir.exists():
        status = "insufficient_input"
        status_reason = "input path missing"
    elif not unique_docs:
        status = "insufficient_input"
        status_reason = "no readable text file indexed"
    elif not queries:
        status = "partial"
        status_reason = "no query supplied"
    elif any(not item["covered"] for item in coverage_queries):
        status = "partial"
        status_reason = "at least one query has no hit"
    elif global_unmatched or any(
            m.get("reason") == "filter_unmatched" for m in misses):
        status = "partial"
        status_reason = "at least one filter matched nothing"
    elif any(item.get("reason") in ("no_text_layer", "unreadable", "extract_error", "stat_error")
             for item in skipped):
        status = "partial"
        status_reason = "some files could not be indexed"
    else:
        status = "success"
        status_reason = "all queries covered and all filters satisfied"
    run_log.append("status: %s (%s)" % (status, status_reason))

    low_confidence = [d["file"] for d in all_docs if d["confidence"] == "low"]
    truncated = [d["file"] for d in all_docs if d["truncated"]]
    skipped_reasons = Counter(item.get("reason", "unknown") for item in skipped)
    stats = {
        "files_seen": files_seen,
        "files_indexed": len(all_docs),
        "files_ranked": len(unique_docs),
        "files_skipped": len(skipped),
        "duplicates": len(duplicates),
        "passages": len(passages),
        "sections": sum(len(doc["sections"]) for doc in unique_docs),
        "bytes_indexed": sum(doc["bytes"] for doc in all_docs),
        "chars_indexed": sum(doc["chars"] for doc in all_docs),
        "pdf_files": sum(1 for doc in all_docs if doc["ext"] in PDF_EXTS),
        "low_confidence_files": len(low_confidence),
        "truncated_files": len(truncated),
        "queries": len(queries),
        "hits": len(hits),
        "misses": len(misses),
        "applied_filters": global_applied,
        "unmatched_filters": global_unmatched,
        "skipped_reasons": {key: skipped_reasons[key] for key in sorted(skipped_reasons)},
        "input_root": str(input_dir),
    }

    if not queries:
        uncertainty.append("No query field was found in case.json or task.md, so no ranking was performed.")
    if any(doc["ext"] in PDF_EXTS for doc in all_docs):
        uncertainty.append(
            "PDF text is extracted heuristically from FlateDecode streams and Tj/TJ strings; "
            "page numbers are approximate and no OCR is performed.")
    if low_confidence:
        uncertainty.append(
            "%d file(s) use low-confidence decoding (latin-1, replacement characters or PDF heuristics)."
            % len(low_confidence))
    if truncated:
        uncertainty.append("%d file(s) were truncated at the byte cap." % len(truncated))
    if skipped:
        uncertainty.append("%d file(s) were skipped; see skipped and skipped_reasons." % len(skipped))
    if duplicates:
        uncertainty.append(
            "%d duplicate file(s) share content with an earlier file and were not ranked twice."
            % len(duplicates))
    if any(m.get("reason") in ("filter_unmatched", "global_filter_unmatched") for m in misses):
        uncertainty.append(
            "At least one filter matched nothing; constraints were reported instead of relaxed.")
    if status == "partial" and queries and not hits:
        uncertainty.append("No query produced a hit; absence of evidence is not evidence of absence.")
    if not all_docs:
        uncertainty.append("The library index is empty, so no passage-level claim can be made.")
    uncertainty.append(
        "Matching is lexical (BM25-lite over paragraphs), not semantic embedding; synonyms and paraphrases may be missed.")
    uncertainty.append("All outputs are deterministic: no timestamps, random IDs or network calls.")

    qview = [{
        "id": query["id"],
        "text": query["text"],
        "source": query["source"],
        "filters": query.get("filters") or {},
    } for query in queries]
    summary = build_summary(status, queries, hits, misses)
    coverage = {
        "queries_total": len(queries),
        "queries_with_hits": sum(1 for item in coverage_queries if item["covered"]),
        "per_query": coverage_queries,
        "required_fields": {},
        "output_contract_fields": {},
        "files_with_hits": sorted(set(hit["file"] for hit in hits)),
    }
    payload = {
        "case_id": case_id,
        "status": status,
        "queries": qview,
        "hits": hits,
        "evidence": evidence,
        "coverage": coverage,
        "misses": misses,
        "index_stats": stats,
        "uncertainty": uncertainty,
        "run_log": run_log,
        "summary": summary,
        "skipped": skipped,
        "duplicates": duplicates,
    }

    output = {"case_id": case_id}
    mapping = []
    for field in contract:
        if norm_key(field) == "case_id":
            output[field] = case_id
            mapping.append({"key": field, "mapped_to": "case_id", "mode": "exact"})
            continue
        canonical, value, mode = resolve_contract_field(field, payload)
        if canonical:
            output[field] = value
            mapping.append({"key": field, "mapped_to": canonical, "mode": mode})
        else:
            output[field] = unknown_value(field, payload)
            mapping.append({"key": field, "mapped_to": None, "mode": "unknown"})
    for key, value in payload.items():
        output.setdefault(key, value)
    output["contract_extras"] = {
        "contract_source": "case.json:output_contract",
        "contract_keys": contract,
        "mapping": mapping,
        "unknown_fields": [item["key"] for item in mapping if item["mode"] == "unknown"],
        "canonical_fields": sorted(payload.keys()),
        "generator_version": VERSION,
    }

    coverage["required_fields"] = {
        field: output.get(field) not in (None, "", [], {}) for field in required_fields
    }
    coverage["output_contract_fields"] = {field: field in output for field in contract}

    out_file.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_file, output)
    run_log.append("write: result.json")
    index_payload = {
        "case_id": case_id,
        "generator": "sk_entry.py",
        "version": VERSION,
        "input_root": str(input_dir),
        "files": [{
            "path": doc["file"],
            "ext": doc["ext"],
            "bytes": doc["bytes"],
            "sha256": doc["sha256"],
            "hash_scope": doc["hash_scope"],
            "extractor": doc["extractor"],
            "encoding": doc["encoding"],
            "confidence": doc["confidence"],
            "chars": doc["chars"],
            "years": doc["years"],
            "authors": doc["authors"],
            "sections": [item["heading"] for item in doc["sections"]],
            "passages": len(doc["passages"]),
            "duplicate_of": doc.get("duplicate_of"),
        } for doc in all_docs],
        "skipped": skipped,
        "duplicates": duplicates,
        "queries": qview,
        "stats": stats,
    }
    try:
        write_json(work_dir / "index.json", index_payload)
        run_log.append("write: index.json")
        review = build_review(case_id, status, qview, hits, misses, stats, uncertainty)
        write_text(work_dir / "run-review.md", review)
        run_log.append("write: run-review.md (draft)")
        out_review = out_file.parent / "run-review.md"
        if not out_review.exists():
            write_text(out_review, review)
            run_log.append("write: output run-review.md (draft)")
    except OSError as exc:
        errors.append("work artifacts not written: %s" % exc)

    return 0


def emergency_result(out_file, reason, input_raw=None):
    """Last-resort contract-complete result; never raises."""
    case_path, _input_dir = resolve_input(input_raw or "/input")
    case, contract, case_id = {}, [], "unknown"
    try:
        if case_path.is_file():
            loaded = read_json_file(case_path)
            if isinstance(loaded, dict):
                case = loaded
                contract = contract_fields(case)
                if isinstance(case.get("case_id"), str):
                    case_id = case["case_id"]
    except Exception:
        pass
    payload = {
        "case_id": case_id,
        "status": "failed",
        "queries": [],
        "hits": [],
        "evidence": [],
        "coverage": {"queries_total": 0, "queries_with_hits": 0, "per_query": []},
        "misses": [{"query_id": None, "reason": reason, "detail": "unhandled runtime failure"}],
        "index_stats": {},
        "uncertainty": ["The run did not complete; no scientific claim is made."],
        "run_log": ["entry: sk_entry.py v%s" % VERSION, "failure: %s" % reason],
        "summary": "Run failed before retrieval; see misses.",
        "skipped": [],
        "duplicates": [],
    }
    output = {"case_id": case_id}
    mapping = []
    for field in contract:
        if norm_key(field) == "case_id":
            output[field] = case_id
            mapping.append({"key": field, "mapped_to": "case_id", "mode": "exact"})
            continue
        canonical, value, mode = resolve_contract_field(field, payload)
        if canonical:
            output[field] = value
            mapping.append({"key": field, "mapped_to": canonical, "mode": mode})
        else:
            output[field] = unknown_value(field, payload)
            mapping.append({"key": field, "mapped_to": None, "mode": "unknown"})
    for key, value in payload.items():
        output.setdefault(key, value)
    output["contract_extras"] = {
        "contract_source": "case.json:output_contract",
        "contract_keys": contract,
        "mapping": mapping,
        "unknown_fields": [item["key"] for item in mapping if item["mode"] == "unknown"],
        "generator_version": VERSION,
        "failure_reason": reason,
    }
    try:
        write_json(out_file, output)
    except OSError:
        pass


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Deterministic cross-file research passage retrieval (stdlib only)")
    parser.add_argument("--input", "--case", dest="input", default="/input",
                        help="input directory or case.json (default /input)")
    parser.add_argument("--work", dest="work", default="/app/work",
                        help="work directory for index.json / run-review draft")
    parser.add_argument("--out", "--output", dest="out", default="/app/output",
                        help="output directory or result .json path")
    parser.add_argument("--version", action="version", version="sk_entry " + VERSION)
    return parser.parse_args(argv)


def raw_out(argv):
    value = "/app/output"
    for index, item in enumerate(argv):
        if item in ("--out", "--output") and index + 1 < len(argv):
            value = argv[index + 1]
        elif item.startswith("--out="):
            value = item.split("=", 1)[1]
        elif item.startswith("--output="):
            value = item.split("=", 1)[1]
    return value


def raw_input(argv):
    value = "/input"
    for index, item in enumerate(argv):
        if item in ("--input", "--case") and index + 1 < len(argv):
            value = argv[index + 1]
        elif item.startswith("--input="):
            value = item.split("=", 1)[1]
        elif item.startswith("--case="):
            value = item.split("=", 1)[1]
    return value


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parse_args(raw)
    except SystemExit:
        out_file, _out_dir = resolve_out(raw_out(raw))
        emergency_result(out_file, "invalid_arguments", raw_input(raw))
        return 0
    try:
        return run(args)
    except Exception as exc:
        out_file, _out_dir = resolve_out(args.out)
        emergency_result(out_file, "%s: %s" % (type(exc).__name__, exc), args.input)
        return 0


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except Exception:
        pass
    sys.exit(0)
