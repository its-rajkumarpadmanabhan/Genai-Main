import os

from dotenv import load_dotenv
load_dotenv()  # local dev convenience — no-op if no .env file is present (e.g. on Render)

import base64
import io
import wave
from pydantic import BaseModel, Field
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
class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(description="Summary of vocal tone and panic level")
    immediate_safety_steps: str = Field(description="Text summary of physical action steps")
    deescalation_script: str = Field(description="FULL spoken response combining empathy AND immediate safety steps")

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
        1. Identify emotional state and risk level from tone and vocal markers for vocal_risk_analysis.
        2. Provide 2 immediate de-escalation actions for immediate_safety_steps.
        3. Provide a single unified spoken script for deescalation_script written directly to the person.
           CRITICAL REQUIREMENT: The "deescalation_script" MUST combine both warm empathetic reassurance AND the immediate physical safety/first-aid steps into one single spoken response (3-5 clear sentences).
        """

        # Enforce response_schema so Gemini returns valid JSON every time
        response = client.models.generate_content(
            model=ANALYSIS_MODEL,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VoiceInterventionResponse
            )
        )
        result_text = response.text

        # Extract spoken text safely
        spoken_text = ""
        try:
            import json as _json
            parsed_data = _json.loads(result_text)
            spoken_text = parsed_data.get("deescalation_script", "")
        except Exception as err:
            print(f"[JSON Parse Error on Backend]: {err}")

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


app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    # Binds dynamically to cloud host port (Render, Railway, Heroku)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
