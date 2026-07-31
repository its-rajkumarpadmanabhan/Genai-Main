"""
BEACON — Core API Views (Django REST Framework)
Replaces the three main endpoints from main.py:
  GET  /api/health
  GET  /api/nearest-hospitals
  POST /api/voice-intervention

All response shapes are identical to the FastAPI version.
"""

import json
import urllib.parse
import urllib.request

from django.conf import settings
from pydantic import BaseModel, Field
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .utils import (
    fetch_hospitals_data,
    generate_free_neural_speech,
    is_valid_speech,
)

# ── Active Gemini fast production endpoints ────────────────────────────────────
ANALYSIS_MODEL = 'gemini-flash-latest'
FALLBACK_ANALYSIS_MODEL = 'gemini-2.0-flash-lite'
TTS_VOICE = 'Kore'


# ── Pydantic schema (same as main.py VoiceInterventionResponse) ───────────────
class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description='Analysis of vocal tone, distress level, or summary of user inquiry strictly in the user-selected response language.'
    )
    detected_specialty: str = Field(
        description='Identified medical requirement or condition (e.g. Emergency Medicine, Neurology, General Medicine).'
    )
    immediate_safety_steps: str = Field(
        description='Immediate physical safety or first-aid steps strictly in the user-selected response language.'
    )
    deescalation_script: str = Field(
        description=(
            'The COMPLETE spoken script written strictly in the native script of the requested '
            'response language. MUST follow conversational flow: 1. Safety Advice for the condition, '
            '2. Hospital Referral (Name, Ownership, Distance, Time).'
        )
    )
    severity_level: str = Field(
        description=(
            'Classify the medical severity of this query. MUST be one of: '
            '"critical" (life-threatening emergency like heart attack, stroke, severe bleeding, '
            'breathing difficulty, unconsciousness, poisoning, severe allergic reaction), '
            '"urgent" (needs prompt medical attention like chest pain, high fever, fracture, '
            'severe pain, infection, burns), '
            '"moderate" (medical concern that needs care like headache, mild pain, cold/flu, '
            'skin issues, digestive problems), '
            '"low" (general health inquiry, non-medical question, greetings, or casual conversation).'
        )
    )


def _get_gemini_client():
    """Lazily initialises the Gemini client (returns None if no API key set)."""
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        return None
    try:
        from google import genai
        return genai.Client(api_key=api_key)
    except Exception as exc:
        print(f'[GEMINI CLIENT ERROR] {exc}')
        return None


def _is_auth_error(exc: Exception) -> bool:
    """Detects Gemini 401/UNAUTHENTICATED errors so we surface a friendly message."""
    msg = str(exc).lower()
    return any(k in msg for k in (
        '401', 'unauthenticated', 'invalid authentication',
        'access_token_type_unsupported', 'api_key_invalid',
    ))


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(k in msg for k in ('429', 'RESOURCE_EXHAUSTED'))


# ── Health Check ──────────────────────────────────────────────────────────────
class HealthView(APIView):
    """GET /api/health"""
    permission_classes = [AllowAny]

    def get(self, request):
        client = _get_gemini_client()
        return Response({
            'status': 'online',
            'gemini_configured': bool(client),
        })


# ── Nearest Hospitals ─────────────────────────────────────────────────────────
class NearestHospitalsView(APIView):
    """GET /api/nearest-hospitals?lat=&lon=&location_query=&accuracy="""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        lat_raw = request.query_params.get('lat')
        lon_raw = request.query_params.get('lon')
        location_query = request.query_params.get('location_query')
        accuracy_raw = request.query_params.get('accuracy')

        try:
            lat = float(lat_raw) if lat_raw else None
            lon = float(lon_raw) if lon_raw else None
            accuracy = float(accuracy_raw) if accuracy_raw else None
        except (ValueError, TypeError):
            return Response(
                {'detail': 'Invalid coordinate values.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        headers = {'User-Agent': 'BeaconEngine/1.0'}

        # Geocode text query → lat/lon via Nominatim (same as main.py)
        if (lat is None or lon is None) and location_query:
            try:
                encoded_q = urllib.parse.quote(location_query)
                geo_url = f'https://nominatim.openstreetmap.org/search?q={encoded_q}&format=json&limit=1'
                req = urllib.request.Request(geo_url, headers=headers)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    geo_data = json.loads(resp.read().decode())
                    if geo_data:
                        lat = float(geo_data[0]['lat'])
                        lon = float(geo_data[0]['lon'])
            except Exception:
                pass

        if lat is None or lon is None:
            return Response(
                {'detail': 'GPS Coordinates or search query required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        hospitals = fetch_hospitals_data(lat, lon, accuracy_m=accuracy)
        return Response({
            'status': 'success',
            'search_center': {'lat': lat, 'lon': lon, 'accuracy_m': accuracy},
            'count': len(hospitals),
            'hospitals': hospitals,
        })


# ── Voice Intervention ────────────────────────────────────────────────────────
class VoiceInterventionView(APIView):
    """POST /api/voice-intervention (multipart/form-data)"""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        from google.genai import types

        client = _get_gemini_client()
        if not client:
            return Response(
                {'detail': 'Gemini Engine missing.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        audio_file = request.FILES.get('file')
        if not audio_file:
            return Response(
                {'detail': 'No audio received.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        language = request.data.get('language', 'English')

        try:
            lat = float(request.data.get('lat')) if request.data.get('lat') else None
            lon = float(request.data.get('lon')) if request.data.get('lon') else None
            accuracy = float(request.data.get('accuracy')) if request.data.get('accuracy') else None
        except (ValueError, TypeError):
            lat = lon = accuracy = None

        try:
            audio_bytes = audio_file.read()
            if not audio_bytes:
                return Response(
                    {'detail': 'No audio received.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Filter background noise
            if not is_valid_speech(audio_bytes):
                return Response({'status': 'ignore', 'message': 'Noise detected'})

            mime_type = audio_file.content_type or 'audio/webm'
            audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

            # Fetch real-time nearby hospitals if GPS available (fast_mode for sub-second response)
            hospitals_list = []
            if lat is not None and lon is not None:
                hospitals_list = fetch_hospitals_data(lat, lon, accuracy_m=accuracy, fast_mode=True)

            hospital_summary_lines = []
            for idx, h in enumerate(hospitals_list):
                hospital_summary_lines.append(
                    f"Facility #{idx+1}: Name='{h['name']}' ({h['ownership']} | {h['type']}) "
                    f"| Distance={h['distance_km']} km | Drive Reach Time=~{h['reach_time_mins']} mins "
                    f"| Status={h['status']} (Open={h['is_open']}) | Doctor={h['doctor']}"
                )

            hospital_context_str = (
                '\n'.join(hospital_summary_lines)
                if hospital_summary_lines
                else 'No live GPS medical facilities found.'
            )

            prompt = f"""
            You are Beacon, an intelligent voice emergency health assistant.
            Analyze the audio input from user '{request.user.username}'.

            DETECTED REAL-TIME NEARBY MEDICAL FACILITIES:
            {hospital_context_str}

            CRITICAL CONVERSATIONAL FLOW RULES:
            1. FIRST: If the user describes a condition (headache, chest pain, body pain, etc.),
               ALWAYS provide the immediate first-aid, safety advice, or 'what to do' FIRST.
            2. SECOND: ONLY AFTER providing safety advice, provide the nearest hospital referral.
            3. USER COUNT REQUESTS: If the user asks for '5 hospitals' or '10 hospitals', you MUST
               provide that specific number from the list above. If no number is asked, default to
               the most relevant one.
            4. HOSPITAL REFERRAL DETAILS: For each hospital mentioned, explicitly state:
               NAME, OWNERSHIP (Government/Private), DISTANCE (km), and TIME (mins).
            5. IF CLOSED: If the nearest is closed, announce it and redirect to the next nearest OPEN facility.

            CRITICAL LANGUAGE & SCRIPT RULES FOR TEXT-TO-SPEECH (TTS):
            1. MANDATORY TARGET RESPONSE LANGUAGE: You MUST generate all response content (vocal_risk_analysis, immediate_safety_steps, deescalation_script) STRICTLY AND EXCLUSIVELY in the user-selected Response Language: '{language}'.
            2. IGNORE AUDIO LANGUAGE FOR OUTPUT: Do NOT reply in the language spoken by the user in the audio if it differs from '{language}'. Regardless of what language the user speaks in the audio (e.g., English), translate and understand the user's inquiry, but generate all outputs and speech scripts EXCLUSIVELY in '{language}'.
            3. STRICT SCRIPT & PHONETIC TRANSLITERATION FOR NON-ENGLISH (e.g., Malayalam, Tamil, Hindi, Spanish, Arabic):
               - Write EVERYTHING (vocal_risk_analysis, immediate_safety_steps, deescalation_script) strictly in '{language}' native script.
               - DO NOT mix Latin/English alphabet characters inside non-English script output.
               - Transliterate ALL hospital names, clinic names, medical terms, abbreviations (e.g., ICU, Dr., St.), and units (e.g., km -> കിലോമീറ്റർ, mins -> മിനിറ്റ്) PHONETICALLY into the target native script!
               - Examples for Malayalam:
                 * Instead of 'Regional Healthcare Clinic', write 'റീജിയണൽ ഹെൽത്ത്‌കെയർ ക്ലിനിക്ക്'.
                 * Instead of 'St. Jude Urgent Care Facility', write 'സെന്റ് ജൂഡ് അടിയന്തര പരിചരണ കേന്ദ്രം'.
                 * Instead of 'Emergency General Hospital', write 'എമർജൻസി ജനറൽ ഹോസ്പിറ്റൽ'.
                 * Instead of 'City Critical Care & Trauma Center', write 'സിറ്റി ക്രിട്ടിക്കൽ കെയർ ആൻഡ് ട്രോമ സെന്റർ'.
                 * Instead of '2 km', write '2 കിലോമീറ്റർ'.
               - This ensures both the displayed "Beacon Analysis Output" text and the Text-to-Speech (TTS) audio reader pronounce all words (both native words and transliterated English names/terms) with 100% correct accuracy without voice synthesis glitches.

            OUTPUT MUST BE VALID JSON MATCHING THE SCHEMA EXACTLY.
            """

            # Primary model → fallback chain (gemini-3.6-flash → gemini-3.5-flash-lite → gemini-flash-latest)
            try:
                response = client.models.generate_content(
                    model=ANALYSIS_MODEL,
                    contents=[prompt, audio_part],
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json',
                        response_schema=VoiceInterventionResponse,
                    ),
                )
            except Exception as exc:
                if _is_auth_error(exc):
                    raise exc
                if any(k in str(exc) for k in ('429', 'RESOURCE_EXHAUSTED', 'NOT_FOUND', '404')):
                    try:
                        response = client.models.generate_content(
                            model=FALLBACK_ANALYSIS_MODEL,
                            contents=[prompt, audio_part],
                            config=types.GenerateContentConfig(
                                response_mime_type='application/json',
                                response_schema=VoiceInterventionResponse,
                            ),
                        )
                    except Exception:
                        response = client.models.generate_content(
                            model='gemini-flash-latest',
                            contents=[prompt, audio_part],
                            config=types.GenerateContentConfig(
                                response_mime_type='application/json',
                                response_schema=VoiceInterventionResponse,
                            ),
                        )
                else:
                    raise exc

            result_text = response.text

            # Extract the spoken script for TTS
            spoken_text = ''
            try:
                parsed_data = json.loads(result_text)
                spoken_text = parsed_data.get('deescalation_script', '')
            except Exception as parse_err:
                print(f'[JSON PARSE ERROR] {parse_err}')

            # Free Neural Speech Synthesis via Microsoft Edge TTS
            audio_b64 = generate_free_neural_speech(spoken_text, language)

            should_show_hospitals = bool(hospitals_list) and not any(
                k in result_text.lower()
                for k in ['no medical intervention required', 'non-emergency query']
            )

            return Response({
                'status': 'success',
                'data': result_text,
                'audio_base64': audio_b64,
                'audio_mime': 'audio/mp3',
                'audio_error': None if audio_b64 else 'Audio synthesis fallback',
                'hospitals': hospitals_list if should_show_hospitals else [],
            })

        except Exception as exc:
            exc_str = str(exc)
            print(f'[VOICE INTERVENTION ERROR] {exc_str}')

            if _is_auth_error(exc):
                return Response(
                    {
                        'detail': (
                            'Gemini API key is invalid or expired. '
                            'Get a valid API key from https://aistudio.google.com/apikey '
                            'and update GEMINI_API_KEY in your .env file, then restart the server.'
                        )
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            if _is_rate_limit_error(exc):
                return Response(
                    {'detail': 'System busy. Please retry in 10 seconds.'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            return Response(
                {'detail': f'Audio processing failed: {exc_str}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
