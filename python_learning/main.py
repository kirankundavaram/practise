# from fastapi import FastAPI
# from pydantic import BaseModel
# import pickle


# app = FastAPI(title = "AI")

# with open("model.pkl",'rb') as f:
#     model =pickle.load(f)

# class salary_for_experiense(BaseModel):
#     experience :int
#     education :int
#     skills:int

# @app.get('/')
# def home():
#     return "application working"


# @app.post('/predict')
# def experienseCalculation(data:salary_for_experiense):
#     prediction = model.predict([[data.experience, data.education, data.skills]])
#     return {
#         "predicted_salary": int(prediction[0])
#     }

from fastapi import FastAPI
from pydantic import BaseModel
import pickle

# Create FastAPI app
app = FastAPI(title="Spam Detection AI")

# Load trained NLP model
with open("spam_model.pkl", "rb") as f:
    spam_model = pickle.load(f)

# Load vectorizer
with open("vectorizer.pkl", "rb") as f:
    vectorizer = pickle.load(f)

# Request body schema
class MessageInput(BaseModel):
    text: str

@app.get("/")
def root():
    return {"status": "Spam Detection API is running 🚀"}

@app.post("/predict")
def predict_spam(data: MessageInput):
    # Convert text to vector
    text_vector = vectorizer.transform([data.text])

    # Predict
    prediction = spam_model.predict(text_vector)[0]

    return {
        "message": data.text,
        "prediction": prediction
    }
