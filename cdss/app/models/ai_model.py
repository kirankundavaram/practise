from sentence_transformers import SentenceTransformer
from functools import lru_cache


@lru_cache()
def load_model():
    # Lightweight + strong semantic model
    return SentenceTransformer("all-MiniLM-L6-v2")