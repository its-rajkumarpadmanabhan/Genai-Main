import os
import requests
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional
from google import genai
from google.genai import types

from auth import router as auth_router

app = FastAPI(
    title="Beacon - GenAI Recovery & Prevention Platform",
    version="1.0.0",
    description="Enterprise Multi-Modal Harm Reduction & Emergency Script Engine"
)

# Enable CORS for public access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Account system: signup / login / forgot-password / reset-password
app.include_router(auth_router)


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

class ZeroTypingRequest(BaseModel):
    user_type: str = Field(..., description="'individual' or 'caregiver'")
    crisis_level: str = Field(..., description="'acute_craving', 'caregiver_deescalation', 'exit_strategy', 'overdose_risk'")
    substance_category: str = Field(default="General", description="Substance type")
    latitude: Optional[float] = Field(default=37.7749)
    longitude: Optional[float] = Field(default=-122.4194)

class EducationQuery(BaseModel):
    query_topic: str
    audience: str = Field(default="family_and_patient")

def fetch_live_environmental_context(lat: float, lon: float) -> dict:
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        resp = requests.get(url, timeout=3)
        if resp.status_code == 200:
            data = resp.json().get("current_weather", {})
            return {
                "temperature": f"{data.get('temperature')}°C",
                "condition_code": data.get("weathercode"),
                "is_day": "Daytime" if data.get("is_day") == 1 else "Nighttime"
            }
    except Exception:
        pass
    return {"temperature": "Unknown", "is_day": "Unknown"}

@app.get("/api/health")
async def health_check():
    return {"status": "online", "gemini_configured": client is not None}

@app.post("/api/emergency-script")
async def generate_emergency_script(payload: ZeroTypingRequest):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing. Set GEMINI_API_KEY env variable.")

    env_context = fetch_live_environmental_context(payload.latitude, payload.longitude)

    system_instruction = """
    You are an AI Clinical Decision Support Engine specializing in Substance Use Disorder (SUD) emergency interventions.
    Your objective is to provide ZERO-TYPING, ultra-high clarity actions when the user's cognitive load is at maximum capacity.
    Outputs MUST be empathetic, clinically sound, actionable, and structured cleanly in JSON.
    """

    prompt = f"""
    Target User Role: {payload.user_type.upper()}
    Emergency Level: {payload.crisis_level}
    Substance Context: {payload.substance_category}
    Real Environmental Context: Time of day is {env_context['is_day']}, Temp: {env_context['temperature']}.

    Generate an immediate intervention returning EXACT JSON with keys:
    1. "immediate_action": One bold sentence (max 15 words) of what to do RIGHT NOW.
    2. "verbatim_script": Exact words to say out loud or text directly to a trusted contact or caregiver.
    3. "somatic_grounding": A 30-second somatic physiological grounding exercise.
    4. "safety_protocol": Critical harm reduction or emergency helpline protocol.
    5. "samhsa_hotline": "1-800-662-4357 (24/7 Real National Helpline)"
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.2
            )
        )
        return {"status": "success", "data": response.text, "context": env_context}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini Inference Error: {str(e)}")

@app.post("/api/voice-intervention")
async def process_voice_crisis(file: UploadFile = File(...), user_type: str = Form("individual")):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing. Set GEMINI_API_KEY env variable.")

    try:
        audio_bytes = await file.read()
        mime_type = file.content_type or "audio/wav"
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        prompt = f"""
        Analyze this emergency audio clip from a {user_type} navigating a high-cognitive-load recovery crisis.
        1. Identify emotional state and risk level from tone and vocal markers.
        2. Provide an immediate calming response script.
        3. Give 2 immediate de-escalation actions.

        Return JSON with keys: "vocal_risk_analysis", "deescalation_script", "immediate_safety_steps"
        """

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3
            )
        )
        return {"status": "success", "data": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Audio Processing Error: {str(e)}")

@app.post("/api/educational-resources")
async def generate_educational_module(payload: EducationQuery):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing.")

    prompt = f"""
    Provide evidence-based clinical recovery knowledge for: '{payload.query_topic}'.
    Target Audience: {payload.audience}.
    Return JSON with keys:
    1. "clinical_summary": 2-sentence explanation of neurobiology/intervention.
    2. "actionable_coping_mechanisms": Array of 3 concrete prevention techniques.
    3. "caregiver_guidance": How family members can support this specific challenge.
    """

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3
            )
        )
        return {"status": "success", "data": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    # Binds dynamically to cloud host port (Render, Railway, Heroku)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)