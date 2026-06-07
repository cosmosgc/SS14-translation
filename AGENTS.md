# SS14 Translation Tools — Agent Guide

## Two subsystems

**Argos Translator** (`translate_ftl.py`)
- Offline, translates `.ftl` (Fluent) files via a pre-trained Argos model
- Run: `start.bat` or `python translate_ftl.py`
- Config: `.env` in repo root (`.env` is gitignored; copy from `.env.example`)
- Merge behavior: skips keys already present in target; appends new ones. Set `OVERWRITE_EXISTING_KEYS=true` in `.env` to replace.
- Preserves: Fluent vars (`{$var}`), methods (`{ CAPITALIZE(...) }`), BBcode tags (`[color=...][/color]`)
- Argos model language codes have aliases: `pt-br` → `pb`, but also accepts full locale codes

**LLM Translator** (`llm_translate/`)
- Flask web app that scans `.ftl` files comparing source vs target locale, translates missing entries via an OpenAI-compatible LLM
- Run: `start_llm_translate.bat` or `python -m llm_translate`
- Serves web UI at `http://127.0.0.1:5002`
- Config: `llm_translate/.env` for LLM settings, root `.env` for locale paths (fallback chain)
- Sends full FTL entries (not text fragments) to the LLM for better context

## Commands

```powershell
# install deps (both subsystems)
install.bat                 # or: python -m pip install -r requirements.txt

# Argos: run translation
start.bat                   # or: python translate_ftl.py

# LLM: run web UI
start_llm_translate.bat     # or: python -m llm_translate

# Build standalone EXE (Argos tool only)
build.bat                   # output: .\dist\SS14Translator.exe
```

## Config quirks

- `SOURCE_ARGOS_CODE` / `TARGET_ARGOS_CODE` override locale detection. Default pair: `en` → `pb`. `language_code()` in `translate_ftl.py:145` normalizes and aliases `pt-br` → `pb`.
- `ARGOS_MODEL_PATH` defaults to `./translate-en_pb-1_9.argosmodel` if it exists at script root.
- `ARGOS_DATA_DIR` (default `./.argos`) overrides XDG config/cache dirs for Argos.
- `PROJECT_ROOT` and locale dirs can all be overridden via root `.env`. Default locale dirs under `Resources/Locale/{locale}`.
- `llm_translate/` config loads `llm_translate/.env` first, then root `.env`, then `os.environ`.
- LLM translator derives `SOURCE_LOCALE_DIR` / `TARGET_LOCALE_DIR` from root `.env` (same paths as Argos tool).

## Architecture notes

- `translate_ftl.py`: single-file ~520 lines. Parses FTL AST, translates text nodes via Argos, appends to target files.
- `llm_translate/`: Flask app with async LLM calls. Caches scan results in `llm_translate/.cache/`. Background scan thread walks `.ftl` files comparing source vs target locale dirs.
- LLM translator sends full serialized FTL entries with `--- KEY ---` delimiters. Validates returned FTL via `FluentParser.parse_entry()`. Detects missing keys and invalid syntax.
- `.argos/`: cached Argos model data in repo (git-tracked).
- `build/`, `dist/`: PyInstaller build artifacts (gitignored).
- Both translators share the same root `.env` for project paths.

## Dependencies

- `requirements.txt` covers both subsystems: `fluent.syntax`, `flask`, `httpx`, `python-dotenv`, `argostranslate`, `tqdm`.

## Git

- Remote: `https://github.com/cosmosgc/SS14-translation.git`
- Only `main` branch. Small repo (~5 commits).
