from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.cdss import router as cdss_router

app = FastAPI(title="CDSS Backend trail")

app.include_router(cdss_router, prefix="/api")


# ✅ CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # for development only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



@app.get("/")
def health_check():
    return {"status": "CDSS backend is running"}

