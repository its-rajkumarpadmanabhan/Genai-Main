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

ANALYSIS_MODEL = "gemini-3.6-flash"
FALLBACK_ANALYSIS_MODEL = "gemini-3.5-flash-lite"
TTS_VOICE = "Kore"


class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, or summary of user inquiry in target language."
    )
    detected_specialty: str = Field(
        description="Identified medical requirement or condition."
    )
    immediate_safety_steps: str = Field(
        description="Immediate physical safety or first-aid steps."
    )
    deescalation_script: str = Field(
        description="Spoken script in native language. MUST state hospital name, ownership (Govt/Private), distance in km, and realistic driving time."
    )


def calculate_distance_and_time(
    lat1: float, lon1: float, lat2: float, lon2: float
):
    """Calculates realistic driving distance and time."""
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
    
    # 1.6x factor for urban road winding
    driving_dist_km = round(straight_dist_km * 1.6, 1)
    # 20 km/h realistic average speed for urban traffic
    reach_time_mins = max(2, round((driving_dist_km / 20.0) * 60))
    return driving_dist_km, reach_time_mins


def determine_open_status(tags: dict) -> str:
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


def fetch_hospitals_data(lat: float, lon: float, specialty_keyword: Optional[str] = None) -> List[dict]:
    headers = {"User-Agent": "BeaconEngine/1.0"}
    
    overpass_query = f"""
    [out:json][timeout:10];
    (
      node["amenity"~"hospital|clinic|dentist|doctors"](around:30000, {lat}, {lon});
      way["amenity"~"hospital|clinic"](around:30000, {lat}, {lon});
      node["healthcare"~"hospital|clinic|doctor"](around:30000, {lat}, {lon});
    );
    out center 25;
    """
    
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
    ]

    result = None
    data_bytes = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")

    for endpoint in endpoints:
        try:
            req = urllib.request.Request(endpoint, data=data_bytes, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode())
                if result and "elements" in result:
                    break
        except Exception as e:
            continue

    if not result: return []

    hospitals = []
    for elem in result.get("elements", []):
        tags = elem.get("tags", {})
        name = tags.get("name") or tags.get("name:en") or "Medical Center"
        
        # Ownership Classification Logic
        operator = (tags.get("operator") or tags.get("official_name") or "").lower()
        name_lower = name.lower()
        if any(term in operator or term in name_lower for term in ["government", "govt", "medical college", "district hospital", "general hospital"]):
            ownership = "Government Hospital"
        else:
            ownership = "Private Hospital"

        status = determine_open_status(tags)
        elem_lat = elem.get("lat") or (elem.get("center", {}).get("lat") if "center" in elem else None)
        elem_lon = elem.get("lon") or (elem.get("center", {}).get("lon") if "center" in elem else None)

        if elem_lat and elem_lon:
            dist_km, reach_time_mins = calculate_distance_and_time(lat, lon, elem_lat, elem_lon)
            
            is_specialty_match = True
            if specialty_keyword and len(specialty_keyword) > 2:
                sk = specialty_keyword.lower()
                combined_tags = f"{name} {tags.get('healthcare:speciality', '')}".lower()
                is_specialty_match = any(term in combined_tags for term in [sk, "hospital", "emergency"])

            hospitals.append({
                "name": name,
                "ownership": ownership,
                "status": status,
                "is_open": not status.startswith("Closed"),
                "distance_km": dist_km,
                "reach_time_mins": reach_time_mins,
                "specialty_match": is_specialty_match
            })

    hospitals.sort(key=lambda x: (not x["is_open"], not x["specialty_match"], x["distance_km"]))
    return hospitals[:8]


async def generate_free_neural_speech(text: str, target_language: str) -> Optional[str]:
    if not text or not HAS_EDGE_TTS: return None
    voice_map = {"Malayalam": "ml-IN-SobhanaNeural", "Tamil": "ta-IN-PallaviNeural", "Hindi": "hi-IN-SwaraNeural", "English": "en-US-AvaNeural"}
    try:
        communicate = edge_tts.Communicate(text, voice_map.get(target_language, "en-US-AvaNeural"))
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio": buf.write(chunk["data"])
        return base64.b64encode(buf.getvalue()).decode("utf-8")
    except: return None


@app.post("/api/voice-intervention")
async def process_voice_crisis(
    file: UploadFile = File(...),
    language: str = Form("English"),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None),
    current_user: User = Depends(get_current_user),
):
    audio_bytes = await file.read()
    hospitals_list = fetch_hospitals_data(lat, lon) if lat and lon else []
    
    # Send detailed GPS-verified info to AI
    hospital_data_str = "\n".join([f"- {h['name']} ({h['ownership']}) | {h['distance_km']} km | {h['reach_time_mins']} mins | Status: {h['status']}" for h in hospitals_list])

    prompt = f"""
    You are Beacon. Analyze the user's voice for medical issues.
    
    NEAREST HOSPITALS (GPS-VERIFIED DATA):
    {hospital_data_str}

    INSTRUCTIONS:
    1. Identify symptom/disease (e.g. Headache, Chest pain).
    2. Write response in {language} (Native script for non-English).
    3. You MUST state the hospital name, OWNERSHIP (Govt/Private), DISTANCE (km), and ETA (mins).
    4. If the user asks for a 'Government' or 'Private' hospital, filter the provided list for that specific type.
    5. If the nearest hospital is CLOSED, tell the user it is closed and redirect to the nearest OPEN facility.
    6. Ensure the estimated time is realistic based on the travel distance (approx 20km/h speed).
    """

    response = client.models.generate_content(
        model=ANALYSIS_MODEL,
        contents=[prompt, types.Part.from_bytes(data=audio_bytes, mime_type=file.content_type)],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=VoiceInterventionResponse),
    )
    
    parsed = json.loads(response.text)
    audio_b64 = await generate_free_neural_speech(parsed.get("deescalation_script", ""), language)
    return {"status": "success", "data": response.text, "audio_base64": audio_b64, "hospitals": hospitals_list}

app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)