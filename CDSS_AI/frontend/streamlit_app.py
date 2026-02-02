import streamlit as st
import requests

st.set_page_config(page_title="AI CDSS", layout="centered")
st.title("🧠 AI-Only CDSS")

age = st.number_input("Age", min_value=0)
gender = st.selectbox("Gender", ["Male", "Female", "Other"])
symptoms = st.text_area("Symptoms")
drug = st.text_input("Prescribed Drug")
file = st.file_uploader("Upload Drug Document (.docx)", type=["docx"])

if st.button("Analyze"):
    if not file:
        st.warning("Please upload a drug document")
    else:
        response = requests.post(
            "http://127.0.0.1:8000/cdss/analyze",
            data={
                "age": age,
                "gender": gender,
                "symptoms": symptoms,
                "drug_name": drug
            },
            files={"file": file}
        )
        if response.status_code == 200:
            st.subheader("🚨 AI Result")
            st.text(response.json()["result"])
        else:
            st.error(f"Error: {response.status_code}")
