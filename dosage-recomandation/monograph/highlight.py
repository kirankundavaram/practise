import html
import re
from .regexes import HIGHLIGHT_PATTERNS, KEY_TERMS

def _escape_then_highlight(s: str, drug_name: str = "") -> str:
    if s is None:
        return ""
    esc = html.escape(str(s))
    if drug_name:
        esc = re.sub(rf'({re.escape(drug_name)})', r'<b>\1</b>', esc, flags=re.IGNORECASE)
    for pat in HIGHLIGHT_PATTERNS:
        esc = re.sub(pat, r'<b>\1</b>', esc, flags=re.IGNORECASE)
    for term in KEY_TERMS:
        esc = re.sub(rf'({re.escape(term)})', r'<b>\1</b>', esc, flags=re.IGNORECASE)
    return esc

def _level_chip(level: str) -> str:
    level = (level or '').lower()
    color = {
        'critical': '#B91C1C',
        'caution':  '#B45309',
        'info':     '#1D4ED8',
        'safe':     '#047857',
    }.get(level, '#374151')
    label = level.capitalize() if level else 'Info'
    return f"<span style='display:inline-block;padding:2px 8px;border-radius:9999px;background:{color};color:white;font-size:12px;line-height:1'>{html.escape(label)}</span>"

def _section(title: str, body_html: str) -> str:
    if not body_html:
        return ""
    return f"""
    <div style="margin:12px 0;">
      <div style="font-weight:600;margin-bottom:6px;">{html.escape(title)}</div>
      <div>{body_html}</div>
    </div>
    """

def render_dose_summary_html(dose_summary: list, drug_name: str) -> str:
    if not dose_summary:
        return ""
    items = []
    for d in dose_summary:
        text = _escape_then_highlight(d.get('text', ''), drug_name)
        chip = _level_chip(d.get('level', 'info'))
        items.append(f"<li style='margin:6px 0;'>{chip} <span style='margin-left:8px'>{text}</span></li>")
    return f"<ul style='padding-left:18px;margin:0;'>{''.join(items)}</ul>"

def render_structured_alerts_html(structured_alerts: list, drug_name: str) -> str:
    if not structured_alerts:
        return "<p>No structured alerts detected.</p>"
    groups = {}
    for a in structured_alerts:
        at = a.get("AlertType") or "Other"
        ann = a.get("Annotation")
        lines = ann if isinstance(ann, list) else [ann]
        lines = [x for x in lines if x]
        if not lines:
            continue
        groups.setdefault(at, []).extend(lines)
    sections = []
    for at, lines in groups.items():
        lis = []
        for ln in lines:
            lis.append(f"<li style='margin:6px 0'>{_escape_then_highlight(ln, drug_name)}</li>")
        body = f"<ul style='padding-left:18px;margin:0'>{''.join(lis)}</ul>"
        sections.append(_section(at, body))
    return "".join(sections) if sections else "<p>No structured alerts detected.</p>"

def render_dosage_recommendations_html(rec: dict, drug_name: str) -> str:
    if not rec:
        return ""
    from .highlight import _level_chip, _escape_then_highlight  # self-safe reimport
    level = rec.get('level', 'info')
    chip = _level_chip(level)
    primary = _escape_then_highlight(rec.get('primary', ''), drug_name)
    bullets = rec.get('bullets') or []
    bullet_lis = "".join(
        f"<li style='margin:6px 0'>{_escape_then_highlight(b, drug_name)}</li>"
        for b in bullets if b
    )
    bullets_html = f"<ul style='padding-left:18px;margin:0'>{bullet_lis}</ul>" if bullet_lis else ""
    ranges = rec.get('ranges') or {}
    range_strip = ""
    if ranges.get('route') and (ranges.get('min_mg') is not None) and (ranges.get('max_mg') is not None):
        try:
            route = html.escape(str(ranges['route']))
            mn = float(ranges['min_mg']); mx = float(ranges['max_mg'])
            range_strip = f"<div style='font-size:12px;opacity:.8;margin-top:4px;'>Extracted range ({route}): <b>{mn:.0f}–{mx:.0f} mg</b></div>"
        except Exception:
            pass
    panel = f"""
    <div style="border:1px solid #E5E7EB;border-radius:10px;padding:10px 12px;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        {chip}
        <div style="font-weight:600;">Dosage Recommendation</div>
      </div>
      <div style="margin:4px 0">{primary}</div>
      {range_strip}
      {bullets_html}
    </div>
    """
    return panel
