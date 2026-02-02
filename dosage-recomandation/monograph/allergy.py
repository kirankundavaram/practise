# monograph/allergy.py
import re
from typing import Dict, List, Set

# ✨ New: LLM polishing (optional, graceful fallback)
try:
    from .pipes import qa_pipe, pipe_extract_text  # type: ignore
except Exception:
    qa_pipe, pipe_extract_text = None, None

# ----------------------------
# Synonyms / Lexicon
# ----------------------------
# Minimal synonym map; extend as needed (RxNorm/etc. if you have)
_SYNONYMS = {
    "nsaid": {"nsaid", "nsaids", "nonsteroidal anti-inflammatory", "nonsteroidal anti inflammatory"},
    "aspirin": {"aspirin", "asa", "acetylsalicylic acid"},
    "diclofenac": {"diclofenac", "voltaren"},
    "ibuprofen": {"ibuprofen", "motrin", "advil"},
    "naproxen": {"naproxen", "aleve"},
    "cox-2": {"cox-2", "cox2", "celecoxib"},
    "celecoxib": {"celecoxib", "celebrex", "cox-2", "cox2"},
    "penicillin": {"penicillin", "penicillins", "pcn"},
    "cephalosporin": {"cephalosporin", "cephalosporins", "cef-"},
    "sulfa": {"sulfa", "sulfonamide", "sulfonamides"},
    "peanut": {"peanut", "arachis"},
    "soy": {"soy", "soya", "soybean"},
    "lactose": {"lactose"},
    "shellfish": {"shellfish"},
}

# ----------------------------
# Monograph pattern detectors
# ----------------------------
_PATTERNS = {
    "contra": re.compile(
        r"(contraindicat(?:ed|ion))|"
        r"(do\s+not\s+use)|"
        r"(history\s+of\s+severe\s+hypersensitivity)|"
        r"(anaphylaxi(?:s|es))",
        re.I
    ),
    "hypersens": re.compile(
        r"(hypersensitivit(?:y|ies))|"
        r"(allergy|allergies|allergic\s+reactions?)|"
        r"(bronchospasm|urticaria|angioedema)",
        re.I
    ),
    # Explicit cross-reactivity lines common with NSAIDs/aspirin
    "nsaid_cross": re.compile(
        r"(aspirin[-\s]sensitive)|(aspirin\s+triad)|"
        r"(cross[-\s]?react(?:ion|ive|ivity)\s+with\s+other\s+nsaids?)|"
        r"(patients?\s+with\s+asthma,\s*urticaria,\s*or\s+other\s+allergic\s+type\s+reactions\s+after\s+taking\s+aspirin\s+or\s+other\s+nsaids?)",
        re.I
    ),
}

# ----------------------------
# Helpers
# ----------------------------
def _normalize_tokens(items: List[str]) -> Set[str]:
    """
    Normalize a list of strings into a set of lowercase tokens, splitting on whitespace,
    punctuation, commas/semicolons/slashes. Keeps hyphens as separators.
    """
    toks = set()
    for it in (items or []):
        s = re.sub(r'[^a-z0-9\s\-]+', ' ', str(it).lower()).strip()
        for tok in re.split(r'[\s,;\/]+', s):
            tok = tok.strip()
            if tok:
                toks.add(tok)
    return toks

def _expand_synonyms(tokens: Set[str]) -> Set[str]:
    """
    Expand tokens with any matching synonym bundles. Adds the bundle key and all
    members if any overlap is found.
    """
    out = set(tokens)
    for key, syns in _SYNONYMS.items():
        if tokens & syns or key in tokens:
            out.add(key)
            out |= syns
    return out

def _severity_rank(val: str) -> int:
    order = {"info": 0, "caution": 1, "danger": 2}
    return order.get((val or "").lower(), 0)

def _max_severity(a: str, b: str) -> str:
    return a if _severity_rank(a) >= _severity_rank(b) else b

def extract_allergy_signals_from_monograph(text: str) -> Dict[str, bool]:
    """Return coarse-grained signals & whether NSAID cross-reactivity is discussed."""
    t = text or ""
    return {
        "mentions_contra": bool(_PATTERNS["contra"].search(t)),
        "mentions_hypersens": bool(_PATTERNS["hypersens"].search(t)),
        "mentions_nsaid_cross": bool(_PATTERNS["nsaid_cross"].search(t)),
        # quick heuristic for NSAID/aspirin presence
        "mentions_nsaid": bool(re.search(r"\bnsaid[s]?\b|nonsteroidal", t, re.I)),
        "mentions_aspirin": bool(re.search(r"\baspirin\b|\basa\b", t, re.I)),
    }

# ----------------------------
# ✨ Polishing via qa_pipe
# ----------------------------
def _polish_sentence(text: str) -> str:
    """Use qa_pipe to lightly rewrite a sentence with a clinical, concise tone."""
    if not qa_pipe or not text:
        return text
    prompt = (
        "Rewrite the statement below in a concise, professional, clinician-facing style. "
        "Keep it factual, unambiguous, and avoid hedging. Do not add new facts.\n"
        f"TEXT: {text}"
    )
    try:
        res = qa_pipe(prompt, max_length=128, do_sample=False)
        out = (pipe_extract_text(res) or "").strip()
        # guard against over-generation / prompt echoes
        out = re.sub(r'^\s*(rewrite|statement|text)[:\-–]\s*', '', out, flags=re.I)
        return out or text
    except Exception:
        return text

def _polish_list(lines: List[str]) -> List[str]:
    return [_polish_sentence(s) for s in (lines or []) if s]

def _polish_body_preserve_prefix(prefix: str, body: str) -> str:
    """
    Polish only the body text with qa_pipe, then reattach the prefix.
    Ensures the allergy list (prefix) is never removed by the model.
    """
    if not body:
        return (prefix or "").strip()
    polished = _polish_sentence(body)
    return (prefix or "") + (polished or body)

# ----------------------------
# Drug-token helpers
# ----------------------------
def _drug_token_set(drug_name: str) -> Set[str]:
    """
    Build a token set for the prescribed drug, including synonym expansion
    (brand names, class shorthands present in _SYNONYMS).
    """
    tokens = _normalize_tokens([drug_name])  # handles multi-word names into component tokens
    tokens = _expand_synonyms(tokens)
    # Include a compacted version (no spaces/hyphens) for cases like "acetylsalicylic acid" -> "acetylsalicylicacid"
    compact = re.sub(r'[^a-z0-9]+', '', (drug_name or '').lower())
    if compact:
        tokens.add(compact)
    return tokens

def _string_found_in_text_any(needles: Set[str], haystack: str) -> bool:
    """Case-insensitive substring test for any needle in haystack."""
    if not needles:
        return False
    if not haystack:
        return False
    t = haystack.lower()
    return any(n and n in t for n in needles)

def _expand_allergy_string_to_tokens(allergy_str: str) -> Set[str]:
    """Normalize a single allergy string and expand by synonyms if known."""
    toks = _normalize_tokens([allergy_str])
    return _expand_synonyms(toks)

# ----------------------------
# Main evaluator
# ----------------------------
def evaluate_allergies(patient_allergies: List[str], monograph_text: str, drug_name: str = "") -> Dict:
    """
    Compare patient allergies with monograph allergy/cross-reactivity language.

    Returns a dict:
      {
        "alert": {AlertType, Severity, Annotation: [polished strings...] } | None,
        "severity": "info"|"caution"|"danger",
        "matches": [raw strings...],
        "actions": [raw strings...],
        "signals": {...},
        "matches_polished": [strings...],
        "actions_polished": [strings...],
        "direct_drug_allergy": bool,
        "unmatched_allergies": [str, ...],
        "allergies_csv": str,
        "unmatched_allergies_csv": str,
        "no_conflict_body": str | None,
        "no_conflict_body_polished": str | None,
        "no_conflict_note": str | None,
        "no_conflict_note_polished": str | None,
      }
    """
    monograph_text = monograph_text or ""
    ptoks_base = _normalize_tokens(patient_allergies)
    ptoks = _expand_synonyms(ptoks_base)

    sigs = extract_allergy_signals_from_monograph(monograph_text)

    matches: List[str] = []
    actions: List[str] = []
    severity = "info"  # info | caution | danger
    direct_drug_allergy = False

    # Heuristic: is this an NSAID context (helps class logic for diclofenac/ibuprofen/naproxen/celecoxib, etc.)
    suspect_nsaid_class = sigs["mentions_nsaid"] or bool(
        re.search(r"\b(diclofenac|ibuprofen|naproxen|cox-?2|celecoxib)\b", monograph_text, re.I)
    )

    # --- Direct drug allergy (exact or synonym) -> Danger hard stop
    drug_norm = re.sub(r'[^a-z0-9]+', ' ', (drug_name or "").lower()).strip()
    drug_toks: Set[str] = set()
    if drug_norm:
        drug_toks = _drug_token_set(drug_name)  # includes synonym expansion
        if ptoks & drug_toks:
            direct_drug_allergy = True
            matches.append(
                f"This patient has a documented allergy to {drug_name}, which is the medication currently being prescribed."
            )
            actions.append(
                f"{drug_name} is contraindicated for this patient. Do not initiate therapy; select an alternative agent to avoid a serious hypersensitivity reaction."
            )
            severity = "danger"

    # --- NSAID/aspirin cross-reactivity cases (e.g., Diclofenac with 'NSAID' or 'Aspirin' allergy)
    if suspect_nsaid_class and not direct_drug_allergy:
        if {"aspirin", "nsaid"} & ptoks:
            matches.append(
                "The patient reports aspirin/NSAID hypersensitivity, and cross-reactivity among NSAIDs is well-described."
            )
            actions.append(
                "Avoid this NSAID; consider a non-NSAID alternative and ensure the allergy is documented."
            )
            severity = _max_severity(severity, "danger")

    # --- Common class/excipient alignments that merit caution
    if "penicillin" in ptoks and re.search(r"\bpenicillin", monograph_text, re.I):
        matches.append("Penicillin allergy noted in the context of monograph references.")
        severity = _max_severity(severity, "caution")
    if "cephalosporin" in ptoks and re.search(r"\bcephalosporin", monograph_text, re.I):
        matches.append("Cephalosporin allergy noted in the context of monograph references.")
        severity = _max_severity(severity, "caution")
    if "sulfa" in ptoks and re.search(r"\bsulfonamid", monograph_text, re.I):
        matches.append("Sulfonamide allergy noted in the context of monograph references.")
        severity = _max_severity(severity, "caution")

    for excipient, label, pat in [
        ("peanut", "peanut", r"peanut|arachis"),
        ("soy", "soy", r"soy|soya|soybean"),
        ("lactose", "lactose", r"lactose"),
    ]:
        if any(key in ptoks for key in _SYNONYMS.get(label, {label})) and re.search(pat, monograph_text, re.I):
            matches.append(f"Excipient caution: {label} is mentioned in the monograph.")
            severity = _max_severity(severity, "caution")

    # --- Lift to caution if monograph has generic hypersensitivity text and patient has any reported allergy
    if sigs["mentions_hypersens"] and patient_allergies and severity == "info":
        severity = "caution"

    # ---------- Build unmatched list + “no specific conflict” message ----------
    allergies_csv = ", ".join([a for a in patient_allergies if a]) if patient_allergies else ""
    unmatched_allergies: List[str] = []
    if patient_allergies:
        for al_str in patient_allergies:
            al_tokens = _expand_allergy_string_to_tokens(al_str)
            seen_in_mono = _string_found_in_text_any(al_tokens, monograph_text)
            overlaps_drug = bool(al_tokens & drug_toks) if drug_toks else False
            if not seen_in_mono and not overlaps_drug:
                unmatched_allergies.append(al_str)

    no_conflict_note = None
    no_conflict_note_polished = None
    no_conflict_body = None
    no_conflict_body_polished = None

    # Only show this message if we have allergy inputs but *no* specific matches were detected
    if (not matches) and patient_allergies:
        drug_disp = drug_name or "this medication"
        tail_bits = []
        if sigs.get("mentions_hypersens"):
            tail_bits.append("the monograph contains general hypersensitivity warnings")
        if sigs.get("mentions_nsaid_cross"):
            tail_bits.append("class-level NSAID cross-reactivity is discussed")
        tail = f" However, {', and '.join(tail_bits)}." if tail_bits else ""

        prefix = f"Patient-reported allergies: {allergies_csv}. "

        if unmatched_allergies:
            unmatched_csv = ", ".join(unmatched_allergies)
            no_conflict_body = (
                f"The monograph for {drug_disp} does not explicitly cite these allergens or a specific cross-reactivity "
                f"with {drug_disp} in the extracted text; no direct conflict was detected for: {unmatched_csv}.{tail} "
                f"Please verify allergy history and monitor as appropriate."
            )
        else:
            # All allergies were mentioned generically or overlapped via class tokens, but no concrete conflict text found.
            no_conflict_body = (
                f"The monograph for {drug_disp} does not explicitly document a direct conflict with these inputs "
                f"in the extracted text; no specific contraindication was detected.{tail} "
                f"Please verify allergy history and monitor as appropriate."
            )

        # Full notes (raw + polished) with preserved prefix
        no_conflict_note = prefix + no_conflict_body
        no_conflict_body_polished = _polish_sentence(no_conflict_body) if no_conflict_body else None
        no_conflict_note_polished = _polish_body_preserve_prefix(prefix, no_conflict_body)

    # ✨ Polished phrasing for matches/actions (non-destructive; we still return originals too)
    matches_polished = _polish_list(matches)
    actions_polished = _polish_list(actions)

    # If polishing returned empty by accident, keep originals
    if not any(matches_polished) and matches:
        matches_polished = matches[:]
    if not any(actions_polished) and actions:
        actions_polished = actions[:]

    # Build alert bundle (use polished text for UI annotation)
    alert = None
    if matches_polished or actions_polished or severity in ("caution", "danger"):
        annotation: List[str] = []
        if matches_polished:
            # Join matches into one polished paragraph to keep the UI tidy
            annotation.append("Findings: " + " ".join([m.strip().rstrip(".") + "." for m in matches_polished if m]))
        if actions_polished:
            annotation.append("Action: " + " ".join([a.strip().rstrip(".") + "." for a in actions_polished if a]))
        if not annotation:
            annotation.append("Allergy/hypersensitivity language present in the monograph; review patient history.")

        alert = {
            "AlertType": "Allergy Alert",
            "Severity": severity,
            "Annotation": annotation
        }

    return {
        "alert": alert,
        "severity": severity,
        "matches": matches,                               # raw
        "actions": actions,                               # raw
        "matches_polished": matches_polished,             # ✨ polished
        "actions_polished": actions_polished,             # ✨ polished
        "signals": sigs,
        "direct_drug_allergy": direct_drug_allergy,       # for UI gating
        "unmatched_allergies": unmatched_allergies,       # for chips/hints
        "allergies_csv": allergies_csv,                   # convenience for UI
        "unmatched_allergies_csv": ", ".join(unmatched_allergies) if unmatched_allergies else "",
        "no_conflict_body": no_conflict_body,             # body only
        "no_conflict_body_polished": no_conflict_body_polished,
        "no_conflict_note": no_conflict_note,             # full text incl. prefix
        "no_conflict_note_polished": no_conflict_note_polished,  # full + polished body
    }

# Fast lowercase helper
_to_lc = lambda s: (s or '').strip().lower()
