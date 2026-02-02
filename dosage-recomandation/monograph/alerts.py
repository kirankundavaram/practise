import html, re
from .state import last_state
from .utils import extract_full_text
from .regexes import (
    RE_CONTRA, RE_INTERACT, RE_MONITOR, RE_DURATION_DAYS, RE_EVERY_HOURS,
    RE_DOSE, RE_DOSE_G, RE_DOSE_MCG, RE_MAX, CONTRA_KEY_TERMS, INTERACT_DRUGS
)

def find_contraindications(text_lines_lower):
    return list(dict.fromkeys([
        ln for ln in text_lines_lower
        if RE_CONTRA.search(ln) or any(k in ln for k in CONTRA_KEY_TERMS)
    ]))

def find_interaction_lines(text_lines_lower):
    inter = []
    for ln in text_lines_lower:
        if RE_INTERACT.search(ln) or any(d in ln for d in INTERACT_DRUGS):
            inter.append(ln)
    return list(dict.fromkeys(inter))

def find_allergy_alerts(text_lines_lower, allergies):
    alerts = []
    if not allergies:
        return alerts
    for allergy in allergies:
        a = allergy.lower()
        related_lines = []
        for ln in text_lines_lower:
            if a in ln:
                highlighted = re.sub(f"({re.escape(a)})", r"<span style='color:red;'>\1</span>", ln, flags=re.I)
                related_lines.append(highlighted)
        if related_lines:
            alert_text = f"Patient is allergic to <b>{allergy}</b>. Relevant monograph warnings:<br> • " + "<br> • ".join(related_lines)
            alerts.append(alert_text)
    return alerts

def generate_structured_alerts(drug_name, monograph_text, dose_info, patient_info, proposed_dose_text, route):
    alerts = []

    def add_alert(alert_type, annotation_lines):
        if not annotation_lines:
            return
        if isinstance(annotation_lines, str):
            annotation_lines = [annotation_lines]
        ann = [str(line).strip() for line in annotation_lines if str(line).strip()]
        if not ann:
            return
        alerts.append({"AlertType": alert_type, "Annotation": ann if len(ann) > 1 else ann[0]})

    full_text = last_state.get("full_text") or extract_full_text(monograph_text)
    text_lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    text_lines_lower = [ln.lower() for ln in text_lines]

    age = patient_info.get('age')
    sex = (patient_info.get('sex') or '').strip().lower()
    weight_kg = patient_info.get('weight_kg')
    pregnant = bool(patient_info.get('pregnant'))
    breastfeeding = bool(patient_info.get('breastfeeding'))
    renal_impairment = (patient_info.get('renal_impairment') or '').strip().lower() in ['yes','true','on','1','y']
    hepatic_impairment = (patient_info.get('hepatic_impairment') or '').strip().lower() in ['yes','true','on','1','y']
    current_meds = [m.lower() for m in patient_info.get('current_meds', [])]

    egfr = None
    crcl = None
    scr = None
    duration_days = None
    indication = (patient_info.get('indication') or '').strip()

    for k in ['egfr', 'crcl', 'scr', 'duration_days']:
        v = patient_info.get(k)
        try:
            if v is not None and v != '':
                if k in ['egfr', 'crcl', 'scr']:
                    val = float(v)
                else:
                    val = int(float(v))
                locals()[k] = val
        except Exception:
            pass

    proposed_mg = None
    proposed_mgkg = None
    freq_every_hours = None

    if proposed_dose_text:
        mg_vals = [float(x) for x in RE_DOSE.findall(proposed_dose_text)]
        g_vals = [float(x) * 1000.0 for x in RE_DOSE_G.findall(proposed_dose_text)]
        mcg_vals = [float(x) / 1000.0 for x in RE_DOSE_MCG.findall(proposed_dose_text)]
        all_mg = mg_vals + g_vals + mcg_vals
        if all_mg:
            proposed_mg = max(all_mg)
        mgkg_found = re.findall(r'(\d+(?:\.\d+)?)\s*mg\s*/\s*kg(?:\s*/\s*day)?\b', proposed_dose_text, flags=re.I)
        if mgkg_found:
            try:
                proposed_mgkg = float(mgkg_found[0])
            except:
                pass
        m = RE_EVERY_HOURS.search(proposed_dose_text)
        if m:
            try:
                freq_every_hours = int(m.group(1))
            except:
                pass

    mgkg_vals = dose_info.get('mgkg_vals') or []
    if mgkg_vals:
        rec_min, rec_max = (min(mgkg_vals), max(mgkg_vals))
        if weight_kg is None and proposed_mgkg is None:
            add_alert(
                "Low dose/high dose based on Dose range based Alert",
                f"Weight not provided and dose not in mg/kg. Pediatric dosing for {drug_name} commonly uses a mg/kg/day rule (e.g., {rec_min:.0f}–{rec_max:.0f} mg/kg/day). Unable to validate the prescribed dose."
            )
        elif proposed_mgkg is not None:
            if proposed_mgkg < rec_min:
                add_alert(
                    "Low dose/high dose based on Dose range based Alert",
                    f"Prescribed pediatric dose is {drug_name} {proposed_mgkg:.0f} mg/kg/day. The recommended range is {rec_min:.0f}–{rec_max:.0f} mg/kg/day. This is below the effective range."
                )
            elif proposed_mgkg > rec_max:
                add_alert(
                    "Low dose/high dose based on Dose range based Alert",
                    f"Prescribed pediatric dose is {drug_name} {proposed_mgkg:.0f} mg/kg/day. The recommended maximum is {rec_max:.0f} mg/kg/day. Risk of overdose—consider reducing."
                )
        else:
            if proposed_mg is not None and freq_every_hours is not None and weight_kg not in (None, ''):
                try:
                    doses_per_day = max(1, int(24 / freq_every_hours))
                    daily_mg = proposed_mg * doses_per_day
                    mgkg_day = daily_mg / max(1e-6, float(weight_kg))
                    if mgkg_day < rec_min:
                        add_alert("Low dose/high dose based on Dose range based Alert",
                                  f"Calculated pediatric dose is {drug_name} {mgkg_day:.0f} mg/kg/day (weight {weight_kg} kg, {proposed_mg:.0f} mg every {freq_every_hours} h). Recommended {rec_min:.0f}–{rec_max:.0f} mg/kg/day. This is below range.")
                    elif mgkg_day > rec_max:
                        add_alert("Low dose/high dose based on Dose range based Alert",
                                  f"Calculated pediatric dose is {drug_name} {mgkg_day:.0f} mg/kg/day (weight {weight_kg} kg, {proposed_mg:.0f} mg every {freq_every_hours} h). Exceeds recommended maximum {rec_max:.0f} mg/kg/day.")
                except Exception:
                    pass
            else:
                add_alert(
                    "Low dose/high dose based on Dose range based Alert",
                    f"Weight provided ({weight_kg} kg) but dose not given in mg/kg/day and frequency not specified. {drug_name} pediatric dosing typically {min(mgkg_vals):.0f}–{max(mgkg_vals):.0f} mg/kg/day; unable to validate."
                )

    if proposed_mg is not None:
        add_alert("Dose alert based on drug", f"Proposed absolute dose parsed: {proposed_mg:.0f} mg.")

    text_lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    max_lines = [ln for ln in text_lines if RE_MAX.search(ln)]
    if max_lines and proposed_mg is not None:
        max_candidates = []
        for ln in max_lines:
            max_candidates += [float(x) for x in RE_DOSE.findall(ln)]
            max_candidates += [float(x) * 1000.0 for x in RE_DOSE_G.findall(ln)]
        if max_candidates:
            mx_allowed = max(max_candidates)
            if proposed_mg > mx_allowed:
                add_alert("Maximum dose alert",
                          f"Prescribed {drug_name} {proposed_mg:.0f} mg exceeds a labeled maximum near {mx_allowed:.0f} mg found in monograph. Please do not exceed.")

    if duration_days is not None:
        dur_lines = [ln for ln in text_lines if ("day" in ln.lower() and (indication.lower() in ln.lower() if indication else True))]
        monograph_days = None
        for ln in dur_lines:
            m = RE_DURATION_DAYS.search(ln)
            if m:
                monograph_days = int(m.group(1))
                break
        if monograph_days and duration_days != monograph_days:
            add_alert("Duration alert",
                      f"Prescribed duration is {duration_days} days{(' for ' + indication) if indication else ''}. Recommended duration in monograph appears to be {monograph_days} days. Please align to reduce failure/relapse.")

    if age is None or age == '':
        add_alert("Age/Sex group wise dose alert (Pediatrics, Adults, Geriatrics)",
                  f"Age not provided. Dose recommendations for {drug_name} can differ for neonates, pediatrics, adults, and geriatrics. Provide age to validate appropriately.")

    if (patient_info.get('renal_impairment') or '').strip() or (egfr is not None) or (crcl is not None):
        if egfr is None and crcl is None:
            add_alert("Renal dose alert based on Health condition",
                      f"Patient has renal impairment history, but no eGFR/CrCl provided. Dosing of {drug_name} may require adjustment per monograph. Provide eGFR or CrCl.")
        else:
            kidney_metric = egfr if egfr is not None else crcl
            if kidney_metric is not None and kidney_metric < 30 and proposed_mg is not None:
                add_alert("Renal dose alert based on Lab values (eGFR, CRCL, Serum creatinine)",
                          f"(eGFR/CrCl {kidney_metric:.0f} mL/min): Prescribed {drug_name} {proposed_mg:.0f} mg may need extended interval (e.g., every 12–24 h) or reduced dose per monograph renal guidance.")

    if hepatic_impairment:
        add_alert("Hepatic dose alert based on Health condition",
                  f"{drug_name} prescribed in hepatic impairment. Monograph recommends monitoring liver enzymes and assessing for hepatic side effects during prolonged use.")

    if pregnant:
        add_alert("Pregnancy dose alert",
                  f"{drug_name} during pregnancy should use the lowest effective dose when benefits outweigh risks, per monograph wording. Monitor as clinically indicated.")

    informative = []
    for s in dose_info.get('dose_sentences', [])[:3]:
        informative.append(str(s))
    if informative:
        alerts.append({"AlertType": "Dose alert based on drug", "Annotation": informative})

    return alerts
