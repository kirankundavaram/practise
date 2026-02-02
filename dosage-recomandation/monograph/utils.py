from functools import lru_cache
from bs4 import BeautifulSoup
import re

def load_text_from_file(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        return f.read()

def extract_full_text(html_or_text):
    return BeautifulSoup(html_or_text, 'lxml').get_text("\n")

def extract_tables_as_bullets(html_text):
    soup = BeautifulSoup(html_text, 'lxml')
    bullets = []
    for table in soup.find_all('table'):
        headers = [th.get_text(strip=True) for th in table.find_all('th')]
        for row in table.find_all('tr'):
            cells = [td.get_text(strip=True) for td in row.find_all('td')]
            if not cells:
                continue
            if headers and len(headers) == len(cells):
                bullets.append(" • ".join(f"{h}: {c}" for h, c in zip(headers, cells)))
            else:
                bullets.append(" • ".join(cells))
    return "\n".join(bullets)

def extract_tables_as_html(html_text):
    soup = BeautifulSoup(html_text, 'lxml')
    tables = soup.find_all('table')
    clean_html = ""
    for t in tables:
        heading = None
        for prev in t.find_all_previous():
            if prev.name and re.match(r'h\d', prev.name, re.I):
                heading = prev.get_text(strip=True)
                break
        for tag in t.find_all(True):
            for attr in ["style", "class", "id", "width", "height", "border"]:
                tag.attrs.pop(attr, None)
        if heading:
            clean_html += f"<h4>{heading}</h4>\n"
        clean_html += str(t)
    return clean_html


@lru_cache(maxsize=8192)
def normspace(s: str) -> str:
    import re
    return re.sub(r"\s+", " ", s or "").strip()
