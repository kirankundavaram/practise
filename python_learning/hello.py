import pandas as pd
from sklearn.linear_model import LinearRegression
import pickle

# Load data
df = pd.read_csv("data.csv")

X = df[["experience", "education", "skills"]]
y = df["salary"]

# Train model
model = LinearRegression()
model.fit(X, y)

# Save model
with open("model.pkl", "wb") as f:
    pickle.dump(model, f)

print("✅ AI model trained and saved")
print("Model accuracy (R²):", model.score(X, y))
