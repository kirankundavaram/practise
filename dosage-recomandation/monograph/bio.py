import re
from .pipes import summarizer_pipe, qa_pipe, pipe_extract_text
from .routes_catalog import route_to_phrase, _canonical_route_name

def _norm_dash(v):
    return "-" if v in (None, "", [], ()) else v

def _norm_list_or_dash(items):
    if not items:
        return "-"
    if isinstance(items, str):
        items = [x.strip() for x in re.split(r',|;', items) if x.strip()]
    if not items:
        return "-"
    return ", ".join(items)

def create_patient_paragraph(patient):
    """
    Keeps the structured block format used elsewhere, but avoids writing a
    narrative sentence that claims 'not pregnant or breastfeeding'.
    """
    if not patient or not isinstance(patient, dict):
        return ""

    drug = (patient.get('drug_name') or "").strip()
    dose_raw = (patient.get('proposed_dose') or "").strip()
    dose = dose_raw
    dose = re.sub(r'(\d)\s*(mg\b)', r'\1 mg', dose, flags=re.I)
    dose = re.sub(r'(\d)\s*(mcg\b)', r'\1 mcg', dose, flags=re.I)
    dose = re.sub(r'(\d)\s*(g\b)', r'\1 g', dose, flags=re.I)

    route = (patient.get('selected_route') or "").strip()
    duration = (patient.get('duration_days') or "").strip()
    indication = (patient.get('indication') or "").strip()

    age = patient.get('age')
    sex = (patient.get('sex') or "").strip()
    weight = patient.get('weight_kg') or patient.get('weight')

    identity_bits = []
    if age not in (None, ""):
        try:
            identity_bits.append(f"{int(float(age))}-year-old")
        except Exception:
            identity_bits.append(str(age))
    if sex:
        identity_bits.append(sex.lower())

    weight_clause = ""
    try:
        if weight not in (None, ""):
            weight_clause = f" weighing {float(weight):.1f} kg"
    except Exception:
        pass

    preg = bool(patient.get('pregnant'))
    breast = bool(patient.get('breastfeeding'))
    # Only mention if true; otherwise omit
    if preg and breast:
        preg_text = "The patient is pregnant and breastfeeding."
    elif preg:
        preg_text = "The patient is pregnant."
    elif breast:
        preg_text = "The patient is breastfeeding."
    else:
        preg_text = ""  # OMIT default 'not pregnant'

    nar_identity = f"The patient is a {' '.join(identity_bits)}{weight_clause}" if identity_bits else "The patient"
    nar_drug = _norm_dash(drug)
    nar_dose = _norm_dash(dose if dose else dose_raw)
    nar_route_phrase = route_to_phrase(route)
    nar_duration = (f"{duration} days" if str(duration).strip() else "-")
    nar_indication = _norm_dash(indication)

    narrative = (
        f"{nar_identity}, currently prescribed {nar_drug} {nar_dose}{(' ' + nar_route_phrase) if nar_route_phrase else ''} "
        f"for {nar_duration} to treat {nar_indication}."
    ).replace("  ", " ").strip()
    if preg_text:
        narrative = f"{narrative} {preg_text}"

    renal = _norm_dash((patient.get('renal_impairment') or "").strip())
    hepatic = _norm_dash((patient.get('hepatic_impairment') or "").strip())

    egfr = _norm_dash(patient.get('egfr'))
    crcl = _norm_dash(patient.get('crcl'))
    scr = _norm_dash(patient.get('scr'))

    allergies = _norm_list_or_dash(patient.get('allergies'))
    current_meds = _norm_list_or_dash(patient.get('current_meds'))

    cp_drug = nar_drug
    cp_dose = nar_dose
    cp_route = _canonical_route_name(route) if route else "-"
    cp_duration = _norm_dash(nar_duration)
    cp_ind = nar_indication

    core_line = (
        f"Core Prescription:\n"
        f"Drug: {cp_drug}   |   Dose: {cp_dose}   |   Route: {cp_route}   |   Duration: {cp_duration}   |   Indication: {cp_ind}"
    )

    key_line = (
        "Key Patient Factors:\n"
        f"Age: {_norm_dash(age)}   |   Sex: {_norm_dash(sex.title() if sex else '')}   |   Weight: "
        f"{('-' if weight in (None, '') else f'{float(weight):.1f} kg')}   |   "
        f"Renal: {renal} (eGFR: {egfr}, CrCl: {crcl}, Scr: {scr})   |   Hepatic: {hepatic}"
    )

    safety_line = (
        "Safety Modifiers:\n"
        f"Pregnant: {'Yes' if preg else 'No'}   |   Breastfeeding: {'Yes' if breast else 'No'}   |   Allergies: {allergies}   |   Current Meds: {current_meds}"
    )

    paragraph = narrative + "\n\n" + core_line + "\n" + key_line + "\n" + safety_line
    return paragraph

def polish_with_hf_llm(text, sex=None):
    if not text:
        return ""

    parts = text.split("\n\n", 1)
    narrative = parts[0]
    blocks = parts[1] if len(parts) > 1 else ""

    subj = "They"
    poss = "their"
    if sex:
        s = str(sex).strip().lower()
        if s == "male":
            subj, poss = "He", "his"
        elif s == "female":
            subj, poss = "She", "her"

    polished_narr = narrative
    try:
        if qa_pipe:
            prompt = (
                f"Rewrite the following into a clear, professional clinical paragraph. "
                f"Preserve all facts; do not add new information. Use gender-appropriate pronouns ({subj}/{poss}).\n\n{narrative}"
            )
            res = qa_pipe(prompt, max_length=12000, do_sample=False)
            maybe = pipe_extract_text(res)
            if maybe:
                polished_narr = re.sub(r'\s+', ' ', maybe).strip()
        elif summarizer_pipe:
            res = summarizer_pipe(narrative[:1000], max_length=1200, min_length=120, do_sample=False)
            maybe = pipe_extract_text(res)
            if maybe:
                polished_narr = re.sub(r'\s+', ' ', maybe).strip()
    except Exception:
        pass

    polished_narr = re.sub(r'(\d)\s*(mg\b)', r'\1 mg', polished_narr, flags=re.I)
    polished_narr = re.sub(r'(\d)\s*(mcg\b)', r'\1 mcg', polished_narr, flags=re.I)
    polished_narr = re.sub(r'(\d)\s*(g\b)', r'\1 g', polished_narr, flags=re.I)

    final_text = polished_narr
    if blocks:
        final_text += "\n\n" + blocks
    return final_text

def build_patient_bio_text(patient):
    """
    Produce a concise narrative with ONLY the info actually provided.
    - Omits default fillers like 'not specified', 'no known drug allergies', etc.
    - Mentions pregnancy/breastfeeding ONLY if True.
    - Includes renal/hepatic and labs ONLY if any were provided.
    - Includes allergies/meds ONLY if lists were provided.
    """
    def clean_num(x, fmt=float):
        try:
            return fmt(x)
        except Exception:
            return None

    route_map = {
        "oral": "by mouth",
        "ophthalmic": "as eye drops",
        "otic": "as ear drops",
        "topical": "on the skin",
        "nasal": "as a nasal preparation",
        "inhalation": "by inhalation",
        "intravenous": "by IV",
        "intramuscular": "by IM injection",
        "subcutaneous": "by subcutaneous injection",
        "intradermal": "by intradermal injection",
        "sublingual": "under the tongue",
        "buccal": "between the cheek and gum",
        "vaginal": "as a vaginal preparation",
        "rectal": "as a rectal preparation",
        "transdermal": "as a skin patch",
        "intrathecal": "by intrathecal injection",
        "epidural": "by epidural injection"
    }

    drug = (patient.get('drug_name') or "the medication").strip()
    dose_raw = (patient.get('proposed_dose') or "").strip()
    dose = re.sub(r'(\d)\s*(mg\b)', r'\1 mg', dose_raw, flags=re.I)
    dose = re.sub(r'(\d)\s*(mcg\b)', r'\1 mcg', dose, flags=re.I)
    dose = re.sub(r'(\d)\s*(g\b)', r'\1 g', dose, flags=re.I)

    route_raw = (patient.get('selected_route') or "").strip()
    route_text = route_to_phrase(route_raw) or route_map.get(route_raw.lower(), "")

    duration = (str(patient.get('duration_days') or "").strip())
    indication = (patient.get('indication') or "").strip()

    age = (str(patient.get('age') or "").strip())
    sex = (patient.get('sex') or "").strip().lower()
    weight = patient.get('weight_kg') or patient.get('weight')
    weight_val = clean_num(weight, float)

    subj = "They"; poss = "their"
    if sex == "male": subj, poss = "He", "his"
    elif sex == "female": subj, poss = "She", "her"

    preg = bool(patient.get('pregnant'))
    breast = bool(patient.get('breastfeeding'))

    renal_flag = (patient.get('renal_impairment') or "").strip()
    hepatic_flag = (patient.get('hepatic_impairment') or "").strip()
    egfr = (str(patient.get('egfr') or "").strip())
    crcl = (str(patient.get('crcl') or "").strip())
    scr  = (str(patient.get('scr')  or "").strip())

    labs_bits = []
    if egfr or crcl or scr:
        if egfr: labs_bits.append(f"eGFR {egfr}")
        if crcl: labs_bits.append(f"CrCl {crcl}")
        if scr:  labs_bits.append(f"Scr {scr}")
    labs_text = f" ({', '.join(labs_bits)})" if labs_bits else ""

    allergies = [x for x in (patient.get('allergies') or []) if str(x).strip()]
    meds = [x for x in (patient.get('current_meds') or []) if str(x).strip()]

    who_bits = []
    if age: who_bits.append(f"{age}-year-old")
    if sex in ("male", "female"):
        who_bits.append("man" if sex == "male" else "woman")
    if weight_val is not None:
        who_bits.append(f"weighing {weight_val:.1f} kg")
    who = " ".join(who_bits) if who_bits else "The patient"

    sents = []

    # Prescription sentence (only include parts that exist)
    dose_part = f"{drug} {dose}".strip() if dose or dose_raw else drug
    route_part = f" {route_text}" if route_text else ""
    dur_part = f" for {duration} days" if duration else ""
    ind_part = f" to treat {indication}" if indication else ""
    s1 = f"{who} has been prescribed {dose_part}{route_part}{dur_part}{ind_part}.".replace("  ", " ").strip()
    sents.append(s1)

    # Pregnancy/Breastfeeding: include only if True
    if preg and breast:
        sents.append(f"{subj} is pregnant and breastfeeding.")
    elif preg:
        sents.append(f"{subj} is pregnant.")
    elif breast:
        sents.append(f"{subj} is breastfeeding.")
    # else: omit

    # Renal/Hepatic/Labs: include only if any provided
    renal_bits = []
    if renal_flag:
        renal_bits.append(f"Kidney function: {renal_flag}{labs_text if labs_text else ''}.")
    elif labs_text:
        renal_bits.append(f"Kidney function{labs_text}.")
    if hepatic_flag:
        renal_bits.append(f"Liver function: {hepatic_flag}.")
    if renal_bits:
        sents.append(" ".join(renal_bits))

    # Allergies/Current meds: include only if present
    if allergies:
        sents.append("Allergies: " + ", ".join(allergies) + ".")
    if meds:
        sents.append("Current medications: " + ", ".join(meds) + ".")

    out = " ".join(sents)
    return re.sub(r"\s{2,}", " ", out).strip()

def build_patient_bio_html(patient):
    """
    Structured HTML block:
    - Narrative paragraph omits 'not pregnant or breastfeeding'.
    - In Safety Modifiers table, show rows ONLY when data exists:
        * Pregnant row only if True
        * Breastfeeding row only if True
        * Allergies row only if non-empty list
        * Current Meds row only if non-empty list
    """
    drug = (patient.get('drug_name') or "").strip()
    dose_raw = (patient.get('proposed_dose') or "").strip()
    dose = re.sub(r'(\d)\s*(mg\b)', r'\1 mg', dose_raw, flags=re.I)
    dose = re.sub(r'(\d)\s*(mcg\b)', r'\1 mcg', dose, flags=re.I)
    dose = re.sub(r'(\d)\s*(g\b)', r'\1 g', dose, flags=re.I)

    route = (patient.get('selected_route') or "").strip()
    route_phrase = route_to_phrase(route)
    canon_route = _canonical_route_name(route)

    duration = (patient.get('duration_days') or "").strip()
    indication = (patient.get('indication') or "").strip()

    age = patient.get('age')
    sex = (patient.get('sex') or "").strip()
    weight = patient.get('weight_kg') or patient.get('weight')

    def dash(v):
        return "-" if v in (None, "", [], ()) else v

    def kv_li(label, value):
        return f"<li><span class='k'>{label}:</span> <span class='v'>{value}</span></li>"

    identity_bits = []
    if age not in (None, ""):
        try:
            identity_bits.append(f"{int(float(age))}-year-old")
        except Exception:
            identity_bits.append(str(age))
    if sex:
        identity_bits.append(sex.title())
    identity_str = " ".join(identity_bits) if identity_bits else "Patient"

    try:
        weight_text = f"{float(weight):.1f} kg" if weight not in (None, "") else "-"
    except Exception:
        weight_text = "-"

    preg = bool(patient.get('pregnant'))
    breast = bool(patient.get('breastfeeding'))
    # Avoid stating 'not pregnant or breastfeeding' in HTML narrative too
    if preg and breast:
        preg_text = "The patient is pregnant and breastfeeding."
    elif preg:
        preg_text = "The patient is pregnant."
    elif breast:
        preg_text = "The patient is breastfeeding."
    else:
        preg_text = ""

    nar_drug = dash(drug)
    nar_dose = dash(dose if dose else dose_raw)
    nar_duration = (f"{duration} days" if str(duration).strip() else "-")
    nar_indication = dash(indication)

    narrative_html = (
        f"<p><strong>{identity_str}</strong> (weight: {weight_text}) is currently prescribed "
        f"<strong>{nar_drug} {nar_dose}{(' ' + route_phrase) if route_phrase else ''}</strong> "
        f"for <strong>{nar_duration}</strong> to treat <strong>{nar_indication}</strong>."
        f"{(' ' + preg_text) if preg_text else ''}</p>"
    )

    renal = dash((patient.get('renal_impairment') or "").strip())
    hepatic = dash((patient.get('hepatic_impairment') or "").strip())
    egfr = dash(patient.get('egfr'))
    crcl = dash(patient.get('crcl'))
    scr = dash(patient.get('scr'))
    renal_detail = f"{renal} (eGFR: {egfr}, CrCl: {crcl}, Scr: {scr})"

    # Build Core & Key sections (same as before)
    core_ul = (
        "<ul class='kv'>"
        + kv_li('Drug', nar_drug)
        + kv_li('Dose', nar_dose)
        + kv_li('Route', dash(canon_route))
        + kv_li('Duration', nar_duration)
        + kv_li('Indication', nar_indication)
        + "</ul>"
    )

    key_ul = (
        "<ul class='kv'>"
        + kv_li('Age', dash(age))
        + kv_li('Sex', dash(sex.title() if sex else ''))
        + kv_li('Weight', weight_text)
        + kv_li('Renal', renal_detail)
        + kv_li('Hepatic', hepatic)
        + "</ul>"
    )

    # SAFETY: only show rows when data exists
    allergies_list = [x for x in (patient.get('allergies') or []) if str(x).strip()]
    current_meds_list = [x for x in (patient.get('current_meds') or []) if str(x).strip()]

    safety_items = []
    if preg:
        safety_items.append(kv_li('Pregnant', 'Yes'))
    if breast:
        safety_items.append(kv_li('Breastfeeding', 'Yes'))
    if allergies_list:
        safety_items.append(kv_li('Allergies', ", ".join(allergies_list)))
    if current_meds_list:
        safety_items.append(kv_li('Current Meds', ", ".join(current_meds_list)))

    safety_ul = "<ul class='kv'>" + "".join(safety_items) + "</ul>" if safety_items else "<div class='muted'>No safety modifiers provided.</div>"

    html_block = (
        "<div class='patient-bio'>"
        + narrative_html
        + "<h4>Core Prescription</h4>"
        + core_ul
        + "<h4>Key Patient Factors</h4>"
        + key_ul
        + "<h4>Safety Modifiers</h4>"
        + safety_ul
        + "</div>"
    )
    return html_block
