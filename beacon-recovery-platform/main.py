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
TTS_VOICE = "Kore"


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
        description="The COMPLETE spoken script written strictly in the native script of the requested response language. MUST state the specific nearest hospital name, ownership (Govt/Private), exact distance in kilometers, estimated drive reach time in minutes, and if the closest is closed, explicitly announce it and redirect to the next nearest open facility."
    )


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


def fetch_hospitals_data(lat: float, lon: float, specialty_keyword: Optional[str] = None) -> List[dict]:
    """Scans map centered at user's exact GPS coordinates using reliable Overpass API mirrors."""
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
            print(f"[OVERPASS MIRROR FAIL - {endpoint}]: {e}")
            continue

    if not result:
        return []

    elements = result.get("elements", [])
    hospitals = []

    for elem in elements:
        tags = elem.get("tags", {})
        name = (
            tags.get("name")
            or tags.get("name:en")
            or "Medical Center / Health Clinic"
        )
        
        # Ownership Classification Logic
        operator = (tags.get("operator") or tags.get("official_name") or "").lower()
        name_lower = name.lower()
        if any(term in operator or term in name_lower for term in ["government", "govt", "medical college", "district hospital", "general hospital"]):
            ownership = "Government Hospital"
        else:
            ownership = "Private Hospital"

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
            or ("Dental Specialist" if "Dentist" in amenity else "General Emergency Physician")
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

            is_specialty_match = True
            if specialty_keyword and len(specialty_keyword) > 2:
                sk = specialty_keyword.lower()
                combined_tags = f"{name} {facility_type} {doctor} {tags.get('healthcare:speciality', '')}".lower()
                is_specialty_match = any(term in combined_tags for term in [sk, "hospital", "emergency", "clinic"])

            hospitals.append(
                {
                    "name": name,
                    "ownership": ownership,
                    "type": facility_type,
                    "phone": phone,
                    "address": address,
                    "doctor": doctor,
                    "status": status,
                    "is_open": not status.startswith("Closed"),
                    "specialty_match": is_specialty_match,
                    "distance_km": dist_km,
                    "reach_time_mins": reach_time_mins,
                    "maps_url": maps_url,
                    "lat": elem_lat,
                    "lon": elem_lon,
                }
            )

    # Sort strictly: Open facilities first, matching specialty, then by exact distance in kilometers
    hospitals.sort(key=lambda x: (not x["is_open"], not x["specialty_match"], x["distance_km"]))
    return hospitals[:8]


async def generate_free_neural_speech(text: str, target_language: str) -> Optional[str]:
    """Synthesizes audio using Microsoft's free Edge Neural Voice engine."""
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

        # GPS Data Verification
        hospitals_list = []
        if lat is not None and lon is not None:
            hospitals_list = fetch_hospitals_data(lat, lon)

        hospital_summary_lines = []
        for idx, h in enumerate(hospitals_list):
            hospital_summary_lines.append(
                f"Facility #{idx+1}: Name='{h['name']}' ({h['ownership']} | {h['type']}) | Distance={h['distance_km']} km | Drive Reach Time=~{h['reach_time_mins']} mins | Status={h['status']} (Open={h['is_open']}) | Doctor={h['doctor']}"
            )

        hospital_context_str = (
            "\n".join(hospital_summary_lines)
            if hospital_summary_lines
            else "No live GPS medical facilities found."
        )

        prompt = f"""
        You are Beacon, an intelligent voice emergency health assistant.
        Analyze the audio input from user '{current_user.username}'.

        DETECTED REAL-TIME NEARBY MEDICAL FACILITIES (SCANNED AT EXACT USER GPS LATITUDE/LONGITUDE: {lat}, {lon}):
        {hospital_context_str}

        CRITICAL INTENT, GPS ROUTING & MULTILINGUAL MANDATES:

        1. AUTOMATIC SPOKEN LANGUAGE TRANSLATION:
           - Analyze the medical issue (e.g., headache, chest pain, leg pain, stomach pain).
           - Generate ALL JSON fields (vocal_risk_analysis, detected_specialty, immediate_safety_steps, deescalation_script) EXCLUSIVELY in target language: {language}.
           - Write strictly in the NATIVE SCRIPT of {language} (e.g., Malayalam script മലയാളം).

        2. ACCURATE GPS HOSPITAL ANNOUNCEMENT MANDATE:
           - You MUST explicitly tell the user the PARTICULAR HOSPITAL NAME, OWNERSHIP (Government/Private), EXACT DISTANCE IN KILOMETERS, and ESTIMATED REACH TIME IN MINUTES.
           - Check the 'Status' of Facility #1.
           - IF FACILITY #1 IS CLOSED:
             - Explicitly announce in native script that the closest option ({hospitals_list[0]['name'] if hospitals_list else 'facility'}) is currently closed.
             - Immediately redirect them to the NEXT NEAREST OPEN facility ({hospitals_list[1]['name'] if len(hospitals_list)>1 else 'emergency center'}), stating its exact name, ownership, distance ({hospitals_list[1]['distance_km'] if len(hospitals_list)>1 else '2'} km), and estimated driving time (~{hospitals_list[1]['reach_time_mins'] if len(hospitals_list)>1 else '5'} mins drive).
           - IF FACILITY #1 IS OPEN:
             - Announce Facility #1 ({hospitals_list[0]['name'] if hospitals_list else 'medical facility'}), stating ownership, exact distance ({hospitals_list[0]['distance_km'] if hospitals_list else '1'} km) and estimated drive reach time (~{hospitals_list[0]['reach_time_mins'] if hospitals_list else '3'} mins drive).

        3. STRICT SCOPE SAFETY:
           - NEVER recommend restaurants, food, or non-medical services.

        4. CASUAL QUERIES:
           - If query is non-medical (e.g. "Who are you?"), answer conversationally in native script of {language} without listing hospitals.

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