import base64
import io
import os
import wave
from typing import Optional

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
    messages = [
        err.get("msg", "Invalid input").replace("Value error, ", "")
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"detail": "; ".join(messages) or "Invalid input."},
    )


app.include_router(auth_router)


@app.get("/", include_in_schema=False)
async def root():
    return FileResponse("templates/login.html")


GEMINI_API_KEY = (
    (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
)
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

ANALYSIS_MODEL = "gemini-2.5-flash"
TTS_MODEL = "gemini-2.5-flash-preview-tts"
TTS_VOICE = "Kore"


class VoiceInterventionResponse(BaseModel):
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

    tts_models = [TTS_MODEL, "gemini-2.5-flash-tts"]

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
    language: str = Form("English"),  # Accepts selected language
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

        Evaluate user intent:

        1. IF THE USER IS ASKING GENERAL/META QUESTIONS ABOUT THE SYSTEM (e.g., "Who are you?", "What do you do?", "How many people do you help?", "What can you do?"):
           - vocal_risk_analysis: State "Informational inquiry regarding Beacon system identity and capabilities." in {language}.
           - immediate_safety_steps: State "No emergency physical action required." in {language}.
           - deescalation_script: Provide a concise, friendly response in {language} explaining that you are Beacon, an AI-powered crisis response assistant built to offer real-time voice guidance, de-escalation, and physical safety instructions during emergency situations.

        2. IF THE USER IS EXPERIENCING AN ACTUAL EMERGENCY OR CRISIS (e.g., panic, physical injury, distress, pain):
           - vocal_risk_analysis: Identify emotional state, distress level, and risk assessment from vocal markers in {language}.
           - immediate_safety_steps: Provide 2 immediate physical action/first-aid steps for screen reference in {language}.
           - deescalation_script: CRITICAL REQUIREMENT — "deescalation_script" MUST combine BOTH warm empathetic reassurance AND the immediate physical safety/first-aid steps (e.g., keeping an injured leg still, applying pressure, slow deep breathing) into one single spoken response (3-5 sentences) written entirely in {language}.
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
                response = client.models.generate_content(
                    model="gemini-2.5-flash-lite",
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

        # Gemini TTS synthesizes native pronunciation for Malayalam, Tamil, Hindi, Spanish, etc.
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


app.mount(
    "/", StaticFiles(directory="templates", html=True), name="templates"
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)