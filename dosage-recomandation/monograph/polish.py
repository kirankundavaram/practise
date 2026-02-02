# monograph/polish.py
from __future__ import annotations
from typing import List
import re

# Reuse your transformers pipes (optional at runtime)
try:
    from .pipes import qa_pipe, summarizer_pipe, pipe_extract_text
except Exception:
    qa_pipe = None
    summarizer_pipe = None
    def pipe_extract_text(x):  # fallback
        if isinstance(x, (list, tuple)) and x:
            x = x[0]
        if isinstance(x, dict):
            for k in ("generated_text", "summary_text", "output_text", "text"):
                if k in x and x[k]:
                    return x[k]
            for v in x.values():
                if v:
                    return v
            return ""
        return str(x) if x is not None else ""

_UNIT_FIXES = [
    (r'(\d)\s*(mg\b)', r'\1 mg'),
    (r'(\d)\s*(mcg\b)', r'\1 mcg'),
    (r'(\d)\s*(g\b)', r'\1 g'),
    (r'(\d)\s*(mL\b)', r'\1 mL'),
    (r'\s{2,}', ' ')
]

def _norm_units(s: str) -> str:
    if not s:
        return s
    for pat, rep in _UNIT_FIXES:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    return s.strip()

def polish_evidence_sentence(s: str) -> str:
    """Polish a single sentence (fallback to cleaned text if no pipe)."""
    s = (s or "").strip()
    if not s:
        return s
    try:
        if qa_pipe:
            prompt = (
                "Rewrite the following dosage evidence clearly and professionally. "
                "Preserve facts, do not invent info, normalize units (e.g., 100 mg, 10 mL). "
                "Return only the rewritten text.\n\n"
                f"TEXT:\n{s}"
            )
            out = qa_pipe(prompt, max_length=2048, do_sample=False)
            text = pipe_extract_text(out)
            return _norm_units(text)
        elif summarizer_pipe:
            out = summarizer_pipe(s[:1000], max_length=200, min_length=30, do_sample=False)
            text = pipe_extract_text(out)
            return _norm_units(text)
    except Exception:
        pass
    return _norm_units(s)

def polish_evidence_list(lines: List[str]) -> str:
    """
    Polish a list of evidence snippets into a cohesive paragraph.
    No hard sentence cap: use as much as needed, but keep it concise.
    """
    lines = [x for x in (lines or []) if str(x).strip()]
    if not lines:
        return ""
    joined = " ".join(lines)
    try:
        if qa_pipe:
            prompt = (
                "You are a clinical editor. Merge the following dosage evidence into a cohesive, well-structured paragraph. "
                "Preserve the content, avoid fabricating details, and normalize units (e.g., 100 mg, 10 mL). "
                "Prefer active voice; remove table artifacts and repeated phrases; keep dosage facts and frequencies clear. "
                "Return only the polished paragraph.\n\n"
                f"EVIDENCE:\n{joined}"
            )
            out = qa_pipe(prompt, max_length=6000, do_sample=False)
            text = pipe_extract_text(out)
            return _norm_units(text)
        elif summarizer_pipe:
            out = summarizer_pipe(joined[:3000], max_length=600, min_length=120, do_sample=False)
            text = pipe_extract_text(out)
            return _norm_units(text)
    except Exception:
        pass
    return _norm_units(joined)
