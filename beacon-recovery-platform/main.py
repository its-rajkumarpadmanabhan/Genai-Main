import base64
import io
import json
import math
import os
import urllib.parse
import urllib.request
import wave
import struct  # Added to replace audioop
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
TTS_VOICE = "Kore"

# Noise Detection Threshold
MIN_AUDIO_THRESHOLD = 500


class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, or summary of user inquiry in target language."
    )
    detected_specialty: str = Field(
        description="Identified medical requirement or condition (e.g. Emergency Medicine, Neurology, General Medicine)."
    )
    immediate_safety_steps: str = Field(
        description="Immediate physical safety or first-aid steps in target language."
    )
    deescalation_script: str = Field(
        description="The COMPLETE spoken script written strictly in the native script of the requested response language. MUST follow conversational flow: 1. Safety Advice for the condition, 2. Hospital Referral (Name, Ownership, Distance, Time)."
    )


def is_valid_speech(audio_data: bytes) -> bool:
    """Calculates RMS of PCM audio data to filter out background noise."""
    if len(audio_data) < 2:
        return False
    # Unpack 16-bit samples (little-endian)
    count = len(audio_data) // 2
    samples = struct.unpack('<' + 'h' * count, audio_data[:count * 2])
    rms = math.sqrt(sum(s * s for s in samples) / count)
    return rms > MIN_AUDIO_THRESHOLD


def calculate_distance_and_time(
    lat1: float, lon1: float, lat2: float, lon2: float
):
    """Calculates realistic driving distance (1.6x winding) and time (20 km/h average)."""
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
    straight_dist_km = R * c

    # Realistic road distance (straight line * 1.6 factor for urban roads)
    driving_dist_km = round(straight_dist_km * 1.6, 1)
    # 20 km/h is a realistic average speed including traffic/signals in urban areas
    reach_time_mins = max(2, round((driving_dist_km / 20.0) * 60))
    return driving_dist_km, reach_time_mins


def determine_open_status(tags: dict) -> str:
    """Evaluates whether a hospital or clinic is currently open or 24/7."""
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


def fetch_hospitals_data(lat: float, lon: float, specialty_keyword: Optional[str] = None, accuracy_m: Optional[float] = None) -> List[dict]:
    """Scans map with expanding radius. Returns [] if no genuine facilities are verified."""
    headers = {"User-Agent": "BeaconEngine/1.0"}
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter"
    ]
    
    radiuses = [5000, 20000, 50000]

    if accuracy_m and accuracy_m > 0:
        radiuses = [r for r in radiuses if r >= accuracy_m]
        if not radiuses:
            radiuses = [5000, 20000, 50000]
        widened_radius = int(accuracy_m + 5000)
        if widened_radius not in radiuses:
            radiuses.append(widened_radius)
            radiuses.sort()
    
    for radius in radiuses:
        overpass_query = f"""
        [out:json][timeout:15];
        (
          node["amenity"~"hospital|clinic"](around:{radius}, {lat}, {lon});
          way["amenity"~"hospital|clinic"](around:{radius}, {lat}, {lon});
        );
        out center 20;
        """
        data_bytes = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
        
        for endpoint in endpoints:
            try:
                req = urllib.request.Request(endpoint, data=data_bytes, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    result = json.loads(resp.read().decode())
                    elements = result.get("elements", [])
                    
                    if elements:
                        hospitals = []
                        for elem in elements:
                            tags = elem.get("tags", {})
                            amenity = tags.get("amenity", "").lower()
                            opening_hours = tags.get("opening_hours", "").lower()
                            
                            if "veterinary" in amenity or "animal" in tags.get("description", "").lower():
                                continue
                            if "closed" in opening_hours or "off" in opening_hours:
                                continue
                                
                            name = tags.get("name") or tags.get("name:en") or "Medical Center"
                            operator = (tags.get("operator") or "").lower()
                            ownership = "Government Hospital" if any(t in operator or t in name.lower() for t in ["government", "govt", "district"]) else "Private Hospital"
                            
                            elem_lat = elem.get("lat") or elem.get("center", {}).get("lat")
                            elem_lon = elem.get("lon") or elem.get("center", {}).get("lon")
                            
                            if elem_lat and elem_lon:
                                dist_km, reach_time = calculate_distance_and_time(lat, lon, elem_lat, elem_lon)
                                hospitals.append({
                                    "name": name,
                                    "ownership": ownership,
                                    "distance_km": dist_km,
                                    "reach_time_mins": reach_time,
                                    "status": determine_open_status(tags),
                                    "lat": elem_lat,
                                    "lon": elem_lon
                                })
                        
                        if hospitals:
                            hospitals.sort(key=lambda x: x["distance_km"])
                            return hospitals
            except Exception as e:
                print(f"[OVERPASS ERROR] {e}")
                continue
                
    return []

async def generate_free_neural_speech(text: str, target_language: str) -> Optional[str]:
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
    accuracy: Optional[float] = Query(None, description="GPS accuracy radius in meters"),
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

    hospitals = fetch_hospitals_data(lat, lon, accuracy_m=accuracy)
    return {
        "status": "success",
        "search_center": {"lat": lat, "lon": lon, "accuracy_m": accuracy},
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
    accuracy: Optional[float] = Form(None, description="GPS accuracy radius in meters"),
    current_user: User = Depends(get_current_user),
):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing.")

    try:
        audio_bytes = await file.read()
        
        # UNCONSCIOUS/EMERGENCY CHECK: 
        # Even if audio is silent/noise, we treat it as an emergency triage event.
        is_emergency_silent = not is_valid_speech(audio_bytes)
        
        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        hospitals_list = []
        if lat is not None and lon is not None:
            hospitals_list = fetch_hospitals_data(lat, lon, accuracy_m=accuracy)

        hospital_summary_lines = []
        for idx, h in enumerate(hospitals_list):
            hospital_summary_lines.append(
                f"Facility #{idx+1}: Name='{h['name']}' ({h['ownership']}) | Distance={h['distance_km']} km | Reach={h['reach_time_mins']} mins | Status={h['status']}"
            )

        hospital_context_str = (
            "\n".join(hospital_summary_lines)
            if hospital_summary_lines
            else "NO MEDICAL FACILITIES DETECTED. DIRECT USER TO CALL 108 EMERGENCY SERVICES IMMEDIATELY."
        )

        # UPDATED PROMPT: Explicitly handles unconscious/unresponsive state
        prompt = f"""
        You are Beacon, the Emergency Response Engine.
        USER: '{current_user.username}'
        STATUS: {'POTENTIALLY UNCONSCIOUS/UNRESPONSIVE (Silent/Noise Input detected)' if is_emergency_silent else 'ACTIVE VOICE INPUT DETECTED'}

        NEARBY FACILITIES (TRUTH DATA):
        {hospital_context_str}

        CRITICAL OPERATIONAL RULES:
        1. IF STATUS IS UNCONSCIOUS/SILENT: Prioritize immediate life-saving instructions (BLS) and confirm they should call 108.
        2. IF ACTIVE INPUT: Analyze medical condition and provide triage.
        3. ALWAYS: Provide the nearest facility details provided in the context above.
        4. IF NO FACILITIES LISTED: DO NOT INVENT ANY. Tell the user to call 108/911.
        
        LANGUAGE: {language}
        OUTPUT: VALID JSON ONLY.
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

        audio_b64 = await generate_free_neural_speech(spoken_text, language)

        return {
            "status": "success",
            "data": result_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/mp3",
            "hospitals": hospitals_list
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)