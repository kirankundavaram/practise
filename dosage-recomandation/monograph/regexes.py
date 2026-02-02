import re

RE_CONTRA = re.compile(r'contraindicat', re.IGNORECASE)
RE_INTERACT = re.compile(r'\b(interact|interaction|concomit|co-?administration|synerg|increas|potentiat)\b', re.IGNORECASE)
RE_MONITOR = re.compile(r'\b(monitor|baseline|check)\b', re.IGNORECASE)

RE_DOSE = re.compile(r'(\d+(?:\.\d+)?)\s*mg\b', re.IGNORECASE)
RE_DOSE_G = re.compile(r'(\d+(?:\.\d+)?)\s*g\b', re.IGNORECASE)
RE_DOSE_MCG = re.compile(r'(\d+(?:\.\d+)?)\s*mcg\b', re.IGNORECASE)
RE_DOSE_MGKG = re.compile(r'(\d+(?:\.\d+)?)\s*mg\s*/\s*kg(?:\s*/\s*day)?\b', re.IGNORECASE)
RE_DOSE_MGML = re.compile(r'(\d+(?:\.\d+)?)\s*mg\s*/\s*ml\b', re.IGNORECASE)

RE_WV = re.compile(r'(\d+(?:\.\d+)?)\s*%\s*w\s*/\s*v\b', re.IGNORECASE)
RE_WW = re.compile(r'(\d+(?:\.\d+)?)\s*%\s*w\s*/\s*w\b', re.IGNORECASE)

RE_DURATION_DAYS = re.compile(r'(\d+)\s*(?:days|day)\b', re.IGNORECASE)
RE_EVERY_HOURS = re.compile(r'(?:every|q)\s*(\d+)\s*(?:hours|hrs|h)\b', re.IGNORECASE)
RE_EGFR = re.compile(r'\begfr\b', re.IGNORECASE)
RE_MAX = re.compile(r'\b(max(?:imum)?|do not exceed)\b', re.IGNORECASE)

CONTRA_KEY_TERMS = [
    'pregnan','third trimester','breast','lactat','renal','hepatic',
    'cabg','asthma','ulcer','bleed'
]

INTERACT_DRUGS = [
    'warfarin','aspirin','lithium','digoxin','methotrexate','cyclosporine','tacrolimus',
    'diuretic','ace inhibitor','metformin','ssri','snri','pemetrexed','probenecid','quinolone',
    'voriconazole','rifampin'
]

HIGHLIGHT_PATTERNS = [
    r'(\b\d+(?:\.\d+)?\s*mg(?:\s*/\s*kg(?:\s*/\s*day)?)?\b)',
    r'(\b\d+(?:\.\d+)?\s*mcg\b)',
    r'(\b\d+(?:\.\d+)?\s*g\b)',
    r'(\b\d+\s*(?:mL/min|ml/min)\b)',
    r'(\b\d+\s*(?:hours|hrs|h)\b)',
    r'(\b\d+\s*(?:days|day)\b)',
    r'(\b\d+\s*kg\b)',
    r'(\b\d+\s*mg\s*/\s*ml\b)',
    r'(\b\d+\s*mg\s*every\s*\d+\s*(?:hours|hrs|h)\b)',
    r'(\b\d+\s*[–-]\s*\d+\s*(?:mg|mcg|mg/kg(?:/\s*day)?)\b)',
]

KEY_TERMS = [
    'streptococcal pharyngitis','acute rheumatic fever','gonorrhea',
    'neonate','pediatric','adult','geriatric','pregnancy',
    'ckd','chronic kidney disease','hepatic impairment','renal impairment'
]
