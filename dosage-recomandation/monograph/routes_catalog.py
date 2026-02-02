from functools import lru_cache
import re

MEDICATION_ROUTES = {
    "Oral": ["Oral", "PO", "P.O.", "By Mouth", "Per Os", "Swallow", "Orally", "Peroral"],
    "Rectal": ["Rectal", "PR", "P.R.", "Per Rectum", "Rectally"],
    "Topical": ["Topical", "Cutaneous", "Dermal", "Skin", "External", "Topically", "Dermatological"],
    "Ophthalmic": ["Ophthalmic", "Ocular", "Eye", "Ophth", "Ophthalmological", "Conjunctival"],
    "Otic": ["Otic", "Aural", "Ear", "Otological", "Oticly", "Auricular"],
    "Nasal": ["Nasal", "Intranasal", "Nose", "Nasally", "Rhinal"],
    "Inhalation": ["Inhalation", "Inhaled", "Pulmonary", "Respiratory", "Nebulized", "Inhalational", "Aerosol"],
    "Intravenous": ["Intravenous", "IV", "I.V.", "Intravenous Injection", "IV Push", "IV Infusion", "Intravenously"],
    "Intramuscular": ["Intramuscular", "IM", "I.M.", "Intramuscular Injection", "Intramuscularly"],
    "Subcutaneous": ["Subcutaneous", "SC", "S.C.", "SubQ", "Subcut", "Subcutaneous Injection", "Subcutaneously"],
    "Intradermal": ["Intradermal", "ID", "I.D.", "Intracutaneous", "Intradermally"],
    "Sublingual": ["Sublingual", "SL", "S.L.", "Under Tongue", "Sublingually"],
    "Buccal": ["Buccal", "Bucc", "Between Cheek and Gum", "Buccally"],
    "Vaginal": ["Vaginal", "PV", "P.V.", "Per Vagina", "Intravaginal", "Vaginally"],
    "Transdermal": ["Transdermal", "TD", "T.D.", "Patch", "Dermal Patch", "Transdermally"],
    "Intrathecal": ["Intrathecal", "IT", "I.T.", "Spinal", "Intraspinal", "Intrathecally"],
    "Epidural": ["Epidural", "ED", "E.D.", "Peridural", "Epidurally"]
}

ROUTE_PATTERNS = []
for route, kws in MEDICATION_ROUTES.items():
    for kw in kws:
        pat = re.compile(rf'\b{re.escape(kw.lower())}\b')
        ROUTE_PATTERNS.append((pat, route))

ROUTE_PRIORITY = [
    "Oral", "Intravenous", "Intramuscular", "Subcutaneous", "Rectal",
    "Topical", "Transdermal", "Sublingual", "Buccal", "Vaginal",
    "Inhalation", "Nasal", "Ophthalmic", "Otic", "Intradermal",
    "Intrathecal", "Epidural"
]

def _canonical_route_name(route: str) -> str:
    if not route:
        return ""
    rlow = route.strip().lower()
    for canon in MEDICATION_ROUTES.keys():
        if canon.lower() == rlow:
            return canon
    for canon, syns in MEDICATION_ROUTES.items():
        for s in syns:
            if s.lower() == rlow:
                return canon
    return route.strip().title()

_ROUTE_PHRASE = {
    "Oral": "orally",
    "Rectal": "rectally",
    "Topical": "topically",
    "Ophthalmic": "as eye drops",
    "Otic": "as ear drops",
    "Nasal": "as a nasal preparation",
    "Inhalation": "by inhalation",
    "Intravenous": "intravenously",
    "Intramuscular": "by intramuscular injection",
    "Subcutaneous": "by subcutaneous injection",
    "Intradermal": "by intradermal injection",
    "Sublingual": "sublingually",
    "Buccal": "buccally",
    "Vaginal": "as a vaginal preparation",
    "Transdermal": "as a transdermal patch",
    "Intrathecal": "by intrathecal injection",
    "Epidural": "by epidural injection",
}

def route_to_phrase(route: str) -> str:
    canon = _canonical_route_name(route)
    return _ROUTE_PHRASE.get(canon, canon.lower() if canon else "")

def strip_route_from_text(text: str, route: str) -> str:
    if not text or not route:
        return text
    canon = _canonical_route_name(route)
    synonyms = (MEDICATION_ROUTES.get(canon) or []) + [canon]
    s = text
    for kw in synonyms:
        if not kw:
            continue
        pat = re.compile(r'\b' + re.escape(kw) + r'\b', flags=re.IGNORECASE)
        s = pat.sub(' ', s)
    s = re.sub(r'\b(by|via|per|route|po|p\.o\.|iv|i\.v\.|im|i\.m\.|sc|s\.c\.)\b', ' ', s, flags=re.IGNORECASE)
    s = re.sub(r'\s{2,}', ' ', s).strip(' ,;/')
    return s

def detect_route_in_text(text_line: str):
    s = text_line.lower()
    best, best_pos = None, None
    for pat, route in ROUTE_PATTERNS:
        m = pat.search(s)
        if m:
            pos = m.start()
            if best_pos is None or pos < best_pos:
                best_pos, best = pos, route
    return best

def detect_route_near(text_line: str, number_start_idx: int):
    s = text_line.lower()
    best_route = None
    best_pos = -1
    first_route = None
    first_pos = None
    for pat, route in ROUTE_PATTERNS:
        for m in pat.finditer(s):
            pos = m.start()
            if first_pos is None or pos < first_pos:
                first_pos, first_route = pos, route
            if pos <= number_start_idx and pos > best_pos:
                best_pos, best_route = pos, route
    if best_route is not None:
        return best_route
    return first_route
