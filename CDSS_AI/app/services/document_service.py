from docx import Document
def extract_text(file):
    doc = Document(file)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())