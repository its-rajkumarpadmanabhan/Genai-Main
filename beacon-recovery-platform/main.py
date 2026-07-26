import base64
import io
import os
import wave
import json
from typing import Optional
import urllib.parse
import urllib.request

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile, Query
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
    messages = [err.get("msg", "Invalid input").replace("Value error, ", "") for err in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"detail": "; ".join(messages) or "Invalid input."},
    )


app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("templates/login.html")


GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ANALYSIS_MODEL = "gemini-3.6-flash"
FALLBACK_ANALYSIS_MODEL = "gemini-3.5-flash-lite"
TTS_MODEL = "gemini-3.1-flash-tts-preview"
TTS_FALLBACK_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"


class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, or summary of platform informational inquiry."
    )
    immediate_safety_steps: str = Field(
        description="Text bullet points or summary of immediate physical safety steps."
    )
    deescalation_script: str = Field(
        description="Spoken response script combining empathetic validation AND immediate safety instructions."
    )


def gemini_auth_error_detail(e: Exception, context: str) -> str:
    msg = str(e)
    if "UNAUTHENTICATED" in msg or "401" in msg or "PERMISSION_DENIED" in msg or "403" in msg:
        return f"{context}: Gemini rejected the API key."
    return f"{context}: {msg}"


def synthesize_speech(text: str, retries: int = 3):
    if not client:
        return None, "Gemini client not configured."
    if not text:
        return None, "No text provided for audio synthesis."

    tts_models = [TTS_MODEL, TTS_FALLBACK_MODEL]

    last_error = "Unknown failure"
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
                if not candidates:
                    continue

                parts = candidates[0].content.parts if candidates[0].content else None
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
                last_error = str(e)
                continue

    return None, f"TTS synthesis failed: {last_error}"


@app.get("/api/health")
async def health_check():
    if not client:
        return {"status": "online", "gemini_configured": False, "gemini_key_valid": False}
    try:
        next(iter(client.models.list()))
        return {"status": "online", "gemini_configured": True, "gemini_key_valid": True}
    except Exception as e:
        return {
            "status": "online",
            "gemini_configured": True,
            "gemini_key_valid": False,
            "gemini_error": gemini_auth_error_detail(e, "Key check failed"),
        }


@app.get("/api/nearest-hospitals")
async def get_nearest_hospitals(
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    location_query: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """
    Scans for hospitals/clinics via OpenStreetMap (Overpass API).
    Accepts latitude/longitude OR a text location query (e.g. "Thiruvananthapuram").
    """
    headers = {"User-Agent": "BeaconEmergencyPlatform/1.0"}

    # If user provided a text query instead of GPS coordinates, geocode it first
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
                else:
                    raise HTTPException(status_code=404, detail=f"Location '{location_query}' not found.")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to geocode location query: {str(e)}")

    if lat is None or lon is None:
        raise HTTPException(
            status_code=400,
            detail="Latitude and longitude OR a location search query are required.",
        )

    # Query Overpass API for hospitals/clinics within 10,000 meters (10 km)
    overpass_query = f"""
    [out:json][timeout:10];
    (
      node["amenity"="hospital"](around:10000, {lat}, {lon});
      node["amenity"="clinic"](around:10000, {lat}, {lon});
      way["amenity"="hospital"](around:10000, {lat}, {lon});
      way["amenity"="clinic"](around:10000, {lat}, {lon});
    );
    out center 10;
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
            name = tags.get("name") or tags.get("name:en") or "Medical Facility / Clinic"
            facility_type = tags.get("amenity", "hospital").capitalize()
            phone = tags.get("phone") or tags.get("contact:phone") or tags.get("emergency:phone") or "N/A"
            address = tags.get("addr:street") or tags.get("addr:full") or tags.get("addr:suburb") or "Nearby"

            elem_lat = elem.get("lat") or (elem.get("center", {}).get("lat") if "center" in elem else None)
            elem_lon = elem.get("lon") or (elem.get("center", {}).get("lon") if "center" in elem else None)

            maps_url = ""
            if elem_lat and elem_lon:
                maps_url = f"https://www.google.com/maps/dir/?api=1&destination={elem_lat},{elem_lon}"

            hospitals.append({
                "name": name,
                "type": facility_type,
                "phone": phone,
                "address": address,
                "maps_url": maps_url,
                "lat": elem_lat,
                "lon": elem_lon
            })

        return {
            "status": "success",
            "search_center": {"lat": lat, "lon": lon},
            "count": len(hospitals),
            "hospitals": hospitals[:6]  # Return top 6 nearest facilities
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hospital search service unavailable: {str(e)}")


@app.post("/api/voice-intervention")
async def process_voice_crisis(
    file: UploadFile = File(...),
    user_type: str = Form("individual"),
    language: str = Form("English"),
    current_user: User = Depends(get_current_user),
):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing. Set GEMINI_API_KEY env variable.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received.")

        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        prompt = f"""
        You are Beacon, an emergency response and crisis AI assistant.
        Analyze this audio clip from a {user_type}.

        CRITICAL LANGUAGE INSTRUCTION:
        You MUST provide ALL outputs in the JSON schema exclusively in language: {language}.

        Evaluate user intent:
        1. IF GENERAL/META QUESTIONS ("Who are you?", "What do you do?"):
           - vocal_risk_analysis: Informational inquiry regarding Beacon system.
           - immediate_safety_steps: No emergency physical action required.
           - deescalation_script: Friendly concise response explaining Beacon emergency assistant capabilities.

        2. IF EMERGENCY OR CRISIS:
           - vocal_risk_analysis: Identify emotional state, distress level, and risk assessment.
           - immediate_safety_steps: Provide 2 immediate physical action/first-aid steps.
           - deescalation_script: COMBINE BOTH empathetic reassurance AND physical safety steps into 1 spoken script (3-5 sentences).
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
        except Exception as model_err:
            if "429" in str(model_err) or "RESOURCE_EXHAUSTED" in str(model_err) or "404" in str(model_err):
                response = client.models.generate_content(
                    model=FALLBACK_ANALYSIS_MODEL,
                    contents=[prompt, audio_part],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=VoiceInterventionResponse,
                    ),
                )
            else:
                raise model_err

        result_text = response.text
        spoken_text = ""
        try:
            parsed_data = json.loads(result_text)
            spoken_text = parsed_data.get("deescalation_script", "")
        except Exception as parse_err:
            print(f"[JSON PARSE ERROR] {parse_err}")

        audio_b64, audio_error = synthesize_speech(spoken_text)

        return {
            "status": "success",
            "data": result_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/wav",
            "audio_error": audio_error,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=gemini_auth_error_detail(e, "Audio Processing Error"))


app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)