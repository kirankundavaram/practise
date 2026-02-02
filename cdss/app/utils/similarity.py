from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

def similarity_score(vec1, vec2):
    return cosine_similarity(
        np.array([vec1]),
        np.array([vec2])
    )[0][0]
