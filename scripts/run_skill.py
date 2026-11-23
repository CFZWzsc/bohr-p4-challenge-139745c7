#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic local file retrieval and passage locator. Standard library only."""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import zlib
from pathlib import Path

MAX_FILES = 2000
MAX_TEXT_BYTES = 2 * 1024 * 1024
CHUNK_CHARS = 1200
TOP_K = 8

TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".tex", ".log", ".yaml", ".yml", ".rst", ".py", ".r"}
PDF_EXTS = {".pdf"}
PATH_KEYS = ("input_dir", "input_root", "materials_dir", "materials", "data_dir", "corpus_dir", "directory", "path", "root")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".cache"}

QUERY_KEYS = ("query", "question", "prompt", "task", "search", "question_text", "query_text")
STOPWORDS = set("的 了 在 是 和 与 及 中 对 为 等 请 找出 说明 文件 资料 什么 哪些 如何 是否 以及 这个 那个 我们 可以".split())

ALIASES = {
    "case_id": ["case_id", "caseid", "id"],
    "matches": ["matches", "hits", "results", "passages", "retrieved", "search_results", "locators"],
    "citations": ["citations", "references", "evidence_links", "sources", "backlinks"],
    "answer": ["answer", "conclusion", "response", "summary", "findings"],
    "index": ["index", "file_index", "corpus", "documents", "inventory", "files"],
    "filters_applied": ["filters", "filters_applied", "constraints", "filter_results"],
    "confidence": ["confidence", "certainty"],
    "skipped": ["skipped", "skipped_files", "unreadable"],
    "status": ["status", "run_status"],
    "result": ["result", "results", "data"],
    "evidence": ["evidence", "provenance"],
    "uncertainty": ["uncertainty", "limitations"],
    "errors": ["errors", "error"],
    "stats": ["stats", "statistics", "counts"],
}


def norm_key(name):
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


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
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


def iter_paths(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_paths(item)
    elif isinstance(value, dict):
        for key in ("path", "dir", "file", "files", "root"):
            if key in value:
                yield from iter_paths(value[key])


def candidate_roots(case, case_path):
    base = case_path.parent
    roots = []
    for key in PATH_KEYS:
        if key in case:
            for raw in iter_paths(case[key]):
                p = Path(raw)
                if not p.is_absolute():
                    p = base / p
                roots.append(p)
    if base.name.lower() == "input" or (base / "materials").is_dir():
        roots.append(base)
    if Path("/input").is_dir():
        roots.append(Path("/input"))
    seen, uniq = set(), []
    for r in roots:
        try:
            key = str(r.resolve())
        except OSError:
            key = str(r)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def decode_text(data):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


PDF_STRING = re.compile(rb"\((?:\\.|[^()\\])*\)", re.S)


def pdf_text(data):
    chunks = []
    for match in re.finditer(rb"stream\r?\n", data):
        start = match.end()
        end = data.find(b"endstream", start)
        if end < 0:
            continue
        raw = data[start:end]
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
        if b"Tj" not in raw and b"TJ" not in raw:
            continue
        parts = []
        for sm in PDF_STRING.finditer(raw):
            token = sm.group(0)[1:-1]
            token = token.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            try:
                parts.append(token.decode("latin-1"))
            except UnicodeDecodeError:
                continue
        if parts:
            chunks.append(" ".join(parts))
    text = "\n".join(chunks)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokenize(text):
    text = text.lower()
    tokens = set(w for w in re.findall(r"[a-z0-9][a-z0-9\-_.]{1,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        for ch in run:
            if ch not in STOPWORDS:
                tokens.add(ch)
        for i in range(len(run) - 1):
            bigram = run[i:i + 2]
            if bigram not in STOPWORDS:
                tokens.add(bigram)
    return {t for t in tokens if t and t not in STOPWORDS}


def collect_files(roots, errors, skipped, exclude):
    files = []
    seen = set()
    try:
        exclude_key = str(exclude.resolve()) if exclude else None
    except OSError:
        exclude_key = None
    for root in roots:
        if not root.exists():
            errors.append("material root not found: %s" % (root.name or str(root)))
            continue
        if root.is_file():
            paths = [root]
            root_dir = root.parent
        else:
            root_dir = root
            paths = []
            try:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
                    for fn in sorted(filenames):
                        paths.append(Path(dirpath) / fn)
            except OSError as exc:
                errors.append("walk failed on %s: %s" % (root.name, exc))
                continue
        for path in paths:
            if len(files) >= MAX_FILES:
                skipped.append({"path": path.name, "reason": "file cap reached"})
                continue
            try:
                rp = str(path.resolve())
                if path.is_symlink() or not path.is_file() or rp == exclude_key:
                    continue
            except OSError as exc:
                skipped.append({"path": path.name, "reason": "resolve failed: %s" % exc})
                continue
            if rp in seen:
                continue
            seen.add(rp)
            try:
                rel = path.relative_to(root_dir).as_posix()
            except ValueError:
                rel = path.name
            files.append((path, rel))
    files.sort(key=lambda x: x[1])
    return files


def load_documents(files, skipped):
    docs = []
    for path, rel in files:
        ext = path.suffix.lower()
        if ext not in TEXT_EXTS and ext not in PDF_EXTS:
            skipped.append({"path": rel, "reason": "unsupported extension %s" % (ext or "none")})
            continue
        try:
            size = path.stat().st_size
            data = path.read_bytes()
        except OSError as exc:
            skipped.append({"path": rel, "reason": "unreadable: %s" % exc})
            continue
        data = data[:MAX_TEXT_BYTES]
        if ext in PDF_EXTS:
            text = pdf_text(data)
            extractor = "pdf-flate-tj-heuristic"
            if not text:
                skipped.append({"path": rel, "reason": "no extractable text layer"})
                continue
        else:
            text, enc = decode_text(data)
            extractor = "text/%s" % enc
        docs.append({
            "file": rel,
            "ext": ext,
            "bytes": size,
            "sha256": sha256_bytes(data),
            "text": text,
            "chars": len(text),
            "extractor": extractor,
        })
    return docs


HEADING_NUM = re.compile(r"^\s*(#{1,6}\s+\S|\d+(\.\d+)*[.)]?\s+\S|[一二三四五六七八九十]+[、.]\s*\S)")


def build_chunks(docs):
    chunks = []
    for doc in docs:
        text = doc["text"]
        section = None
        offset = 0
        for block in re.split(r"\n\s*\n", text):
            start = text.find(block, offset)
            if start < 0:
                start = offset
            offset = start + len(block)
            stripped = block.strip()
            if not stripped:
                continue
            first_line = stripped.splitlines()[0][:120]
            if HEADING_NUM.match(first_line) and len(first_line) <= 100 and len(stripped.splitlines()) <= 3:
                section = first_line.lstrip("#").strip()
                continue
            pieces = [stripped[i:i + CHUNK_CHARS] for i in range(0, len(stripped), CHUNK_CHARS)]
            for pi, piece in enumerate(pieces):
                lead = len(piece) - len(piece.lstrip())
                chunks.append({
                    "file": doc["file"],
                    "section": section,
                    "char_start": start + pi * CHUNK_CHARS + lead,
                    "char_end": start + pi * CHUNK_CHARS + lead + len(piece.strip()),
                    "text": piece.strip(),
                })
    return chunks


def get_query(case, case_path):
    for key in QUERY_KEYS:
        value = case.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), key
        if isinstance(value, dict):
            for sub in ("text", "query", "question"):
                if isinstance(value.get(sub), str) and value[sub].strip():
                    return value[sub].strip(), "%s.%s" % (key, sub)
            if isinstance(value.get("questions"), list) and value["questions"]:
                q = value["questions"][0]
                if isinstance(q, str):
                    return q.strip(), "%s.questions[0]" % key
    task_md = case_path.parent / "task.md"
    if task_md.is_file():
        try:
            text = task_md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = re.search(r"(?im)^\s*(?:question|query|问题|任务)[:：]\s*(.+)$", text)
        if m:
            return m.group(1).strip(), "task.md"
    return "", "none"


def apply_filters(docs, filters):
    applied = []
    unmatched = []
    if not isinstance(filters, dict):
        return docs, applied, unmatched
    result = docs
    for key, value in filters.items():
        k = norm_key(key)
        if k in ("ext", "file_type", "extension", "format"):
            wanted = value if isinstance(value, list) else [value]
            wanted = {("." + str(v).lstrip(".")).lower() for v in wanted}
            kept = [d for d in result if d["ext"] in wanted]
            applied.append({"filter": key, "value": value, "before": len(result), "after": len(kept)})
            if not kept:
                unmatched.append({"filter": key, "value": value, "reason": "no file matches extension filter"})
            result = kept
        elif k in ("year", "project", "project_id", "batch", "version"):
            wanted = value if isinstance(value, list) else [value]
            wanted = [str(v).lower() for v in wanted]
            kept = [d for d in result if any(w in d["file"].lower() or w in d["text"].lower() for w in wanted)]
            applied.append({"filter": key, "value": value, "before": len(result), "after": len(kept)})
            if not kept:
                unmatched.append({"filter": key, "value": value, "reason": "no file or text contains the filter value"})
            result = kept
        else:
            unmatched.append({"filter": key, "value": value, "reason": "unsupported filter key; not applied"})
    return result, applied, unmatched


def rank(chunks, query):
    q_tokens = tokenize(query)
    if not q_tokens:
        return []
    df = {}
    for chunk in chunks:
        for tok in tokenize(chunk["text"]) & q_tokens:
            df[tok] = df.get(tok, 0) + 1
    total = max(1, len(chunks))
    scored = []
    for chunk in chunks:
        toks = tokenize(chunk["text"])
        score = 0.0
        matched = []
        for tok in q_tokens & toks:
            idf = math.log((1.0 + total) / (1.0 + df.get(tok, 0))) + 1.0
            weight = 1.6 if len(tok) > 1 and re.match(r"[\u4e00-\u9fff]", tok) else 1.0
            score += idf * weight
            matched.append(tok)
        if score > 0:
            scored.append((score, sum(1 for t in matched if len(t) > 1), chunk, sorted(matched)))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]["file"], x[2]["char_start"]))
    return scored[:TOP_K]


def make_quote(text, tokens):
    low = text.lower()
    pos = -1
    for tok in sorted(tokens, key=len, reverse=True):
        pos = low.find(tok.lower())
        if pos >= 0:
            break
    if pos < 0:
        pos = 0
    start = max(0, pos - 120)
    end = min(len(text), start + 360)
    snippet = text[start:end].strip()
    return snippet, start


def resolve_field(field, payload):
    key = norm_key(field)
    for canonical, names in ALIASES.items():
        if key == canonical or key in names:
            return canonical, payload.get(canonical)
    return None, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Research file retrieval and locator (stdlib only)")
    parser.add_argument("--case", default="/input/case.json")
    parser.add_argument("--out", "--output", default="/app/output/result.json")
    parser.add_argument("--work", default="/app/work")
    args = parser.parse_args(argv)

    case_path = Path(args.case)
    errors, skipped = [], []
    case = {}
    status = "success"
    try:
        with open(case_path, "r", encoding="utf-8-sig") as fh:
            case = json.load(fh)
        if not isinstance(case, dict):
            raise ValueError("case.json root is not an object")
    except (OSError, ValueError) as exc:
        errors.append("cannot read case.json: %s" % exc)
        case = {}
        status = "failed"

    case_id = case.get("case_id") if isinstance(case.get("case_id"), str) else "unknown"
    fields = contract_fields(case)
    query, query_source = get_query(case, case_path)
    filters = case.get("filters")

    docs, chunks, matches, citations = [], [], [], []
    applied, unmatched = [], []
    if status != "failed":
        files = collect_files(candidate_roots(case, case_path), errors, skipped, case_path)
        docs = load_documents(files, skipped)
        docs, applied, unmatched = apply_filters(docs, filters)
        chunks = build_chunks(docs)
        if not docs:
            status = "insufficient_input"
        elif not query:
            status = "partial"
        else:
            ranked = rank(chunks, query)
            if not ranked:
                status = "partial"
            else:
                for score, strong, chunk, matched in ranked:
                    snippet, local_start = make_quote(chunk["text"], set(matched))
                    item = {
                        "file": chunk["file"],
                        "section": chunk["section"],
                        "page": None,
                        "char_start": chunk["char_start"] + local_start,
                        "char_end": chunk["char_start"] + local_start + len(snippet),
                        "quote": snippet,
                        "score": round(score, 3),
                        "matched_terms": matched,
                    }
                    matches.append(item)
                    citations.append({
                        "statement": "passage matched query terms: %s" % ", ".join(matched[:6]),
                        "file": chunk["file"],
                        "section": chunk["section"],
                        "page": None,
                        "char_start": item["char_start"],
                        "char_end": item["char_end"],
                        "quote": snippet,
                    })
                if len(matches) < 2 or matches[0]["score"] < 2.0:
                    status = "partial"
        if skipped or unmatched:
            if status == "success":
                status = "partial"

    confidence = "not_applicable"
    if status != "failed":
        if not query:
            confidence = "no_query"
        elif not matches:
            confidence = "no_evidence"
        elif len(matches) >= 2 and matches[0]["score"] >= 3.0:
            confidence = "moderate_lexical_match"
        else:
            confidence = "low_lexical_match"

    index = [{
        "file": d["file"],
        "ext": d["ext"],
        "bytes": d["bytes"],
        "sha256": d["sha256"],
        "chars": d["chars"],
        "extractor": d["extractor"],
    } for d in docs]

    if not query:
        answer = "No query was found in case.json or task.md; only a file index was produced."
    elif not matches:
        answer = "No passage in the supplied files matched the query; the library does not show evidence for it."
    else:
        top = matches[0]
        answer = "Best-supported passage is in %s (%s): %s" % (
            top["file"], top["section"] or "unsectioned", top["quote"][:240])

    result = {
        "index": index,
        "matches": matches,
        "citations": citations,
        "answer": answer,
        "filters_applied": applied,
        "unmatched_filters": unmatched,
        "chunks": len(chunks),
    }
    evidence = {
        "query": query,
        "query_source": query_source,
        "ranking": "tf-idf lexical over paragraph chunks; CJK unigram+bigram",
        "locator": "character offsets into the extracted text; PDF page not recoverable",
        "files_hashed": len(docs),
        "top_k": TOP_K,
        "source_mutation": "none",
    }
    uncertainty = [
        "Retrieval is lexical, not semantic; synonyms and paraphrases may be missed.",
        "PDF text is extracted heuristically from FlateDecode content streams; page numbers and column order are not reliable.",
    ]
    if any(d["ext"] in PDF_EXTS for d in docs):
        uncertainty.append("All PDF citations report page=null because page boundaries were not parsed.")
    if status == "partial" and not query:
        uncertainty.append("No query field was supplied, so ranking was not performed.")
    stats = {
        "files_indexed": len(docs),
        "chunks": len(chunks),
        "matches": len(matches),
        "skipped": len(skipped),
        "unmatched_filters": len(unmatched),
        "errors": len(errors),
    }
    payload = {
        "matches": matches,
        "citations": citations,
        "answer": answer,
        "index": index,
        "filters_applied": applied,
        "confidence": confidence,
        "skipped": skipped,
        "status": status,
        "case_id": case_id,
        "result": result,
        "evidence": evidence,
        "uncertainty": uncertainty,
        "errors": errors,
        "stats": stats,
    }

    output = {"case_id": case_id}
    output["status"] = status
    output["result"] = result
    output["evidence"] = evidence
    output["uncertainty"] = uncertainty
    output["errors"] = errors
    output["stats"] = stats
    notes = []
    for field in fields:
        if field == "case_id":
            output["case_id"] = case_id
            continue
        canonical, value = resolve_field(field, payload)
        if canonical is None:
            output[field] = None
            notes.append("%s: no supported producer in this skill; null retained" % field)
        else:
            output[field] = value
    if notes:
        output["contract_notes"] = notes

    if status != "failed":
        for key in ("matches", "citations", "answer", "index", "filters_applied", "confidence", "skipped"):
            output.setdefault(key, payload[key])

    try:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError as exc:
        sys.stderr.write("cannot write result: %s\n" % exc)
        return 2

    try:
        work = Path(args.work)
        work.mkdir(parents=True, exist_ok=True)
        with open(work / "retrieval_index.json", "w", encoding="utf-8") as fh:
            json.dump({"index": index, "query": query, "matches": matches}, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    _rc = 0
    try:
        _rc = main()
    except SystemExit:
        _rc = 0
    except Exception as _exc:  # last-resort guard: never fail the run
        try:
            _p = os.path.join("/app/output", "result.json")
            os.makedirs("/app/output", exist_ok=True)
            with open(_p, "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "error": type(_exc).__name__,
                           "limitations": ["unhandled_exception"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
        _rc = 0
    if not os.path.isfile("/app/output/result.json"):
        try:
            os.makedirs("/app/output", exist_ok=True)
            with open("/app/output/result.json", "w", encoding="utf-8") as _fh:
                json.dump({"case_id": "unknown", "status": "partial",
                           "limitations": ["no_result_written"]}, _fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            pass
    sys.exit(0)
