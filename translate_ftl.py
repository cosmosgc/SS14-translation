from __future__ import annotations

import copy
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv
from fluent.syntax import ast as ftl_ast
from fluent.syntax.parser import FluentParser
from fluent.syntax.serializer import FluentSerializer
from tqdm import tqdm


@dataclass
class Config:
    project_root: Path
    source_locale: str
    target_locale: str
    source_argos_code: str | None
    target_argos_code: str | None
    source_dir: Path
    target_dir: Path
    unchanged_log_path: Path
    argos_model_path: Path | None
    auto_install_model: bool
    argos_data_dir: Path
    log_level: str
    log_file_progress: bool
    show_progress_bar: bool
    overwrite_existing_keys: bool


def as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_config() -> Config:
    load_dotenv()

    script_dir = Path(__file__).resolve().parent
    default_root = script_dir.parent.parent
    project_root = Path(os.getenv("PROJECT_ROOT", str(default_root))).resolve()

    source_locale = os.getenv("SOURCE_LOCALE", "en-US")
    target_locale = os.getenv("TARGET_LOCALE", "pt-BR")
    source_argos_code = os.getenv("SOURCE_ARGOS_CODE")
    target_argos_code = os.getenv("TARGET_ARGOS_CODE")

    source_dir = Path(
        os.getenv(
            "SOURCE_LOCALE_DIR",
            str(project_root / "Resources" / "Locale" / source_locale),
        )
    ).resolve()
    target_dir = Path(
        os.getenv(
            "TARGET_LOCALE_DIR",
            str(project_root / "Resources" / "Locale" / target_locale),
        )
    ).resolve()

    unchanged_log_path = Path(
        os.getenv("UNCHANGED_LOG_PATH", str(script_dir / "unchanged_translations.txt"))
    ).resolve()

    argos_model_env = os.getenv("ARGOS_MODEL_PATH")
    if argos_model_env:
        argos_model_path = Path(argos_model_env).resolve()
    else:
        default_model = script_dir / "translate-en_pb-1_9.argosmodel"
        argos_model_path = default_model.resolve() if default_model.exists() else None

    auto_install_model = as_bool(os.getenv("AUTO_INSTALL_MODEL"), True)
    argos_data_dir = Path(
        os.getenv("ARGOS_DATA_DIR", str(script_dir / ".argos"))
    ).resolve()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file_progress = as_bool(os.getenv("LOG_FILE_PROGRESS"), True)
    show_progress_bar = as_bool(os.getenv("SHOW_PROGRESS_BAR"), True)
    overwrite_existing_keys = as_bool(os.getenv("OVERWRITE_EXISTING_KEYS"), False)

    return Config(
        project_root=project_root,
        source_locale=source_locale,
        target_locale=target_locale,
        source_argos_code=source_argos_code,
        target_argos_code=target_argos_code,
        source_dir=source_dir,
        target_dir=target_dir,
        unchanged_log_path=unchanged_log_path,
        argos_model_path=argos_model_path,
        auto_install_model=auto_install_model,
        argos_data_dir=argos_data_dir,
        log_level=log_level,
        log_file_progress=log_file_progress,
        show_progress_bar=show_progress_bar,
        overwrite_existing_keys=overwrite_existing_keys,
    )


class ColorFormatter(logging.Formatter):
    RESET = "\033[0m"
    COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, self.RESET)
        label = record.levelname
        message = super().format(record)
        return f"{color}[{label}] {message}{self.RESET}"


class TqdmLoggingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            tqdm.write(msg)
        except Exception:
            self.handleError(record)


def setup_logger(level_name: str) -> logging.Logger:
    logger = logging.getLogger("ftl_translator")
    logger.handlers.clear()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False

    handler = TqdmLoggingHandler()
    handler.setFormatter(ColorFormatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def language_code(locale: str) -> str:
    normalized = locale.strip().lower()
    aliases = {
        "pt-br": "pb",
        "pt_br": "pb",
    }
    if normalized in aliases:
        return aliases[normalized]
    return normalized.split("-")[0].split("_")[0]


def ensure_argos_translation(
    from_code: str,
    to_code: str,
    model_path: Path | None,
    auto_install: bool,
    argos_data_dir: Path,
):
    argos_data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("XDG_CONFIG_HOME", str(argos_data_dir))
    os.environ.setdefault("XDG_CACHE_HOME", str(argos_data_dir / "cache"))
    os.environ.setdefault("ARGOS_PACKAGES_DIR", str(argos_data_dir / "packages"))
    os.environ.setdefault("ARGOS_TRANSLATE_DATA_DIR", str(argos_data_dir / "data"))

    import argostranslate.package
    import argostranslate.settings
    import argostranslate.translate

    class OfflineArgosTranslator:
        def __init__(self, translation_obj):
            self._fallback = translation_obj
            self._pkg = None
            self._translator = None

            current = translation_obj
            while hasattr(current, "underlying"):
                current = current.underlying
            if hasattr(current, "pkg"):
                self._pkg = current.pkg

            if self._pkg is not None:
                import ctranslate2

                model_path = str(self._pkg.package_path / "model")
                self._translator = ctranslate2.Translator(
                    model_path,
                    device=argostranslate.settings.device,
                    inter_threads=argostranslate.settings.inter_threads,
                    intra_threads=argostranslate.settings.intra_threads,
                    compute_type=argostranslate.settings.compute_type,
                )

        def translate(self, text: str) -> str:
            if self._pkg is None or self._translator is None:
                return self._fallback.translate(text)

            tokenized = [self._pkg.tokenizer.encode(text)]
            target_prefix = None
            if self._pkg.target_prefix != "":
                target_prefix = [[self._pkg.target_prefix]]

            translated_batches = self._translator.translate_batch(
                tokenized,
                target_prefix=target_prefix,
                replace_unknowns=True,
                max_batch_size=argostranslate.settings.batch_size,
                batch_type="tokens",
                beam_size=1,
                num_hypotheses=1,
                length_penalty=0.2,
                return_scores=False,
            )
            tokens = translated_batches[0].hypotheses[0]
            return self._pkg.tokenizer.decode(tokens)

    def _find_translation():
        languages = argostranslate.translate.get_installed_languages()
        from_lang = next((lang for lang in languages if lang.code == from_code), None)
        to_lang = next((lang for lang in languages if lang.code == to_code), None)
        if from_lang is None or to_lang is None:
            return None
        return from_lang.get_translation(to_lang)

    translation = _find_translation()
    if translation is not None:
        return OfflineArgosTranslator(translation)

    if auto_install and model_path and model_path.exists():
        argostranslate.package.install_from_path(str(model_path))
        translation = _find_translation()
        if translation is not None:
            return OfflineArgosTranslator(translation)

    raise RuntimeError(
        f"No Argos translation found for '{from_code}' -> '{to_code}'. "
        "Install an Argos model or set ARGOS_MODEL_PATH."
    )


TAG_PATTERN = re.compile(r"\\?\[/?[a-zA-Z][^\]]*\]")
LETTER_PATTERN = re.compile(r"[A-Za-zÀ-ÿ]")


def translate_text_value(value: str, translator) -> str:
    if not value.strip():
        return value
    if not LETTER_PATTERN.search(value):
        return value

    parts = re.split(r"(\\?\[/?[a-zA-Z][^\]]*\])", value)
    translated_parts: list[str] = []
    for part in parts:
        if not part:
            continue
        if TAG_PATTERN.fullmatch(part):
            translated_parts.append(part)
            continue
        # Keep markup/control fragments intact. Fluent can split constructs like
        # "[color={$color}]" into text + placeables, and translating fragments
        # such as "[color=" can corrupt the final tag layout.
        if any(ch in part for ch in "[]{}"):
            translated_parts.append(part)
            continue
        if not LETTER_PATTERN.search(part):
            translated_parts.append(part)
            continue

        leading_len = len(part) - len(part.lstrip())
        trailing_len = len(part) - len(part.rstrip())
        core_end = len(part) - trailing_len if trailing_len > 0 else len(part)
        core = part[leading_len:core_end]
        if not core:
            translated_parts.append(part)
            continue

        translated_core = translator.translate(core)
        translated_parts.append(part[:leading_len] + translated_core + part[core_end:])
    return "".join(translated_parts)


def walk_and_translate_text_nodes(node: object, translator) -> None:
    if isinstance(node, ftl_ast.TextElement):
        node.value = translate_text_value(node.value, translator)
        return

    if isinstance(node, list):
        for item in node:
            walk_and_translate_text_nodes(item, translator)
        return

    if not isinstance(node, ftl_ast.SyntaxNode):
        return

    for value in vars(node).values():
        if isinstance(value, ftl_ast.SyntaxNode) or isinstance(value, list):
            walk_and_translate_text_nodes(value, translator)


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
            if isinstance(value, ftl_ast.SyntaxNode) or isinstance(value, list):
                _walk(value)

    _walk(node)
    return texts


def normalize_text_chunks(chunks: Iterable[str]) -> str:
    return "\n".join(chunk.strip() for chunk in chunks if chunk.strip())


def parse_resource(parser: FluentParser, path: Path) -> ftl_ast.Resource:
    text = path.read_text(encoding="utf-8")
    return parser.parse(text)


def append_entries(target_file: Path, serialized_entries: list[str]) -> None:
    if not serialized_entries:
        return

    target_file.parent.mkdir(parents=True, exist_ok=True)

    chunks = [chunk.rstrip("\n") for chunk in serialized_entries if chunk.strip()]
    if not chunks:
        return

    existing = ""
    if target_file.exists():
        existing = target_file.read_text(encoding="utf-8")

    output = "\n\n".join(chunks).rstrip() + "\n"
    if existing.strip():
        if not existing.endswith("\n"):
            existing += "\n"
        if not existing.endswith("\n\n"):
            existing += "\n"
        target_file.write_text(existing + output, encoding="utf-8")
    else:
        target_file.write_text(output, encoding="utf-8")


def serialize_resource_entries(serializer: FluentSerializer, entries: list[object]) -> str:
    chunks: list[str] = []
    for entry in entries:
        serialized = serializer.serialize_entry(entry).rstrip()
        if serialized:
            chunks.append(serialized)
    if not chunks:
        return ""
    return "\n\n".join(chunks).rstrip() + "\n"


def main() -> int:
    config = resolve_config()
    logger = setup_logger(config.log_level)

    if not config.source_dir.exists():
        logger.error(f"Source locale directory does not exist: {config.source_dir}")
        return 1

    from_code = (config.source_argos_code or language_code(config.source_locale)).lower()
    to_code = (config.target_argos_code or language_code(config.target_locale)).lower()
    logger.info(
        f"Starting translation: {config.source_locale} ({from_code}) -> {config.target_locale} ({to_code})"
    )

    try:
        translator = ensure_argos_translation(
            from_code=from_code,
            to_code=to_code,
            model_path=config.argos_model_path,
            auto_install=config.auto_install_model,
            argos_data_dir=config.argos_data_dir,
        )
        logger.info("Argos translator is ready.")
    except Exception as exc:
        logger.error(str(exc))
        return 1

    parser = FluentParser(with_spans=False)
    serializer = FluentSerializer(with_junk=False)

    unchanged_logs: list[str] = []
    translated_entries_count = 0
    touched_files_count = 0
    scanned_entries_count = 0
    existing_entries_count = 0
    overwritten_entries_count = 0

    source_files = sorted(config.source_dir.rglob("*.ftl"))
    logger.info(f"Scanning {len(source_files)} source file(s) from {config.source_dir}")
    progress_bar = tqdm(
        source_files,
        total=len(source_files),
        desc="Translating FTL files",
        unit="file",
        dynamic_ncols=True,
        disable=not config.show_progress_bar,
    )
    for source_file in progress_bar:
        rel = source_file.relative_to(config.source_dir)
        target_file = config.target_dir / rel
        progress_bar.set_postfix_str(str(rel))

        source_resource = parse_resource(parser, source_file)

        existing_ids: set[str] = set()
        target_entries: list[object] = []
        target_index_by_id: dict[str, int] = {}
        if target_file.exists():
            target_resource = parse_resource(parser, target_file)
            target_entries = list(target_resource.body)
            for idx, target_entry in enumerate(target_entries):
                entry_id = extract_entry_id(target_entry)
                if entry_id:
                    existing_ids.add(entry_id)
                    target_index_by_id[entry_id] = idx

        new_serialized_entries: list[str] = []
        file_translated_count = 0
        file_overwritten_count = 0

        for entry in source_resource.body:
            entry_id = extract_entry_id(entry)
            if not entry_id:
                continue
            scanned_entries_count += 1
            entry_exists = entry_id in existing_ids
            if entry_exists and not config.overwrite_existing_keys:
                existing_entries_count += 1
                continue

            original_entry = copy.deepcopy(entry)
            translated_entry = copy.deepcopy(entry)
            walk_and_translate_text_nodes(translated_entry, translator)

            original_text = normalize_text_chunks(collect_text_elements(original_entry))
            translated_text = normalize_text_chunks(collect_text_elements(translated_entry))

            if original_text and original_text == translated_text:
                unchanged_logs.append(f"{source_file}:{entry_id}")

            serialized = serializer.serialize_entry(translated_entry).rstrip()
            if serialized:
                translated_entries_count += 1
                file_translated_count += 1
                if config.overwrite_existing_keys and entry_exists:
                    target_entries[target_index_by_id[entry_id]] = translated_entry
                    file_overwritten_count += 1
                    overwritten_entries_count += 1
                else:
                    new_serialized_entries.append(serialized)

        wrote_file = False
        if new_serialized_entries:
            append_entries(target_file, new_serialized_entries)
            wrote_file = True

        if config.overwrite_existing_keys and file_overwritten_count > 0:
            if not target_entries and target_file.exists():
                target_entries = list(parse_resource(parser, target_file).body)
            merged_text = serialize_resource_entries(serializer, target_entries)
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(merged_text, encoding="utf-8")
            wrote_file = True

        if wrote_file:
            touched_files_count += 1
            if config.log_file_progress:
                if config.overwrite_existing_keys:
                    logger.info(
                        f"{rel}: +{file_translated_count} translated ({file_overwritten_count} overwritten, {file_translated_count - file_overwritten_count} added)"
                    )
                else:
                    logger.info(
                        f"{rel}: +{file_translated_count} new entr{'y' if file_translated_count == 1 else 'ies'}"
                    )

    if unchanged_logs:
        config.unchanged_log_path.parent.mkdir(parents=True, exist_ok=True)
        config.unchanged_log_path.write_text("\n".join(unchanged_logs) + "\n", encoding="utf-8")
        logger.warning(f"Unchanged translations logged: {config.unchanged_log_path}")
    else:
        logger.info("No unchanged translations detected.")

    logger.info("Translation completed.")
    logger.info(f"Source files scanned: {len(source_files)}")
    logger.info(f"Entries scanned: {scanned_entries_count}")
    logger.info(f"Entries already in target: {existing_entries_count}")
    if config.overwrite_existing_keys:
        logger.info(f"Entries overwritten in target: {overwritten_entries_count}")
    logger.info(f"Target files updated: {touched_files_count}")
    logger.info(f"New entries translated: {translated_entries_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
