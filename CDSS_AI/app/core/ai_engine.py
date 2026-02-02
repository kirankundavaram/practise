import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi"

def analyze_with_ai(patient, document_text):
    prompt = f"""
You are a clinical AI assistant.

Patient Details:
Age: {patient.age}
Gender: {patient.gender}
Symptoms: {patient.symptoms}

Prescribed Drug:
{patient.drug_name}

Drug Document:
{document_text}

Tasks:
1. Decide if the drug is suitable.
2. Identify risks or contraindications.
3. Generate alerts.

Respond in this format:

Status:
Alerts:
- alert 1
- alert 2
Explanation:
"""
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False
    }

    response = requests.post(OLLAMA_URL, json=payload)
    return response.json()["response"]