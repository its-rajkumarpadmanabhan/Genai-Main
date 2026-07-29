<img width="1912" height="945" alt="image" src="https://github.com/user-attachments/assets/7343a5e4-08dd-441e-a131-cb46887b2f97" />

🚨 Beacon Engine: Universal Emergency Voice Assistant
Beacon Engine is a cross-platform, intelligent voice-driven emergency health assistant designed for rapid, real-time medical triage. Built on the Gemini 3.6-flash engine, it combines GPS-verified hospital routing with instant AI-driven first-aid advice.

🌟 Key Features
🗣️ Voice-First Triage: Analyzes vocal distress and symptoms to identify urgent medical needs.

🏥 Real-Time GPS Routing: Scans the immediate surroundings (30km radius) for hospitals, clinics, and specialists.

🌍 Multilingual Native Support: Answers in the user's preferred language, utilizing native scripts (e.g., Malayalam, Hindi, Tamil).

⚡ Safety-First Workflow: Strictly provides first-aid/safety advice before any facility referral.

🚗 Urban-Optimized Routing: Calculates ETA using 20 km/h average traffic speeds and 1.6x road-winding coefficients.

🔐 Admin-Ready: Built-in modular auth for secure dashboard access and system administration.

🛠️ How It Works
Audio Ingestion: Users send an audio clip describing their condition.

Noise Filtering: Our system utilizes RMS amplitude thresholding to discard background noise and empty mic inputs, ensuring only actual speech is processed.

Triage Processing: The engine classifies the medical specialty (e.g., Cardiology, General Medicine).

Safety & Referral:

Step 1: AI generates immediate, life-saving first-aid steps.

Step 2: AI maps the nearest open medical facility based on the user's specific coordinates.

Step 3: AI handles appointments and routing dynamically.

Voice Synthesis: The response is returned via high-quality, free Microsoft Edge Neural Speech synthesis.

🚀 Technical Stack
Language: Python 3.14+

Framework: FastAPI

Intelligence: Google Gemini 3.6-Flash / 3.5-Flash-Lite

Mapping: OpenStreetMap (Overpass API)

Speech: Microsoft Edge TTS (edge-tts)

Deployment: Optimized for high-concurrency environments

📋 Conversational Protocol
Beacon follows a strict protocol for user safety:

Immediate Aid: If a symptom is detected, the AI speaks the "First-Aid/Safety Advice" first.

Context-Aware Routing: Only after safety is addressed does the AI suggest the specific hospital.

Count-Based Referrals: If a user asks for "5 hospitals," Beacon provides exactly that number based on geographic proximity.

Verification: If a requested hospital is closed, the system automatically redirects to the next nearest open facility.


🛡️ Security & Privacy
Zero-Noise Hallucination: The system includes a hard-coded pre-flight check that rejects audio files not meeting volume thresholds, preventing accidental activations.

Admin Gatekeeping: All sensitive administrative routes are protected via custom require_admin dependency injection.

📝 License
This project is open-source and intended for emergency health assistance. Always consult with a licensed professional for non-emergency medical decisions.

Made with ❤️ for Global Emergency Response

---

## Deploying the Django Backend to Render

The full-stack web platform (beacon-django/) is a Django 4 + REST API app deployable to Render.com for free.

### Prerequisites
- GitHub account with this repo pushed
- Render.com account (sign up free with GitHub)
- Gmail account with 2-Step Verification enabled (for email notifications)

---

### Step 1 - Create a PostgreSQL Database on Render

1. Go to dashboard.render.com
2. Click New + -> PostgreSQL
3. Set Name: beacon-db, Plan: Free
4. Click Create Database
5. Copy the Internal Database URL (needed in Step 3)

---

### Step 2 - Create a Web Service

1. Click New + -> Web Service
2. Connect repository: its-rajkumarpadmanabhan/Genai-Main
3. Configure:

| Setting | Value |
|---|---|
| Name | beacon-django |
| Root Directory | beacon-django (REQUIRED - subfolder with manage.py) |
| Environment | Python 3 |
| Build Command | pip install -r requirements.txt && python manage.py collectstatic --noinput |
| Start Command | gunicorn beacon.wsgi:application --bind 0.0.0.0:$PORT --workers 4 |
| Plan | Free |

---

### Step 3 - Set Environment Variables

In Web Service -> Environment tab, add:

| Key | Value |
|---|---|
| DEBUG | False |
| DJANGO_SECRET_KEY | Click Generate in Render UI |
| JWT_SECRET | Any long random string |
| ALLOWED_HOSTS | beacon-django.onrender.com |
| FRONTEND_BASE_URL | https://beacon-django.onrender.com |
| DATABASE_URL | Internal Database URL from Step 1 |
| GEMINI_API_KEY | Your Google Gemini API key |
| SMTP_HOST | smtp.gmail.com |
| SMTP_PORT | 587 |
| SMTP_USER | yourname@gmail.com |
| SMTP_PASSWORD | 16-char Gmail App Password (no spaces) |
| SMTP_FROM_EMAIL | yourname@gmail.com |
| SMTP_FROM_NAME | Beacon Recovery Platform |

Gmail App Password: myaccount.google.com/apppasswords -> Generate for Mail. Requires 2-Step Verification.

---

### Step 4 - Deploy

Click Create Web Service. Render automatically:
1. Installs requirements.txt
2. Runs collectstatic
3. Runs python manage.py migrate (Procfile release command)
4. Starts Gunicorn server

Live URL: https://beacon-django.onrender.com

---

### Step 5 - Create Admin User (first time only)

Open the Shell tab in Render dashboard and run:
  python manage.py createsuperuser

---

### Auto-Deploy

Every git push to main triggers an automatic redeploy on Render.

---

### Local Development Setup

  # Clone the repo
  git clone https://github.com/its-rajkumarpadmanabhan/Genai-Main.git
  cd Genai-Main/beacon-django

  # Create and activate virtual environment
  python -m venv venv
  venv\Scripts\activate

  # Install dependencies
  pip install -r requirements.txt

  # Configure environment
  copy .env.example .env

  # Run migrations and start server
  python manage.py migrate
  python manage.py runserver

Open: http://localhost:8000

---

### User Roles and Pages

| Role | URL | Features |
|---|---|---|
| Admin | /admin-dashboard | Manage all users, platform stats |
| Doctor | /doctor-profile | Appointments, video calls, prescriptions |
| Patient | /patient-profile | Book appointments, caretaker requests, documents |
| Caretaker | /caretaker-dashboard | Monitor patients, book on behalf, emergency alerts |

---

Free Tier Note: Render free services sleep after 15 min of inactivity. First request after idle takes ~30s to wake up. Upgrade for always-on availability.
