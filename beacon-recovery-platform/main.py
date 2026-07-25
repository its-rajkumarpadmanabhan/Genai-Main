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
    messages = []
    for err in exc.errors():
        msg = err.get("msg", "Invalid input")
        msg = msg.replace("Value error, ", "")
        messages.append(msg)
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages) or "Invalid input."})

# Include Account System
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

ANALYSIS_MODEL = "gemini-3.5-flash"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"


def gemini_auth_error_detail(e: Exception, context: str) -> str:
    msg = str(e)
    if "UNAUTHENTICATED" in msg or "401" in msg or "PERMISSION_DENIED" in msg or "403" in msg:
        return (
            f"{context}: Gemini rejected the API key (invalid or malformed GEMINI_API_KEY). "
            "Generate a fresh key at https://aistudio.google.com/apikey, set it as GEMINI_API_KEY "
            "in your host's environment variables (no quotes, no extra spaces/newlines), then redeploy."
        )
    return f"{context}: {msg}"


def synthesize_speech(text: str, retries: int = 1) -> Optional[str]:
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
async def process_voice_crisis(
    file: UploadFile = File(...)
):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing. Set GEMINI_API_KEY env variable.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received. Please try recording again.")

        raw_mime = file.content_type or "audio/webm"
        clean_mime = raw_mime.split(";")[0].strip()
        
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=clean_mime)

        prompt = """
        You are an emergency voice intervention system.

        Tasks:
        1. Transcribe what the user said in the audio verbatim.
        2. Analyze their vocal risk state based on their words and tone.
        3. Formulate 2 immediate safety action steps tailored strictly to their specific situation.
        4. Draft the SPOKEN SCRIPT (`deescalation_script`). THIS SCRIPT WILL BE CONVERTED TO VOICE AUDIO. 
           It MUST directly combine a short, warm, grounding statement AND the immediate action steps into natural spoken sentences.

        Return strictly valid raw JSON with keys:
        - "transcription": "<exact user spoken words>",
        - "vocal_risk_analysis": "<analysis of risk and emotion>",
        - "deescalation_script": "<complete spoken response combining reassuring words AND action steps to read aloud>",
        - "immediate_safety_steps": ["<step 1>", "<step 2>"]
        """

        response = client.models.generate_content(
            model=ANALYSIS_MODEL,
            contents=[prompt, audio_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        result_text = response.text

        spoken_text = ""
        transcription = ""
        try:
            import json as _json
            clean_json_str = result_text.strip().replace("```json", "").replace("```", "").strip()
            parsed_data = _json.loads(clean_json_str)
            
            transcription = parsed_data.get("transcription", "")
            spoken_text = parsed_data.get("deescalation_script", "")
            
            if not spoken_text.strip():
                steps = parsed_data.get("immediate_safety_steps", [])
                if isinstance(steps, list) and len(steps) > 0:
                    spoken_text = "Please follow these steps right now: " + " ".join(steps)
                else:
                    spoken_text = result_text
        except Exception as json_err:
            print(f"JSON Parsing Error: {json_err}. Raw text: {result_text}")
            spoken_text = result_text

        if not spoken_text.strip():
            spoken_text = "I am listening. Please stay calm and locate a safe place to sit down while I assist you."

        audio_b64 = synthesize_speech(spoken_text)

        return {
            "status": "success",
            "transcription": transcription,
            "deescalation_script": spoken_text,
            "data": result_text,
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