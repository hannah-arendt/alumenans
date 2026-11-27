# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Git

- Never commit (or amend, or push) unless the user explicitly asks for it. The user makes the commits; leave changes in the working tree for them to review and commit.

## What this is

ALumenans is a desktop app for sociologists that turns Russian-language interview recordings into transcripts, then supports proofreading and anonymization of personal data. Everything runs locally and offline after first launch. The only network use is the first-run download of models. All Python packages ship inside the builds; the app never runs pip. The app runs on Windows (Inno Setup installer with bundled Python) and macOS (an app bundle that is also built on Windows).

Layout: the `alumenans` package is in `src/alumenans/`, with `tests/` and `tools/` at the repo root. `packaging/windows/` holds the Windows installer, `packaging/windows/dev/` the `.bat` launchers for running from a checkout, and `packaging/macos/` the macOS build. Dependencies and metadata are in `pyproject.toml`.

## Commands

```bash
pip install -e .[dev]                                             # or: export PYTHONPATH=src
python -m unittest discover -s tests                              # full suite (~360 tests, ~1s)
pytest                                                            # same suite; pyproject sets pythonpath=src
python -m unittest tests.test_proofreading                        # one module
python -m unittest tests.test_proofreading.SuggestionTests.test_low_acoustic_confidence_becomes_a_review_marker  # one test
python -m alumenans                                               # launch the app (downloads models on first run)
python -m alumenans.transcription.worker transcribe rec.m4a --output result.json --speakers 2   # transcription without the UI
python tools/evaluate.py --reference .golden/<ref> --result result.json            # word error rate (WER) / speaker / anonymization metrics
python tools/startup_check.py                                     # startup diagnostics
python -m alumenans --self-test                                   # headless check that a build works (no window, no models)
python tools/version.py set 0.4.0                                  # set the version in .iss, pyproject.toml and __init__.py (or: make set-version V=0.4.0)
```

The root `Makefile` wraps these with uv and `.venv`: `make install` (or `make install-cpu` for CPU-only torch), `make test`, `make test T=tests.test_proofreading`, `make run`, `make self-test`; `make help` lists all targets. On Linux, `make run` also needs `make linux-gui` (the optional `linux-gui` extra: Qt for pywebview's window; Linux is a dev platform only, not shipped).

Tests are written with `unittest`; pytest runs them as well. Some tests fail with `ModuleNotFoundError` unless the heavy runtime packages are installed (`natasha`, `pymorphy3`, `numpy`, …). `pip install -e .` installs them. The exact pins live only in `pyproject.toml`.

Builds:
- Windows: `packaging/windows/build-installer.ps1`. It needs Inno Setup 6 (found via `$env:ISCC`, PATH, Program Files or LOCALAPPDATA) and a Python runtime in `packaging/windows/python-runtime/`, which `python packaging/windows/fetch-runtime.py` downloads from python-build-standalone. It pip-installs the `pyproject.toml` dependencies (via `packaging/dependencies.py`) into that runtime and import-checks them before compiling. The version comes from `packaging/windows/ALumenans.iss` (`#define AppVersion`) and must match `pyproject.toml` and `alumenans/__init__.py`; `test_packaging.py` checks this.
- macOS: `packaging/macos/build-mac.py`. It runs on Windows, downloads python-build-standalone into `packaging/macos/runtime-cache/`, and cross-installs the dependencies for `aarch64-apple-darwin` / macOS 14 with **uv**, which must be on PATH. uv is used instead of `pip --platform` because pip would evaluate `sys_platform` markers for the host and drop pyobjc. `check_packages` rejects any non-Mach-O native code, i.e. a C extension that got built from source on the host.
- Both builds copy the whole `src/alumenans` tree and start the app with `python -m alumenans`. There is no per-file list to maintain.

CI (`.github/workflows/ci.yml`, on every PR and push to `main`) only calls the reusable `.github/workflows/build.yml`, which holds the jobs:
- Linux: the test suite plus `make self-test`.
- Windows: builds the installer, installs it silently, checks the Start-menu shortcut, then runs `--self-test` with the installed `python.exe` and `pythonw.exe`.
- macOS: the app is built on a Windows runner, as in real life, then unpacked with `ditto` on macos-15, the oldest Apple Silicon runner GitHub hosts (the app's minimum, macOS 14, is no longer available as arm64), where `Contents/MacOS/ALumenans --self-test` is run through the real launcher.

Releases (`.github/workflows/release.yml`) are started by hand from the Actions tab and never bumped in PRs:
- The version is picked by `tools/version.py next`: the code's version if it is newer than every release and tag (so setting e.g. 1.0.0 by hand is respected), otherwise the newest release with the minor number raised.
- It runs `build.yml` with that version, so the release is built and tested by the same jobs as CI, then commits the version on top of the released commit and pushes only the tag `vX.Y.Z`. `main` keeps its old version number; that is expected.
- The installers and `SHA256SUMS.txt` are attached to the release, which is a draft by default.

`src/alumenans/self_test.py` holds the checks. Add one when a new kind of breakage shows up only in installed builds.

## Architecture

**Shell (`app.py`, `launch.py`, `server.py`, `api.py`).** `app.main()` starts a `ThreadingHTTPServer` on 127.0.0.1 and a pywebview window that shows `resources/anonymizer.html`. `launch.py` prepares the start (build check, model download, setup window), `server.py` holds the HTTP `Handler`, `api.py` holds `AppApi`, and `current_audio.py` holds the open recording that both of them use. The single-file HTML holds the whole UI: transcription, proofreading and anonymization tabs. The UI reaches Python in two ways:
- **HTTP endpoints on `Handler`** for stateless text analysis: `/analyze`, `/proofread`, `/inflect`, `/known-words`, `/guide-report`, `/warmup`, `/health`, plus the UI catalog script `/i18n.js` and audio serving with byte ranges.
- **`AppApi`, exposed as `window.pywebview.api`**, for stateful operations: picking media, transcription jobs, project and study files, and saving txt, docx or encrypted key archives.

A separate setup-window process shows progress while `prepare_launch()` downloads models. It closes when the UI calls `mark_interface_ready`. If a module listed in `dependencies.py` is missing, launch stops with an "incomplete build" message (`is_incomplete_build()` recognizes it in either UI language). Nothing is installed at runtime.

**Paths (`paths.py`).** `RESOURCES` holds the read-only files that ship with the package. `INSTALL_ROOT` is the folder that contains the package: `{app}` when installed, `src/` in a checkout, or `ALUMENANS_HOME` if that is set. The app keeps its state there (`.installed`, `.startup.log`, `.last-folder`, `.researcher`, `.ui-language`; `paths.state_file()` renames the pre-0.3.0 Russian-named files on first read), plus `models/` when the folder is writable; otherwise `models_home()` falls back to a per-user folder. Child processes start as modules (`-m alumenans`, `-m alumenans.transcription.worker`) with `child_environment()`, which puts `INSTALL_ROOT` on `PYTHONPATH`. Running a file inside the package directly would break its imports.

**Transcription runs in its own process (`transcription/`).** `service.TranscriptionManager` spawns `worker`, which uses onnx-asr and sherpa-onnx for automatic speech recognition (ASR) and diarization. The worker reports progress as JSON lines on stdout, and sends the finished transcript as one `result` message on the same channel, so no plaintext copy is left on disk (`--output` still writes a file for command-line use). `open_protocol_channel` keeps the real stdout private to the protocol and points descriptor 1 at stderr, which the manager reads separately, so library output can't corrupt the result. A separate process keeps memory free before the anonymization models (GLiNER, Natasha) load. `core.py` holds the pure, model-free logic (chunking, validation, sentence endings, turn boundaries), and most transcription tests target it.

**Anonymization pipeline (`detection/`, entry point `detection.spans.find_spans`).** It merges entity spans from several sources: the curated dictionary `resources/names-and-organizations.txt` (public names that stay unmasked), GLiNER, Natasha named-entity recognition (NER), vocative detection (`vocative.py`), self-introductions (`introduction.py`), and place classification (`places.py`). It then filters out false positives such as speech fillers, capitalized verbs and the study's subject terms. Pseudonyms are inflected to match the case of the original word (Natasha morphology + pymorphy3). Inside the package: `patterns.py` (regex rules), `curated.py` (public names), `morphology.py`, `models.py` (loading Natasha NER and GLiNER, `MODEL_STATE`), `subject.py` (the study's subject terms), `filters.py` (false positives), `spans.py` (merging and grouping).

**Persistence (`storage/`, SQLite encrypted with SQLCipher via `sqlcipher3`):**
- `cipher.py`: one study password opens every file of a study. It is stretched once per unlock (PBKDF2) into a raw key; each file is opened with that key plus the study salt, which SQLCipher keeps as the file's first 16 bytes. All pages, the WAL included, are encrypted. The stores import `from sqlcipher3 import dbapi2 as sqlite3`: its exceptions are not stdlib `sqlite3.Error`. Nothing touches a store until a study folder is chosen and unlocked (`AppApi._study_gate`), and nothing is written next to the audio.
- `project_store.py`: one project file per interview, kept in the *study* folder rather than next to the audio, so it survives deletion of the recording. Turn IDs come from the audio, not the text, so decision anchors survive text edits.
- `study_store.py`: data shared across a series of interviews: the guide, study documents, and the explicit "do not anonymize" list. The app may *suggest* entries for this list but must never add them silently.
- `exchange.py`: a file-based boundary with the shared environment. A study file comes in and anonymized exports go out. It must never make network calls.
- `secret_zip.py`: an AES-256 WinZip-compatible zip for the anonymization key, built with stdlib only.

**Guide (`guide/`):** `parse.py` turns the interview guide into structured questions and branches, `align.py` matches the transcript against it, `report.py` reports coverage, and `speakers.py` uses it to tell the interviewer from the respondent. The parsing does not trust question numbers, separates read-aloud text from interviewer prompts, and skips branches that were not taken.

## Conventions and gotchas

- **Code minimalism.** Never add a special case when the general case already covers the same behaviour. Less code is the direction the codebase is moving in: prefer deleting a rule to keeping it "just in case".

- **Recording language and UI language are independent.** Recordings are Russian only; the UI is Russian or English, chosen by the user (switch in the top bar, stored in `.ui-language`, passed to child processes as `ALUMENANS_UI_LANGUAGE`). In new code, never tie language-data assumptions (ASR model, NER, morphology, word lists, regexes) to the UI language or its strings, or the reverse. What is written into the user's documents follows the recording language: pseudonyms («Персона 1»), speaker labels («Спикер 2»), exported file names and the key file stay Russian whatever the UI language.

- **UI text goes through a catalog, never a literal.** Python: `t("key", **params)` from `alumenans.i18n`, with the strings in `src/alumenans/locales/ru.py` and `en.py`. The screen: `tr("key", {params})` and `trn("key", n)` (plural `.one`/`.few`/`.many` keys) in JS, and `data-i18n="key"` / `data-i18n-title` / `data-i18n-placeholder` / `data-i18n-aria-label` in the markup, with the strings in `const I18N` in `resources/i18n.js` (loaded by `anonymizer.html` before its own script and served by `Handler` at `/i18n.js`). Add every new key to all languages: `tests/test_i18n.py` checks that the keys and placeholders match and that every key used exists. Tests that assert Russian messages pin the language with `i18n.set_language("ru", persist=False)` in `setUpModule`, because CI has no `.installed` and falls back to the system locale.

- **Language rule: English for everything except user-facing text.** File and folder names, identifiers, comments, docstrings, and developer-facing output (build scripts, test messages, `tools/startup_check.py`, the startup log) are English.
  - Russian stays only where an end user reads it: the `ru` UI catalogs (`locales/ru.py`, `I18N.ru` in `resources/i18n.js`), mac launcher dialogs, installer texts, `first-run.txt`, `python-setup.html`, `HOW-TO-RUN.txt`, and exported file names. Their English counterparts are `locales/en.py`, `I18N.en`, `first-run.en.txt` and `HOW-TO-RUN.en.txt`.
  - Russian *language data* the app processes also stays: GLiNER label prompts, word lists, regexes, the names dictionary, and Russian sample text in tests.
  - In new code, comments and docstrings contain no Russian words unless the point cannot be made without them (a rule about a specific Russian ending, say). Give examples in English: "Hello," and not «Привет,». Older code quotes Russian examples in comments throughout; that is being phased out, so don't copy it.
- Imports are absolute: `from alumenans.storage.project_store import …`. Mock targets use the full dotted path of the module that looks the name up: `patch("alumenans.launch.subprocess.run")`.
- **New third-party import → add an exact pin to `pyproject.toml`.** If the app can't start without it, also add it to `REQUIRED_MODULES` in `src/alumenans/dependencies.py`. `tests/test_packaging.py` fails if an import or a required module isn't declared in pyproject. If torch is bumped, check which macOS its wheels need (`MACOS_MIN` in the Mac builder; `LSMinimumSystemVersion` in `Info.plist` must match).
- Tests that read source or HTML use `alumenans.paths.PACKAGE_DIR` / `RESOURCES`, not `__file__`.
- `tests/test_ui_contract.py` asserts exact JS snippets in `anonymizer.html`, and wording through the page plus its catalog script (`UI` there, or `screen_catalogs()["ru"]`). Renaming UI strings, keys or handlers breaks it on purpose, so update both together.
- The app runs under `pythonw` with no console, so `sys.stdout` and `sys.stderr` are replaced with devnull. Don't rely on prints for diagnostics. Startup failures go to `startup_stack.txt` in `INSTALL_ROOT` (see `startup_stack.py`).
- In Windows `.bat` files, keep the commands themselves ASCII-only, because cmd breaks on Cyrillic labels and variable names.
- **Licensing.** The app is GPL-3.0-or-later (`LICENSE`). Both builds run `packaging/third_party_licenses.py` on the packages they ship, and it fails if a distribution has no license file. For such a package, add its upstream text to `packaging/license-texts/` and to `OVERRIDES`. When a dependency is added or removed, update `THIRD-PARTY-NOTICES.md`; `tests/test_licenses.py` checks that every direct dependency is listed there.
- Prefer the standard library for new functionality. Every dependency adds to the installer size and has to have macOS arm64 wheels.
