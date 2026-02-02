import pickle

# Load trained model
with open("model.pkl", "rb") as f:
    model = pickle.load(f)

# New person data
experience = 4
education = 3
skills = 75

prediction = model.predict([[experience, education, skills]])
print("💰 Predicted Salary:", int(prediction[0]))