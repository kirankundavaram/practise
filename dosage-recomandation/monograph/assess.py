# monograph/assess.py
from __future__ import annotations

from typing import Dict, Any, List, Optional
import re
import html
from types import SimpleNamespace

from .state import last_state

# Polishing helpers (fallbacks included if unavailable)
try:
    from .polish import polish_evidence_sentence, polish_evidence_list  # type: ignore
except Exception:
    def polish_evidence_sentence(s: str) -> str:
        return s or ""
    def polish_evidence_list(lines: List[str]) -> str:
        return " ".join([x for x in lines if x])

from .utils import (
    extract_full_text,
    extract_tables_as_bullets,
    extract_tables_as_html,
)

from .dosage import (
    extract_dosage_info,
    classify_dose,
    _pick_route_key_for_range,
    _safe_ranges_summary,
)
try:
    from .dosage import infer_best_route  # type: ignore
except Exception:
    infer_best_route = None

from .alerts import (
    generate_structured_alerts,
    find_allergy_alerts,
)
from .recommendations import derive_dosage_recommendations
from .highlight import (
    render_dose_summary_html,
    render_structured_alerts_html,
)
from .routes_catalog import detect_route_in_text

# OPTIONAL allergy evaluator (new, richer logic)
try:
    from .allergy import evaluate_allergies  # type: ignore
except Exception:
    def evaluate_allergies(patient_allergies: List[str], monograph_text: str, drug_name: str = "") -> Dict[str, Any]:
        # Graceful no-op if the module isn't present
        return {"alert": None, "severity": "info", "matches": [], "actions": [], "signals": {}}

# Regex fallbacks
try:
    from .regexes import (
        RE_MONITOR, RE_DOSE, RE_DOSE_G, RE_DOSE_MCG,
        RE_CONTRA, RE_INTERACT, CONTRA_KEY_TERMS, INTERACT_DRUGS,
    )
except Exception:
    RE_MONITOR = re.compile(r'\b(monitor|baseline|check)\b', re.IGNORECASE)
    RE_DOSE = re.compile(r'(\d+(?:\.\d+)?)\s*mg\b', re.IGNORECASE)
    RE_DOSE_G = re.compile(r'(\d+(?:\.\d+)?)\s*g\b', re.IGNORECASE)
    RE_DOSE_MCG = re.compile(r'(\d+(?:\.\d+)?)\s*mcg\b', re.IGNORECASE)
    RE_CONTRA = re.compile(r'contraindicat', re.IGNORECASE)
    RE_INTERACT = re.compile(r'\b(interact|interaction|concomit|co-?administration|synerg|increas|potentiat)\b', re.IGNORECASE)
    CONTRA_KEY_TERMS = ['pregnan','third trimester','breast','lactat','renal','hepatic','cabg','asthma','ulcer','bleed']
    INTERACT_DRUGS = ['warfarin','aspirin','lithium','digoxin','methotrexate','cyclosporine','tacrolimus','diuretic','ace inhibitor','metformin','ssri','snri','pemetrexed','probenecid','quinolone','voriconazole','rifampin']


def _find_contraindications(text_lines_lower: List[str]) -> List[str]:
    out, seen = [], set()
    for ln in text_lines_lower:
        if RE_CONTRA.search(ln) or any(k in ln for k in CONTRA_KEY_TERMS):
            k = ln.strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
    return out

def _find_interaction_lines(text_lines_lower: List[str]) -> List[str]:
    out, seen = [], set()
    for ln in text_lines_lower:
        if RE_INTERACT.search(ln) or any(d in ln for d in INTERACT_DRUGS):
            k = ln.strip()
            if k and k not in seen:
                seen.add(k)
                out.append(k)
    return out

def _color_line(item: Dict[str, Any]) -> str:
    color_map = {'critical': 'red', 'caution': 'orange', 'safe': 'green', 'info': 'blue'}
    return (
        f"<span style='color:{color_map.get(item.get('level',''),'black')}; "
        f"font-weight:bold;'>{html.escape(item.get('text',''))}</span>"
    )

def _infer_best_route_fallback(dose_info: Dict[str, Any], proposed_dose_text: str = "") -> Optional[str]:
    try:
        explicit = detect_route_in_text(proposed_dose_text or "")
        if explicit:
            return explicit
    except Exception:
        pass
    rd = (dose_info or {}).get('route_doses', {}) or {}
    if not rd:
        return None
    if "Unspecified" in rd:
        return "Unspecified"
    return sorted(rd.keys())[0]

def _coerce_to_text(val: Any) -> str:
    """
    Normalize any list/dict/etc. to a single string for safe HTML escaping.
    """
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (list, tuple)):
        return " ".join(_coerce_to_text(x) for x in val if x)
    if isinstance(val, dict):
        if 'text' in val and val['text']:
            return _coerce_to_text(val['text'])
        return " ".join(_coerce_to_text(v) for v in val.values() if v)
    return str(val)

def _format_route_case_html(route_name: str, doses: List[float], polished: Any, concentration_based: bool) -> str:
    """Pretty HTML block for a route: range, examples, and polished narrative."""
    title = html.escape(route_name or "Unspecified")
    dose_html = ""
    examples_html = ""
    note_html = ""

    if doses:
        try:
            mn, mx = min(doses), max(doses)
            dose_html = f"<div><strong>Fixed-dose range:</strong> {mn:.0f}–{mx:.0f} mg</div>"
        except Exception:
            pass
        try:
            uniq = []
            for d in doses:
                v = float(d)
                if v not in uniq:
                    uniq.append(v)
            examples = ", ".join([f"{v:.0f} mg" for v in uniq[:5]])
            if examples:
                examples_html = f"<div><strong>Examples:</strong> {examples}</div>"
        except Exception:
            pass

    if concentration_based:
        note_html = "<div><em>Concentration-based dosing noted (mg/mL); compare by volume/frequency per label.</em></div>"

    polished_text = _coerce_to_text(polished)
    body = html.escape(polished_text or "").replace("\n", " ").strip()
    body = re.sub(r'\s{2,}', ' ', body)

    return (
        "<div class='ai-summary' style='margin-top:10px'>"
        f"<div style='font-weight:600;margin-bottom:6px'>{title}</div>"
        f"{dose_html}{examples_html}{note_html}"
        f"<div style='margin-top:6px;line-height:1.6'>{body}</div>"
        "</div>"
    )

# --------------------------
# number-word to digit helper
# --------------------------
_NUMWORDS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12
}

def _replace_number_words(txt: str) -> str:
    """
    Replace simple number words (one..twelve) with digits so regexes pick them up.
    Keeps case-insensitive.
    """
    def _repl(m):
        w = m.group(0).lower()
        return str(_NUMWORDS.get(w, w))
    if not txt:
        return txt
    pattern = r'\b(' + '|'.join(re.escape(k) for k in _NUMWORDS.keys()) + r')\b'
    return re.sub(pattern, _repl, txt, flags=re.IGNORECASE)
# --------------------------
# end helper
# --------------------------

# --------------------------
# NEW: frequency-aware proposed dose parser
# (same as in your recommendations/previous assess versions)
# --------------------------
def _parse_proposed_total(proposed_dose_text: str):
    """
    Parse proposed dose text to return (per_admin_mg, freq_per_day, total_mg_per_day).

    Supports patterns like:
      - '150 mg 3 times a day' -> (150.0, 3, 450.0)
      - '150 mg once daily'   -> (150.0, 1, 150.0)
      - '150 mg every 8 hours'-> (150.0, 3, 450.0)
      - '150 mg q8h'          -> (150.0, 3, 450.0)
      - '450 mg/day'          -> (450.0, None, 450.0)

    Returns (None, None, None) if nothing parseable.
    """
    if not proposed_dose_text:
        return (None, None, None)
    # normalize common number words -> digits so "three times" is recognized
    txt = _replace_number_words(proposed_dose_text.lower())

    # per-admin mg extraction
    try:
        mg_list = [float(x) for x in RE_DOSE.findall(txt or "")]
    except Exception:
        mg_list = []
    per_admin = max(mg_list) if mg_list else None

    freq = None

    # common word patterns
    if re.search(r'\bonce\b|one time|one-time|daily|per day|every day|\bqd\b|\bq\.d\b', txt):
        freq = 1
    if re.search(r'\btwice\b|two times|two-time|\bbid\b|\bb\.i\.d\b|twice daily', txt):
        freq = 2

    # explicit "N times" pattern (now also captures words turned into digits)
    m = re.search(r'(\d+)\s*(?:x|times|time)\s*(?:a|per)?\s*(?:day|daily|d)?', txt)
    if m:
        try:
            freq = int(m.group(1))
        except Exception:
            pass

    # every N hours -> 24 / N
    m2 = re.search(r'every\s*(\d+(?:\.\d+)?)\s*(?:-?hr|hours|hour|h)\b', txt)
    if m2:
        try:
            hours = float(m2.group(1))
            if hours > 0:
                freq = max(1, int(round(24.0 / hours)))
        except Exception:
            pass

    # q8h, q12h patterns
    m3 = re.search(r'\bq(\d{1,2})h\b', txt)
    if m3:
        try:
            hours = int(m3.group(1))
            if hours > 0:
                freq = max(1, int(round(24.0 / hours)))
        except Exception:
            pass

    # explicit total per day: '450 mg/day' or '450 mg per day'
    total_explicit = None
    m4 = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*mg\s*(?:/|per)\s*day', txt)
    if m4:
        try:
            total_explicit = float(m4.group(1))
        except Exception:
            total_explicit = None

    # if explicit total found and no per-admin value, treat as total (freq=1)
    if total_explicit is not None and per_admin is None:
        per_admin = total_explicit
        freq = 1

    # compute total
    if per_admin is not None and freq is not None:
        total = per_admin * freq
    elif total_explicit is not None:
        total = total_explicit
    elif per_admin is not None:
        total = per_admin
        if freq is None:
            freq = 1
    else:
        total = None

    return (per_admin, freq, total)

# --------------------------
# monograph dose snippet extractor
# --------------------------
def _extract_monograph_dose_snippet(full_text: str) -> Optional[str]:
    """
    Try to find a monograph sentence that contains an mg dose and likely frequency.
    Prefer sentences with 'recommended' or 'recommended dosage/dose'.
    Returns the sentence (snippet) if found, else None.
    """
    if not full_text:
        return None
    # normalize whitespace and split into sentences (basic)
    pieces = re.split(r'(?<=[\.\?\!])\s+', full_text)
    # first pass: look for sentences mentioning recommended + dose
    for sent in pieces:
        if not sent or not RE_DOSE.search(sent):
            continue
        if re.search(r'recommend(ed|ed dosage|ed dose|recommended dose|recommended dosage)', sent, flags=re.I):
            return sent.strip()
    # second pass: any sentence containing a dose and a frequency-like token
    freq_tokens = r'(times|time|daily|per day|every|qd|q\d+h|once|twice|q\.d\b|bid|q8h|q12h)'
    for sent in pieces:
        if not sent or not RE_DOSE.search(sent):
            continue
        if re.search(freq_tokens, sent, flags=re.I):
            return sent.strip()
    # fallback: return first sentence containing a dose
    for sent in pieces:
        if RE_DOSE.search(sent):
            return sent.strip()
    return None
# --------------------------
# end extractor
# --------------------------

def assess(
    monograph_text: str,
    drug_name: str,
    patient_info: Dict[str, Any] | None = None,
    proposed_dose_text: str = "",
    route: str | None = None,
    priority: bool = True
) -> Dict[str, Any]:
    """
    Build the report dict used by templates.
    - Extract dosage evidence (including per-route cases).
    - Polish & format evidence for UI.
    - Infer route when UI does not provide it.
    - Keep behavior backward compatible.
    """
    if patient_info is None:
        patient_info = {}

    monograph_hash = hash(monograph_text)
    if last_state.get("monograph_hash") != monograph_hash:
        full_text = extract_full_text(monograph_text)
        tables = extract_tables_as_bullets(monograph_text)
        tables_html = extract_tables_as_html(monograph_text)
        last_state.update({
            "monograph_hash": monograph_hash,
            "full_text": full_text,
            "tables": tables,
            "tables_html": tables_html
        })
    else:
        full_text = last_state["full_text"]

    text_lines: List[str] = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    text_lines_lower: List[str] = [ln.lower() for ln in text_lines]

    report: Dict[str, Any] = {
        'drug': drug_name,
        'flags': [],
        'contraindications': [],
        'dose_summary': [],
        'monitoring': [],
        'allergy_alerts': [],
        'details': {}
    }

    # 1) Dosage extraction (returns route_cases)
    dosage_info = extract_dosage_info(full_text)
    report['dose_routes'] = dosage_info.get('route_doses', {})

    # Build polished & formatted per-route cases
    route_cases_raw: Dict[str, Dict[str, Any]] = dosage_info.get('route_cases', {}) or {}
    concentration_routes = set(dosage_info.get('concentration_routes', []) or [])

    # What your HTML needs:
    # - details.route_cases = RAW (doses_mg, sentences)
    # - route_cases_polished = {route: {doses_mg, sentences_polished}}
    report['details']['route_cases'] = route_cases_raw  # for your first table

    route_cases_polished: Dict[str, Dict[str, Any]] = {}
    # Optional: also provide a formatted HTML chunk if you want to embed later
    route_cases_html: Dict[str, str] = {}

    for route_name, bundle in route_cases_raw.items():
        sents = bundle.get('sentences', []) or []
        try:
            polished = polish_evidence_list(sents) if sents else ""
        except Exception:
            polished = " ".join(sents)
        polished_text = _coerce_to_text(polished)

        doses = bundle.get('doses_mg', []) or []
        route_cases_polished[route_name] = {
            "doses_mg": doses,
            "sentences_polished": polished_text,
        }

        # Optional, not used by your template, but handy if needed:
        route_cases_html[route_name] = _format_route_case_html(
            route_name=route_name,
            doses=doses,
            polished=polished_text,
            concentration_based=(route_name in concentration_routes)
        )

    report['route_cases_polished'] = route_cases_polished
    report['route_cases_html'] = route_cases_html  # not referenced by your template

    # 2) Contra/Interactions
    contra_lines = _find_contraindications(text_lines_lower)
    interaction_lines = _find_interaction_lines(text_lines_lower)

    # 3) Route inference (no UI route)
    effective_route = route
    if not effective_route:
        try:
            explicit = detect_route_in_text(proposed_dose_text or "")
        except Exception:
            explicit = None
        if explicit:
            effective_route = explicit
        else:
            if callable(infer_best_route):
                try:
                    effective_route = infer_best_route(dosage_info, proposed_dose_text)  # type: ignore
                except Exception:
                    effective_route = None
            if not effective_route:
                effective_route = _pick_route_key_for_range(dosage_info, route=None) or \
                                  _infer_best_route_fallback(dosage_info, proposed_dose_text)

    report['route_used'] = effective_route

    # 4) Range summary for the chosen route
    report['ranges_summary'] = _safe_ranges_summary(dosage_info, selected_route=effective_route)

    # 5) Monitoring & allergies (legacy simple allergy scan)
    monitor_lines = [ln for ln in text_lines if RE_MONITOR.search(ln)]
    legacy_allergy_alerts = find_allergy_alerts(text_lines_lower, patient_info.get("allergies", []))
    report['allergy_alerts'] = legacy_allergy_alerts  # kept for backward compatibility

    # 6) Parse proposed dose into mg (UPDATED: frequency-aware)
    proposed_mg = None
    parsed_proposed = SimpleNamespace(per_admin=None, freq=None, total=None)
    if proposed_dose_text:
        try:
            per_admin, freq_per_day, total_mg = _parse_proposed_total(proposed_dose_text)
            if total_mg is not None:
                proposed_mg = float(total_mg)
            else:
                # fallback to previous extraction behavior
                nums = RE_DOSE.findall(proposed_dose_text)
                gnums = RE_DOSE_G.findall(proposed_dose_text)
                mcgnums = RE_DOSE_MCG.findall(proposed_dose_text)
                mg_list = [float(x) for x in nums] + [float(x) * 1000 for x in gnums] + [float(x) / 1000 for x in mcgnums]
                if mg_list:
                    proposed_mg = max(mg_list)
            parsed_proposed.per_admin = per_admin
            parsed_proposed.freq = freq_per_day
            parsed_proposed.total = total_mg
        except Exception:
            proposed_mg = None
            parsed_proposed = SimpleNamespace(per_admin=None, freq=None, total=None)

    # store parsed proposed for template use (as object with attributes)
    report['details']['proposed_dose_parsed'] = parsed_proposed

    # 6b) Parse monograph heuristic to extract a likely monograph dosing snippet and parse it
    monograph_parsed_obj = SimpleNamespace(per_admin=None, freq=None, total=None, total_min=None, total_max=None, range_is_daily=False)
    try:
        monograph_snip = _extract_monograph_dose_snippet(full_text)
        if monograph_snip:
            # Normalize numbers words so parsing is better
            snip = _replace_number_words(monograph_snip)

            # 1) Look for explicit "150 to 200 mg/day" or "150-200 mg/day" patterns -> mark totals and range_is_daily
            rng = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*(?:-|to|–)\s*([0-9]+(?:\.[0-9]+)?)\s*mg\s*(?:/|per)?\s*day', snip, flags=re.I)
            if rng:
                try:
                    tmin = float(rng.group(1))
                    tmax = float(rng.group(2))
                    monograph_parsed_obj.total_min = tmin
                    monograph_parsed_obj.total_max = tmax
                    monograph_parsed_obj.range_is_daily = True
                except Exception:
                    pass

            # 2) Look for explicit single total like "150 mg/day"
            if monograph_parsed_obj.total_min is None:
                single = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*mg\s*(?:/|per)\s*day', snip, flags=re.I)
                if single:
                    try:
                        t = float(single.group(1))
                        monograph_parsed_obj.total = t
                        monograph_parsed_obj.range_is_daily = True
                    except Exception:
                        pass

            # 3) If the sentence contains "divid" (divided) and has parenthetical examples, try to extract per-admin examples
            #    but DO NOT change total_min/max that were just set (we only use per-admin examples for display/debug)
            if re.search(r'\bdivid', snip, flags=re.I):
                # find parenthetical content like "(50 mg three times a day or four times a day)"
                par = re.search(r'\(([^)]+)\)', snip)
                if par:
                    par_txt = par.group(1)
                    # parse example per-admin+freq from parenthetical text using the same helper
                    try:
                        p_admin, p_freq, p_total = _parse_proposed_total(par_txt)
                        if p_admin is not None:
                            monograph_parsed_obj.per_admin = p_admin
                        if p_freq is not None:
                            monograph_parsed_obj.freq = p_freq
                        # if parenthetical also contains an explicit /day total, capture that too (rare)
                        if p_total is not None and monograph_parsed_obj.total is None:
                            monograph_parsed_obj.total = p_total
                    except Exception:
                        pass

            # 4) If we didn't detect explicit daily totals above, still try the generic parser on the snippet
            if monograph_parsed_obj.total_min is None and monograph_parsed_obj.total is None:
                try:
                    m_per_admin, m_freq, m_total = _parse_proposed_total(snip)
                    if m_total is not None:
                        monograph_parsed_obj.total = m_total
                        monograph_parsed_obj.range_is_daily = True
                    else:
                        # If parser found per-admin+freq, populate those fields (but don't set range_is_daily)
                        if m_per_admin is not None:
                            monograph_parsed_obj.per_admin = m_per_admin
                        if m_freq is not None:
                            monograph_parsed_obj.freq = m_freq
                except Exception:
                    pass

        else:
            monograph_parsed_obj = SimpleNamespace(per_admin=None, freq=None, total=None, total_min=None, total_max=None, range_is_daily=False)
    except Exception:
        monograph_parsed_obj = SimpleNamespace(per_admin=None, freq=None, total=None, total_min=None, total_max=None, range_is_daily=False)

    report['details']['monograph_dose_parsed'] = monograph_parsed_obj

    # 7) Dose summary (sample snippets)
    if dosage_info.get('dose_sentences'):
        report['dose_summary'].append({'text': "Extracted dosage info from monograph.", 'level': 'info'})
        for s in dosage_info['dose_sentences'][:5]:
            report['dose_summary'].append({'text': s, 'level': 'info'})

    if proposed_mg is not None:
        comp = classify_dose(proposed_mg, dosage_info, route=effective_route, priority=priority)
        report['dose_summary'].extend(comp)

    # Trim
    report['monitoring'] = [{'text': ln, 'level': 'caution'} for ln in monitor_lines[:6]]
    report['contraindications'] = contra_lines[:6]

    # 8) Structured alerts (baseline)
    structured_alerts = generate_structured_alerts(
        drug_name=drug_name,
        monograph_text=monograph_text,
        dose_info=dosage_info,
        patient_info=patient_info,
        proposed_dose_text=proposed_dose_text,
        route=effective_route
    )

    # 8b) Allergy evaluator (new, richer logic)
    allergy_eval = evaluate_allergies(
        patient_allergies=patient_info.get("allergies", []),
        monograph_text=full_text,   # use normalized full text
        drug_name=drug_name
    )
    if allergy_eval.get("alert"):
        structured_alerts.append(allergy_eval["alert"])

    report['structured_alerts'] = structured_alerts

    # 🚫 If hard contraindication via allergy, force overall status = danger and SUPPRESS dosage recommendations
    if allergy_eval.get("severity") == "danger":
        report["dosage_recommendation_status"] = "danger"
        rsn = report.setdefault("dosage_recommendation_reasons", [])
        rsn.append("Allergy contraindication detected (auto-classified).")

        # Provide a clear contraindication message instead of computing recommendations
        report["dosage_recommendations"] = {
            "primary": "Contraindicated due to allergy.",
            "bullets": [],
            "html": "<p style='color:#b91c1c; font-weight:bold'>Contraindicated: Do not administer due to allergy.</p>"
        }

        # Build small summary & UI fragments before exiting
        summary_lines = []
        if report.get('dose_summary'):
            summary_lines.append("<strong>💊 Dose:</strong>")
            summary_lines += [_color_line(d) for d in report['dose_summary']]
        report['overall_summary'] = "<br>".join(summary_lines)

        report['dose_summary_html'] = render_dose_summary_html(report.get('dose_summary', []), drug_name)
        report['structured_alerts_html'] = render_structured_alerts_html(report.get('structured_alerts', []), drug_name)
        report['dosage_recommendations_html'] = report['dosage_recommendations']['html']

        # Keep extras for export/debug
        report['details'] = {
            **report['details'],
            'all_contraindications': contra_lines,
            'all_interactions': interaction_lines,
            'all_dose_sentences': dosage_info.get('dose_sentences', []),
            'all_monitor_lines': monitor_lines,
            'all_allergy_alerts': legacy_allergy_alerts,
            'allergy_eval': allergy_eval,  # richer evaluator dump
            'mgkg_vals': dosage_info.get('mgkg_vals', []),
            'mgml_vals': dosage_info.get('mgml_vals', []),
            'unit_formats': dosage_info.get('unit_formats', []),
        }
        return report  # ✅ early exit: no normal recommendations

    # 9) Overall summary (tiny HTML)
    summary_lines = []
    if report.get('dose_summary'):
        summary_lines.append("<strong>💊 Dose:</strong>")
        summary_lines += [_color_line(d) for d in report['dose_summary']]
    report['overall_summary'] = "<br>".join(summary_lines)

    # 10) Dosage recommendations (only if not contraindicated by allergy)
    report['dosage_recommendations'] = derive_dosage_recommendations(
        report=report,
        dose_info=dosage_info,
        patient_info=patient_info,
        proposed_dose_text=proposed_dose_text,
        drug_name=drug_name,
        route=effective_route
    )

    # UI fragments
    report['dose_summary_html'] = render_dose_summary_html(report.get('dose_summary', []), drug_name)
    report['structured_alerts_html'] = render_structured_alerts_html(report.get('structured_alerts', []), drug_name)
    report['dosage_recommendations_html'] = report['dosage_recommendations'].get('html')

    # Keep extras for export/debug
    report['details'] = {
        **report['details'],
        'all_contraindications': contra_lines,
        'all_interactions': interaction_lines,
        'all_dose_sentences': dosage_info.get('dose_sentences', []),
        'all_monitor_lines': monitor_lines,
        'all_allergy_alerts': legacy_allergy_alerts,
        'allergy_eval': allergy_eval,  # richer evaluator dump
        'mgkg_vals': dosage_info.get('mgkg_vals', []),
        'mgml_vals': dosage_info.get('mgml_vals', []),
        'unit_formats': dosage_info.get('unit_formats', []),
    }
    return report
