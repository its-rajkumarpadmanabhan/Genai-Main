import base64
import io
import json
import math
import os
import urllib.parse
import urllib.request
import wave
import struct  # Added to replace audioop
from typing import List, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from auth import User, get_current_user, router as auth_router

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False
    print("[WARN] edge-tts package not installed. Speech synthesis will fall back to text.")

load_dotenv()

app = FastAPI(
    title="Beacon Engine",
    version="1.0.0",
    description="Cross-Platform Universal Emergency Voice Engine",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)

GEMINI_API_KEY = (
    (os.getenv("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
)
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Active Gemini 3.x production endpoints
ANALYSIS_MODEL = "gemini-3.6-flash"
FALLBACK_ANALYSIS_MODEL = "gemini-3.5-flash-lite"
TTS_VOICE = "Kore"

# Noise Detection Threshold
MIN_AUDIO_THRESHOLD = 500


class VoiceInterventionResponse(BaseModel):
    vocal_risk_analysis: str = Field(
        description="Analysis of vocal tone, distress level, or summary of user inquiry in target language."
    )
    detected_specialty: str = Field(
        description="Identified medical requirement or condition (e.g. Emergency Medicine, Neurology, General Medicine)."
    )
    immediate_safety_steps: str = Field(
        description="Immediate physical safety or first-aid steps in target language."
    )
    deescalation_script: str = Field(
        description="The COMPLETE spoken script written strictly in the native script of the requested response language. MUST follow conversational flow: 1. Safety Advice for the condition, 2. Hospital Referral (Name, Ownership, Distance, Time)."
    )


def is_valid_speech(audio_data: bytes) -> bool:
    """Calculates RMS of PCM audio data to filter out background noise."""
    if len(audio_data) < 2:
        return False
    # Unpack 16-bit samples (little-endian)
    count = len(audio_data) // 2
    samples = struct.unpack('<' + 'h' * count, audio_data[:count * 2])
    rms = math.sqrt(sum(s * s for s in samples) / count)
    return rms > MIN_AUDIO_THRESHOLD


def calculate_distance_and_time(
    lat1: float, lon1: float, lat2: float, lon2: float
):
    """Calculates realistic driving distance (1.6x winding) and time (20 km/h average)."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    straight_dist_km = R * c

    # Realistic road distance (straight line * 1.6 factor for urban roads)
    driving_dist_km = round(straight_dist_km * 1.6, 1)
    # 20 km/h is a realistic average speed including traffic/signals in urban areas
    reach_time_mins = max(2, round((driving_dist_km / 20.0) * 60))
    return driving_dist_km, reach_time_mins


def determine_open_status(tags: dict) -> str:
    """Evaluates whether a hospital or clinic is currently open or 24/7."""
    opening_hours = tags.get("opening_hours", "").strip().lower()
    amenity = tags.get("amenity", "").strip().lower()
    emergency = tags.get("emergency", "").strip().lower()

    if "24/7" in opening_hours or emergency == "yes" or amenity == "hospital":
        return "Open (24/7 Emergency)"

    if opening_hours:
        if "off" in opening_hours or "closed" in opening_hours:
            return "Closed"
        return f"Open ({tags.get('opening_hours')})"

    return "Open / Active"


def fetch_hospitals_data(lat: float, lon: float, specialty_keyword: Optional[str] = None, accuracy_m: Optional[float] = None) -> List[dict]:
    """Uses the same robust query logic that worked in index.html to guarantee data delivery."""
    headers = {"User-Agent": "BeaconEngine/1.0"}
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter"
    ]
    
    # We use a 15km default, but will cascade if empty
    radiuses = [15000, 30000, 50000]
    
    for radius in radiuses:
        # This query is expanded to include 'healthcare' tags, which is why your index.html worked
        overpass_query = f"""
        [out:json][timeout:25];(
          node["amenity"~"hospital|clinic|doctors"](around:{radius},{lat},{lon});
          way["amenity"~"hospital|clinic|doctors"](around:{radius},{lat},{lon});
          node["healthcare"~"hospital|clinic|doctor"](around:{radius},{lat},{lon});
          way["healthcare"~"hospital|clinic|doctor"](around:{radius},{lat},{lon});
        );out center tags;
        """
        data_bytes = urllib.parse.urlencode({"data": overpass_query}).encode("utf-8")
        
        for endpoint in endpoints:
            try:
                req = urllib.request.Request(endpoint, data=data_bytes, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode())
                    elements = result.get("elements", [])
                    
                    if elements:
                        hospitals = []
                        for el in elements:
                            tags = el.get("tags", {})
                            lat_val = el.get("lat") or el.get("center", {}).get("lat")
                            lon_val = el.get("lon") or el.get("center", {}).get("lon")
                            
                            if not lat_val or not lon_val: continue
                            
                            dist, time = calculate_distance_and_time(lat, lon, lat_val, lon_val)
                            
                            hospitals.append({
                                "name": tags.get("name") or "Medical Facility",
                                "ownership": tags.get("amenity") or tags.get("healthcare") or "Clinic",
                                "distance_km": dist,
                                "reach_time_mins": time,
                                "status": determine_open_status(tags),
                                "lat": lat_val,
                                "lon": lon_val
                            })
                        
                        # Return sorted by distance
                        return sorted(hospitals, key=lambda x: x["distance_km"])
            except Exception as e:
                print(f"[FETCH ERROR] {e}")
                continue
                
    return []

async def generate_free_neural_speech(text: str, target_language: str) -> Optional[str]:
    """Synthesizes audio using Microsoft's free Edge Neural Voice engine."""
    if not text or not HAS_EDGE_TTS:
        return None

    voice_map = {
        "Malayalam": "ml-IN-SobhanaNeural",
        "Tamil": "ta-IN-PallaviNeural",
        "Hindi": "hi-IN-SwaraNeural",
        "Spanish": "es-ES-ElviraNeural",
        "Arabic": "ar-SA-ZariyahNeural",
        "English": "en-US-AvaNeural",
    }
    voice = voice_map.get(target_language, "en-US-AvaNeural")

    try:
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])

        audio_bytes = buf.getvalue()
        if audio_bytes:
            return base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        print(f"[EDGE-TTS ERROR] {e}")

    return None


HOME_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beacon — Nearby Hospitals</title>
<style>
  :root{
    --bg-deep: #150a2e;
    --bg-mid: #2a1352;
    --ring-soft: rgba(123,63,228,0.35);
    --glow: #b98bff;
    --live: #3ef2a0;
    --danger: #ff6b6b;
    --label: #b9a8ff;
    --card-bg: #1c0e38;
  }
  *{box-sizing:border-box;}
  html,body{ margin:0; min-height:100%; background:var(--bg-deep); font-family:'Segoe UI', system-ui, -apple-system, sans-serif; overflow-y:auto; }
  .stage{
    position:relative; width:100%; max-width:420px; min-height:100vh; margin:0 auto; padding:24px 0 40px;
    background:radial-gradient(circle at 50% 42%, var(--bg-mid) 0%, var(--bg-deep) 68%);
    display:flex; flex-direction:column; align-items:center;
  }
  .radar{ position:relative; width:340px; height:340px; display:flex; align-items:center; justify-content:center; }
  .ring{ position:absolute; border-radius:50%; border:1px solid var(--ring-soft); top:50%; left:50%; transform:translate(-50%,-50%); }
  .ring.r1{width:100%; height:100%;}
  .ring.r2{width:76%; height:76%;}
  .ring.r3{width:52%; height:52%;}
  .pulse{
    position:absolute; top:50%; left:50%; width:30%; height:30%; border-radius:50%;
    transform:translate(-50%,-50%);
    background:radial-gradient(circle, rgba(185,139,255,0.25) 0%, rgba(185,139,255,0) 70%);
    animation: pulse-out 3.2s ease-out infinite;
  }
  .pulse.p2{ animation-delay:1.6s; }
  @keyframes pulse-out{ 0%{width:30%;height:30%;opacity:0.55;} 100%{width:100%;height:100%;opacity:0;} }
  .sweep{
    position:absolute; top:50%; left:50%; width:50%; height:2px; transform-origin:0 50%;
    background:linear-gradient(90deg, rgba(185,139,255,0.85), rgba(185,139,255,0));
    animation:spin 6s linear infinite; filter:blur(0.3px);
  }
  @keyframes spin{ from{transform:translate(0,-50%) rotate(0deg);} to{transform:translate(0,-50%) rotate(360deg);} }
  .center-avatar{
    position:relative; width:150px; height:150px; border-radius:50%;
    background:radial-gradient(circle at 35% 30%, #6b3fc9, #2c1454 70%);
    box-shadow:0 0 0 6px rgba(185,139,255,0.12), 0 0 40px 6px rgba(123,63,228,0.55);
    display:flex; align-items:center; justify-content:center; z-index:5;
    animation: breathe 4s ease-in-out infinite; cursor:pointer; transition:transform 0.15s ease;
  }
  @keyframes breathe{ 0%,100%{box-shadow:0 0 0 6px rgba(185,139,255,0.12),0 0 40px 6px rgba(123,63,228,0.55);} 50%{box-shadow:0 0 0 10px rgba(185,139,255,0.20),0 0 55px 10px rgba(123,63,228,0.75);} }
  .center-avatar:hover{ transform:scale(1.04); }
  .center-avatar:active{ transform:scale(0.98); }
  .center-avatar.locating{ animation: locating-pulse 1.1s ease-in-out infinite !important; }
  @keyframes locating-pulse{ 0%,100%{box-shadow:0 0 0 6px rgba(62,242,160,0.18),0 0 40px 8px rgba(62,242,160,0.5);} 50%{box-shadow:0 0 0 12px rgba(62,242,160,0.28),0 0 60px 14px rgba(62,242,160,0.75);} }
  .center-avatar svg{ width:64px; height:64px; opacity:0.92; }
  .tap-hint{
    position:absolute; bottom:-30px; left:50%; transform:translateX(-50%);
    font-size:10.5px; letter-spacing:0.8px; color:var(--label); white-space:nowrap;
    text-transform:uppercase; opacity:0.85; animation: hint-fade 2.4s ease-in-out infinite;
  }
  @keyframes hint-fade{ 0%,100%{opacity:0.45;} 50%{opacity:0.95;} }
  .spot{ position:absolute; display:flex; flex-direction:column; align-items:center; gap:6px; z-index:6; animation: float 5s ease-in-out infinite; cursor:pointer; }
  .spot .bubble{
    width:64px; height:64px; border-radius:50%;
    background:linear-gradient(160deg,#3b2166,#1c0e38);
    border:2px solid rgba(185,139,255,0.55);
    box-shadow:0 4px 18px rgba(0,0,0,0.45), 0 0 18px rgba(123,63,228,0.35);
    display:flex; align-items:center; justify-content:center; position:relative;
  }
  .spot .bubble svg{ width:30px; height:30px; }
  .spot .dist{ font-size:12.5px; font-weight:600; color:var(--label); letter-spacing:0.3px; text-shadow:0 0 12px rgba(123,63,228,0.6); white-space:nowrap; order:-1; margin-bottom:4px; }
  .spot .name{ font-size:10.5px; font-weight:600; color:#e7ddff; max-width:86px; text-align:center; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .spot .live-dot{ position:absolute; top:-2px; right:-2px; width:14px; height:14px; border-radius:50%; background:var(--live); box-shadow:0 0 10px 2px var(--live); border:2px solid var(--bg-deep); }
  .spot.s1{ top:6%;  left:2%;  animation-delay:0s; }
  .spot.s2{ top:-2%; right:2%; animation-delay:1.1s; }
  .spot.s3{ bottom:8%; left:6%; animation-delay:0.6s; }
  .spot.s4{ bottom:0%; right:6%; animation-delay:1.7s; }
  @keyframes float{ 0%,100%{transform:translateY(0px);} 50%{transform:translateY(-8px);} }
  .status-line{ color:var(--label); font-size:12px; letter-spacing:0.5px; margin-top:14px; opacity:0.85; text-align:center; min-height:16px; padding:0 20px; }
  .search-wrap{ position:static; margin-top:22px; width:82%; max-width:320px; }
  .search-bar{ width:100%; border-radius:999px; background:rgba(255,255,255,0.06); border:1px solid rgba(185,139,255,0.35); backdrop-filter:blur(6px); animation: shimmer 3s ease-in-out infinite; }
  @keyframes shimmer{ 0%,100%{border-color:rgba(185,139,255,0.25);} 50%{border-color:rgba(185,139,255,0.6);} }
  .search-bar input{ width:100%; background:transparent; border:none; outline:none; color:#e7ddff; text-align:center; font-size:13px; letter-spacing:1.2px; padding:14px 20px; }
  .search-bar input::placeholder{ color:#cfc2f7; opacity:0.7; text-transform:uppercase; letter-spacing:1.5px; font-size:12px; }
  .controls{ position:static; margin-top:22px; display:flex; align-items:center; gap:18px; }
  .stop-btn{
    padding:14px 46px; border-radius:999px; background:linear-gradient(180deg,#3a1f6b,#20103e);
    border:1px solid rgba(185,139,255,0.4); color:#e7ddff; font-weight:700; font-size:13px;
    letter-spacing:2px; cursor:pointer; box-shadow:0 6px 20px rgba(0,0,0,0.5);
    transition:transform 0.15s ease, box-shadow 0.15s ease;
  }
  .stop-btn:hover{ transform:translateY(-2px); box-shadow:0 10px 26px rgba(123,63,228,0.45); }
  .stop-btn:active{ transform:translateY(0); }
  .results{ width:88%; max-width:340px; margin-top:26px; display:flex; flex-direction:column; gap:12px; }
  .results h3{ color:var(--label); font-size:12px; letter-spacing:2px; text-transform:uppercase; margin:4px 0 2px; font-weight:600; }
  .card{ background:var(--card-bg); border:1px solid rgba(185,139,255,0.25); border-radius:16px; padding:14px 16px; display:flex; gap:12px; align-items:flex-start; }
  .card .icon{ flex:0 0 auto; width:38px; height:38px; border-radius:50%; background:linear-gradient(160deg,#3b2166,#1c0e38); border:1px solid rgba(185,139,255,0.4); display:flex; align-items:center; justify-content:center; }
  .card .icon svg{ width:18px; height:18px; }
  .card .info{ flex:1; min-width:0; }
  .card .info .top{ display:flex; justify-content:space-between; gap:8px; align-items:baseline; }
  .card .info .top .cname{ color:#f1eaff; font-weight:600; font-size:14px; }
  .card .info .top .cdist{ color:var(--glow); font-weight:700; font-size:13px; white-space:nowrap; }
  .card .info .ctype{ color:var(--label); font-size:11px; text-transform:uppercase; letter-spacing:0.6px; margin-top:2px; }
  .card .info .cstatus{ font-size:12px; margin-top:6px; font-weight:600; }
  .card .info .cstatus.open{ color:var(--live); }
  .card .info .cstatus.closed{ color:var(--danger); }
  .card .info a.dir{ display:inline-block; margin-top:8px; font-size:11.5px; color:var(--live); text-decoration:none; font-weight:600; letter-spacing:0.3px; }
  .card .info a.dir:hover{ text-decoration:underline; }
  .empty-msg{ color:var(--label); font-size:13px; text-align:center; padding:10px 0; }
  .login-msg{ color:var(--danger); font-size:12.5px; text-align:center; padding:6px 0; }
  .login-msg a{ color:var(--glow); }
  @media (prefers-reduced-motion: reduce){ .pulse, .sweep, .center-avatar, .spot, .search-bar{ animation:none !important; } }
  @media (max-width:380px){ .radar{ width:280px; height:280px; } .center-avatar{ width:120px; height:120px; } .spot .bubble{ width:52px; height:52px; } }
</style>
</head>
<body>
  <div class="stage">
    <div class="radar">
      <div class="pulse"></div>
      <div class="pulse p2"></div>
      <div class="ring r1"></div>
      <div class="ring r2"></div>
      <div class="ring r3"></div>
      <div class="sweep"></div>
      <div class="center-avatar" id="centerAvatar" title="Tap to find hospitals near you">
        <svg viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="8" r="4" stroke="#e6d9ff" stroke-width="1.5"/>
          <path d="M4 20c0-4.4 3.6-7 8-7s8 2.6 8 7" stroke="#e6d9ff" stroke-width="1.5" stroke-linecap="round"/>
        </svg>
        <span class="tap-hint" id="tapHint">Tap to locate</span>
      </div>
      <div class="spot s1" id="spot0" style="display:none;"></div>
      <div class="spot s2" id="spot1" style="display:none;"></div>
      <div class="spot s3" id="spot2" style="display:none;"></div>
      <div class="spot s4" id="spot3" style="display:none;"></div>
    </div>

    <div class="status-line" id="statusLine">Finding hospitals near you…</div>
    <div class="login-msg" id="loginMsg" style="display:none;"></div>

    <div class="search-wrap">
      <div class="search-bar">
        <input id="searchInput" type="text" placeholder="Search a location instead…" />
      </div>
    </div>

    <div class="controls">
      <button class="stop-btn" id="trackBtn">START LIVE TRACKING</button>
    </div>

    <div class="results" id="results">
      <h3>Nearby hospitals &amp; clinics</h3>
      <div class="empty-msg" id="emptyMsg">Locating you now — results will appear automatically.</div>
    </div>
  </div>

<script>
(function(){
  const HOSPITAL_ICON = '<svg viewBox="0 0 24 24" fill="none"><path d="M3 11.5 12 4l9 7.5" stroke="#c9b6ff" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M5 10v9h14v-9" stroke="#c9b6ff" stroke-width="1.6" stroke-linejoin="round"/><rect x="10" y="13" width="4" height="6" stroke="#c9b6ff" stroke-width="1.4"/></svg>';

  const statusLine  = document.getElementById('statusLine');
  const loginMsg    = document.getElementById('loginMsg');
  const resultsEl   = document.getElementById('results');
  const emptyMsg    = document.getElementById('emptyMsg');
  const searchInput = document.getElementById('searchInput');
  const trackBtn    = document.getElementById('trackBtn');
  const centerAvatar= document.getElementById('centerAvatar');
  const tapHint     = document.getElementById('tapHint');
  const spotEls     = [0,1,2,3].map(i => document.getElementById('spot'+i));

  let watchId = null;
  let tracking = false;
  let lastFetchLoc = null;
  const REFETCH_THRESHOLD_KM = 0.5;

  // main.py's own /api/nearest-hospitals requires a Bearer token (see auth.py's get_current_user).
  // Change the storage key below if your login page saves the token under a different name.
  function getToken(){
    return localStorage.getItem('access_token') || sessionStorage.getItem('access_token');
  }
  function authHeaders(){
    const token = getToken();
    return token ? { 'Authorization': 'Bearer ' + token } : {};
  }

  function haversineKm(lat1, lon1, lat2, lon2){
    const R = 6371;
    const dLat = (lat2-lat1) * Math.PI/180;
    const dLon = (lon2-lon1) * Math.PI/180;
    const a = Math.sin(dLat/2)**2 +
              Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) *
              Math.sin(dLon/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  }

  async function fetchNearbyFacilities(lat, lon, accuracy){
    const token = getToken();
    if(!token){ throw { authRequired: true }; }
    const params = new URLSearchParams({ lat, lon });
    if(accuracy) params.set('accuracy', accuracy);
    const res = await fetch(`/api/nearest-hospitals?${params.toString()}`, { headers: authHeaders() });
    if(res.status === 401){ throw { authRequired: true }; }
    if(!res.ok) throw new Error('Backend request failed: ' + res.status);
    const data = await res.json();
    return data.hospitals || [];
  }

  function render(facilities, originLat, originLon){
    spotEls.forEach((el, i) => {
      const f = facilities[i];
      if(!f){ el.style.display = 'none'; return; }
      el.style.display = 'flex';
      el.innerHTML = `
        <span class="dist">${f.distance_km} km · ~${f.reach_time_mins} min</span>
        <div class="bubble" style="position:relative;">
          ${f.status && f.status.includes('24/7') ? '<span class="live-dot"></span>' : ''}
          ${HOSPITAL_ICON}
        </div>
        <span class="name">${f.name}</span>
      `;
      el.onclick = () => window.open(`https://www.google.com/maps/dir/?api=1&origin=${originLat},${originLon}&destination=${f.lat},${f.lon}`, '_blank');
    });

    resultsEl.querySelectorAll('.card').forEach(c => c.remove());
    emptyMsg.style.display = facilities.length ? 'none' : 'block';
    if(!facilities.length){
      emptyMsg.textContent = 'No hospitals or clinics found nearby. Try widening the search.';
      return;
    }
    facilities.slice(0, 20).forEach(f => {
      const isClosed = (f.status || '').toLowerCase() === 'closed';
      const card = document.createElement('div');
      card.className = 'card';
      card.innerHTML = `
        <div class="icon">${HOSPITAL_ICON}</div>
        <div class="info">
          <div class="top">
            <span class="cname">${f.name}</span>
            <span class="cdist">${f.distance_km} km · ~${f.reach_time_mins} min</span>
          </div>
          <div class="ctype">${f.ownership}</div>
          <div class="cstatus ${isClosed ? 'closed' : 'open'}">${f.status}</div>
          <a class="dir" target="_blank" href="https://www.google.com/maps/dir/?api=1&origin=${originLat},${originLon}&destination=${f.lat},${f.lon}">GET DIRECTIONS →</a>
        </div>
      `;
      resultsEl.appendChild(card);
    });
  }

  async function refreshFor(lat, lon, accuracy){
    statusLine.textContent = 'Searching nearby hospitals & clinics…';
    loginMsg.style.display = 'none';
    try{
      const facilities = await fetchNearbyFacilities(lat, lon, accuracy);
      render(facilities, lat, lon);
      statusLine.textContent = tracking
        ? `Live · tracking your location (${lat.toFixed(4)}, ${lon.toFixed(4)})`
        : `Showing results for (${lat.toFixed(4)}, ${lon.toFixed(4)})`;
      lastFetchLoc = {lat, lon};
    }catch(err){
      if(err && err.authRequired){
        statusLine.textContent = 'Please log in to see nearby hospitals.';
        loginMsg.style.display = 'block';
        loginMsg.innerHTML = 'Your session is missing or expired — <a href="/login">log in again</a>.';
      }else{
        statusLine.textContent = 'Could not reach the hospital directory. Check your connection and try again.';
      }
      console.error(err);
    }finally{
      centerAvatar.classList.remove('locating');
    }
  }

  function onPosition(pos){
    const lat = pos.coords.latitude;
    const lon = pos.coords.longitude;
    const accuracy = pos.coords.accuracy;
    if(!lastFetchLoc || haversineKm(lastFetchLoc.lat, lastFetchLoc.lon, lat, lon) >= REFETCH_THRESHOLD_KM){
      refreshFor(lat, lon, accuracy);
    }
  }

  function onPositionError(err){
    centerAvatar.classList.remove('locating');
    let msg = "Couldn't get your location — use the search box below instead.";
    if(err && err.code === err.PERMISSION_DENIED){
      msg = 'Location permission denied — allow location access, or use the search box below instead.';
    }else if(err && err.code === err.TIMEOUT){
      msg = "Couldn't get a location fix — tap the avatar to retry, or use the search box below.";
    }
    statusLine.textContent = msg;
    emptyMsg.textContent = 'Search a location above to see nearby hospitals and clinics.';
  }

  function getPositionWithFallback(successCb, errorCb){
    if(!navigator.geolocation){
      errorCb({ code: 0, message: 'unsupported' });
      return;
    }
    navigator.geolocation.getCurrentPosition(
      successCb,
      (err) => {
        if(err && err.code === err.TIMEOUT){
          statusLine.textContent = 'Still locating… trying a faster method.';
          navigator.geolocation.getCurrentPosition(
            successCb,
            errorCb,
            { enableHighAccuracy: false, maximumAge: 60000, timeout: 25000 }
          );
        } else {
          errorCb(err);
        }
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 12000 }
    );
  }

  function startTracking(){
    if(!navigator.geolocation){
      statusLine.textContent = "Geolocation isn't supported in this browser — use search instead.";
      return;
    }
    tracking = true;
    trackBtn.textContent = 'STOP LIVE TRACKING';
    getPositionWithFallback(pos => { onPosition(pos); }, onPositionError);
    watchId = navigator.geolocation.watchPosition(onPosition, (err) => {
      if(err && err.code === err.TIMEOUT) return;
      onPositionError(err);
    }, {
      enableHighAccuracy: true,
      maximumAge: 15000,
      timeout: 25000
    });
  }

  function beginLocate(force){
    if(!navigator.geolocation){
      statusLine.textContent = "Geolocation isn't supported in this browser — use search instead.";
      return;
    }
    tapHint.style.display = 'none';
    centerAvatar.classList.add('locating');
    statusLine.textContent = 'Finding your location…';
    if(!tracking){
      startTracking();
    }else if(force){
      getPositionWithFallback(
        pos => { lastFetchLoc = null; onPosition(pos); },
        onPositionError
      );
    }
  }

  centerAvatar.addEventListener('click', () => beginLocate(true));

  function stopTracking(){
    if(watchId !== null) navigator.geolocation.clearWatch(watchId);
    watchId = null;
    tracking = false;
    trackBtn.textContent = 'RESUME LIVE TRACKING';
    statusLine.textContent = 'Live tracking paused.';
  }

  trackBtn.addEventListener('click', () => {
    if(tracking){
      stopTracking();
    }else{
      centerAvatar.classList.add('locating');
      statusLine.textContent = 'Finding your location…';
      tapHint.style.display = 'none';
      startTracking();
    }
  });

  searchInput.addEventListener('keydown', async (e) => {
    if(e.key !== 'Enter' || !searchInput.value.trim()) return;
    statusLine.textContent = 'Looking up that location…';
    try{
      const token = getToken();
      if(!token){
        statusLine.textContent = 'Please log in to search.';
        loginMsg.style.display = 'block';
        loginMsg.innerHTML = '<a href="/login">Log in</a> to search a location.';
        return;
      }
      stopTracking();
      const params = new URLSearchParams({ location_query: searchInput.value.trim() });
      const res = await fetch(`/api/nearest-hospitals?${params.toString()}`, { headers: authHeaders() });
      if(res.status === 401){
        statusLine.textContent = 'Please log in to see nearby hospitals.';
        loginMsg.style.display = 'block';
        loginMsg.innerHTML = 'Your session is missing or expired — <a href="/login">log in again</a>.';
        return;
      }
      const data = await res.json();
      render(data.hospitals || [], data.search_center.lat, data.search_center.lon);
      statusLine.textContent = `Showing results for (${data.search_center.lat.toFixed(4)}, ${data.search_center.lon.toFixed(4)})`;
    }catch(err){
      statusLine.textContent = 'Search failed — check your connection.';
      console.error(err);
    }
  });

  // Auto-start: locate the visitor as soon as the page opens — no manual location entry required.
  beginLocate(false);
})();
</script>
</body>
</html>
"""


@app.get("/api/health")
async def health_check():
    if not client:
        return {"status": "online", "gemini_configured": False}
    return {"status": "online", "gemini_configured": True}


@app.get("/api/nearest-hospitals")
async def get_nearest_hospitals(
    lat: Optional[float] = Query(None),
    lon: Optional[float] = Query(None),
    location_query: Optional[str] = Query(None),
    accuracy: Optional[float] = Query(None, description="GPS accuracy radius in meters, from navigator.geolocation on the client"),
    current_user: User = Depends(get_current_user),
):
    headers = {"User-Agent": "BeaconEngine/1.0"}

    if (lat is None or lon is None) and location_query:
        try:
            encoded_q = urllib.parse.quote(location_query)
            geo_url = f"https://nominatim.openstreetmap.org/search?q={encoded_q}&format=json&limit=1"
            req = urllib.request.Request(geo_url, headers=headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
                geo_data = json.loads(resp.read().decode())
                if geo_data:
                    lat = float(geo_data[0]["lat"])
                    lon = float(geo_data[0]["lon"])
        except Exception:
            pass

    if lat is None or lon is None:
        raise HTTPException(
            status_code=400, detail="GPS Coordinates or search query required."
        )

    hospitals = fetch_hospitals_data(lat, lon, accuracy_m=accuracy)
    return {
        "status": "success",
        "search_center": {"lat": lat, "lon": lon, "accuracy_m": accuracy},
        "count": len(hospitals),
        "hospitals": hospitals,
    }


@app.post("/api/voice-intervention")
async def process_voice_crisis(
    file: UploadFile = File(...),
    user_type: str = Form("individual"),
    language: str = Form("English"),
    lat: Optional[float] = Form(None),
    lon: Optional[float] = Form(None),
    accuracy: Optional[float] = Form(None, description="GPS accuracy radius in meters, from navigator.geolocation on the client"),
    current_user: User = Depends(get_current_user),
):
    if not client:
        raise HTTPException(status_code=500, detail="Gemini Engine missing.")

    try:
        audio_bytes = await file.read()
        if not audio_bytes:
            raise HTTPException(status_code=400, detail="No audio received.")

        # Ignore noise/static
        if not is_valid_speech(audio_bytes):
             return {"status": "ignore", "message": "Noise detected"}

        mime_type = file.content_type or "audio/webm"
        audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)

        # GPS Data Verification
        hospitals_list = []
        if lat is not None and lon is not None:
            hospitals_list = fetch_hospitals_data(lat, lon, accuracy_m=accuracy)

        hospital_summary_lines = []
        for idx, h in enumerate(hospitals_list):
            hospital_summary_lines.append(
                f"Facility #{idx+1}: Name='{h['name']}' ({h['ownership']} | {h['type']}) | Distance={h['distance_km']} km | Drive Reach Time=~{h['reach_time_mins']} mins | Status={h['status']} (Open={h['is_open']}) | Doctor={h['doctor']}"
            )

        hospital_context_str = (
            "\n".join(hospital_summary_lines)
            if hospital_summary_lines
            else "No live GPS medical facilities found."
        )

        prompt = f"""
        You are Beacon, an intelligent voice emergency health assistant.
        Analyze the audio input from user '{current_user.username}'.

        DETECTED REAL-TIME NEARBY MEDICAL FACILITIES:
        {hospital_context_str}

        CRITICAL CONVERSATIONAL FLOW RULES:
        1. FIRST: If the user describes a condition (headache, chest pain, body pain, etc.), ALWAYS provide the immediate first-aid, safety advice, or 'what to do' FIRST. 
        2. SECOND: ONLY AFTER providing safety advice, provide the nearest hospital referral.
        3. USER COUNT REQUESTS: If the user asks for '5 hospitals' or '10 hospitals', you MUST provide that specific number from the list above. If no number is asked, default to the most relevant one.
        4. HOSPITAL REFERRAL DETAILS: For each hospital mentioned, explicitly state: NAME, OWNERSHIP (Government/Private), DISTANCE (km), and TIME (mins). 
        5. IF CLOSED: If the nearest is closed, announce it and redirect to the next nearest OPEN facility.

        LANGUAGE: Generate response in {language} (use native script for non-English).
        OUTPUT MUST BE VALID JSON MATCHING THE SCHEMA EXACTLY.
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
        except Exception as e:
            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e) or "NOT_FOUND" in str(e):
                response = client.models.generate_content(
                    model=FALLBACK_ANALYSIS_MODEL,
                    contents=[prompt, audio_part],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=VoiceInterventionResponse,
                    ),
                )
            else:
                raise e

        result_text = response.text
        spoken_text = ""
        try:
            parsed_data = json.loads(result_text)
            spoken_text = parsed_data.get("deescalation_script", "")
        except Exception as parse_err:
            print(f"[JSON PARSE ERROR] {parse_err}")

        # Free Neural Speech Synthesis
        audio_b64 = await generate_free_neural_speech(spoken_text, language)

        should_show_hospitals = bool(hospitals_list) and not any(
            k in result_text.lower()
            for k in ["no medical intervention required", "non-emergency query"]
        )

        return {
            "status": "success",
            "data": result_text,
            "audio_base64": audio_b64,
            "audio_mime": "audio/mp3",
            "audio_error": None if audio_b64 else "Audio synthesis fallback",
            "hospitals": hospitals_list if should_show_hospitals else [],
        }
    except Exception as e:
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            raise HTTPException(
                status_code=429,
                detail="System busy. Please retry in 10 seconds."
            )
        raise HTTPException(status_code=500, detail=str(e))


AUTH_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Beacon — Log In</title>
<style>
  :root{
    --bg-deep: #150a2e; --bg-mid: #2a1352; --ring-soft: rgba(123,63,228,0.35);
    --glow: #b98bff; --live: #3ef2a0; --danger: #ff6b6b; --label: #b9a8ff; --card-bg: #1c0e38;
  }
  *{box-sizing:border-box;}
  html,body{ margin:0; min-height:100%; background:var(--bg-deep); font-family:'Segoe UI', system-ui, -apple-system, sans-serif; }
  .stage{
    min-height:100vh; display:flex; align-items:center; justify-content:center;
    background:radial-gradient(circle at 50% 42%, var(--bg-mid) 0%, var(--bg-deep) 68%);
    padding:24px 16px;
  }
  .card{
    width:100%; max-width:360px; background:var(--card-bg); border:1px solid rgba(185,139,255,0.25);
    border-radius:20px; padding:32px 28px; box-shadow:0 10px 40px rgba(0,0,0,0.5);
  }
  h1{ color:#f1eaff; font-size:20px; margin:0 0 4px; text-align:center; }
  .sub{ color:var(--label); font-size:12.5px; text-align:center; margin:0 0 24px; }
  .tabs{ display:flex; border-radius:999px; background:rgba(255,255,255,0.06); padding:4px; margin-bottom:22px; }
  .tabs button{
    flex:1; padding:10px; border:none; border-radius:999px; background:transparent; color:var(--label);
    font-weight:700; font-size:12px; letter-spacing:1px; cursor:pointer; text-transform:uppercase;
  }
  .tabs button.active{ background:linear-gradient(180deg,#3a1f6b,#20103e); color:#f1eaff; }
  form{ display:none; flex-direction:column; gap:12px; }
  form.active{ display:flex; }
  input{
    width:100%; padding:13px 16px; border-radius:12px; background:rgba(255,255,255,0.06);
    border:1px solid rgba(185,139,255,0.3); color:#e7ddff; font-size:13.5px; outline:none;
  }
  input::placeholder{ color:#a999d6; }
  input:focus{ border-color:rgba(185,139,255,0.7); }
  .submit-btn{
    margin-top:6px; padding:13px; border-radius:999px; background:linear-gradient(180deg,#3a1f6b,#20103e);
    border:1px solid rgba(185,139,255,0.4); color:#e7ddff; font-weight:700; font-size:13px;
    letter-spacing:1.5px; cursor:pointer; text-transform:uppercase;
  }
  .submit-btn:disabled{ opacity:0.6; cursor:default; }
  .msg{ min-height:18px; font-size:12.5px; text-align:center; margin-top:14px; }
  .msg.error{ color:var(--danger); }
  .msg.success{ color:var(--live); }
</style>
</head>
<body>
  <div class="stage">
    <div class="card">
      <h1>Beacon</h1>
      <p class="sub">Sign in to see hospitals near you</p>

      <div class="tabs">
        <button id="tabLogin" class="active">Log In</button>
        <button id="tabSignup">Sign Up</button>
      </div>

      <form id="loginForm" class="active">
        <input id="loginIdentifier" type="text" placeholder="Username or email" autocomplete="username" required />
        <input id="loginPassword" type="password" placeholder="Password" autocomplete="current-password" required />
        <button class="submit-btn" type="submit">Log In</button>
      </form>

      <form id="signupForm">
        <input id="suUsername" type="text" placeholder="Username" autocomplete="username" required />
        <input id="suEmail" type="email" placeholder="Email" autocomplete="email" required />
        <input id="suMobile" type="text" placeholder="Mobile number" autocomplete="tel" required />
        <input id="suPassword" type="password" placeholder="Password" autocomplete="new-password" required />
        <button class="submit-btn" type="submit">Create Account</button>
      </form>

      <div class="msg" id="authMsg"></div>
    </div>
  </div>

<script>
(function(){
  const tabLogin = document.getElementById('tabLogin');
  const tabSignup = document.getElementById('tabSignup');
  const loginForm = document.getElementById('loginForm');
  const signupForm = document.getElementById('signupForm');
  const authMsg = document.getElementById('authMsg');

  // If a valid-looking session already exists, skip straight to the main page.
  if(localStorage.getItem('access_token')){
    window.location.href = '/';
  }

  function showMsg(text, kind){
    authMsg.textContent = text;
    authMsg.className = 'msg' + (kind ? ' ' + kind : '');
  }

  tabLogin.addEventListener('click', () => {
    tabLogin.classList.add('active'); tabSignup.classList.remove('active');
    loginForm.classList.add('active'); signupForm.classList.remove('active');
    showMsg('');
  });
  tabSignup.addEventListener('click', () => {
    tabSignup.classList.add('active'); tabLogin.classList.remove('active');
    signupForm.classList.add('active'); loginForm.classList.remove('active');
    showMsg('');
  });

  // After a successful login OR signup, store the token and land on the main
  // hospital-finder page automatically — no extra click needed.
  function goToMainPage(){
    showMsg('Success — loading your nearby hospitals…', 'success');
    window.location.href = '/';
  }

  loginForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    showMsg('Signing in…');
    const identifier = document.getElementById('loginIdentifier').value.trim();
    const password = document.getElementById('loginPassword').value;
    try{
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier, password })
      });
      const data = await res.json();
      if(!res.ok){ showMsg(data.detail || 'Login failed.', 'error'); return; }
      localStorage.setItem('access_token', data.access_token);
      goToMainPage();
    }catch(err){
      showMsg('Could not reach the server — check your connection.', 'error');
      console.error(err);
    }
  });

  signupForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    showMsg('Creating your account…');
    const payload = {
      username: document.getElementById('suUsername').value.trim(),
      email: document.getElementById('suEmail').value.trim(),
      mobile_number: document.getElementById('suMobile').value.trim(),
      password: document.getElementById('suPassword').value
    };
    try{
      const res = await fetch('/api/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if(!res.ok){
        const detail = Array.isArray(data.detail)
          ? data.detail.map(d => d.msg).join(' ')
          : (data.detail || 'Signup failed.');
        showMsg(detail, 'error');
        return;
      }
      localStorage.setItem('access_token', data.access_token);
      goToMainPage();
    }catch(err){
      showMsg('Could not reach the server — check your connection.', 'error');
      console.error(err);
    }
  });
})();
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def serve_home():
    """Serves the auto-geolocation hospital-finder page (built into this file — no
    external template needed for it to work). Uses the same locate-then-track
    architecture that already works in the reference index.html, wired to this
    file's own /api/nearest-hospitals endpoint."""
    return HTMLResponse(content=HOME_PAGE_HTML)


@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    """Combined login/signup page. On success it stores the returned access_token
    and redirects straight to '/' — the hospital-finder page — so the user lands
    there automatically after logging in or signing up."""
    return HTMLResponse(content=AUTH_PAGE_HTML)


# Any *other* static pages (login.html, reset-password.html, etc.) are still served
# from ./templates if that folder exists. Guarded so a missing folder can't crash
# the whole app at startup — which, if it happened, would look exactly like
# "nothing loads, including location."
if os.path.isdir("templates"):
    app.mount("/", StaticFiles(directory="templates", html=True), name="templates")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)