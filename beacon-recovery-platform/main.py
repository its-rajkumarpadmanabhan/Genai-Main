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


def synthesize_speech(text: str, retries: int = 3):
    """
    Turns a line of text into spoken audio using Gemini TTS and returns
    (audio_base64, error_message). Never raises -- a voice hiccup should
    never break the rest of the response -- but unlike before, it no longer
    swallows the reason silently. Every failed attempt is printed to stderr
    (visible in your Render/host logs) and the *last* failure reason is
    returned to the caller so it can be surfaced in the API response too.

    Google's own TTS docs note the model occasionally returns text tokens
    instead of audio on a request, causing a 500 -- expected to happen on a
    small percentage of calls, and their recommended fix is exactly what this
    does: retry automatically (bumped from 1 retry to 3).
    """
    if not client:
        return None, "Gemini client not configured (missing GEMINI_API_KEY)."
    if not text:
        return None, "No text was provided to synthesize."

    last_error = "Unknown TTS failure."
    for attempt in range(retries + 1):
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
                last_error = f"Gemini TTS returned no candidates (attempt {attempt + 1})."
                print(f"[TTS ERROR] {last_error}")
                continue

            finish_reason = getattr(candidates[0], "finish_reason", None)
            parts = candidates[0].content.parts if candidates[0].content else None
            if not parts or not getattr(parts[0], "inline_data", None):
                # This is the documented "returned text instead of audio" quirk.
                # Log what it actually said/why, instead of just retrying blind.
                stray_text = getattr(parts[0], "text", None) if parts else None
                last_error = (
                    f"Gemini TTS returned text instead of audio on attempt {attempt + 1} "
                    f"(finish_reason={finish_reason}, text={stray_text!r})."
                )
                print(f"[TTS ERROR] {last_error}")
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
            last_error = gemini_auth_error_detail(e, f"TTS call failed (attempt {attempt + 1})")
            print(f"[TTS ERROR] {last_error}")
            continue

    return None, last_error


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

        # Use the real mime type the browser sent -- not a hardcoded guess --
        # so Gemini decodes the actual codec that was recorded.
        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        prompt = f"""
        Analyze this emergency audio clip from a {user_type} navigating a high-cognitive-load recovery crisis.
        1. Identify emotional state and risk level from tone and vocal markers.
        2. Provide an immediate calming response script written as warm, spoken words directly to the
           person (2-4 short sentences, natural to read out loud -- this will be converted to speech).
        3. Give 2 immediate de-escalation actions.

        Return JSON with keys: "vocal_risk_analysis", "deescalation_script", "immediate_safety_steps"
        """

        response = client.models.generate_content(
            model=ANALYSIS_MODEL,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        result_text = response.text

        # Speak the calming script back -- this is the actual voice reply.
        spoken_text = ""
        try:
            import json as _json
            spoken_text = _json.loads(result_text).get("deescalation_script", "")
        except Exception:
            pass
        audio_b64, audio_error = synthesize_speech(spoken_text)
        if audio_error:
            # Visible in server logs already (synthesize_speech prints each
            # attempt), but also surfaced here so the frontend/browser
            # console shows exactly why voice failed instead of a silent
            # "unavailable" -- open browser devtools Network tab and look at
            # this response, or check your host's logs for [TTS ERROR] lines.
            print(f"[VOICE INTERVENTION] TTS failed, returning text-only. Reason: {audio_error}")

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
    # Binds dynamically to cloud host port (Render, Railway, Heroku)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)