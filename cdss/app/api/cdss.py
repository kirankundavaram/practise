from fastapi import APIRouter
from pydantic import BaseModel
from app.services.cdss_service import evaluate_drug

router = APIRouter()

class cdssRequestKeys(BaseModel):
    patient_details: str
    drug_name: str
    drug_information: str

class testingFunctionKeys(BaseModel):
    name:str
    age:str
    gender:str

@router.post("/check-drug")
def check_drug(data: cdssRequestKeys):
    ai_result = evaluate_drug(
        patient_text=data.patient_details,
        drug_text=data.drug_information
    )

    return {
        "drug": data.drug_name,
        "ai_result": ai_result
    }

users=[]
@router.post("/testing-api")
def testingFunction(inputData:testingFunctionKeys):
    user={"name":inputData.name,"age":inputData.age,"gender":inputData.gender}
    users.append(user)
    return {
        "message" :"SUCCESS",
        "status":200,
        "data":users
    }
