from fastapi import APIRouter, UploadFile, File, Form
from app.models.patient import Patient
from app.services.document_service import extract_text
from app.core.ai_engine import analyze_with_ai

router = APIRouter()


@router.post("/analyze")
async def analyze(age: int = Form(...),gender: str = Form(...),symptoms: str = Form(...),drug_name: str = Form(...),file: UploadFile = File(...)):
      patient = Patient(age=age, gender=gender, symptoms=symptoms, drug_name=drug_name)
      document_text = extract_text(file.file)
      result = analyze_with_ai(patient, document_text)
      return {"result": result}