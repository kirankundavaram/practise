from pydantic import BaseModel

class Patient(BaseModel):
    age: int
    gender: str
    symptoms: str
    drug_name: str
