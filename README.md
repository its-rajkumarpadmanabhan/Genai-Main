<img width="1912" height="945" alt="image" src="https://github.com/user-attachments/assets/7343a5e4-08dd-441e-a131-cb46887b2f97" />

To provide you with a high-quality README that you can copy and paste into your GitHub repository (which will display perfectly as documentation), I have formatted it below.

GitHub uses Markdown for READMEs, so you don't need a PDF file. Simply create a file named README.md in your repository root, paste the content below, and it will render beautifully with emojis and structure.

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
