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
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages) or "Invalid input."})

# Include Account System Router
app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    if os.path.exists("templates/login.html"):
        return FileResponse("templates/login.html")
    elif os.path.exists("login.html"):
        return FileResponse("login.html")
    raise HTTPException(status_code=404, detail="login.html not found")


@app.get("/app.html", include_in_schema=False)
async def get_app_page():
    possible_paths = [
        "app.html",
        "templates/app.html",
        "static/app.html",
        "beacon-recovery-platform/app.html",
        "beacon-recovery-platform/templates/app.html"
    ]
    for path in possible_paths:
        if os.path.exists(path):
            return FileResponse(path)
    raise HTTPException(status_code=404, detail="app.html file not found on server.")


GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

MODEL_ID = "gemini-2.0-flash"
TTS_VOICE = "Kore"


def gemini_auth_error_detail(e: Exception, context: str) -> str:
    msg = str(e)
    if "RESOURCE_EXHAUSTED" in msg or "429" in msg:
        return f"{context}: Rate limit hit. Please wait 15 seconds before retrying."
    if "UNAUTHENTICATED" in msg or "401" in msg or "PERMISSION_DENIED" in msg or "403" in msg:
        return f"{context}: Invalid GEMINI_API_KEY. Please verify key in environment settings."
    return f"{context}: {msg}"


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
            "gemini_error": gemini_auth_error_detail(e, "Key check failed")
        }


@app.post("/api/voice-intervention")
async def process_voice_crisis(file: UploadFile = File(...)):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing. Set GEMINI_API_KEY env variable.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received. Please record again.")

        raw_mime = file.content_type or "audio/webm"
        clean_mime = raw_mime.split(";")[0].strip()
        
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=clean_mime)

        # 1. Transcribe speech and formulate emergency response script
        prompt = """
        You are an emergency voice intervention system.
        
        Tasks:
        1. Transcribe the exact speech spoken by the user in the audio clip verbatim.
        2. Draft a warm 2-sentence emergency response script combining reassuring words and 2 immediate action steps tailored to what they said.

        Return strictly valid raw JSON:
        {
          "transcription": "<exact user spoken words>",
          "deescalation_script": "<warm spoken response script with immediate actions>"
        }
        """

        analysis_response = client.models.generate_content(
            model=MODEL_ID,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        transcription = "Audio processed."
        spoken_text = ""
        try:
            import json as _json
            clean_json = analysis_response.text.strip().replace("```json", "").replace("```", "").strip()
            parsed = _json.loads(clean_json)
            transcription = parsed.get("transcription", transcription)
            spoken_text = parsed.get("deescalation_script", "")
        except Exception as parse_err:
            print(f"[JSON Parse Error]: {parse_err}")
            spoken_text = analysis_response.text

        if not spoken_text.strip():
            spoken_text = "I received your message and I am here to help. Please take a slow, deep breath."

        # 2. Convert response text into audio output
        audio_b64 = None
        try:
            tts_response = client.models.generate_content(
                model=MODEL_ID,
                contents=f"Say out loud clearly and calmly: {spoken_text}",
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=TTS_VOICE)
                        )
                    )
                )
            )

            if tts_response.candidates and tts_response.candidates[0].content:
                for part in tts_response.candidates[0].content.parts:
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data and getattr(inline_data, "data", None):
                        pcm_data = inline_data.data
                        buf = io.BytesIO()
                        with wave.open(buf, "wb") as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(24000)
                            wf.writeframes(pcm_data)
                        audio_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                        break
        except Exception as tts_err:
            print(f"[TTS Fallback Triggered]: {tts_err}")

        # Return transcription, script, and base64 audio payload
        return {
            "status": "success",
            "transcription": transcription,
            "deescalation_script": spoken_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/wav"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=gemini_auth_error_detail(e, "Audio Processing Error"))


if os.path.exists("templates"):
    app.mount("/templates", StaticFiles(directory="templates"), name="templates")
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)