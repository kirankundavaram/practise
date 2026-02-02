import re, html
from typing import List
from .pipes import summarizer_pipe, qa_pipe, pipe_extract_text
from .bio import build_patient_bio_text
from .highlight import render_dosage_recommendations_html, render_structured_alerts_html


# ----------------------------
# Helpers: summarization, text
# ----------------------------
def hf_summary(text):
    if not summarizer_pipe:
        return None
    try:
        raw = summarizer_pipe(text[:1200], max_length=80, min_length=32, do_sample=False)[0]  # trimmed for speed
        if isinstance(raw, dict):
            for key in ('summary_text','generated_text','output_text','text'):
                if key in raw:
                    raw_text = raw.get(key) or ''
                    return "<p>" + re.sub(r'<.*?>', '', raw_text).replace("\n","</p><p>") + "</p>" if raw_text else None
        return None
    except:
        return None

def _join_unique(lines, sep="; "):
    seen, out = set(), []
    for ln in lines or []:
        if not ln:
            continue
        k = " ".join(str(ln).split())
        kl = k.lower()
        if k and kl not in seen:
            seen.add(kl); out.append(k)
    return sep.join(out)

def _mk_section(title, body):
    body = (body or "").strip()
    if not body:
        return ""
    return f"{title}:\n{body}\n\n"

def _norm_units(s: str) -> str:
    s = re.sub(r'(\d)\s*(mg\b)', r'\1 mg', s, flags=re.I)
    s = re.sub(r'(\d)\s*(mcg\b)', r'\1 mcg', s, flags=re.I)
    s = re.sub(r'(\d)\s*(g\b)', r'\1 g', s, flags=re.I)
    s = re.sub(r'(\d)\s*(mL\b)', r'\1 mL', s, flags=re.I)
    return s


# ---------------------------------------
# Optional: conservative fallback status
# ---------------------------------------
_DANGER_TERMS = [
    "contraindicated", "do not use", "avoid use", "stop therapy", "discontinue",
    "black box", "boxed warning", "life-threatening", "severe hypersensitivity",
    "pregnancy category x", "absolute contraindication"
]
_CAUTION_TERMS = [
    "reduce", "decrease", "increase", "adjust", "use with caution", "monitor closely",
    "renal impairment", "hepatic impairment", "elderly", "geriatr", "pediatric",
    "risk increased", "use lowest effective dose", "temporarily withhold", "hold dose"
]
_SAFE_TERMS = [
    "within range", "within the range", "within the extracted",  # added to catch "within the extracted Oral range"
    "within labeled range", "falls within",
    "appropriate", "acceptable", "no adjustment", "compatible",
    "ok to continue", "dose is appropriate"
]

def _contains_any(text: str, terms) -> bool:
    t = (text or "").lower()
    return any(term in t for term in terms)

def _extract_structured_alert_text(structured_alerts) -> List[str]:
    out = []
    for a in structured_alerts or []:
        ann = a.get("Annotation")
        if isinstance(ann, list):
            out.extend([str(x) for x in ann if x])
        elif ann:
            out.append(str(ann))
        if a.get("AlertType"):
            out.append(str(a.get("AlertType")))
    return out

def _set_recommendation_status_inplace(r: dict) -> None:
    """
    Fallback classifier (used ONLY if no status has been set by the narrative).
    """
    if not isinstance(r, dict) or r.get("dosage_recommendation_status"):
        return

    rec = r.get('dosage_recommendations') or {}
    primary = (rec.get('primary') or "").strip()
    bullets = " ".join([str(b or "") for b in (rec.get('bullets') or [])])
    structured_alerts = r.get('structured_alerts') or []
    alerts_text = " ".join(_extract_structured_alert_text(structured_alerts))
    corpus = " ".join([primary, bullets, alerts_text]).strip().lower()

    if _contains_any(corpus, _DANGER_TERMS):
        r["dosage_recommendation_status"] = "danger"
        r["dosage_recommendation_reasons"] = ["Danger terms detected (fallback)."]
        return
    if _contains_any(corpus, _CAUTION_TERMS):
        r["dosage_recommendation_status"] = "caution"
        r["dosage_recommendation_reasons"] = ["Caution terms detected (fallback)."]
        return
    if _contains_any(corpus, _SAFE_TERMS):
        r["dosage_recommendation_status"] = "safe"
        r["dosage_recommendation_reasons"] = ["Safe terms detected (fallback)."]
        return
    r["dosage_recommendation_status"] = "caution"
    r["dosage_recommendation_reasons"] = ["Insufficient signal (fallback)."]


# ----------------------------------------------------
# Builders (now only set status if missing)
# ----------------------------------------------------
def build_expanded_case_summary(patient, report):
    # only set a fallback if status isn't decided yet
    if isinstance(report, dict) and not report.get("dosage_recommendation_status"):
        _set_recommendation_status_inplace(report)

    drug = (report.get('drug') or patient.get('drug_name') or "the medication").strip()
    bio = build_patient_bio_text(patient)
    rec = report.get('dosage_recommendations') or {}
    rec_primary = (rec.get('primary') or "").strip()
    rec_bullets = [re.sub(r"\s+", " ", str(b)).strip() for b in (rec.get('bullets') or [])]
    ranges = rec.get('ranges') or {}
    route_key = ranges.get('route')
    min_mg, max_mg = ranges.get('min_mg'), ranges.get('max_mg')

    structured = report.get('structured_alerts') or []
    alerts_by_type = {}
    for a in structured:
        at = a.get("AlertType") or "Other"
        ann = a.get("Annotation")
        lines = ann if isinstance(ann, list) else [ann]
        lines = [str(x) for x in lines if x]
        if not lines:
            continue
        alerts_by_type.setdefault(at, []).extend(lines)

    presentation = bio

    dose_txt = (patient.get('proposed_dose') or "").strip()
    route_phrase = (patient.get('selected_route') or "").strip()
    dur_txt = (patient.get('duration_days') or "").strip()
    indication = (patient.get('indication') or "").strip()

    bits = []
    if dose_txt: bits.append(dose_txt)
    if route_phrase: bits.append(route_phrase)
    if dur_txt: bits.append(f"for {dur_txt} days")
    if indication: bits.append(f"for {indication}")
    current_therapy = f"{drug} " + " ".join(bits) if bits else f"{drug} (details not fully specified)"

    renal_phrase = (patient.get('renal_impairment') or "not specified").strip()
    hepatic_phrase = (patient.get('hepatic_impairment') or "not specified").strip()
    egfr = (patient.get('egfr') or "").strip()
    crcl = (patient.get('crcl') or "").strip()
    scr  = (patient.get('scr') or "").strip()
    labs_bits = []
    if egfr or crcl or scr:
        labs_bits.append(f"eGFR {egfr or 'n/a'}")
        labs_bits.append(f"CrCl {crcl or 'n/a'}")
        labs_bits.append(f"Scr {scr or 'n/a'}")
    labs_text = ", ".join(labs_bits) if labs_bits else "No recent renal labs provided."
    hx_labs = f"Renal status: {renal_phrase}. Hepatic status: {hepatic_phrase}. Labs: {labs_text}"

    key_alert_order = [
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
        "Pre-Medication Alert",
        "Dose alert based on drug",
    ]
    risk_lines = []
    for k in key_alert_order:
        if k in alerts_by_type:
            risk_lines.append(_join_unique(alerts_by_type[k], sep=" | "))
    risk_text = "\n• " + "\n• ".join([x for x in risk_lines if x]) if risk_lines else "No high-priority alerts detected from the monograph extraction."

    range_text = ""
    if route_key and min_mg is not None and max_mg is not None:
        try:
            range_text = f"Extracted {route_key} fixed-dose range: {min_mg:.0f}–{max_mg:.0f} mg."
        except Exception:
            range_text = f"Extracted {route_key} fixed-dose range available."
    addl_points = _join_unique(rec_bullets, sep=" | ")
    rationale = " ".join([p for p in [rec_primary, range_text, addl_points] if p]).strip() or "Dose rationale could not be determined from the monograph snippets."

    mon_lines = [d.get('text') for d in (report.get('monitoring') or []) if d.get('text')]
    if not mon_lines:
        mon_lines = [
            "Clinical response and adverse effects",
            "Renal and hepatic function as clinically indicated",
            "Allergy or hypersensitivity reactions",
        ]
    monitoring = "\n• " + "\n• ".join(_join_unique(mon_lines, sep="||").split("||"))

    route_counsel = {
        "oral": "Take with a full glass of water; take with food if GI upset occurs unless contraindicated.",
        "intravenous": "Report infusion-site reactions; remain for observation if required.",
        "intramuscular": "Expect local soreness; rotate injection sites.",
        "subcutaneous": "Review injection technique; rotate sites; monitor for local reactions.",
        "topical": "Apply a thin layer to clean, dry skin; avoid broken skin unless directed.",
        "ophthalmic": "Wash hands; avoid touching the dropper tip; wait between drops if multiple meds.",
        "otic": "Warm the bottle in hands; avoid tip contamination; remain with affected ear up briefly.",
        "inhalation": "Demonstrate device technique; rinse mouth after use if steroid-containing.",
        "nasal": "Prime device if needed; aim slightly outward; avoid overuse.",
        "transdermal": "Place on clean, dry, hairless skin; rotate sites; do not cut patches."
    }
    rc = route_counsel.get((patient.get('selected_route') or "").strip().lower())
    counseling = "\n• " + "\n".join([
        "Purpose of therapy and expected benefits",
        rc or "Correct use based on route; demonstrate and return-demonstrate if needed",
        "Possible side effects and when to seek care",
        "Missed dose instructions per label",
        "Drug–drug/food interactions of note based on current medication list",
    ])

    plan_summary = (
        f"Proceed with {current_therapy}; "
        f"monitor as outlined; reassess efficacy/safety within 48–72 hours or sooner if concerns."
    )

    text = ""
    text += _mk_section("Presentation", presentation)
    text += _mk_section("Current Therapy", current_therapy)
    text += _mk_section("Relevant History & Labs", hx_labs)
    text += _mk_section("Risk & Alerts (from monograph extraction)", risk_text)
    text += _mk_section("Dosage Rationale", rationale)
    text += _mk_section("Monitoring Plan", monitoring)
    text += _mk_section("Patient Counseling", counseling)
    text += _mk_section("Plan Summary", plan_summary)

    sex = (patient.get('sex') or "").strip().lower()
    subj, poss = ("They", "their")
    if sex == "male":   subj, poss = "He", "his"
    if sex == "female": subj, poss = "She", "her"

    try:
        if qa_pipe:
            llm_input = (
                "Rewrite the clinical case summary below.\n"
                "- Preserve facts and structure.\n"
                "- Improve clarity, flow, and professionalism.\n"
                "- Do NOT invent new information.\n"
                f"- Use gender-appropriate pronouns ({subj}/{poss}).\n"
                "- Return ONLY the rewritten summary text. No preamble, headings, or quotes.\n"
                "### INPUT START ###\n"
                f"{text}\n"
                "### INPUT END ###\n"
            )
            res = qa_pipe(llm_input, max_length=4000, do_sample=False)
            maybe = pipe_extract_text(res) or ""
            if maybe:
                out = maybe.strip()
                out = re.sub(
                    r'^\s*(?:(?:"|“)?(?:rewrite|polish|preserve facts|improve|return only|do not|input start|input end)[^.\n]*[.\n])+',
                    '',
                    out,
                    flags=re.IGNORECASE
                )
                out = re.sub(r'###\s*INPUT\s*START\s*###', '', out, flags=re.I)
                out = re.sub(r'###\s*INPUT\s*END\s*###', '', out, flags=re.I)
                out = re.sub(r'\s+\n', '\n', out)
                out = re.sub(r'\n{3,}', '\n\n', out).strip()
                out = _norm_units(out)
                if out:
                    return out
        return _norm_units(text.strip())
    except Exception:
        return _norm_units(text.strip())


def build_full_ai_report_html(patient, report):
    # only set a fallback if status isn't decided yet
    if isinstance(report, dict) and not report.get("dosage_recommendation_status"):
        _set_recommendation_status_inplace(report)

    p = patient or {}
    r = report or {}

    bio_txt = build_patient_bio_text(p)
    ranges = r.get('ranges_summary', '')
    rec = r.get('dosage_recommendations', {}) or {}
    rec_primary = rec.get('primary', '')
    rec_bullets = rec.get('bullets', [])
    rec_html = render_dosage_recommendations_html(rec, (r.get('drug') or p.get('drug_name') or ""))

    shtml = render_structured_alerts_html(r.get('structured_alerts', []), (r.get('drug') or p.get('drug_name') or ""))

    # include polished per-route evidence (already UI-safe from assess.py)
    route_cases = r.get('route_cases') or {}

    mon_lines = [d.get('text') for d in (r.get('monitoring') or []) if d.get('text')]
    if not mon_lines:
        mon_lines = [
            "Clinical response and adverse effects",
            "Renal and hepatic function as clinically indicated",
            "Allergy or hypersensitivity reactions",
        ]
    mon_html = "<ul>" + "".join([f"<li>{html.escape(x)}</li>" for x in _join_unique(mon_lines, sep='||').split('||')]) + "</ul>"

    parts = []
    parts.append(f"<h3>Fully Expanded AI-Style Clinical Report</h3>")
    parts.append(f"<p>{html.escape(bio_txt)}</p>")

    # Evidence by route (polished, formatted)
    if route_cases:
        parts.append("<h4>Evidence by Route</h4>")
        for rname in sorted(route_cases.keys()):
            blk = route_cases[rname] or {}
            if isinstance(blk, dict) and blk.get("html"):
                parts.append(str(blk["html"]))

    parts.append("<h4>Therapeutic Range Summary</h4>")
    parts.append(f"<p>{html.escape(ranges) if ranges else '-'}</p>")
    parts.append("<h4>Dosage Recommendation</h4>")
    if rec_primary:
        parts.append(f"<p>{html.escape(rec_primary)}</p>")
    if rec_bullets:
        parts.append("<ul>" + "".join([f"<li>{html.escape(b)}</li>" for b in rec_bullets]) + "</ul>")
    if rec_html:
        parts.append(rec_html)
    parts.append("<h4>Structured Alerts & Risks</h4>")
    parts.append(shtml or "<p>No structured alerts detected.</p>")
    parts.append("<h4>Monitoring Plan</h4>")
    parts.append(mon_html)
    return "\n".join(parts)


def build_dashboard_html(patient, report):
    # only set a fallback if status isn't decided yet
    if isinstance(report, dict) and not report.get("dosage_recommendation_status"):
        _set_recommendation_status_inplace(report)

    p = patient or {}
    r = report or {}

    def gv(k, default="-"):
        v = p.get(k)
        return default if v in (None, "", [], ()) else v

    age = gv('age')
    sex = gv('sex')
    wt = gv('weight_kg') or gv('weight') or "-"
    preg = "Yes" if p.get('pregnant') else "No"
    breast = "Yes" if p.get('breastfeeding') else "No"
    renal = (p.get('renal_impairment') or "-")
    hepatic = (p.get('hepatic_impairment') or "-")
    egfr = gv('egfr')
    crcl = gv('crcl')
    scr = gv('scr')

    drug = (p.get('drug_name') or r.get('drug') or "-")
    dose = gv('proposed_dose')
    route = gv('selected_route')
    dur = gv('duration_days')
    ind = gv('indication')

    rec = r.get('dosage_recommendations', {}) or {}
    rec_primary = rec.get('primary', '-') or '-'
    ranges = r.get('ranges_summary', '-') or '-'
    rec_bullets = "; ".join(rec.get('bullets', []) or []) or "-"

    rows = [
        ("Demographics",
         f"{age} / {sex} / {wt} kg / Preg: {preg} / BF: {breast}",
         "High-risk modifiers present",
         "Avoid unsafe agents; confirm trimester"),
        ("Renal Function",
         f"Status: {renal} (eGFR {egfr}, CrCl {crcl}, Scr {scr})",
         "NSAIDs may precipitate AKI",
         "Avoid nephrotoxins; monitor RFTs"),
        ("Hepatic Function",
         f"Status: {hepatic}",
         "Hepatically metabolized agents: risk ↑",
         "Prefer safer alternatives if needed; monitor LFTs"),
        ("Current Prescription",
         f"{drug} {dose} {route} × {dur} days for {ind}",
         "Dose may exceed labeled range",
         "Align dose/indication to label"),
        ("Therapeutic Standards",
         ranges,
         "Use within labeled ranges only",
         "Align regimen to extracted range"),
        ("AI Recommendation",
         "-",
         rec_primary,
         rec_bullets),
    ]

    style = ("max-width:100%;overflow-x:auto;")
    table_style = (
        "width:100%;border-collapse:separate;border-spacing:0;"
        "table-layout:fixed;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;"
        "font-size:14.5px;"
        "box-shadow:0 4px 14px rgba(0,0,0,0.06);"
    )
    thead_tr_style = "background:#f3f4f6;"
    th_style = (
        "padding:12px 14px;text-align:left;font-weight:600;white-space:nowrap;"
        "border-bottom:1px solid #e5e7eb;"
    )
    td_style = (
        "padding:12px 14px;vertical-align:top;line-height:1.5;"
        "border-bottom:1px solid #f1f5f9;word-wrap:break-word;word-break:break-word;"
    )
    td_section_style = (
        "padding:12px 14px;vertical-align:top;font-weight:600;color:#111827;"
        "border-bottom:1px solid #f1f5f9;white-space:nowrap;"
    )

    html_rows = []
    for idx, (sec, pdata, finding, recmd) in enumerate(rows):
        zebra = "background:#ffffff;" if idx % 2 == 0 else "background:#fbfdff;"
        html_rows.append(
            "<tr style='{zebra}'>"
            "<td style='{tds}'>{sec}</td>"
            "<td style='{td}'>{pdata}</td>"
            "<td style='{td}'>{finding}</td>"
            "<td style='{td}'>{recmd}</td>"
            "</tr>".format(
                zebra=zebra,
                tds=td_section_style,
                td=td_style,
                sec=html.escape(str(sec)),
                pdata=html.escape(str(pdata)),
                finding=html.escape(str(finding)),
                recmd=html.escape(str(recmd)),
            )
        )

    colgroup = (
        "<colgroup>"
        "<col style='width:18%'>"
        "<col style='width:32%'>"
        "<col style='width:25%'>"
        "<col style='width:25%'>"
        "</colgroup>"
    )

    return (
        "<div style='{wrap}'>"
        "<h3 style='margin:0 0 10px;color:#111827;'>Dashboard-Style Report</h3>"
        "<table style='{table}'>"
        f"{colgroup}"
        "<thead><tr style='{thead_tr}'>"
        "<th style='{th}'>Section</th>"
        "<th style='{th}'>Patient Data</th>"
        "<th style='{th}'>AI Findings</th>"
        "<th style='{th}'>Recommendations</th>"
        "</tr></thead>"
        "<tbody>{rows}</tbody>"
        "</table>"
        "</div>"
    ).format(
        wrap=style,
        table=table_style,
        thead_tr=thead_tr_style,
        th=th_style,
        rows="".join(html_rows)
    )


def build_narrative_one_paragraph(patient, report):
    """
    One clean paragraph:
    - Removes boilerplate/missing-data lines from the bio.
    - Suppresses vague mg/kg bullets unless weight AND numeric mg/kg range exist.
    - Highlights actionable primary recommendation (red/orange/green).
    ALSO: decides and sets report['dosage_recommendation_status'] based on the same keyword logic.
    """
    p = patient or {}
    r = report or {}

    # 1) Start from bio & drop unhelpful sentences
    bio_raw = build_patient_bio_text(p) or ""

    def _split_sentences(text: str) -> List[str]:
        return [s.strip() for s in re.split(r'(?<=[\.\!\?])\s+', text.strip()) if s.strip()]

    def _is_informative(s: str) -> bool:
        if re.search(r'\b(not specified|unable to validate|not given|frequency not specified)\b', s, re.I):
            return False
        if re.search(r'\bno known drug allergies\b', s, re.I):
            return False
        if re.search(r'\bno other regular medicines\b', s, re.I):
            return False
        if re.search(r'\bnot pregnant or breastfeeding\b', s, re.I):
            return False
        if re.search(r'Kidney function is not specified.*liver function is not specified', s, re.I):
            return False
        return True

    bio_clean = " ".join([s for s in _split_sentences(bio_raw) if _is_informative(s)])

    # 2) Dosage recommendation: filter bullets
    rec = r.get('dosage_recommendations', {}) or {}
    primary = (rec.get('primary') or "").strip()
    bullets_raw = rec.get('bullets', []) or []
    ranges = rec.get('ranges') or {}
    mgkg_min, mgkg_max = ranges.get('mgkg_min'), ranges.get('mgkg_max')
    has_weight = bool(p.get('weight_kg') or p.get('weight'))

    filtered_bullets: List[str] = []
    for b in bullets_raw:
        bs = re.sub(r'\s{2,}', ' ', str(b or '')).strip()
        if not bs:
            continue
        if not _is_informative(bs):
            continue
        if "mg/kg/day" in bs.lower():
            has_numbers = bool(re.search(r'\d', bs))
            if not (has_weight and ((mgkg_min is not None and mgkg_max is not None) or has_numbers)):
                continue
        filtered_bullets.append(bs.rstrip('.'))

    # 3) Highlight + infer status from PRIMARY (source of truth for status)
    def _infer_status_from_primary(txt: str) -> str:
        t_low = (txt or "").lower()

        # Danger signals
        if re.search(r'\b(above|exceed(?:s|ed|ing)?|too\s+high|decreas(?:e|ing)|reduc(?:e|ing))\b', t_low):
            return "danger"

        # Caution signals
        if re.search(r'\b(below|increase(?:s|d|ing)?|too\s+low|insufficient)\b', t_low):
            return "caution"

        # Safe signals (flexible)
        if (
            re.search(r'\bwithin\b.*\brange\b', t_low) or
            re.search(r'\bwithin\s+(the\s+)?(label(?:led|ed)?|extracted)\s+range\b', t_low) or
            re.search(r'\b(falls?\s+within|in\s+range|inside\s+range)\b', t_low) or
            re.search(r'\b(appropriate|acceptable|ok|compatible|no\s+adjustment)\b', t_low)
        ):
            return "safe"

        return ""

    status = _infer_status_from_primary(primary)

    # If primary didn't match, lightly look at bullets for signals (optional)
    if not status and filtered_bullets:
        bullets_blob = " ".join(filtered_bullets).lower()
        if re.search(r'\b(above|exceed(?:s|ed|ing)?|too\s+high|decreas(?:e|ing)|reduc(?:e|ing))\b', bullets_blob):
            status = "danger"
        elif re.search(r'\b(below|increase(?:s|d|ing)?|too\s+low|insufficient)\b', bullets_blob):
            status = "caution"
        elif (
            re.search(r'\bwithin\b.*\brange\b', bullets_blob) or
            re.search(r'\bwithin\s+(the\s+)?(label(?:led|ed)?|extracted)\s+range\b', bullets_blob) or
            re.search(r'\b(falls?\s+within|in\s+range|inside\s+range)\b', bullets_blob) or
            re.search(r'\b(appropriate|acceptable|ok|compatible|no\s+adjustment)\b', bullets_blob)
        ):
            status = "safe"

    # IMPORTANT CHANGE:
    # Do NOT force "caution" as a last resort here.
    # If still blank, leave it for the global fallback classifier to decide.
    if status:
        r["dosage_recommendation_status"] = status
        r["dosage_recommendation_reasons"] = [f"Derived from narrative primary ('{primary}')."]

    # Build the narrative paragraph (with colored primary)
    def _highlight(txt: str) -> str:
        t_low = (txt or "").lower()
        if re.search(r'\b(above|exceed(?:s|ed|ing)?|too\s+high|decreas(?:e|ing)|reduc(?:e|ing))\b', t_low):
            return f"<strong style='color:#b91c1c'>{html.escape(txt)}</strong>"  # red
        if re.search(r'\b(below|increase(?:s|d|ing)?|too\s+low|insufficient)\b', t_low):
            return f"<strong style='color:#b45309'>{html.escape(txt)}</strong>"  # orange
        if (
            re.search(r'\bwithin\b.*\brange\b', t_low) or
            re.search(r'\bwithin\s+(the\s+)?(label(?:led|ed)?|extracted)\s+range\b', t_low) or
            re.search(r'\b(falls?\s+within|in\s+range|inside\s+range)\b', t_low) or
            re.search(r'\b(appropriate|acceptable|ok|compatible|no\s+adjustment)\b', t_low)
        ):
            return f"<strong style='color:#166534'>{html.escape(txt)}</strong>"  # green
        return f"<strong>{html.escape(txt)}</strong>"

    parts: List[str] = []
    if bio_clean:
        parts.append(html.escape(bio_clean))
    if primary:
        parts.append(_highlight(primary))
    if filtered_bullets:
        parts.append("; ".join([html.escape(b) for b in filtered_bullets]))

    para = " ".join(parts).strip()
    para = re.sub(r'\s{2,}', ' ', para)
    return para
