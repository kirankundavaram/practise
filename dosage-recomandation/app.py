from flask import Flask, request, render_template, jsonify
from werkzeug.utils import secure_filename
import os, time, json, hashlib

from monograph.state import last_state
from monograph.utils import load_text_from_file
from monograph.assess import assess
from monograph.bio import (
    create_patient_paragraph,
    polish_with_hf_llm,
    build_patient_bio_html,
    build_patient_bio_text,
)
from monograph.report import (
    hf_summary,
    build_expanded_case_summary,
    build_full_ai_report_html,
    build_dashboard_html,
    build_narrative_one_paragraph,
)
from monograph.routes_catalog import strip_route_from_text

# -------------------- Config --------------------
app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
# Prevent giant uploads from choking the server (10 MB default)
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get("MAX_CONTENT_LENGTH_MB", "10")) * 1024 * 1024
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Truncation limits for expensive LLM steps (tune as needed)
MONOGRAPH_MAX_CHARS_FOR_SUMMARY = int(os.environ.get("MONO_SUMMARY_MAX", "15000"))
MONOGRAPH_MAX_CHARS_FOR_CASE = int(os.environ.get("MONO_CASE_MAX", "15000"))

# Feature flags (disable heavy HF on CPU-only boxes)
DISABLE_HF_SUMMARY = os.environ.get("DISABLE_HF_SUMMARY", "0") == "1"
DISABLE_HF_POLISH  = os.environ.get("DISABLE_HF_POLISH", "0") == "1"

# Simple in-process cache (LRU-ish cap)
_CACHE = {}
_CACHE_MAX = 64  # keep it small; use Redis if you need cross-process

def _now_ms():
    return int(time.time() * 1000)

def _timed(label, fn, *a, **kw):
    t0 = _now_ms()
    r = fn(*a, **kw)
    dur = _now_ms() - t0
    app.logger.info(f"{label} took {dur} ms")
    return r

def _hash_payload(monograph_text: str, patient_info: dict, drug_name: str, proposed_dose: str):
    key_obj = {
        "monograph_sha256": hashlib.sha256(monograph_text.encode("utf-8", errors="ignore")).hexdigest(),
        "patient_info": patient_info,
        "drug_name": drug_name,
        "proposed_dose": proposed_dose,
    }
    raw = json.dumps(key_obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _cache_get(k):
    return _CACHE.get(k)

def _cache_set(k, v):
    if len(_CACHE) >= _CACHE_MAX:
        # drop an arbitrary key (simple cap)
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[k] = v

# -------------------- Flask Routes --------------------
@app.route('/')
def index():
    # always reset state for a clean form
    last_state.clear()
    return render_template(
        'index.html',
        report={},
        patient={},
        drug_name='',
        dose_routes={},
        ranges_summary='',
        patient_bio=None,
        patient_bio_polished=None,
        patient_bio_html=None,
        patient_bio_readable=None,
        case_summary=None,
        dose_summary_html='',
        structured_alerts_html='',
        dosage_recommendations_html='',
        full_ai_report=None,
        dashboard_html=None,
        narrative_one_paragraph=None
    )

@app.route('/analyze', methods=['POST'])
def analyze():
    """Internal route for UI rendering (HTML)."""
    t_req = _now_ms()

    f = request.files.get('monograph')
    if not f:
        return "No monograph uploaded", 400

    # Save once; disk I/O is fine at this size but we could also read in-memory if needed
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(f.filename))
    _timed("save_upload", f.save, filepath)

    monograph_text = _timed("load_text", load_text_from_file, filepath) or ""
    return _do_analysis(monograph_text, request.form, render_html=True, t_req=t_req)

@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """API endpoint for external clients (JSON in → JSON out, no HTML)."""
    t_req = _now_ms()
    data = request.get_json(force=True, silent=True) or {}
    monograph_text = data.get("monograph_text", "").strip()
    if not monograph_text:
        return jsonify({"error": "Missing monograph_text"}), 400

    ctx = _do_analysis(monograph_text, data, render_html=False, t_req=t_req)

    # Return clean JSON (strip HTML fragments)
    clean_resp = {
        "report": ctx["report"],
        "patient": ctx["patient"],
        "drug_name": ctx["drug_name"],
        "patient_bio": ctx["patient_bio"],
        "patient_bio_polished": ctx["patient_bio_polished"],
        "patient_bio_readable": ctx["patient_bio_readable"],
        "case_summary": ctx["case_summary"],
        "narrative_one_paragraph": ctx["narrative_one_paragraph"],
        "ranges_summary": ctx["ranges_summary"],
    }
    return jsonify(clean_resp)

# -------------------- Shared analysis core --------------------
def _do_analysis(monograph_text, payload, render_html: bool, t_req: int):
    drug_name = (payload.get('drug_name') or '').strip()
    proposed_dose = (payload.get('proposed_dose') or '').strip()
    selected_route = None  # infer in assess()

    # labs
    egfr, crcl, scr = (payload.get('egfr','').strip(),
                       payload.get('crcl','').strip(),
                       payload.get('scr','').strip())
    duration_days = (payload.get('duration_days') or '').strip()
    indication = (payload.get('indication') or '').strip()

    weight = (payload.get('weight') or '').strip()
    try:
        weight_kg = float(weight) if weight else None
    except Exception:
        weight_kg = None

    patient_info = {
        'name': (payload.get('name') or '').strip(),
        'age': payload.get('age',''),
        'sex': payload.get('sex',''),
        'weight_kg': weight_kg,
        'pregnant': (payload.get('pregnant','no') or '').lower() in ['yes','true','on','1'],
        'breastfeeding': (payload.get('breastfeeding','no') or '').lower() in ['yes','true','on','1'],
        'allergies': [x.strip() for x in (payload.get('allergies') or '').split(',') if x.strip()],
        'current_meds': [x.strip() for x in (payload.get('current_meds') or '').split(',') if x.strip()],
        'conditions': [x.strip() for x in (payload.get('conditions') or '').split(',') if x.strip()],
        'health_conditions': [x.strip() for x in (payload.get('health_conditions') or payload.get('conditions') or '').split(',') if x.strip()],
        'renal_impairment': payload.get('renal_impairment',''),
        'hepatic_impairment': payload.get('hepatic_impairment',''),
        'egfr': egfr, 'crcl': crcl, 'scr': scr,
        'duration_days': duration_days, 'indication': indication,
        "drug_name": drug_name, "proposed_dose": proposed_dose, "selected_route": ""
    }

    # -------- cache key --------
    cache_key = _hash_payload(monograph_text, patient_info, drug_name, proposed_dose)
    cached = _cache_get(cache_key)
    if cached:
        app.logger.info("cache hit")
        last_state.update(cached['last_state'])
        if render_html:
            resp = render_template('index.html', **cached['render_ctx'])
        else:
            resp = cached['render_ctx']
        app.logger.info(f"analyze total took {_now_ms() - t_req} ms (CACHED)")
        return resp

    # -------- assess --------
    report = _timed("assess", assess,
        monograph_text,
        drug_name,
        patient_info,
        proposed_dose_text=proposed_dose,
        route=selected_route
    )

    inferred = report.get('route_used') or ""
    patient_info['selected_route'] = inferred
    if proposed_dose:
        patient_info['proposed_dose'] = strip_route_from_text(proposed_dose, inferred)

    # -------- optional HF summary --------
    if not DISABLE_HF_SUMMARY:
        mono_for_summary = monograph_text[:MONOGRAPH_MAX_CHARS_FOR_SUMMARY]
        try:
            hf = _timed("hf_summary", hf_summary, mono_for_summary)
            if hf:
                report['hf_generated_summary'] = hf
        except Exception as e:
            app.logger.warning(f"hf_summary failed: {e}")

    # -------- patient bio --------
    raw_bio = _timed("create_patient_paragraph", create_patient_paragraph, patient_info)
    if not DISABLE_HF_POLISH:
        try:
            polished_bio = _timed("polish_with_hf_llm", polish_with_hf_llm, raw_bio, sex=patient_info.get('sex'))
        except Exception as e:
            polished_bio = raw_bio
    else:
        polished_bio = raw_bio

    bio_html = _timed("build_patient_bio_html", build_patient_bio_html, patient_info)
    bio_readable = _timed("build_patient_bio_text", build_patient_bio_text, patient_info)

    # -------- longer narrative --------
    case_summary = _timed("build_expanded_case_summary", build_expanded_case_summary,
                          patient_info, {**report, "monograph_text": monograph_text[:MONOGRAPH_MAX_CHARS_FOR_CASE]})

    full_ai_report = _timed("build_full_ai_report_html", build_full_ai_report_html, patient_info, report)
    dashboard_html = _timed("build_dashboard_html", build_dashboard_html, patient_info, report)
    narrative_one_paragraph = _timed("build_narrative_one_paragraph", build_narrative_one_paragraph, patient_info, report)

    last_update = {
        'text': monograph_text,
        'report': report,
        'patient': patient_info,
        'drug_name': drug_name,
        'patient_bio': raw_bio,
        'patient_bio_polished': polished_bio,
        'patient_bio_html': bio_html,
        'patient_bio_readable': bio_readable,
        'case_summary': case_summary,
        'full_ai_report': full_ai_report,
        'dashboard_html': dashboard_html,
        'narrative_one_paragraph': narrative_one_paragraph
    }
    last_state.update(last_update)

    render_ctx = dict(
        report=report,
        patient=patient_info,
        drug_name=drug_name,
        dose_routes=report.get('dose_routes', {}),
        ranges_summary=report.get('ranges_summary', ''),
        patient_bio=raw_bio,
        patient_bio_polished=polished_bio,
        patient_bio_html=bio_html,
        patient_bio_readable=bio_readable,
        case_summary=case_summary,
        dose_summary_html=report.get('dose_summary_html', ''),
        structured_alerts_html=report.get('structured_alerts_html', ''),
        dosage_recommendations_html=report.get('dosage_recommendations_html', ''),
        full_ai_report=full_ai_report,
        dashboard_html=dashboard_html,
        narrative_one_paragraph=narrative_one_paragraph
    )

    # -------- cache --------
    _cache_set(cache_key, {
        "last_state": last_update,
        "render_ctx": render_ctx
    })

    app.logger.info(f"analyze total took {_now_ms() - t_req} ms")

    if render_html:
        return render_template('index.html', **render_ctx)
    else:
        return render_ctx

if __name__ == '__main__':
    # Dev server only. In production, use gunicorn:
    #   gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:5003 app:app
    app.run(host="0.0.0.0", port=5000, debug=False)