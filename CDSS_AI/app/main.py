from fastapi import FastAPI
from app.api.analyze import router

app = FastAPI(title="AI-Only CDSS")

app.include_router(router, prefix="/cdss")
