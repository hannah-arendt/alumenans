# Third-party notices

ALumenans is free software, licensed under the GNU General Public License,
version 3 or (at your option) any later version. The full text is in
`LICENSE` (`LICENSE.txt` in an installed copy).

The Windows installer and the macOS app bundle the components below. The
full license text of each one is in the `licenses` folder of the installed
program (on the Mac: `ALumenans.app/Contents/Resources/licenses`).
`licenses/INDEX.txt` lists the exact version of every component in that
build. This file is a summary. Where it differs from a component's own
license text, the license text applies.

## Source code

The complete source code of ALumenans, including the build scripts, is
available at https://github.com/hannah-arendt/alumenans.

The corresponding source code of the FFmpeg build that ships with each
release is published with that release at https://github.com/hannah-arendt/alumenans/releases.

## Python runtime

- **CPython 3.13**, from python-build-standalone: PSF License Agreement
  (`licenses/python`).
- The runtime includes libraries that CPython links against, such as
  OpenSSL, SQLite, libffi, zlib, bzip2, xz and expat. Each is under its
  own permissive license.

## FFmpeg (GPL-3.0)

`imageio-ffmpeg` (BSD-2-Clause) bundles an `ffmpeg` program. That build is
configured with `--enable-gpl --enable-version3` and includes x264 and
x265, so the program is distributed under the **GNU GPL, version 3**.
ALumenans runs it as a separate program, to decode audio. Its license and
the GPL text are in `licenses/ffmpeg`. For where to get its source code,
see [Source code](#source-code). Run `ffmpeg -version` to see the exact
version and build configuration.

## Copyleft components used in-process

- **mutagen**: GPL-2.0-or-later. It reads the duration of a recording.
- **certifi**: MPL-2.0. **tqdm**: MPL-2.0 and MIT. ALumenans ships both
  unmodified.

## Permissively licensed components

**MIT:**
- annotated-doc, annotated-types, anyio, bottle, DAWG-Python, DAWG2-Python,
  docopt, filelock, h11
- ipymarkup, markdown-it-py, mdurl
- natasha, navec, razdel, slovnet, yargy
- onnx-asr, onnxruntime, pydantic, pydantic-core
- pymorphy2, pymorphy2-dicts-ru, pymorphy3, pymorphy3-dicts-ru
- PyYAML, rich, setuptools, typer, typing-inspection
- on the Mac: the pyobjc packages (pyobjc-core and the Cocoa, Quartz,
  Security, UniformTypeIdentifiers and WebKit frameworks)
- on Windows: pythonnet, clr-loader and cffi

**BSD:**
- click, fsspec, httpcore, httpx, idna, Jinja2, MarkupSafe, mpmath,
  networkx, protobuf, Pygments, pywebview, sympy
- imageio-ffmpeg (the Python package; the ffmpeg program is covered above)
- on Windows: pycparser
- proxy_tools: its metadata says MIT, but the license file in its
  upstream repository is BSD. The BSD text is the one included.

**Apache-2.0:**
- flatbuffers, gliner, hf-xet, huggingface-hub, intervaltree,
  safetensors, sentencepiece, sortedcontainers
- sherpa-onnx, sherpa-onnx-bin, sherpa-onnx-core
- tokenizers, transformers

**Other permissive licenses:**
- **sqlcipher3**: Zlib. It encrypts the study and interview files, with
  two libraries compiled into it: **SQLCipher** 4.20.0 (BSD-3-Clause,
  Zetetic LLC; `licenses/sqlcipher`) and **OpenSSL** 3.6.0 (Apache-2.0;
  `licenses/openssl`).
- **numpy**: BSD-3-Clause, with bundled parts under 0BSD, MIT, Zlib and
  CC0-1.0.
- **torch**: BSD-3-Clause, with bundled parts under Apache-2.0
  (including the LLVM exception), BSD-2-Clause, BSL-1.0 and MIT.
- **regex**: Apache-2.0 and CNRI-Python.
- **packaging**: Apache-2.0 or BSD-2-Clause.
- **shellingham**: ISC.
- **typing-extensions**: PSF-2.0.

## Data

- **Russian morphology dictionaries** (pymorphy2-dicts-ru,
  pymorphy3-dicts-ru) are compiled from the
  [OpenCorpora](http://opencorpora.org) project.
- **Natasha's models** (navec embeddings, slovnet NER and morphology) ship
  inside the `natasha` package, under the MIT license.

## Models downloaded on first launch

These models are not part of the installer. ALumenans downloads them from
their publishers the first time it starts, and each stays under its own
license:

| Model | Used for | License |
|---|---|---|
| GigaAM v3 (`istupakov/gigaam-v3-onnx`) | speech recognition | MIT |
| Silero VAD (`istupakov/silero-vad-onnx`) | voice activity detection | MIT |
| 3D-Speaker ERes2Net (`3dspeaker_speech_eres2net_sv_en_voxceleb_16k`) | telling speakers apart | Apache-2.0 |
| GLiNER multi v2.1 (`urchade/gliner_multi-v2.1`) | finding names and places | Apache-2.0 |
