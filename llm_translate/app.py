from __future__ import annotations

import asyncio
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from flask import Flask, jsonify, request, render_template
from fluent.syntax import ast as ftl_ast
from fluent.syntax.parser import FluentParser
from fluent.syntax.serializer import FluentSerializer

from llm_translate.cache import TranslateCache
from llm_translate.config import load_config

# --- Globals ---

cfg = load_config()
cache = TranslateCache(cfg.cache_dir)

_lock = threading.Lock()
_file_data: dict[str, FileData] = {}
_all_items: dict[str, FtlItem] = {}
_scan_done = False
_scan_progress = {"total": 0, "done": 0, "phase": ""}

# Atomic visibility counters (bypassed through cache)
_scan_file_count = 0
_scan_item_count = 0

_batch = {"running": False, "total": 0, "done": 0, "errors": [], "cancelled": False}
_batch_cancel = threading.Event()

app = Flask(__name__)

_PARSER = FluentParser(with_spans=False)
_SERIALIZER = FluentSerializer(with_junk=False)

LETTER_RE = re.compile(r"[A-Za-zÀ-ÿ]")


# --- Data classes ---

@dataclass
class FtlItem:
    file_rel: str
    key: str
    source_text: str       # human-readable English text for UI
    source_ftl: str        # serialized FTL of the source entry
    existing_ftl: str = "" # current translation in target (only if already translated)
    llm_ftl: str = ""      # LLM-produced translation (after batch translation)
    status: str = "ORIGINAL"  # ORIGINAL (no translation), TRANSLATED (has existing), BROKEN


@dataclass
class FileData:
    rel: str
    items: list[FtlItem] = field(default_factory=list)


# --- FTL utilities ---

def extract_entry_id(entry: object) -> str | None:
    if isinstance(entry, (ftl_ast.Message, ftl_ast.Term)):
        return entry.id.name
    return None


def collect_text_elements(node: object) -> list[str]:
    texts: list[str] = []
    def _walk(current: object) -> None:
        if isinstance(current, ftl_ast.TextElement):
            texts.append(current.value)
            return
        if isinstance(current, list):
            for item in current:
                _walk(item)
            return
        if not isinstance(current, ftl_ast.SyntaxNode):
            return
        for value in vars(current).values():
            if isinstance(value, (ftl_ast.SyntaxNode, list)):
                _walk(value)
    _walk(node)
    return texts


def has_translatable_text(node: object) -> bool:
    texts = collect_text_elements(node)
    return any(LETTER_RE.search(t) for t in texts)


def reconstruct_text(node: object) -> str:
    texts = collect_text_elements(node)
    return " ".join(t.strip() for t in texts if LETTER_RE.search(t))


def serialize_entry(entry: object) -> str:
    return _SERIALIZER.serialize_entry(entry).rstrip()


def parse_ftl(path: Path) -> ftl_ast.Resource:
    return _PARSER.parse(path.read_text(encoding="utf-8"))


def collect_existing_keys(resource: ftl_ast.Resource) -> dict[str, ftl_ast.SyntaxNode]:
    """Build {key: entry} map from a parsed FTL resource."""
    keys: dict[str, ftl_ast.SyntaxNode] = {}
    for entry in resource.body:
        eid = extract_entry_id(entry)
        if eid:
            keys[eid] = entry
    return keys


# --- Scanner ---

def scan_ftl_files(force: bool = False):
    """Walk ALL source .ftl files. For each entry: track whether target has a translation."""
    global _scan_done, _scan_progress, _file_data, _all_items

    _scan_progress["phase"] = "scanning..."
    _scan_progress["total"] = 0
    _scan_progress["done"] = 0
    _scan_done = False

    if not cfg.source_locale_dir.exists():
        _scan_progress["phase"] = "error: source dir not found"
        _scan_done = True
        return

    source_files = sorted(cfg.source_locale_dir.rglob("*.ftl"))
    _scan_progress["total"] = len(source_files)

    local_files: dict[str, FileData] = {}
    local_items: dict[str, FtlItem] = {}
    FLUSH_INTERVAL = 100

    for idx, src_file in enumerate(source_files):
        _scan_progress["done"] = idx + 1
        rel = src_file.relative_to(cfg.source_locale_dir).as_posix()
        tgt_file = cfg.target_locale_dir / rel

        if not force:
            cur_hash = str(src_file.stat().st_mtime)
            cached_hash = cache.get_file_hash(rel)
            cached_result = cache.get_scan_result(rel)
            if cached_hash == cur_hash and cached_result:
                _restore_cached_file(rel, cached_result, local_files, local_items)
                if idx > 0 and idx % FLUSH_INTERVAL == 0:
                    with _lock:
                        _file_data.clear()
                        _file_data.update(local_files)
                        _all_items.clear()
                        _all_items.update(local_items)
                continue

        try:
            src_resource = parse_ftl(src_file)
        except Exception:
            continue

        existing: dict[str, ftl_ast.SyntaxNode] = {}
        if tgt_file.exists():
            try:
                existing = collect_existing_keys(parse_ftl(tgt_file))
            except Exception:
                pass

        items: list[FtlItem] = []
        for entry in src_resource.body:
            eid = extract_entry_id(entry)
            if not eid:
                continue
            if not has_translatable_text(entry):
                continue

            readable = reconstruct_text(entry)
            ftl_text = serialize_entry(entry)

            if eid in existing:
                existing_ftl = serialize_entry(existing[eid])
                item = FtlItem(
                    file_rel=rel, key=eid,
                    source_text=readable, source_ftl=ftl_text,
                    existing_ftl=existing_ftl, status="TRANSLATED",
                )
            else:
                item = FtlItem(
                    file_rel=rel, key=eid,
                    source_text=readable, source_ftl=ftl_text,
                    status="ORIGINAL",
                )

            items.append(item)
            local_items[f"{rel}:{eid}"] = item

        fd = FileData(rel=rel, items=items)
        local_files[rel] = fd

        cache.set_scan_result(rel, [_item_to_dict(i) for i in items])
        cache.set_file_hash(rel, str(src_file.stat().st_mtime))

        if idx > 0 and idx % FLUSH_INTERVAL == 0:
            with _lock:
                _file_data.clear()
                _file_data.update(local_files)
                _all_items.clear()
                _all_items.update(local_items)

    cache.save()
    _reload_from_cache()
    _scan_progress["phase"] = "done"
    _scan_done = True


def _reload_from_cache():
    """Reload file data from the just-written cache (thread-safe via file I/O)."""
    global _file_data, _all_items
    if not cache.scan_results:
        return
    local_files: dict[str, FileData] = {}
    local_items: dict[str, FtlItem] = {}
    for rel, results in cache.scan_results.items():
        if results:
            _restore_cached_file(rel, results, local_files, local_items)
    with _lock:
        _file_data.clear()
        _file_data.update(local_files)
        _all_items.clear()
        _all_items.update(local_items)


def _restore_cached_file(
    rel: str, cached: list[dict],
    local_files: dict[str, FileData],
    local_items: dict[str, FtlItem],
) -> None:
    items = []
    for d in cached:
        if "key" not in d or "source_text" not in d:
            continue
        item = FtlItem(
            file_rel=d.get("file_rel", rel),
            key=d["key"],
            source_text=d["source_text"],
            source_ftl=d.get("source_ftl", ""),
            existing_ftl=d.get("existing_ftl", ""),
            llm_ftl=d.get("llm_ftl", ""),
            status=d.get("status", "ORIGINAL"),
        )
        items.append(item)
        local_items[f"{rel}:{item.key}"] = item
    local_files[rel] = FileData(rel=rel, items=items)


def _item_to_dict(item: FtlItem) -> dict:
    return {
        "file_rel": item.file_rel,
        "key": item.key,
        "source_text": item.source_text,
        "source_ftl": item.source_ftl,
        "existing_ftl": item.existing_ftl,
        "llm_ftl": item.llm_ftl,
        "status": item.status,
    }


# --- LLM translation ---

_BATCH_SYSTEM_PROMPT = """You are a translator for an SS14 (Space Station 14) game localization project.

Translate each FTL (Fluent) message from {source_lang} to {target_lang}.

Rules:
- Preserve all FTL syntax exactly: {{$vars}}, {{-terms}}(), [selectors], etc.
- Only translate human language text content.
- Keep message IDs and term IDs unchanged.
- Preserve attribute names (lines starting with .).
- Preserve the exact quoting style (double quotes vs. no quotes for single-line values).
- Keep leading/trailing whitespace in text elements if present.
- If a message has no text content to translate, leave it unchanged.
- Output valid FTL syntax. Each message starts with --- KEY --- and ends with ---.

If an "Existing translation" is shown after ---, improve/fix it instead of translating from scratch.
If no existing translation is shown, create a new translation."""


def _build_batch_prompt(items: list[FtlItem]) -> str:
    parts = [f"Translate each FTL message from {cfg.source_lang} to {cfg.target_lang}."]
    parts.append("Preserve all FTL syntax, message IDs, and structure exactly.")
    parts.append("Only translate the English text content.")
    parts.append("")
    for item in items:
        parts.append(f"--- {item.key} ---")
        parts.append(item.source_ftl)
        if item.status == "TRANSLATED" and item.existing_ftl:
            parts.append("---")
            parts.append(f"Existing translation:")
            parts.append(item.existing_ftl)
        parts.append("")
    return "\n".join(parts)


def _parse_batch_response(response: str, items: list[FtlItem]) -> dict[str, str]:
    results: dict[str, str] = {}
    current_key = None
    current_lines: list[str] = []

    for line in response.split("\n"):
        m = re.match(r"^---\s+(\S+)\s+---$", line.strip())
        if m:
            if current_key and current_lines:
                results[current_key] = "\n".join(current_lines).strip()
            current_key = m.group(1)
            current_lines = []
        elif current_key is not None:
            # Skip "Existing translation:" lines in the response
            if line.strip().startswith("Existing translation"):
                continue
            current_lines.append(line)

    if current_key and current_lines:
        results[current_key] = "\n".join(current_lines).strip()

    return results


async def _translate_batch(items: list[FtlItem]) -> list[dict]:
    if not items:
        return []

    prompt_text = _build_batch_prompt(items)
    system = _BATCH_SYSTEM_PROMPT.format(source_lang=cfg.source_lang, target_lang=cfg.target_lang)

    url = f"{cfg.llm_api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": cfg.llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt_text},
        ],
        "temperature": cfg.llm_temperature,
        "max_tokens": cfg.llm_max_tokens,
    }

    try:
        async with httpx.AsyncClient(timeout=cfg.llm_timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            raw = data["choices"][0]["message"]["content"] or ""
    except Exception as exc:
        return [{"error": str(exc)}] * len(items)

    parsed = _parse_batch_response(raw, items)

    results = []
    for item in items:
        trans = parsed.get(item.key, "")
        if not trans:
            results.append({"key": item.key, "llm_ftl": None, "error": "missing_from_response"})
            continue

        try:
            parsed_entry = _PARSER.parse_entry(trans)
            if parsed_entry is None or extract_entry_id(parsed_entry) != item.key:
                raise ValueError("FTL parse failed or key mismatch")
        except Exception:
            results.append({"key": item.key, "llm_ftl": trans, "error": "invalid_ftl"})
            continue

        results.append({"key": item.key, "llm_ftl": trans, "error": None})
    return results


# --- Write to target ---

def _write_file_items(rel: str) -> int:
    """Write/replace translated items in the target .ftl file. Returns count written."""
    with _lock:
        fd = _file_data.get(rel)
        if not fd:
            return 0
        items = list(fd.items)

    # Collect items with LLM translations ready
    to_write = [i for i in items if i.llm_ftl and i.status != "BROKEN"]
    if not to_write:
        return 0

    tgt_file = cfg.target_locale_dir / rel
    tgt_file.parent.mkdir(parents=True, exist_ok=True)

    existing_text = ""
    if tgt_file.exists():
        existing_text = tgt_file.read_text(encoding="utf-8")

    if existing_text.strip():
        resource = _PARSER.parse(existing_text)
        existing_entries: dict[str, int] = {}
        for i, entry in enumerate(resource.body):
            eid = extract_entry_id(entry)
            if eid:
                existing_entries[eid] = i

        modified = False
        for item in to_write:
            new_entry = _PARSER.parse_entry(item.llm_ftl)
            if new_entry is None:
                continue
            if item.key in existing_entries:
                resource.body[existing_entries[item.key]] = new_entry
                modified = True
            else:
                resource.body.append(new_entry)
                modified = True

        if modified:
            new_text = _SERIALIZER.serialize(resource)
            tgt_file.write_text(new_text, encoding="utf-8")
            return len(to_write)
        return 0

    else:
        new_text = "\n\n".join(item.llm_ftl for item in to_write).rstrip() + "\n"
        tgt_file.write_text(new_text, encoding="utf-8")
        return len(to_write)


# --- Background scan ---

def _run_scan_thread(force: bool = False):
    scan_ftl_files(force=force)


def _load_cached_results() -> bool:
    global _scan_done, _scan_progress, _file_data, _all_items

    if not cache.scan_results:
        return False

    local_files: dict[str, FileData] = {}
    local_items: dict[str, FtlItem] = {}

    for rel, strings_data in cache.scan_results.items():
        if strings_data:
            _restore_cached_file(rel, strings_data, local_files, local_items)

    with _lock:
        _file_data = local_files
        _all_items = local_items

    _scan_progress["phase"] = "done"
    _scan_done = True
    return True


# --- Background translation ---

def _run_batch_file(rel: str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_batch_file_worker(rel))
    finally:
        loop.close()


async def _batch_file_worker(rel: str):
    global _batch

    with _lock:
        fd = _file_data.get(rel)
        if not fd:
            return
        # Send ALL items to LLM (both ORIGINAL and TRANSLATED)
        todo = [item for item in fd.items if item.status != "BROKEN"]

    if not todo:
        return

    _batch["running"] = True
    _batch["total"] = len(todo)
    _batch["done"] = 0
    _batch["errors"] = []
    _batch["cancelled"] = False
    _batch_cancel.clear()

    results = await _translate_batch(todo)

    for r in results:
        if _batch_cancel.is_set():
            _batch["cancelled"] = True
            break
        key = r["key"]
        if r.get("error"):
            _batch["errors"].append(f"{key}: {r['error']}")
            _batch["done"] += 1
            # Mark as BROKEN so we skip writing
            with _lock:
                item_key = f"{rel}:{key}"
                item = _all_items.get(item_key)
                if item:
                    item.status = "BROKEN"
            continue
        if r.get("llm_ftl"):
            item_key = f"{rel}:{key}"
            with _lock:
                item = _all_items.get(item_key)
                if item:
                    item.llm_ftl = r["llm_ftl"]
                    cache.set_translation(item_key, r["llm_ftl"])
            _batch["done"] += 1

    # Write all LLM results to target file
    written = _write_file_items(rel)
    if written > 0:
        with _lock:
            fd = _file_data.get(rel)
            if fd:
                # Remove written items or keep with updated status
                for item in fd.items:
                    if item.llm_ftl:
                        if item.existing_ftl:
                            item.existing_ftl = item.llm_ftl
                        else:
                            item.existing_ftl = item.llm_ftl
                        item.status = "TRANSLATED"
                        item.llm_ftl = ""

    cache.save()
    _batch["running"] = False


def _run_batch_all():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_batch_all_worker())
    finally:
        loop.close()


async def _batch_all_worker():
    with _lock:
        rels = list(_file_data.keys())

    for rel in rels:
        if _batch_cancel.is_set():
            _batch["cancelled"] = True
            break
        if _batch.get("running"):
            await asyncio.sleep(0.1)
            continue
        await _batch_file_worker(rel)


# --- Flask routes ---

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    counts = {"ORIGINAL": 0, "TRANSLATED": 0, "BROKEN": 0}
    total = 0
    files = 0
    with _lock:
        for item in _all_items.values():
            s = item.status
            counts[s] = counts.get(s, 0) + 1
            total += 1
        files = len(_file_data)

    return jsonify({
        "files": files,
        "strings": total,
        "counts": counts,
        "scan_done": _scan_done,
        "scan_progress": dict(_scan_progress),
        "source_lang": cfg.source_lang,
        "target_lang": cfg.target_lang,
        "source_locale_dir": str(cfg.source_locale_dir),
        "target_locale_dir": str(cfg.target_locale_dir),
        "llm_model": cfg.llm_model,
        "llm_api_base": cfg.llm_api_base,
    })


@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(force=True) or {}
    mode = data.get("mode", "quick")
    force = mode == "hard"

    phase = _scan_progress.get("phase", "")
    if phase not in ("done", ""):
        return jsonify({"status": "already_running", "mode": "quick" if "quick" in phase else "hard"})

    thread = threading.Thread(target=_run_scan_thread, kwargs={"force": force}, daemon=True)
    thread.start()
    return jsonify({"status": "started", "mode": mode})


@app.route("/api/refresh-cache", methods=["POST"])
def api_refresh_cache():
    cache.load()
    ok = _load_cached_results()
    return jsonify({"status": "ok" if ok else "no_cache"})


@app.route("/api/files")
def api_files():
    q = request.args.get("q", "").strip().lower()
    scope = request.args.get("scope", "").strip().lower()

    with _lock:
        items = []
        for rel, fd in sorted(_file_data.items()):
            if q and q not in rel.lower():
                continue
            orig_count = sum(1 for item in fd.items if item.status == "ORIGINAL")
            total = len(fd.items)
            if scope == "pending" and orig_count == 0:
                continue
            if scope == "done" and orig_count > 0:
                continue
            items.append({
                "rel": rel,
                "total": total,
                "original": orig_count,
                "translated": total - orig_count,
            })
    return jsonify(items)


@app.route("/api/file/<path:rel>")
def api_file(rel: str):
    with _lock:
        fd = _file_data.get(rel)
    if not fd:
        return jsonify({"error": "not_found"}), 404

    items = []
    for item in fd.items:
        items.append({
            "key": item.key,
            "source_text": item.source_text,
            "source_ftl": item.source_ftl,
            "existing_ftl": item.existing_ftl,
            "llm_ftl": item.llm_ftl,
            "status": item.status,
        })
    return jsonify({"file_rel": rel, "items": items})


@app.route("/api/translate", methods=["POST"])
def api_translate():
    data = request.get_json(force=True) or {}
    scope_type = data.get("scope", "file")
    scope_value = data.get("value", "")

    if _batch.get("running"):
        return jsonify({"error": "batch_already_running"}), 409

    if scope_type == "file":
        with _lock:
            if scope_value not in _file_data:
                return jsonify({"error": "file_not_found"}), 404
        thread = threading.Thread(target=_run_batch_file, args=(scope_value,), daemon=True)
        thread.start()
        return jsonify({"status": "started", "scope": scope_value})

    elif scope_type == "all":
        thread = threading.Thread(target=_run_batch_all, daemon=True)
        thread.start()
        return jsonify({"status": "started"})

    return jsonify({"error": "invalid_scope"}), 400


@app.route("/api/progress")
def api_progress():
    with _lock:
        rels = list(_file_data.keys())
        done = sum(1 for item in _all_items.values() if item.status in ("TRANSLATED", "BROKEN"))
        total = len(_all_items)
        pending = sum(1 for item in _all_items.values() if item.status == "ORIGINAL")
    return jsonify({
        "batch": dict(_batch),
        "files": len(rels),
        "strings_total": total,
        "strings_done": done,
        "strings_pending": pending,
    })


@app.route("/api/llm-check")
async def api_llm_check():
    url = f"{cfg.llm_api_base}/models"
    headers = {"Authorization": f"Bearer {cfg.llm_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers=headers)
            connected = resp.status_code < 500
    except Exception:
        connected = False
    return jsonify({"connected": connected})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    _batch_cancel.set()
    return jsonify({"status": "cancelling"})


# --- Main ---

def main():
    import sys

    print(f"Source:  {cfg.source_locale_dir}")
    print(f"Target:  {cfg.target_locale_dir}")
    print(f"LLM:     {cfg.llm_api_base} / {cfg.llm_model}")

    if not cfg.source_locale_dir.exists():
        print(f"ERROR: Source locale dir not found: {cfg.source_locale_dir}")
        sys.exit(1)

    cfg.target_locale_dir.mkdir(parents=True, exist_ok=True)

    loaded = _load_cached_results()
    if loaded:
        print(f"Loaded {len(_file_data)} files from scan cache.")
    else:
        print("No cached scan results found. Click Scan in the UI to start.")

    print(f"Starting web UI at http://127.0.0.1:5002")
    app.run(host="127.0.0.1", port=5002, debug=False, use_reloader=False, threaded=True)
