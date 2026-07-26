import base64
import io
import os
import wave
from math import atan2, cos, radians, sin, sqrt
from typing import Optional

import requests
import uvicorn
from auth import User, get_current_user, router as auth_router
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

load_dotenv()  # local dev convenience — no-op if no .env file is present

app = FastAPI(
    title="Beacon - GenAI Recovery & Prevention Platform",
    version="1.0.0",
    description="Zero-Typing Voice Emergency Intervention Engine",
)

# Enable CORS for public access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    """
    Flatten Pydantic validation errors into a single string so frontend displays
    the actual error message directly.
    """
    messages = [
        err.get("msg", "Invalid input").replace("Value error, ", "")
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": "; ".join(messages) or "Invalid input."},
    )


# Account system: signup / login / forgot-password / reset-password
app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("templates/login.html")


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

# Google Maps: powers the "find nearest hospital/clinic" feature.
# Not required for the rest of the app to work -- /api/nearby-care simply
# returns a clear config error until this is set.
GOOGLE_MAPS_API_KEY = (
    (os.getenv("GOOGLE_MAPS_API_KEY") or "").strip().strip('"').strip("'")
)


def google_maps_configured() -> bool:
    return bool(GOOGLE_MAPS_API_KEY)


# Pydantic Schema guarantees Gemini outputs 100% valid, auto-escaped JSON every time
class VoiceInterventionResponse(BaseModel):
    intent_type: str = Field(
        description="Exactly one of: 'informational', 'emergency', or 'facility_search'."
    )
    facility_query: str = Field(
        description="Only for intent_type='facility_search': the short medical symptom/condition keyword "
        "extracted from the user's speech, written in English (e.g. 'headache', 'stomach ache', 'vomiting') "
        "so it can be used as a place-search keyword. Empty string for all other intent types."
    )
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, or summary of platform informational inquiry translated into the requested language."
    )
    immediate_safety_steps: str = Field(
        description="Text bullet points or summary of immediate physical safety steps translated into the requested language."
    )
    deescalation_script: str = Field(
        description="The COMPLETE spoken response script combining empathetic validation AND immediate physical safety instructions into one single spoken response strictly written in the requested language."
    )


def gemini_auth_error_detail(e: Exception, context: str) -> str:
    msg = str(e)
    if (
        "UNAUTHENTICATED" in msg
        or "401" in msg
        or "PERMISSION_DENIED" in msg
        or "403" in msg
    ):
        return (
            f"{context}: Gemini rejected the API key (invalid or malformed GEMINI_API_KEY). "
            "Generate a fresh key at https://aistudio.google.com/apikey, set it as GEMINI_API_KEY "
            "in your host's environment variables, then redeploy."
        )
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

                parts = (
                    candidates[0].content.parts
                    if candidates[0].content
                    else None
                )
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
        return {
            "status": "online",
            "gemini_configured": False,
            "gemini_key_valid": False,
        }
    try:
        next(iter(client.models.list()))
        return {
            "status": "online",
            "gemini_configured": True,
            "gemini_key_valid": True,
        }
    except Exception as e:
        return {
            "status": "online",
            "gemini_configured": True,
            "gemini_key_valid": False,
            "gemini_error": gemini_auth_error_detail(e, "Key check failed"),
        }


@app.post("/api/voice-intervention")
async def process_voice_crisis(
    file: UploadFile = File(...),
    user_type: str = Form("individual"),
    language: str = Form("English"),
    current_user: User = Depends(get_current_user),
):
    if not client:
        raise HTTPException(
            status_code=500,
            detail="Gemini Engine missing. Set GEMINI_API_KEY env variable.",
        )

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(
                status_code=400,
                detail="No audio received. Please try recording again.",
            )

        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(
            data=audio_bytes, mime_type=mime_type
        )

        prompt = f"""
        You are Beacon, an emergency response and crisis AI assistant.
        Analyze this audio clip from a {user_type}.

        CRITICAL LANGUAGE INSTRUCTION:
        You MUST provide ALL outputs in the JSON schema (vocal_risk_analysis, immediate_safety_steps, and deescalation_script) exclusively in the following language: {language}.
        Even if the input user audio is spoken in a different dialect or language, understand the intent and respond completely in {language}.

        Evaluate user intent and choose EXACTLY ONE of the three cases below. Always fill
        "intent_type" with the matching value ('informational', 'emergency', or 'facility_search')
        and always fill "facility_query" (empty string "" unless case 3 applies).

        1. IF THE USER IS ASKING GENERAL/META QUESTIONS ABOUT THE SYSTEM (e.g., "Who are you?", "What do you do?", "How many people do you help?", "What can you do?"):
           - intent_type: "informational"
           - facility_query: ""
           - vocal_risk_analysis: State "Informational inquiry regarding Beacon system identity and capabilities." in {language}.
           - immediate_safety_steps: State "No emergency physical action required." in {language}.
           - deescalation_script: Provide a concise, friendly response in {language} explaining that you are Beacon, an AI-powered crisis response assistant built to offer real-time voice guidance, de-escalation, and physical safety instructions during emergency situations.

        2. IF THE USER IS EXPERIENCING AN ACTUAL EMERGENCY OR CRISIS (e.g., panic, physical injury, distress, pain):
           - intent_type: "emergency"
           - facility_query: ""
           - vocal_risk_analysis: Identify emotional state, distress level, and risk assessment from vocal markers in {language}.
           - immediate_safety_steps: Provide 2 immediate physical action/first-aid steps for screen reference in {language}.
           - deescalation_script: CRITICAL REQUIREMENT — "deescalation_script" MUST combine BOTH warm empathetic reassurance AND the immediate physical safety/first-aid steps (e.g., keeping an injured leg still, applying pressure, slow deep breathing) into one single spoken response (3-5 sentences) written entirely in {language}.

        3. IF THE USER ASKS TO FIND THE NEAREST HOSPITAL OR CLINIC FOR A SYMPTOM/CONDITION
           (e.g., "check nearest hospital or clinic for headache", "find a clinic near me for stomach ache",
           "where can I go for vomiting", "scan nearby hospitals for my condition"):
           - intent_type: "facility_search"
           - facility_query: the short symptom/condition keyword extracted from the request, written in ENGLISH
             regardless of {language} (e.g. "headache", "stomach ache", "vomiting"), so the backend can use it
             as a place-search keyword.
           - vocal_risk_analysis: Brief note that the user is requesting nearby care for that condition, in {language}.
           - immediate_safety_steps: 1-2 general precautions to take while getting to care, in {language}.
           - deescalation_script: A short, reassuring message in {language} telling the user Beacon is now
             locating the nearest hospital or clinic (checking both open and closed ones) for their condition,
             and that it will tell them how long it will take to get there. Do NOT invent a hospital name or
             travel time here -- the real facility list and travel time are looked up separately.
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
            import json as _json

            parsed_data = _json.loads(result_text)
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
        raise HTTPException(
            status_code=500,
            detail=gemini_auth_error_detail(e, "Audio Processing Error"),
        )


# ----------------------------------------------------------------------------
# Nearest hospital/clinic lookup ("check nearest hospital for headache" etc.)
# ----------------------------------------------------------------------------


class NearbyCareRequest(BaseModel):
    condition: str
    language: str = "English"
    # Caller supplies EITHER lat/lng (preferred, from browser GPS) OR a
    # free-text address/city (fallback when the user denies location access).
    lat: Optional[float] = None
    lng: Optional[float] = None
    address: Optional[str] = None


def google_maps_error_detail(context: str) -> str:
    return (
        f"{context}: Location search isn't configured yet. Generate an API key at "
        "https://console.cloud.google.com/google/maps-apis (enable Places API, "
        "Distance Matrix API, and Geocoding API), set it as GOOGLE_MAPS_API_KEY in "
        "your host's environment variables, then redeploy."
    )


def geocode_address(address: str):
    """Free-text address/city -> (lat, lng). Used when GPS is unavailable/denied."""
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": address, "key": GOOGLE_MAPS_API_KEY},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        return None, f"Could not reach the location service: {e}"

    if data.get("status") != "OK" or not data.get("results"):
        return None, f"Couldn't find '{address}'. Try a more specific address, city, or landmark."

    loc = data["results"][0]["geometry"]["location"]
    return (loc["lat"], loc["lng"]), None


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def search_nearby_facilities(lat: float, lng: float, condition: str, radius_m: int = 5000):
    """
    Queries Google Places Nearby Search for both hospitals and doctors/clinics.
    Deliberately does NOT pass `opennow`, so both open AND currently-closed
    facilities are returned (closed ones are still useful to know about/plan
    around, per the "scan both open and close" requirement).
    """
    seen = {}
    for place_type in ("hospital", "doctor"):
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
                params={
                    "location": f"{lat},{lng}",
                    "radius": radius_m,
                    "type": place_type,
                    "keyword": condition,
                    "key": GOOGLE_MAPS_API_KEY,
                },
                timeout=10,
            )
            data = resp.json()
        except Exception:
            continue

        if data.get("status") not in ("OK", "ZERO_RESULTS"):
            continue

        for result in data.get("results", []):
            place_id = result.get("place_id")
            if place_id and place_id not in seen:
                seen[place_id] = result

    return list(seen.values())


def eta_for_places(lat: float, lng: float, place_ids: list):
    """Real driving distance + 'time to reach' for a list of place_ids."""
    if not place_ids:
        return {}
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/distancematrix/json",
            params={
                "origins": f"{lat},{lng}",
                "destinations": "|".join(f"place_id:{pid}" for pid in place_ids),
                "mode": "driving",
                "key": GOOGLE_MAPS_API_KEY,
            },
            timeout=10,
        )
        data = resp.json()
    except Exception:
        return {}

    result_map = {}
    if data.get("status") == "OK" and data.get("rows"):
        elements = data["rows"][0].get("elements", [])
        for pid, el in zip(place_ids, elements):
            if el.get("status") == "OK":
                result_map[pid] = {
                    "distance_text": el["distance"]["text"],
                    "duration_text": el["duration"]["text"],
                }
    return result_map


@app.post("/api/nearby-care")
async def nearby_care(
    payload: NearbyCareRequest,
    current_user: User = Depends(get_current_user),
):
    if not google_maps_configured():
        raise HTTPException(status_code=500, detail=google_maps_error_detail("Nearby Care"))

    lat, lng = payload.lat, payload.lng
    if lat is None or lng is None:
        if not payload.address:
            raise HTTPException(
                status_code=400,
                detail="No location provided. Share GPS access, or type a city/address to search near.",
            )
        coords, err = geocode_address(payload.address)
        if err:
            raise HTTPException(status_code=400, detail=err)
        lat, lng = coords

    try:
        raw_results = search_nearby_facilities(lat, lng, payload.condition)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Places lookup failed: {e}")

    if not raw_results:
        return {
            "status": "success",
            "condition": payload.condition,
            "facilities": [],
            "message": (
                f"No hospitals or clinics were found nearby for '{payload.condition}'. "
                "If this is urgent, call 911 (or your local emergency number) or 988 for crisis support."
            ),
        }

    for r in raw_results:
        loc = r["geometry"]["location"]
        r["_distance_km"] = haversine_km(lat, lng, loc["lat"], loc["lng"])
    raw_results.sort(key=lambda r: r["_distance_km"])

    # Real driving ETA for the closest handful only, to keep Distance Matrix calls small.
    top_results = raw_results[:5]
    eta_map = eta_for_places(lat, lng, [r["place_id"] for r in top_results])

    facilities = []
    for r in top_results:
        eta = eta_map.get(r["place_id"], {})
        facilities.append({
            "name": r.get("name"),
            "address": r.get("vicinity"),
            "open_now": r.get("opening_hours", {}).get("open_now"),  # True / False / None (unknown)
            "rating": r.get("rating"),
            "distance_text": eta.get("distance_text") or f"~{r['_distance_km']:.1f} km",
            "duration_text": eta.get("duration_text") or "Time to reach unavailable",
            "place_id": r["place_id"],
            "lat": r["geometry"]["location"]["lat"],
            "lng": r["geometry"]["location"]["lng"],
        })

    # Short spoken summary (nearest option + real ETA), translated + read aloud.
    spoken_summary, audio_b64, audio_error = None, None, None
    if client:
        try:
            nearest = facilities[0]
            if nearest["open_now"] is True:
                status_word = "open now"
            elif nearest["open_now"] is False:
                status_word = "currently closed"
            else:
                status_word = "hours unknown"

            summary_prompt = f"""
            Write ONLY 2-3 short, calm, spoken sentences in {payload.language} (no preamble, no labels)
            telling someone that for "{payload.condition}", the nearest option is "{nearest['name']}",
            it is {status_word}, and it is about {nearest['duration_text']} away by car
            ({nearest['distance_text']}). If it is closed, gently suggest calling ahead or checking
            the next nearest option.
            """
            summary_resp = client.models.generate_content(
                model=ANALYSIS_MODEL, contents=summary_prompt
            )
            spoken_summary = (summary_resp.text or "").strip()
        except Exception as e:
            print(f"[NEARBY-CARE SUMMARY ERROR] {e}")

        if spoken_summary:
            audio_b64, audio_error = synthesize_speech(spoken_summary)

    return {
        "status": "success",
        "condition": payload.condition,
        "facilities": facilities,
        "spoken_summary": spoken_summary,
        "audio_base64": audio_b64,
        "audio_mime": "audio/wav",
        "audio_error": audio_error,
    }


app.mount(
    "/", StaticFiles(directory="templates", html=True), name="templates"
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)