# monograph/dosage.py
from __future__ import annotations

from typing import Dict, Any, List, Set, Optional
import re

# Reuse route utilities from the single source of truth
from .routes_catalog import (
    detect_route_in_text,
    detect_route_near,
)
from .routes_catalog import ROUTE_PRIORITY  # for tie-breaking preferences

from .regexes import (
    RE_DOSE, RE_DOSE_G, RE_DOSE_MCG, RE_DOSE_MGKG, RE_DOSE_MGML,
    RE_WV, RE_WW
)


# -------------------- Internal helpers --------------------
def _filter_realistic_doses(doses: List[float]) -> List[float]:
    """
    Trim extreme low/high outliers (10th–90th percentile), preserving old behavior.
    """
    if not doses:
        return []
    sorted_d = sorted(doses)
    n = len(sorted_d)
    lower_idx = max(0, int(n * 0.1))
    upper_idx = min(n - 1, int(n * 0.9))
    return sorted_d[lower_idx:upper_idx + 1]


# -------------------- Dosage extraction --------------------
def extract_dosage_info(text: str) -> Dict[str, Any]:
    """
    Parse a monograph's *plain text* for dosage signals.

    Backward-compatible keys:
      - dose_sentences: [str]
      - route_doses: {route: [mg floats]}
      - mgkg_vals: [float]
      - mgml_vals: [float]
      - dosage_forms: [str]
      - dosage_admin: [str]
      - concentration_routes: [str]
      - unit_formats: [str]

    NEW keys (for “all cases” visibility):
      - route_sentences: {route: [sentences contributing to that route]}
      - route_cases: {route: {"doses_mg":[...], "sentences":[...]}}

    Notes:
      - Fixed mg values are normalized to mg; g→mg and mcg→mg.
      - mg/kg/day and mg/mL are collected separately.
      - Route attribution prefers a route mention BEFORE the number; otherwise the first mention in the sentence.
      - If no route can be inferred, falls back to "Unspecified".
    """
    sentences = re.split(r'(?<=[\n\.])\s+', text or "")
    dosage_forms: List[str] = []
    dosage_admin: List[str] = []
    dose_sentences: List[str] = []
    route_doses: Dict[str, Set[float]] = {}
    route_sentences: Dict[str, List[str]] = {}
    mgkg_vals: Set[float] = set()
    mgml_vals: Set[float] = set()
    concentration_routes: Set[str] = set()
    unit_formats: Set[str] = set()

    current_section = None
    route_context: Optional[str] = None

    def _add_route_sentence(r: Optional[str], s: str):
        rkey = r or "Unspecified"
        route_sentences.setdefault(rkey, [])
        if s not in route_sentences[rkey]:
            route_sentences[rkey].append(s)

    for s in sentences:
        s_clean = (s or "").strip()
        if not s_clean:
            continue
        l = s_clean.lower()

        # crude sectioning hints (retain old logic)
        if "dosage forms" in l or "strengths" in l:
            current_section = "forms"
        elif "dosage & administration" in l or "dosage and administration" in l:
            current_section = "admin"

        seen_route = detect_route_in_text(s_clean)
        if seen_route:
            route_context = seen_route

        # consider dose-like sentences
        if any(x in l for x in [" mg", "mg/kg", "mg/ml", " mcg", " g ", " every ", "% w/v", "% w/w"]):
            dose_sentences.append(s_clean)

            if current_section == "forms":
                dosage_forms.append(s_clean)
            elif current_section == "admin":
                dosage_admin.append(s_clean)

            # mg (absolute) — skip mg/kg or mg/mL tails
            for m in RE_DOSE.finditer(s_clean):
                n = m.group(1)
                tail = s_clean[m.end(): m.end() + 6].lower()
                if re.match(r'\s*/\s*(kg|ml)', tail):
                    continue
                try:
                    dose = float(n)
                    unit_formats.add("mg")
                    route_here = detect_route_near(s_clean, m.start()) or route_context
                    if route_here:
                        route_doses.setdefault(route_here, set()).add(dose)
                    else:
                        route_doses.setdefault("Unspecified", set()).add(dose)
                    _add_route_sentence(route_here, s_clean)
                except Exception:
                    pass

            # g → mg
            for m in RE_DOSE_G.finditer(s_clean):
                try:
                    mg = float(m.group(1)) * 1000.0
                    unit_formats.add("mg")
                    route_here = detect_route_near(s_clean, m.start()) or route_context
                    if route_here:
                        route_doses.setdefault(route_here, set()).add(mg)
                    else:
                        route_doses.setdefault("Unspecified", set()).add(mg)
                    _add_route_sentence(route_here, s_clean)
                except Exception:
                    pass

            # mcg → mg
            for m in RE_DOSE_MCG.finditer(s_clean):
                try:
                    mg = float(m.group(1)) / 1000.0
                    unit_formats.add("mg")
                    route_here = detect_route_near(s_clean, m.start()) or route_context
                    if route_here:
                        route_doses.setdefault(route_here, set()).add(mg)
                    else:
                        route_doses.setdefault("Unspecified", set()).add(mg)
                    _add_route_sentence(route_here, s_clean)
                except Exception:
                    pass

            # mg/kg(/day)
            for m in RE_DOSE_MGKG.finditer(s_clean):
                try:
                    mgkg_vals.add(float(m.group(1)))
                    unit_formats.add("mg/kg")
                    _add_route_sentence(seen_route or route_context, s_clean)
                except Exception:
                    pass

            # mg/mL (concentration)
            for m in RE_DOSE_MGML.finditer(s_clean):
                try:
                    mgml_vals.add(float(m.group(1)))
                    unit_formats.add("mg/mL")
                    route_here = detect_route_near(s_clean, m.start()) or route_context
                    if route_here:
                        concentration_routes.add(route_here)
                    _add_route_sentence(route_here, s_clean)
                except Exception:
                    pass

            if RE_WV.search(s_clean):
                unit_formats.add("% w/v")
                _add_route_sentence(seen_route or route_context, s_clean)

            if RE_WW.search(s_clean):
                unit_formats.add("% w/w")
                _add_route_sentence(seen_route or route_context, s_clean)

    # normalize + filter
    route_doses_filtered: Dict[str, List[float]] = {
        k: _filter_realistic_doses(sorted(v)) for k, v in route_doses.items() if v
    }

    # Build “all cases” bundle
    route_cases = {}
    for r, doses in route_doses_filtered.items():
        route_cases[r] = {
            "doses_mg": doses,
            "sentences": route_sentences.get(r, [])
        }
    # Include routes that had sentences but no fixed mg numbers
    for r in route_sentences:
        if r not in route_cases:
            route_cases[r] = {"doses_mg": [], "sentences": route_sentences[r]}

    return {
        'dose_sentences': list(dict.fromkeys(dose_sentences)),
        'route_doses': route_doses_filtered,
        'mgkg_vals': sorted(mgkg_vals),
        'mgml_vals': sorted(mgml_vals),
        'dosage_forms': dosage_forms,
        'dosage_admin': dosage_admin,
        'concentration_routes': sorted(concentration_routes),
        'unit_formats': sorted(unit_formats),
        # NEW for “all cases”
        'route_sentences': route_sentences,
        'route_cases': route_cases,
    }


# -------------------- Range selection helpers --------------------
def _pick_route_key_for_range(dose_info: Dict[str, Any], route: str | None = None) -> str | None:
    """
    Old behavior preserved: if explicit route available, use it; otherwise
    prefer 'Unspecified', else first key alphabetically.
    """
    route_doses = dose_info.get('route_doses', {}) or {}
    if not route_doses:
        return None
    if route:
        return route if route in route_doses else None
    if "Unspecified" in route_doses:
        return "Unspecified"
    return sorted(route_doses.keys())[0]


def _safe_ranges_summary(dose_info: Dict[str, Any], selected_route: str | None = None) -> str:
    """
    Compact one-line summary for UI; mirrors previous monolithic behavior.
    """
    parts: List[str] = []
    route_doses = dose_info.get('route_doses', {}) or {}
    mgkg_vals = dose_info.get('mgkg_vals') or []
    mgml_vals = dose_info.get('mgml_vals') or []
    unit_formats = dose_info.get('unit_formats') or []

    routes_to_show: List[str] = []
    if selected_route and selected_route in route_doses:
        routes_to_show = [selected_route]
    elif "Unspecified" in route_doses:
        routes_to_show = ["Unspecified"]
    else:
        routes_to_show = sorted(route_doses.keys())

    if routes_to_show:
        r = routes_to_show[0]
        vals = route_doses.get(r, [])
        if vals:
            try:
                mn, mx = (min(vals), max(vals))
                parts.append(f"Route: {r}  |  Min: {mn:.2f} mg  |  Max: {mx:.2f} mg")
            except Exception:
                parts.append(f"Route: {r}")

    if mgkg_vals:
        try:
            parts.append(f"Weight-based: {min(mgkg_vals):.0f}–{max(mgkg_vals):.0f} mg/kg/day")
        except Exception:
            parts.append("Weight-based: mg/kg/day")

    if mgml_vals:
        try:
            lo, hi = min(mgml_vals), max(mgml_vals)
            parts.append(
                f"Concentration noted: {lo:.0f} mg/mL" if lo == hi else
                f"Concentration noted: {lo:.0f}–{hi:.0f} mg/mL"
            )
        except Exception:
            parts.append("Concentration noted (mg/mL)")

    if unit_formats:
        parts.append("Formats in monograph: " + ", ".join(unit_formats))

    return "  |  ".join(parts) if parts else ("Formats in monograph: " + ", ".join(unit_formats) if unit_formats else "")


# -------------------- Route inference (no UI route) --------------------
def infer_best_route(dose_info: Dict[str, Any], proposed_dose_text: str = "") -> Optional[str]:
    """
    Pick the most plausible route when the UI doesn't provide one.

    Strategy:
      1) If the proposed_dose_text includes a route (detect_route_in_text), return it.
      2) Score routes from the monograph:
         score = 2*(number of fixed-mg doses) + (number of sentences touching the route)
         - Penalize "Unspecified" slightly.
      3) Choose the highest score; break ties with ROUTE_PRIORITY.
      4) If nothing usable, return None.
    """
    # Step 1: try to read route from the free-text dose (e.g., "100 mg IV q8h")
    explicit = detect_route_in_text(proposed_dose_text or "")
    if explicit:
        return explicit

    route_cases = dose_info.get("route_cases") or {}
    if not route_cases:
        return None

    scores: Dict[str, int] = {}
    for r, case in route_cases.items():
        doses = case.get("doses_mg") or []
        sents = case.get("sentences") or []
        score = 2 * len(doses) + len(sents)
        if r == "Unspecified":
            score -= 2  # nudge away from Unspecified when other options exist
        scores[r] = score

    if not scores:
        return None

    # Keep only the best
    max_score = max(scores.values())
    candidates = [r for r, sc in scores.items() if sc == max_score]

    # Break ties by ROUTE_PRIORITY
    for pr in ROUTE_PRIORITY:
        if pr in candidates:
            return pr

    # If none of the preferred priorities are in candidates, pick a stable one
    return sorted(candidates)[0] if candidates else None


# -------------------- Classification --------------------
def classify_dose(
    proposed_mg: float,
    dose_info: Dict[str, Any],
    route: str | None = None,
    priority: bool = False
) -> List[Dict[str, str]]:
    """
    Compare a proposed fixed mg dose against extracted fixed-dose ranges,
    respecting the selected route. Emits 'within/below/above' when a range
    exists; otherwise emits informative 'no range' messages.
    """
    results: List[Dict[str, str]] = []
    route_doses = dose_info.get('route_doses', {}) or {}
    concentration_routes = set(dose_info.get('concentration_routes', []) or [])

    # If an explicit route is given, stay within it
    if route:
        if route in route_doses:
            vals = route_doses.get(route, [])
        else:
            if route in concentration_routes or (dose_info.get('mgml_vals') and route):
                results.append({
                    'text': (
                        f"No fixed-mg range found for {route}; dosing appears concentration-based (mg/mL). "
                        f"Cannot compare {proposed_mg:.1f} mg to a fixed range for this route."
                    ),
                    'level': 'info'
                })
            else:
                results.append({
                    'text': f"No dosage range could be extracted for the selected route ({route}).",
                    'level': 'info'
                })
            return results
    else:
        # No route selected: pick a sensible key (keep old behavior)
        route_key = None
        if priority:
            for r in sorted(route_doses.keys()):
                route_key = r
                break
        if route_key is None:
            if "Unspecified" in route_doses:
                route_key = "Unspecified"
            elif route_doses:
                route_key = sorted(route_doses.keys())[0]
        vals = route_doses.get(route_key, []) if route_key else []

    if not vals:
        results.append({'text': "No valid fixed dosage info found in monograph for this route.", 'level': 'info'})
        return results

    try:
        mn, mx = float(min(vals)), float(max(vals))
    except Exception:
        results.append({'text': "Unable to compute a fixed dosage range for comparison.", 'level': 'info'})
        return results

    if proposed_mg < mn:
        results.append({
            'text': f"Proposed dose {proposed_mg:.0f} mg is below recommended fixed range {mn:.0f}–{mx:.0f} mg.",
            'level': 'caution'
        })
    elif proposed_mg > mx:
        results.append({
            'text': f"Proposed dose {proposed_mg:.0f} mg is above recommended fixed range {mn:.0f}–{mx:.0f} mg.",
            'level': 'caution'
        })
    else:
        results.append({
            'text': f"Proposed dose {proposed_mg:.0f} mg is within recommended fixed range {mn:.0f}–{mx:.0f} mg.",
            'level': 'info'
        })

    return results
 