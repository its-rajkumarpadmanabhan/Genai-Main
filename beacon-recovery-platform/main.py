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
from sqlalchemy.orm import Session

from auth import (
    User,
    get_current_user,
    get_db,
    get_user_medical_history,
    router as auth_router,
    save_user_medical_event,
)

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
    deescalation_script: str = Field(
        description="The COMPLETE spoken response script written strictly in the native script of the requested language."
    )


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
    [out:json][timeout:12];
    (
      node["amenity"="hospital"](around:25000, {lat}, {lon});
      node["amenity"="clinic"](around:25000, {lat}, {lon});
      node["amenity"="dentist"](around:25000, {lat}, {lon});
      node["amenity"="doctors"](around:25000, {lat}, {lon});
      way["amenity"="hospital"](around:25000, {lat}, {lon});
      way["amenity"="clinic"](around:25000, {lat}, {lon});
      node["healthcare"="hospital"](around:25000, {lat}, {lon});
      node["healthcare"="clinic"](around:25000, {lat}, {lon});
    );
    out center 20;
    """
    try:
        url = "https://overpass-api.de/api/interpreter"
        data = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)

        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())

        elements = result.get("elements", [])
        hospitals = []

        for elem in elements:
            tags = elem.get("tags", {})
            name = (
                tags.get("name")
                or tags.get("name:en")
                or "Medical Center / Health Clinic"
            )
            amenity = tags.get("amenity") or tags.get("healthcare") or "hospital"
            amenity = amenity.capitalize()
            facility_type = "Dental Clinic" if "Dentist" in amenity else amenity
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
                    if "Dentist" in amenity
                    else "Emergency Medical Physician"
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
        print(f"[HOSPITAL SCAN ERROR] {e}")
        return []


def synthesize_speech(text: str, retries: int = 3):
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
            except Exception as e:
                print(f"[TTS ATTEMPT {attempt+1} ERROR] {e}")
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
            status_code=400, detail="GPS Coordinates or location query required."
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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received.")

        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        history_records = get_user_medical_history(db, current_user.id)
        history_str = "No prior recorded medical visits."
        if history_records:
            history_str = "\n".join(
                [
                    f"- Date: {r['date']}, Reported Condition: '{r['condition']}', Recommended Hospital: '{r['hospital']}'"
                    for r in history_records
                ]
            )

        hospitals_list = []
        if lat is not None and lon is not None:
            hospitals_list = fetch_hospitals_data(lat, lon)

        top_h_str = "No nearby hospital detected."
        if hospitals_list:
            top_h = hospitals_list[0]
            top_h_str = f"Name: {top_h['name']}, Distance: {top_h['distance_km']} km, Drive Reach Time: ~{top_h['reach_time_mins']} minutes away, Specialist: {top_h['doctor']}."

        prompt = f"""
        You are Beacon, an intelligent emergency health AI assistant.
        Analyze the audio input from user '{current_user.username}'.

        USER'S PREVIOUS MEDICAL HISTORY LOGS:
        {history_str}

        NEARBY DETECTED MEDICAL CENTER CONTEXT:
        {top_h_str}

        STRICT INTENT, SAFETY & LANGUAGE MANDATES:

        1. EXACT NATIVE LANGUAGE & SCRIPT MANDATE:
           - Selected target language: {language}.
           - You MUST write the ENTIRE JSON fields (vocal_risk_analysis, immediate_safety_steps, and deescalation_script) in native script of language {language}.
           - For example, if {language} is Malayalam, write in Malayalam script (മലയാളം). If Tamil, write in Tamil script (தமிழ்). If Hindi, write in Devanagari (हिंदी).

        2. STRICT SCOPE & MEDICAL PRIORITIZATION:
           - NEVER search for or offer restaurants, food, entertainment, or non-medical services.
           - If user mentions medical pain/symptom mixed with a casual task (e.g. "I am hungry and have a headache", "I have eye pain and I'm going to a movie"):
             - Prioritize the health issue immediately. Warn against delaying medical care for non-essential tasks.
             - Focus 100% on first-aid and reaching the hospital.

        3. PREVIOUS HISTORY ACKNOWLEDGMENT:
           - If user reports a recurring ailment (headache, toothache, etc.) found in history:
             - Acknowledge prior visit in native script of {language} (e.g., mentioning previous hospital visit for continuity of care vs nearest current hospital).

        4. CASUAL ONLY:
           - If query is 100% non-medical (e.g. "Who are you?"), answer politely in native script of {language} without citing hospitals.

        OUTPUT MUST BE VALID JSON MATCHING SCHEMA.
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

            condition_desc = parsed_data.get("vocal_risk_analysis", "")
            if hospitals_list and not any(
                k in result_text.lower()
                for k in ["no medical intervention required", "non-emergency query"]
            ):
                rec_hospital = hospitals_list[0]["name"]
                save_user_medical_event(db, current_user.id, condition_desc, rec_hospital)

        except Exception as parse_err:
            print(f"[JSON PARSE ERROR] {parse_err}")

        audio_b64, audio_error = synthesize_speech(spoken_text)

        should_show_hospitals = bool(hospitals_list) and not any(
            k in result_text.lower()
            for k in ["no medical intervention required", "non-emergency query"]
        )

        return {
            "status": "success",
            "data": result_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/wav",
            "audio_error": audio_error,
            "hospitals": hospitals_list if should_show_hospitals else [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)