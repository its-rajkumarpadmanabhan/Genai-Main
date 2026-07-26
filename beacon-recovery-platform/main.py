import base64
import io
import json
import math
import os
import urllib.parse
import urllib.request
import wave
from typing import List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from auth import User, get_current_user, router as auth_router

load_dotenv()

app = FastAPI(
    title="Beacon - GenAI Recovery & Prevention Platform",
    version="1.0.0",
    description="Zero-Typing Voice Emergency Intervention Engine",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    messages = [
        err.get("msg", "Invalid input").replace("Value error, ", "")
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": "; ".join(messages) or "Invalid input."},
    )


app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("templates/login.html")


GEMINI_API_KEY = (
    (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
)
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ANALYSIS_MODEL = "gemini-3.6-flash"
FALLBACK_ANALYSIS_MODEL = "gemini-3.5-flash-lite"
TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_FALLBACK_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"


class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, or summary of user inquiry."
    )
    immediate_safety_steps: str = Field(
        description="Immediate physical safety or guidance steps."
    )
    deescalation_script: str = Field(description="Spoken response script.")


def calculate_distance_and_time(
    lat1: float, lon1: float, lat2: float, lon2: float
):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    dist_km = R * c

    driving_dist_km = dist_km * 1.3
    reach_time_mins = max(1, round((driving_dist_km / 30.0) * 60))
    return round(dist_km, 2), reach_time_mins


def fetch_hospitals_data(lat: float, lon: float) -> List[dict]:
    headers = {"User-Agent": "BeaconEmergencyPlatform/1.0"}
    overpass_query = f"""
    [out:json][timeout:10];
    (
      node["amenity"="hospital"](around:15000, {lat}, {lon});
      node["amenity"="clinic"](around:15000, {lat}, {lon});
      node["amenity"="dentist"](around:15000, {lat}, {lon});
      node["amenity"="doctors"](around:15000, {lat}, {lon});
      way["amenity"="hospital"](around:15000, {lat}, {lon});
      way["amenity"="clinic"](around:15000, {lat}, {lon});
    );
    out center 15;
    """
    try:
        url = "https://overpass-api.de/api/interpreter"
        data = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)

        with urllib.request.urlopen(req, timeout=8) as resp:
            result = json.loads(resp.read().decode())

        elements = result.get("elements", [])
        hospitals = []

        for elem in elements:
            tags = elem.get("tags", {})
            name = tags.get("name") or tags.get("name:en") or "Medical Center"
            amenity = tags.get("amenity", "hospital").capitalize()
            facility_type = (
                "Dental Clinic" if amenity == "Dentist" else amenity
            )
            phone = (
                tags.get("phone")
                or tags.get("contact:phone")
                or tags.get("emergency:phone")
                or "108 / 911"
            )
            address = (
                tags.get("addr:street")
                or tags.get("addr:full")
                or tags.get("addr:suburb")
                or "Nearby"
            )
            doctor = (
                tags.get("operator")
                or tags.get("doctor")
                or tags.get("healthcare:speciality")
                or tags.get("speciality")
                or (
                    "Dental Specialist"
                    if amenity == "Dentist"
                    else "Duty Medical Specialist"
                )
            )

            elem_lat = elem.get("lat") or (
                elem.get("center", {}).get("lat") if "center" in elem else None
            )
            elem_lon = elem.get("lon") or (
                elem.get("center", {}).get("lon") if "center" in elem else None
            )

            if elem_lat and elem_lon:
                dist_km, reach_time_mins = calculate_distance_and_time(
                    lat, lon, elem_lat, elem_lon
                )
                maps_url = f"https://www.google.com/maps/dir/?api=1&destination={elem_lat},{elem_lon}"

                hospitals.append(
                    {
                        "name": name,
                        "type": facility_type,
                        "phone": phone,
                        "address": address,
                        "doctor": doctor,
                        "distance_km": dist_km,
                        "reach_time_mins": reach_time_mins,
                        "maps_url": maps_url,
                        "lat": elem_lat,
                        "lon": elem_lon,
                    }
                )

        hospitals.sort(key=lambda x: x["reach_time_mins"])
        return hospitals[:6]
    except Exception as e:
        print(f"[HOSPITAL FETCH ERROR] {e}")
        return []


def synthesize_speech(text: str, retries: int = 2):
    if not client or not text:
        return None, "TTS unavailable"

    tts_models = [TTS_MODEL, TTS_FALLBACK_MODEL]

    for model_name in tts_models:
        for attempt in range(retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=TTS_VOICE
                                )
                            )
                        ),
                    ),
                )
                candidates = getattr(response, "candidates", None)
                if not candidates or not candidates[0].content:
                    continue

                parts = candidates[0].content.parts
                if not parts or not getattr(parts[0], "inline_data", None):
                    continue

                pcm_data = parts[0].inline_data.data
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(24000)
                    wf.writeframes(pcm_data)
                return base64.b64encode(buf.getvalue()).decode("utf-8"), None
            except Exception:
                continue

    return None, "TTS synthesis failed."


@app.get("/api/health")
async def health_check():
    if not client:
        return {"status": "online", "gemini_configured": False}
    return {"status": "online", "gemini_configured": True}


@app.get("/api/nearest-hospitals")
async def get_nearest_hospitals(
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    location_query: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    headers = {"User-Agent": "BeaconEmergencyPlatform/1.0"}

    if (lat is None or lon is None) and location_query:
        try:
            encoded_q = urllib.parse.quote(location_query)
            geo_url = f"https://nominatim.openstreetmap.org/search?q={encoded_q}&format=json&limit=1"
            req = urllib.request.Request(geo_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                geo_data = json.loads(resp.read().decode())
                if geo_data:
                    lat = float(geo_data[0]["lat"])
                    lon = float(geo_data[0]["lon"])
        except Exception:
            pass

    if lat is None or lon is None:
        raise HTTPException(
            status_code=400, detail="GPS Coordinates or search query required."
        )

    hospitals = fetch_hospitals_data(lat, lon)
    return {
        "status": "success",
        "search_center": {"lat": lat, "lon": lon},
        "count": len(hospitals),
        "hospitals": hospitals,
    }


@app.post("/api/voice-intervention")
async def process_voice_crisis(
    file: UploadFile = File(...),
    user_type: str = Form("individual"),
    language: str = Form("English"),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None),
    current_user: User = Depends(get_current_user),
):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received.")

        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(
            data=audio_bytes, mime_type=mime_type
        )

        hospitals_list = []
        if lat is not None and lon is not None:
            hospitals_list = fetch_hospitals_data(lat, lon)

        top_hospital_info = ""
        if hospitals_list:
            top_h = hospitals_list[0]
            top_hospital_info = f"Name: {top_h['name']}, Type: {top_h['type']}, Drive Time: ~{top_h['reach_time_mins']} mins away, Specialist: {top_h['doctor']}."

        prompt = f"""
        You are Beacon, an intelligent AI crisis response and medical emergency assistant.
        Analyze the audio clip from the user carefully.

        REAL-TIME NEARBY MEDICAL FACILITY DATA:
        {top_hospital_info if top_hospital_info else "No live GPS coordinates available."}

        STRICT INTENT CLASSIFICATION RULES:

        EVALUATE THE USER'S INPUT AND CLASSIFY IT INTO ONE OF THREE CATEGORIES:

        --------------------------------------------------------------------------------
        CATEGORY 1: CONVERSATIONAL, META, OR INFORMATIONAL QUESTIONS
        Examples: "What is the first thing you do?", "Who are you?", "What can you do?", "How are you?"
        - vocal_risk_analysis: "Informational inquiry regarding system capabilities and identity."
        - immediate_safety_steps: "No physical emergency action required."
        - deescalation_script: Answer the user's question directly, clearly, and naturally in {language}. 
          (Example: "When you speak to me during an emergency, my first priority is to evaluate your vocal distress, guide you through immediate first-aid steps, and find the nearest hospital or doctor for you.")
        - STRICT RULE: DO NOT mention any hospitals, drive times, or doctors for general questions!
        --------------------------------------------------------------------------------

        CATEGORY 2: CASUAL NON-MEDICAL REQUESTS
        Examples: "I am hungry", "Find me a restaurant", "What is the weather like?"
        - vocal_risk_analysis: "Casual non-emergency query."
        - immediate_safety_steps: "No medical intervention required."
        - deescalation_script: Respond appropriately and conversationally to the user in {language}. Politely clarify that you are an emergency medical response platform designed for health crises and trauma guidance.
        - STRICT RULE: DO NOT recommend orthopedic doctors, hospitals, or emergency steps for food or casual requests!
        --------------------------------------------------------------------------------

        CATEGORY 3: GENUINE MEDICAL CRISIS, TRAUMA, INJURY, DENTAL PAIN, OR ILLNESS
        Examples: "I have a toothache", "My leg is bleeding", "Chest pain", "I feel unconscious", "I broke my arm"
        - vocal_risk_analysis: Assess distress level, vocal markers, and emergency severity in {language}.
        - immediate_safety_steps: Provide 2 immediate physical first-aid/care steps in {language}.
        - deescalation_script: Provide warm, spoken de-escalation instructions in {language} combining:
          1. Empathetic reassurance and physical first-aid steps.
          2. Explicitly announce out loud: The nearest medical facility ({hospitals_list[0]['name'] if hospitals_list else 'medical center'}), estimated reach time (~{hospitals_list[0]['reach_time_mins'] if hospitals_list else '5'} minutes away), and the attending doctor/specialist ({hospitals_list[0]['doctor'] if hospitals_list else 'emergency specialist'}).
        --------------------------------------------------------------------------------

        OUTPUT FORMAT MUST BE VALID JSON IN {language} MATCHING THE SCHEMA EXACTLY.
        """

        try:
            response = client.models.generate_content(
                model=ANALYSIS_MODEL,
                contents=[prompt, audio_part],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VoiceInterventionResponse,
                ),
            )
        except Exception:
            response = client.models.generate_content(
                model=FALLBACK_ANALYSIS_MODEL,
                contents=[prompt, audio_part],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VoiceInterventionResponse,
                ),
            )

        result_text = response.text
        spoken_text = ""
        try:
            parsed_data = json.loads(result_text)
            spoken_text = parsed_data.get("deescalation_script", "")
        except Exception as parse_err:
            print(f"[JSON PARSE ERROR] {parse_err}")

        audio_b64, audio_error = synthesize_speech(spoken_text)

        is_medical_event = any(
            k in spoken_text.lower()
            for k in [
                "hospital",
                "clinic",
                "doctor",
                "specialist",
                "reach time",
                "first-aid",
                "tooth",
                "pain",
                "medical",
            ]
        ) and not any(
            k in result_text.lower()
            for k in ["non-emergency", "informational query"]
        )

        return {
            "status": "success",
            "data": result_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/wav",
            "audio_error": audio_error,
            "hospitals": hospitals_list if is_medical_event else [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount(
    "/", StaticFiles(directory="templates", html=True), name="templates"
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)