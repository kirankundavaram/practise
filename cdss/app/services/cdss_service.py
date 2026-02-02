from app.models.ai_model import load_model
from app.utils.similarity import similarity_score

model = load_model()

def evaluate_drug(patient_text: str, drug_text: str):
    patient_vec = model.encode(patient_text)
    drug_vec = model.encode(drug_text)

    score = similarity_score(patient_vec, drug_vec)

    if score > 0.65:
        status = "❌ NOT SUITABLE"
        explanation = "Patient condition strongly conflicts with drug information"
    elif score > 0.45:
        status = "⚠️ USE WITH CAUTION"
        explanation = "Partial risk detected between patient condition and drug profile"
    else:
        status = "✅ SUITABLE"
        explanation = "No significant risk detected for this patient"

    return {
        "similarity_score": round(float(score), 3),
        "status": status,
        "explanation": explanation
    }
