import re, html
from .regexes import RE_DOSE, RE_DOSE_G, RE_DOSE_MCG
from .routes_catalog import ROUTE_PRIORITY
from .highlight import render_dosage_recommendations_html
from .highlight import render_dose_summary_html, render_structured_alerts_html  # used by assess
# (re-exporting through assess, no circular import)


def _parse_dose_and_freq(dose_text: str):
    """
    Parse a text snippet to extract:
      - per_admin_mg (float or None)
      - freq_per_day (int or None)
      - total_mg_per_day (float or None)

    Returns (per_admin_mg, freq_per_day, total_mg_per_day)
    """
    if not dose_text:
        return (None, None, None)
    txt = dose_text.lower()

    # Normalize some textual numbers (three -> 3) for common cases
    txt = re.sub(r'\bthree\b', '3', txt)
    txt = re.sub(r'\btwo\b', '2', txt)
    txt = re.sub(r'\bone\b', '1', txt)
    txt = re.sub(r'\bfour\b', '4', txt)

    # Per-admin mg extraction (handles mg, g, mcg)
    try:
        mg_vals = [float(x) for x in RE_DOSE.findall(txt or "")]
    except Exception:
        mg_vals = []
    try:
        g_vals = [float(x) * 1000.0 for x in RE_DOSE_G.findall(txt or "")]
    except Exception:
        g_vals = []
    try:
        mcg_vals = [float(x) / 1000.0 for x in RE_DOSE_MCG.findall(txt or "")]
    except Exception:
        mcg_vals = []
    all_per_admin = mg_vals + g_vals + mcg_vals
    per_admin = max(all_per_admin) if all_per_admin else None

    # Frequency detection
    freq = None

    # explicit keywords -> once/twice/daily
    if re.search(r'\bonce\b|one time|one-time|daily|per day|every day|\bqd\b|\bq\.d\b', txt):
        freq = 1
    if re.search(r'\btwice\b|two times|two-time|\bbid\b|\bb\.i\.d\b|twice daily', txt):
        freq = 2

    # explicit "N times" pattern, e.g., "3 times a day", "1x/day", "3x/day"
    m = re.search(r'(\d+)\s*(?:x|times|time)\s*(?:a|per)?\s*(day|daily|d)?', txt)
    if m:
        try:
            freq = int(m.group(1))
        except Exception:
            pass

    # every N hours -> frequency = round(24 / N)
    m2 = re.search(r'every\s*(\d+(?:\.\d+)?)\s*(?:-?hr|hours|hour|h)\b', txt)
    if m2:
        try:
            hours = float(m2.group(1))
            if hours > 0:
                freq = max(1, int(round(24.0 / hours)))
        except Exception:
            pass

    # q8h, q12h, q6h patterns
    m3 = re.search(r'\bq(\d{1,2})h\b', txt)
    if m3:
        try:
            hours = int(m3.group(1))
            if hours > 0:
                freq = max(1, int(round(24.0 / hours)))
        except Exception:
            pass

    # fallback: explicit total per day patterns like '450 mg/day' or '450 mg per day'
    total_explicit = None
    m4 = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*mg\s*(?:/|per)\s*day', txt)
    if m4:
        try:
            total_explicit = float(m4.group(1))
        except Exception:
            total_explicit = None

    # If explicit total found and no per-admin, treat as total (freq=1)
    if total_explicit is not None and per_admin is None:
        per_admin = total_explicit
        freq = 1

    # Compute total
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


def _pick_route_key_for_range(dose_info, route=None):
    route_doses = dose_info.get('route_doses', {}) or {}
    if not route_doses:
        return None
    if route:
        return route if route in route_doses else None
    if "Unspecified" in route_doses:
        return "Unspecified"
    return sorted(route_doses.keys())[0] if route_doses else None


def classify_dose(proposed_mg, dose_info, route=None, priority=False):
    # preserved original behavior (unchanged)
    results = []
    route_doses = dose_info.get('route_doses', {}) or {}
    concentration_routes = set(dose_info.get('concentration_routes', []) or [])

    if route:
        if route in route_doses:
            dose_nums = route_doses.get(route, [])
        else:
            if route in concentration_routes or (dose_info.get('mgml_vals') and route):
                results.append({
                    'text': f"No fixed-mg range found for {route}; dosing appears concentration-based (mg/mL). Cannot compare {proposed_mg:.1f} mg to a range for this route.",
                    'level': 'info'
                })
            else:
                results.append({
                    'text': f"No dosage range could be extracted for the selected route ({route}).",
                    'level': 'info'
                })
            return results
    else:
        route_key = None
        if priority:
            for r in ROUTE_PRIORITY:
                if r in route_doses:
                    route_key = r
                    break
        if route_key is None:
            if "Unspecified" in route_doses:
                route_key = "Unspecified"
            elif route_doses:
                route_key = sorted(route_doses.keys())[0]
        dose_nums = route_doses.get(route_key, []) if route_key else []

    if not dose_nums:
        return [{'text': "No valid fixed dosage info found in monograph for this route.", 'level': 'info'}]
    return results


def _alerts_by_type(structured_alerts):
    idx = {}
    for a in structured_alerts or []:
        at = a.get("AlertType") or ""
        ann = a.get("Annotation")
        lines = ann if isinstance(ann, list) else [ann]
        lines = [str(x) for x in lines if x]
        if not at or not lines:
            continue
        idx.setdefault(at, []).extend(lines)
    return idx


def _extract_monograph_duration_from_alerts(struct_idx):
    lines = (struct_idx.get("Duration alert") or [])
    for ln in lines:
        m = re.search(r'appears to be\s+(\d+)\s+days', ln, flags=re.I)
        if m:
            try:
                return int(m.group(1))
            except:
                pass
    return None


def _severity_from_alerts(struct_idx, has_allergy=False):
    critical_hints = [
        "Maximum dose alert",
        "Low dose/high dose based on Health condition Alert",
        "Low dose/high dose based on Dose range based Alert",
        "Renal dose alert based on Lab values (eGFR, CRCL, Serum creatinine)",
        "Renal dose alert based on Health condition",
        "Hepatic dose alert based on Health condition",
        "Pregnancy dose alert",
        "Age/Sex group wise dose alert (Pediatrics, Adults, Geriatrics)",
        "Weight wise alert",
        "Age based alert that the drug can be given or not",
        "Sex based alert that the drug can be given or not (Male-Only Drug Alert)",
        "Sex based alert that the drug can be given or not (Female-Only Drug Alert)",
        "Pre-Medication Alert"
    ]
    if has_allergy:
        return "caution"
    for k in critical_hints:
        if k in struct_idx:
            return "caution"
    return "info"


def _safe_ranges_summary(dose_info, selected_route=None):
    parts = []
    route_doses = dose_info.get('route_doses', {}) or {}
    mgkg_vals = dose_info.get('mgkg_vals') or []
    mgml_vals = dose_info.get('mgml_vals') or []
    unit_formats = dose_info.get('unit_formats') or []

    routes_to_show = []
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
            if lo == hi:
                parts.append(f"Concentration noted: {lo:.0f} mg/mL")
            else:
                parts.append(f"Concentration noted: {lo:.0f}–{hi:.0f} mg/mL")
        except Exception:
            parts.append("Concentration noted (mg/mL)")

    if unit_formats:
        parts.append("Formats in monograph: " + ", ".join(unit_formats))

    return "  |  ".join(parts) if parts else ("Formats in monograph: " + ", ".join(unit_formats) if unit_formats else "")


def derive_dosage_recommendations(report, dose_info, patient_info, proposed_dose_text, drug_name, route):
    """
    Main recommendation logic:
      - Parse proposed dose into (per_admin, freq, total)
      - Prefer monograph-parsed daily totals/ranges (from assess.py) when available
      - Else try to infer from dose_sentences or per-administration comparisons
    """
    rec = {'level': 'info', 'primary': None, 'bullets': [], 'ranges': {}, 'html': None, 'parsed': {}}

    struct_idx = _alerts_by_type(report.get('structured_alerts') or [])
    route_key = _pick_route_key_for_range(dose_info, route=route)
    min_mg = max_mg = None
    if route_key:
        vals = dose_info.get('route_doses', {}).get(route_key) or []
        if vals:
            min_mg, max_mg = min(vals), max(vals)
            rec['ranges'].update({'route': route_key, 'min_mg': min_mg, 'max_mg': max_mg})

    mgkg_vals = dose_info.get('mgkg_vals') or []
    mgkg_min = mgkg_max = (min(mgkg_vals), max(mgkg_vals)) if mgkg_vals else (None, None)
    mgml_vals = dose_info.get('mgml_vals') or []
    concentration_routes = set(dose_info.get('concentration_routes', []) or [])
    unit_formats = dose_info.get('unit_formats') or []

    # Parse proposed dose
    proposed_mg = None
    per_admin_prop = None
    freq_prop = None
    total_prop = None
    try:
        per_admin_prop, freq_prop, total_prop = _parse_dose_and_freq(proposed_dose_text or "")
        per_admin_prop = float(per_admin_prop) if per_admin_prop is not None else None
        if total_prop is not None:
            proposed_mg = float(total_prop)
            total_prop = float(total_prop)
        elif per_admin_prop is not None:
            # If we only have per-admin and no freq, proposed_mg remains per-admin (ambiguous)
            proposed_mg = float(per_admin_prop)
    except Exception:
        per_admin_prop = None
        freq_prop = None
        total_prop = None
        proposed_mg = None

    # Try to use monograph-parsed dosing (set by assess.py)
    monograph_total_min = monograph_total_max = None
    monograph_per_admin = None
    monograph_freq = None
    monograph_parsed = None
    try:
        monograph_parsed = (report.get('details') or {}).get('monograph_dose_parsed')
    except Exception:
        monograph_parsed = None

    if monograph_parsed:
        # Safe attribute access (monograph_parsed may be SimpleNamespace or dict)
        def _g(k):
            try:
                return getattr(monograph_parsed, k)
            except Exception:
                try:
                    return monograph_parsed.get(k)
                except Exception:
                    return None

        # Extract fields
        m_min = _g('total_min')
        m_max = _g('total_max')
        m_total = _g('total')
        m_range_flag = bool(_g('range_is_daily'))
        m_per = _g('per_admin')
        m_freq = _g('freq')

        # If the monograph parser explicitly produced daily totals or flagged range_is_daily,
        # prefer those totals. NOTE: if range_is_daily is True and we only have route per-admin range
        # (min_mg/max_mg), treat that per-route range AS DAILY TOTALS (do NOT multiply by freq).
        if (m_min is not None and m_max is not None) or (m_total is not None and m_range_flag):
            if m_min is not None and m_max is not None:
                monograph_total_min = float(m_min)
                monograph_total_max = float(m_max)
            elif m_total is not None:
                monograph_total_min = monograph_total_max = float(m_total)
        elif m_range_flag and (min_mg is not None and max_mg is not None):
            # MONOGRAPH TAGGED the snippet as "daily/divided" but monograph_parsed did not include explicit totals.
            # Use the per-route numeric range as DAILY totals (do NOT multiply by m_freq).
            try:
                monograph_total_min = float(min_mg)
                monograph_total_max = float(max_mg)
            except Exception:
                pass
        elif m_per is not None and m_freq is not None:
            # monograph gave per-admin + freq, compute totals
            try:
                monograph_per_admin = float(m_per)
                monograph_freq = int(m_freq)
                monograph_total_min = monograph_total_max = monograph_per_admin * monograph_freq
            except Exception:
                pass
        else:
            # No clear monograph daily totals; leave None to fall back
            pass

    # If monograph totals not set from details, fall back to heuristic parsing of dose_sentences (old behavior)
    if monograph_total_min is None and monograph_total_max is None:
        try:
            sample = " ".join((dose_info.get('dose_sentences') or [])[:3])
            if sample:
                m_per, m_freq, m_total = _parse_dose_and_freq(sample)
                if m_total is not None:
                    monograph_total_min = monograph_total_max = float(m_total)
                elif m_per is not None and m_freq is not None:
                    monograph_per_admin = float(m_per)
                    monograph_freq = int(m_freq)
                    if min_mg is not None and max_mg is not None:
                        # If dose_info has per-admin range, multiply per-admin range by frequency ONLY if
                        # we are confident the monograph sample is per-admin+freq (no daily/divided hint).
                        monograph_total_min = float(min_mg) * monograph_freq
                        monograph_total_max = float(max_mg) * monograph_freq
                    else:
                        monograph_total_min = monograph_total_max = monograph_per_admin * monograph_freq
                else:
                    # leave totals None
                    pass
        except Exception:
            pass

    # store parsed info for UI/debug (non-breaking)
    rec['parsed'] = {
        'proposed': {
            'per_admin': per_admin_prop,
            'freq': freq_prop,
            'total': total_prop
        },
        'monograph': {
            'per_admin': monograph_per_admin,
            'freq': monograph_freq,
            'total_min': monograph_total_min,
            'total_max': monograph_total_max,
            'range_per_admin_min': min_mg,
            'range_per_admin_max': max_mg,
            'from_details_obj': True if monograph_parsed is not None else False
        }
    }

    # Concentration-route handling: keep old behavior
    if route and _pick_route_key_for_range(dose_info, route=route) is None:
        if (route in concentration_routes) or mgml_vals:
            try:
                conc_snip = (
                    f"{min(mgml_vals):.0f}–{max(mgml_vals):.0f} mg/mL" if len(mgml_vals) > 1
                    else (f"{mgml_vals[0]:.0f} mg/mL" if mgml_vals else "mg/mL")
                )
            except Exception:
                conc_snip = "mg/mL"
            rec['primary'] = (
                f"No fixed-mg range extracted for the selected route ({route}). "
                f"This route appears concentration-based ({conc_snip}). "
                f"Use label-directed volume/frequency for {drug_name}; absolute mg comparison is not applicable."
            )
        else:
            formats_msg = ", ".join(unit_formats) if unit_formats else "—"
            rec['primary'] = (
                f"No labeled dosage range could be extracted for the selected route ({route}). "
                f"For {drug_name}, formats available in the monograph include: {formats_msg}."
            )
        rec['level'] = _severity_from_alerts(struct_idx, has_allergy=bool(report.get('allergy_alerts')))
        rec['html'] = render_dosage_recommendations_html(rec, drug_name)
        return rec

    # Comparison logic:
    # 1) If monograph totals detected (monograph_total_min/max), compare proposed daily total (preferred)
    if monograph_total_min is not None and monograph_total_max is not None:
        if proposed_mg is None:
            # If user gave per-admin without freq, prompt for frequency
            if per_admin_prop is not None and freq_prop is None:
                rec['primary'] = (f"Proposed dose appears to be a single administration ({per_admin_prop:.0f} mg). "
                                  f"Monograph lists a daily total range of {monograph_total_min:.0f}–{monograph_total_max:.0f} mg/day. "
                                  f"Please provide frequency (e.g., '3 times a day') to compare daily totals accurately.")
                rec['level'] = 'info'
            else:
                rec['primary'] = f"Use a daily total within the extracted monograph range: {monograph_total_min:.0f}–{monograph_total_max:.0f} mg/day."
        else:
            # proposed_mg interpreted as daily total (if user gave per-admin+freq or explicit /day)
            if proposed_mg < monograph_total_min:
                rec['primary'] = (f"Proposed daily total {proposed_mg:.0f} mg is below extracted monograph daily range "
                                  f"({monograph_total_min:.0f}–{monograph_total_max:.0f} mg/day).")
                rec['level'] = 'caution'
            elif proposed_mg > monograph_total_max:
                rec['primary'] = (f"Proposed daily total {proposed_mg:.0f} mg exceeds extracted monograph daily range "
                                  f"({monograph_total_min:.0f}–{monograph_total_max:.0f} mg/day); reduce dose.")
                rec['level'] = 'caution'
            else:
                rec['primary'] = (f"Proposed daily total {proposed_mg:.0f} mg falls within extracted monograph daily range "
                                  f"({monograph_total_min:.0f}–{monograph_total_max:.0f} mg/day).")
    # 2) Else fall back to per-administration (original behavior)
    elif min_mg is not None and max_mg is not None:
        if per_admin_prop is not None and freq_prop is not None:
            # user provided per-admin + freq: compare per-admin to monograph per-admin range
            proposed_per_admin = per_admin_prop
            if proposed_per_admin < min_mg:
                rec['primary'] = f"Proposed per-administration dose {proposed_per_admin:.0f} mg is below recommended per-administration range ({min_mg:.0f}–{max_mg:.0f} mg)."
                rec['level'] = 'caution'
            elif proposed_per_admin > max_mg:
                rec['primary'] = f"Proposed per-administration dose {proposed_per_admin:.0f} mg exceeds recommended per-administration range ({min_mg:.0f}–{max_mg:.0f} mg)."
                rec['level'] = 'caution'
            else:
                rec['primary'] = f"Proposed per-administration dose {proposed_per_admin:.0f} mg is within the extracted per-administration range ({min_mg:.0f}–{max_mg:.0f} mg)."
        else:
            # ambiguous: fall back to comparing proposed_mg (which might be total or per-admin) to range
            if proposed_mg is None:
                rec['primary'] = f"Use a fixed dose within the extracted {route_key} range: {min_mg:.0f}–{max_mg:.0f} mg."
            else:
                if proposed_mg < min_mg:
                    rec['primary'] = f"Increase dose toward the extracted {route_key} range ({min_mg:.0f}–{max_mg:.0f} mg); current proposal {proposed_mg:.0f} mg is below range."
                    rec['level'] = 'caution'
                elif proposed_mg > max_mg:
                    rec['primary'] = f"Reduce dose to stay within the extracted {route_key} range ({min_mg:.0f}–{max_mg:.0f} mg); current proposal {proposed_mg:.0f} mg exceeds range."
                    rec['level'] = 'caution'
                else:
                    rec['primary'] = f"The proposed {proposed_mg:.0f} mg is within the extracted {route_key} range ({min_mg:.0f}–{max_mg:.0f} mg)."
    # 3) mg/kg fallback
    elif mgkg_min and mgkg_max:
        try:
            rec['primary'] = f"Use a weight-based daily dose of {mgkg_min:.0f}–{mgkg_max:.0f} mg/kg/day (split per monograph)."
        except Exception:
            rec['primary'] = "Use a weight-based daily dose per monograph (mg/kg/day)."
    else:
        formats_msg = ", ".join(unit_formats) if unit_formats else "—"
        rec['primary'] = f"No fixed or weight-based range extracted. Formats present in monograph: {formats_msg}."

    # remaining bullets & alerts (same as before)
    if mgkg_min and mgkg_max:
        try:
            rec['bullets'].append(f"Weight-based rule extracted: {mgkg_min:.0f}–{mgkg_max:.0f} mg/kg/day.")
        except Exception:
            rec['bullets'].append("Weight-based rule extracted: mg/kg/day.")
    if "Maximum dose alert" in struct_idx:
        rec['bullets'].append("A maximum-dose statement was detected; ensure the regimen does not exceed the labeled maximum.")
    dur = _extract_monograph_duration_from_alerts(struct_idx)
    if "Duration alert" in struct_idx and dur:
        rec['bullets'].append(f"Align duration to ~{dur} days per monograph for this indication.")
    if "Low dose/high dose based on Dose range based Alert" in struct_idx:
        rec['bullets'].append(struct_idx["Low dose/high dose based on Dose range based Alert"][0])
    if "Low dose/high dose based on Health condition Alert" in struct_idx:
        rec['bullets'].append(struct_idx["Low dose/high dose based on Health condition Alert"][0])

    has_allergy = bool(report.get('allergy_alerts') or [])
    if has_allergy:
        rec['bullets'].append("Allergy warnings present in monograph; verify agent selection and consider alternatives.")

    if rec['level'] == 'info':
        rec['level'] = _severity_from_alerts(struct_idx, has_allergy=has_allergy)

    rec['html'] = render_dosage_recommendations_html(rec, drug_name)
    return rec


# helpers re-exported for assess
safe_ranges_summary = _safe_ranges_summary
alerts_by_type = _alerts_by_type
pick_route_key_for_range = _pick_route_key_for_range
