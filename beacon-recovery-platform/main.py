import base64
import io
import os
import wave
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from auth import User, get_current_user, router as auth_router

load_dotenv()  # local dev convenience — no-op if no .env file is present (e.g. on Render)

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
    messages = []
    for err in exc.errors():
        msg = err.get("msg", "Invalid input")
        msg = msg.replace("Value error, ", "")
        messages.append(msg)
    return JSONResponse(
        status_code=422, content={"detail": "; ".join(messages) or "Invalid input."}
    )


# Account system: signup / login / forgot-password / reset-password
app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("templates/login.html")


GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Active model endpoints
ANALYSIS_MODEL = "gemini-3.6-flash"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"


# Pydantic Schema ensures Gemini outputs 100% valid, auto-escaped JSON every time
class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, and risk assessment."
    )
    immediate_safety_steps: str = Field(
        description="Text bullet points or summary of immediate physical safety steps."
    )
    deescalation_script: str = Field(
        description="The COMPLETE spoken response script combining empathetic validation AND immediate physical safety instructions into one single spoken response."
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
            "in your host's environment variables (no quotes, no extra spaces/newlines), then redeploy."
        )
    return f"{context}: {msg}"


def synthesize_speech(text: str, retries: int = 3):
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
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=TTS_VOICE
                            )
                        )
                    ),
                ),
            )
            candidates = getattr(response, "candidates", None)
            if not candidates:
                last_error = (
                    f"Gemini TTS returned no candidates (attempt {attempt + 1})."
                )
                print(f"[TTS ERROR] {last_error}")
                continue

            parts = (
                candidates[0].content.parts if candidates[0].content else None
            )
            if not parts or not getattr(parts[0], "inline_data", None):
                stray_text = getattr(parts[0], "text", None) if parts else None
                last_error = (
                    f"Gemini TTS returned text instead of audio on attempt {attempt + 1} "
                    f"(finish_reason={getattr(candidates[0], 'finish_reason', None)}, text={stray_text!r})."
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
            last_error = gemini_auth_error_detail(
                e, f"TTS call failed (attempt {attempt + 1})"
            )
            print(f"[TTS ERROR] {last_error}")
            continue

    return None, last_error


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
        Analyze this emergency audio clip from a {user_type} navigating a high-cognitive-load recovery crisis.

        1. Identify emotional state and risk level for vocal_risk_analysis.
        2. Provide 2 immediate physical action/first-aid steps for immediate_safety_steps.
        3. Provide a single unified spoken script for deescalation_script written directly to the person.
           CRITICAL REQUIREMENT: "deescalation_script" MUST combine BOTH warm empathetic reassurance AND the immediate physical safety/first-aid steps (e.g. keeping an injured leg still, breathing, applying pressure) into one clear, spoken response (3-5 sentences).
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
            if "429" in str(model_err) or "RESOURCE_EXHAUSTED" in str(model_err):
                print(
                    "[API WARNING] Primary model hit rate limit (429). Retrying with gemini-3.5-flash-lite."
                )
                response = client.models.generate_content(
                    model="gemini-3.5-flash-lite",
                    contents=[prompt, audio_part],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=VoiceInterventionResponse,
                    ),
                )
            else:
                raise model_err

        result_text = response.text

        # Extract complete script for TTS audio synthesis
        spoken_text = ""
        try:
            import json as _json

            parsed_data = _json.loads(result_text)
            spoken_text = parsed_data.get("deescalation_script", "")
        except Exception as parse_err:
            print(f"[JSON PARSE ERROR] {parse_err}")

        audio_b64, audio_error = synthesize_speech(spoken_text)
        if audio_error:
            print(
                f"[VOICE INTERVENTION] TTS failed, returning text-only. Reason: {audio_error}"
            )

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


app.mount(
    "/", StaticFiles(directory="templates", html=True), name="templates"
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)