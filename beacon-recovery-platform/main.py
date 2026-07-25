import os

from dotenv import load_dotenv
load_dotenv()  # local dev convenience — no-op if no .env file is present (e.g. on Render)

import base64
import io
import wave

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from typing import Optional
from google import genai
from google.genai import types

from auth import router as auth_router, get_current_user, User

app = FastAPI(
    title="Beacon - GenAI Recovery & Prevention Platform",
    version="1.0.0",
    description="Zero-Typing Voice Emergency Intervention Engine"
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
    FastAPI's default 422 response puts `detail` as a LIST of error objects,
    e.g. {"detail": [{"msg": "Value error, Password needs 8+ characters...", ...}]}.
    Every frontend page here does `throw new Error(data.detail)` expecting a
    plain string -- with a list of objects, JS stringifies it as
    "[object Object]" and the real message (from our @field_validators in
    auth.py) never reaches the user. Flatten it into one readable string so
    signup/login/forgot/reset all show the actual validation message.
    """
    messages = []
    for err in exc.errors():
        msg = err.get("msg", "Invalid input")
        # Pydantic v2 prefixes custom @field_validator ValueErrors with this.
        msg = msg.replace("Value error, ", "")
        messages.append(msg)
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages) or "Invalid input."})

# Account system: signup / login / forgot-password / reset-password
app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    """
    Landing page. No account -> straight to sign in (link to sign up from there).
    The protected main tool lives at /app.html and is guarded client-side +
    by the '/api/...' endpoints requiring a valid session below.
    """
    return FileResponse("templates/login.html")


GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ANALYSIS_MODEL = "gemini-3.5-flash"
TTS_MODEL = "gemini-2.5-flash-preview-tts"  # gemini-3.5-flash is text-output only; TTS needs its own model
TTS_VOICE = "Kore"


def gemini_auth_error_detail(e: Exception, context: str) -> str:
    """
    Turn Google's raw 401/403 errors into a message that actually tells you
    what to do, instead of a wall of JSON. UNAUTHENTICATED / PERMISSION_DENIED
    from Gemini almost always means the key itself is wrong -- not expired
    credentials, not a code bug -- so point straight at fixing the key.
    """
    msg = str(e)
    if "UNAUTHENTICATED" in msg or "401" in msg or "PERMISSION_DENIED" in msg or "403" in msg:
        return (
            f"{context}: Gemini rejected the API key (invalid or malformed GEMINI_API_KEY). "
            "Generate a fresh key at https://aistudio.google.com/apikey, set it as GEMINI_API_KEY "
            "in your host's environment variables (no quotes, no extra spaces/newlines), then redeploy."
        )
    return f"{context}: {msg}"


def synthesize_speech(text: str, retries: int = 1) -> Optional[str]:
    """
    Turns a line of text into spoken audio using Gemini TTS and returns it as
    base64-encoded WAV, ready to hand straight to an <audio> tag on the
    frontend. Returns None (never raises) if synthesis fails or the model
    momentarily returns text instead of audio -- a known rare TTS quirk --
    so a voice hiccup never breaks the rest of the response.
    """
    if not client or not text:
        return None
    for _ in range(retries + 1):
        try:
            response = client.models.generate_content(
                model=TTS_MODEL,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
                        )
                    ),
                ),
            )
            candidates = getattr(response, "candidates", None)
            if not candidates:
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
            return base64.b64encode(buf.getvalue()).decode("utf-8")
        except Exception:
            continue
    return None


@app.get("/api/health")
async def health_check():
    if not client:
        return {"status": "online", "gemini_configured": False, "gemini_key_valid": False}
    try:
        # A minimal real call -- this is the only way to know the key is
        # actually accepted by Google, not just present in the environment.
        next(iter(client.models.list()))
        return {"status": "online", "gemini_configured": True, "gemini_key_valid": True}
    except Exception as e:
        return {
            "status": "online",
            "gemini_configured": True,
            "gemini_key_valid": False,
            "gemini_error": gemini_auth_error_detail(e, "Key check failed")
        }


@app.post("/api/voice-intervention")
async def process_voice_crisis(file: UploadFile = File(...), user_type: str = Form("individual"),
                                current_user: User = Depends(get_current_user)):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing. Set GEMINI_API_KEY env variable.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received. Please try recording again.")

        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        prompt = f"""
        Analyze this emergency audio clip from a {user_type} navigating a high-cognitive-load recovery crisis.
        1. Identify emotional state and risk level from tone and vocal markers.
        2. Provide an immediate calming response script written as warm, spoken words directly to the
           person (2-4 short sentences, natural to read out loud -- this will be converted to speech).
        3. Give 2 immediate de-escalation actions.

        Return strictly valid raw JSON with keys: "vocal_risk_analysis", "deescalation_script", "immediate_safety_steps"
        """

        response = client.models.generate_content(
            model=ANALYSIS_MODEL,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        result_text = response.text

        # Extract the spoken script cleanly
        spoken_text = ""
        try:
            import json as _json
            # Strips any markdown block backticks if Gemini includes them
            clean_json_str = result_text.strip().replace("```json", "").replace("```", "").strip()
            parsed_data = _json.loads(clean_json_str)
            spoken_text = parsed_data.get("deescalation_script", "")
        except Exception as json_err:
            print(f"JSON Parsing Error: {json_err}. Raw text was: {result_text}")
            # Fallback: Use the whole response text instead of leaving it empty!
            spoken_text = result_text

        # If spoken_text is still empty, raise an error instead of using static speech
        if not spoken_text.strip():
            spoken_text = "I received your message and I am here to support you. Let's take a deep breath together."

        # Pass non-empty dynamic text to speech synthesizer
        audio_b64 = synthesize_speech(spoken_text)

        return {
            "status": "success",
            "data": result_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/wav"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=gemini_auth_error_detail(e, "Audio Processing Error"))