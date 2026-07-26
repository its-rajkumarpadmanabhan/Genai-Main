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

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False
    print("[WARN] edge-tts package not installed. Speech synthesis will fall back to text.")

load_dotenv()

app = FastAPI(
    title="Beacon Engine",
    version="1.0.0",
    description="Cross-Platform Universal Emergency Voice Engine",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)

GEMINI_API_KEY = (
    (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
)
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Active Gemini 3.x production endpoints
ANALYSIS_MODEL = "gemini-3.6-flash"
FALLBACK_ANALYSIS_MODEL = "gemini-3.5-flash-lite"
TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_FALLBACK_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"


class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, or summary of user inquiry in target language."
    )
    immediate_safety_steps: str = Field(
        description="Immediate physical safety or first-aid steps in target language."
    )
    deescalation_script: str = Field(
        description="The COMPLETE spoken script written strictly in the native script of the requested response language."
    )


def calculate_distance_and_time(
    lat1: float, lon1: float, lat2: float, lon2: float
):
    """Calculates Haversine distance in kilometers and estimated driving reach time."""
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
    dist_km = round(R * c, 1)

    driving_dist_km = dist_km * 1.3
    reach_time_mins = max(1, round((driving_dist_km / 30.0) * 60))
    return dist_km, reach_time_mins


def determine_open_status(tags: dict) -> str:
    """Evaluates facility operating status."""
    opening_hours = tags.get("opening_hours", "").strip().lower()
    amenity = tags.get("amenity", "").strip().lower()
    emergency = tags.get("emergency", "").strip().lower()

    if "24/7" in opening_hours or emergency == "yes" or amenity == "hospital":
        return "Open (24/7 Emergency)"

    if opening_hours:
        if "off" in opening_hours or "closed" in opening_hours:
            return "Closed"
        return f"Open ({tags.get('opening_hours')})"

    return "Open / Active"


def fetch_hospitals_data(lat: float, lon: float) -> List[dict]:
    """Lightweight & high-speed OpenStreetMap query."""
    headers = {"User-Agent": "BeaconEngine/1.0"}
    overpass_query = f"""
    [out:json][timeout:6];
    (
      node["amenity"~"hospital|clinic|dentist|doctors"](around:25000, {lat}, {lon});
      way["amenity"~"hospital|clinic"](around:25000, {lat}, {lon});
    );
    out center 12;
    """
    try:
        url = "https://overpass-api.de/api/interpreter"
        data = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers)

        with urllib.request.urlopen(req, timeout=5) as resp:
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
            amenity = tags.get("amenity", "hospital").capitalize()
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

            status = determine_open_status(tags)

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
                        "status": status,
                        "distance_km": dist_km,
                        "reach_time_mins": reach_time_mins,
                        "maps_url": maps_url,
                        "lat": elem_lat,
                        "lon": elem_lon,
                    }
                )

        hospitals.sort(key=lambda x: x["distance_km"])
        return hospitals[:6]
    except Exception as e:
        print(f"[FAST MAP SCAN ERROR] {e}")
        return []


async def generate_free_neural_speech(text: str, target_language: str) -> Optional[str]:
    """
    Synthesizes audio using Microsoft's free Edge Neural Voice engine.
    Zero rate limits and zero quota impact on Gemini!
    """
    if not text or not HAS_EDGE_TTS:
        return None

    voice_map = {
        "Malayalam": "ml-IN-SobhanaNeural",
        "Tamil": "ta-IN-PallaviNeural",
        "Hindi": "hi-IN-SwaraNeural",
        "Spanish": "es-ES-ElviraNeural",
        "Arabic": "ar-SA-ZariyahNeural",
        "English": "en-US-AvaNeural",
    }
    voice = voice_map.get(target_language, "en-US-AvaNeural")

    try:
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

        audio_bytes = buf.getvalue()
        if audio_bytes:
            return base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        print(f"[EDGE-TTS ERROR] {e}")

    return None


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
    headers = {"User-Agent": "BeaconEngine/1.0"}

    if (lat is None or lon is None) and location_query:
        try:
            encoded_q = urllib.parse.quote(location_query)
            geo_url = f"https://nominatim.openstreetmap.org/search?q={encoded_q}&format=json&limit=1"
            req = urllib.request.Request(geo_url, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
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
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        hospitals_list = []
        if lat is not None and lon is not None:
            hospitals_list = fetch_hospitals_data(lat, lon)

        hospital_summary_lines = []
        for idx, h in enumerate(hospitals_list):
            hospital_summary_lines.append(
                f"Facility #{idx+1}: {h['name']} ({h['type']}) | Distance: {h['distance_km']} km | Reach: ~{h['reach_time_mins']} mins | Status: {h['status']} | Specialist: {h['doctor']}"
            )

        hospital_context_str = (
            "\n".join(hospital_summary_lines)
            if hospital_summary_lines
            else "No live GPS medical facilities found."
        )

        prompt = f"""
        You are Beacon, an intelligent voice emergency health assistant.
        Analyze the audio input from user '{current_user.username}'.

        DETECTED REAL-TIME NEARBY MEDICAL FACILITIES (SORTED BY DISTANCE):
        {hospital_context_str}

        CRITICAL INTENT, MULTILINGUAL & ROUTING MANDATES:

        1. AUTOMATIC SPOKEN LANGUAGE TRANSLATION:
           - The user may speak ANY language in the audio clip (e.g., Malayalam, Tamil, Hindi, English).
           - Regardless of the input spoken language, you MUST generate ALL fields in the JSON response (vocal_risk_analysis, immediate_safety_steps, and deescalation_script) EXCLUSIVELY in the requested TARGET RESPONSE LANGUAGE: {language}.
           - For non-English target languages (e.g. Malayalam, Tamil, Hindi, Arabic, Spanish), write strictly in the NATIVE SCRIPT of {language} (e.g., Malayalam script മലയാളം) so the TTS voice reads it with authentic pronunciation.

        2. ACCURATE HOSPITAL ROUTING & CLOSED STATUS HANDLING:
           - Review the sorted facilities list above.
           - Check the 'Status' of Facility #1.
           - IF FACILITY #1 IS CLOSED:
             - Explicitly announce in native {language} script that the closest option ({hospitals_list[0]['name'] if hospitals_list else 'facility'}) is currently closed.
             - Immediately redirect them to Facility #2 ({hospitals_list[1]['name'] if len(hospitals_list)>1 else 'emergency center'}), stating exact distance ({hospitals_list[1]['distance_km'] if len(hospitals_list)>1 else '2'} km) and drive reach time (~{hospitals_list[1]['reach_time_mins'] if len(hospitals_list)>1 else '5'} mins drive).
           - IF FACILITY #1 IS OPEN:
             - Direct them straight to Facility #1 ({hospitals_list[0]['name'] if hospitals_list else 'medical facility'}), stating exact distance ({hospitals_list[0]['distance_km'] if hospitals_list else '1'} km) and drive reach time (~{hospitals_list[0]['reach_time_mins'] if hospitals_list else '3'} mins drive).

        3. STRICT SCOPE SAFETY:
           - NEVER recommend restaurants, food, or non-medical services.
           - If user mentions medical pain mixed with casual tasks (e.g., "hungry and headache"), address the health symptom immediately and advise them not to delay medical evaluation.

        4. CASUAL / META QUERIES:
           - If query is non-medical (e.g., "Who are you?"), respond conversationally in native script of {language} without listing hospitals.

        OUTPUT MUST BE VALID JSON MATCHING THE SCHEMA EXACTLY.
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
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "NOT_FOUND" in str(e):
                response = client.models.generate_content(
                    model=FALLBACK_ANALYSIS_MODEL,
                    contents=[prompt, audio_part],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=VoiceInterventionResponse,
                    ),
                )
            else:
                raise e

        result_text = response.text
        spoken_text = ""
        try:
            parsed_data = json.loads(result_text)
            spoken_text = parsed_data.get("deescalation_script", "")
        except Exception as parse_err:
            print(f"[JSON PARSE ERROR] {parse_err}")

        # Free Neural Speech Synthesis
        audio_b64 = await generate_free_neural_speech(spoken_text, language)

        should_show_hospitals = bool(hospitals_list) and not any(
            k in result_text.lower()
            for k in ["no medical intervention required", "non-emergency query"]
        )

        return {
            "status": "success",
            "data": result_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/mp3",
            "audio_error": None if audio_b64 else "Audio synthesis fallback",
            "hospitals": hospitals_list if should_show_hospitals else [],
        }
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            raise HTTPException(
                status_code=429,
                detail="System busy. Please retry in 10 seconds."
            )
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)