# monograph/pipes.py (optimized: lazy loading & optional disable)
import os
from typing import Any, List, Optional

_SUMMARIZER = None
_QA = None
_FAILED = False

def _disabled() -> bool:
    # You can disable HF pipelines with environment variable for speed
    return os.getenv("MONO_DISABLE_HF", "1") not in ("0", "false", "False")

def _ensure_loaded():
    global _SUMMARIZER, _QA, _FAILED
    if _FAILED or _SUMMARIZER is not None or _QA is not None:
        return
    if _disabled():
        # Respect disable flag by not importing transformers at all
        _FAILED = True
        return
    try:
        from transformers import pipeline  # type: ignore
        # Use small models and CPU, non-dynamic shapes to speed cold start
        _SUMMARIZER = pipeline("summarization", model="google/flan-t5-small", device=-1)
        _QA = pipeline("text2text-generation", model="google/flan-t5-small", device=-1)
    except Exception:
        _FAILED = True
        _SUMMARIZER, _QA = None, None

@property
def summarizer_pipe():  # type: ignore
    # Backwards-compatible name; returns None unless enabled and loaded
    _ensure_loaded()
    return _SUMMARIZER

@property
def qa_pipe():  # type: ignore
    _ensure_loaded()
    return _QA

def pipe_extract_text(pipe_result: Any) -> Optional[str]:
    if not pipe_result:
        return None
    item = pipe_result[0] if isinstance(pipe_result, (list, tuple)) else pipe_result
    if isinstance(item, dict):
        for k in ('generated_text', 'summary_text', 'output_text', 'text'):
            v = item.get(k)
            if v:
                return str(v)
        # fall back to first truthy value
        for v in item.values():
            if v:
                return str(v)
        return None
    return str(item)
