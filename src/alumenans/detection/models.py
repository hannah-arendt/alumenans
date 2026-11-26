"""The anonymisation models (Natasha NER, GLiNER): loading and readiness."""

import os
import threading

from alumenans.detection.morphology import morph_tools

_tagger = None


_gliner = None


_tagger_lock = threading.Lock()


_gliner_lock = threading.Lock()


def gliner_model(local_files_only=False):
    global _gliner
    with _gliner_lock:
        if _gliner is None:
            from gliner import GLiNER
            _gliner = GLiNER.from_pretrained(
                "urchade/gliner_multi-v2.1",
                local_files_only=local_files_only,
                low_cpu_mem_usage=True,
            )
    return _gliner


def tagger():
    """Natasha loads on first use, so that the window opens straight away."""
    global _tagger
    with _tagger_lock:
        if _tagger is None:
            from natasha import Segmenter, NewsEmbedding, NewsNERTagger
            _tagger = (Segmenter(), NewsNERTagger(NewsEmbedding()))
    return _tagger


MODEL_STATE = {"setup": "idle", "natasha": "idle", "gliner": "idle"}


_warmup_start_lock = threading.Lock()


_warmup_thread = None


def warmup():
    """Loads the installed models from disk without touching the network."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    MODEL_STATE["setup"] = "models"
    try:
        tagger()
        MODEL_STATE["natasha"] = "ready"
    except Exception:
        MODEL_STATE["natasha"] = "off"
    try:
        gliner_model(local_files_only=True)
        MODEL_STATE["gliner"] = "ready"
    except Exception as error:
        MODEL_STATE["gliner"] = "off"
        MODEL_STATE["error"] = str(error)[:300]
    MODEL_STATE["setup"] = "done"


def request_anonymizer_warmup():
    global _warmup_thread
    with _warmup_start_lock:
        if MODEL_STATE.get("setup") in {"models", "done"}:
            return
        MODEL_STATE["setup"] = "models"
        MODEL_STATE["natasha"] = "loading"
        MODEL_STATE["gliner"] = "loading"
        _warmup_thread = threading.Thread(
            target=warmup,
            daemon=True,
            name="anonymizer-warmup",
        )
        _warmup_thread.start()


def prepare_anonymizer_models_cli():
    try:
        morph_tools()
        tagger()
        gliner_model()
        return 0
    except Exception as error:
        print(str(error)[:400] or type(error).__name__, flush=True)
        return 1
