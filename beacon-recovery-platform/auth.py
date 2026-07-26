"""
BEACON — Authentication & Account System
-----------------------------------------
Real, working signup / login / forgot-password flow:
  - Unique username, email, mobile number enforced at the DB + API layer
  - Passwords hashed with bcrypt (never stored in plain text)
  - Session handled via signed JWT access tokens
  - Welcome email sent automatically on signup (real SMTP delivery)
  - Forgot-password issues a single-use, time-limited reset token and
    emails a real reset link — no fake/stubbed reset flow.

Configure via environment variables (see .env.example):
  JWT_SECRET, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
  SMTP_FROM_EMAIL, SMTP_FROM_NAME, FRONTEND_BASE_URL, DATABASE_URL
"""

import os
import re
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import bcrypt
import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import (Boolean, Column, DateTime, Integer, String,
                         create_engine, or_)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./beacon_users.db")
# Render (and some other providers) issue connection strings with the legacy
# "postgres://" scheme. SQLAlchemy 2.x requires "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_hex(32))
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours
RESET_TOKEN_EXPIRE_MINUTES = 30

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM_EMAIL = os.getenv("SMTP_FROM_EMAIL", SMTP_USER or "no-reply@beacon.app")
SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "Beacon Recovery Platform")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:8000")

EMAIL_ENABLED = bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)

# ----------------------------------------------------------------------------
# Database setup
# ----------------------------------------------------------------------------

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
# pool_pre_ping: tests each connection with a lightweight ping before handing
# it out. Render's free Postgres tier silently drops idle connections; without
# this, the first request after any idle period reuses a dead connection and
# has to fail + reconnect (adding latency, occasionally a full timeout) before
# it even gets to your query.
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(20), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    mobile_number = Column(String(20), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, index=True, nullable=False)
    user_id = Column(Integer, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, default=False)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------------
# Validation rules
# ----------------------------------------------------------------------------

USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,20}$")
# E.164-ish: optional leading +, 7-15 digits total
MOBILE_RE = re.compile(r"^\+?[0-9]{7,15}$")
# At least 8 chars, 1 upper, 1 lower, 1 digit, 1 special char
PASSWORD_RE = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$")


class SignupRequest(BaseModel):
    username: str
    email: EmailStr
    mobile_number: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v):
        if not USERNAME_RE.match(v):
            raise ValueError(
                "Username must be 3-20 characters, letters/numbers/underscore only."
            )
        return v

    @field_validator("mobile_number")
    @classmethod
    def validate_mobile(cls, v):
        v = v.strip().replace(" ", "").replace("-", "")
        if not MOBILE_RE.match(v):
            raise ValueError(
                "Enter a valid mobile number (7-15 digits, optional leading +country code)."
            )
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if not PASSWORD_RE.match(v):
            raise ValueError(
                "Password needs 8+ characters with an uppercase letter, lowercase letter, "
                "number, and special character."
            )
        return v


class LoginRequest(BaseModel):
    identifier: str  # username OR email
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, v):
        if not PASSWORD_RE.match(v):
            raise ValueError(
                "Password needs 8+ characters with an uppercase letter, lowercase letter, "
                "number, and special character."
            )
        return v


# ----------------------------------------------------------------------------
# Password hashing (bcrypt)
# ----------------------------------------------------------------------------

def hash_password(plain_password: str) -> str:
    return bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


# ----------------------------------------------------------------------------
# JWT session tokens
# ----------------------------------------------------------------------------

def create_access_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "email": user.email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token.")


def get_current_user(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
    token = authorization.split(" ", 1)[1]
    payload = decode_access_token(token)
    user = db.query(User).filter(User.id == int(payload["sub"])).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive.")
    return user


# ----------------------------------------------------------------------------
# Email delivery (real SMTP — Gmail App Password, SendGrid SMTP, SES SMTP, etc.)
# ----------------------------------------------------------------------------

def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """
    Sends a real email over SMTP if credentials are configured.
    If SMTP env vars are missing, logs the email to the console instead of
    crashing, so the app remains fully testable before credentials are set.
    """
    if not EMAIL_ENABLED:
        print("=" * 70)
        print(f"[DEV MODE — SMTP NOT CONFIGURED] Would send email to: {to_email}")
        print(f"Subject: {subject}")
        print(html_body)
        print("=" * 70)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=context)
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"[EMAIL ERROR] Failed to send to {to_email}: {e}")
        return False


def send_welcome_email(user: User):
    subject = "Welcome to Beacon — Your Recovery & Prevention Platform"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: auto; background:#0f172a; color:#e2e8f0; padding: 32px; border-radius: 16px;">
      <h1 style="color:#2dd4bf; font-size: 22px;">Welcome, {user.username} 👋</h1>
      <p>Your Beacon account has been created successfully.</p>
      <p>Beacon gives you zero-typing crisis interventions, personalized emergency scripts,
         and verified educational resources — available the moment you need them most.</p>
      <div style="background:#1e293b; border-radius: 10px; padding: 16px; margin: 20px 0;">
        <p style="margin:0; font-size: 13px; color:#94a3b8;">Account Email</p>
        <p style="margin:0; font-weight:bold;">{user.email}</p>
      </div>
      <p>If you ever forget your password, use the "Forgot Password" link on the sign-in page —
         we'll send you a secure reset link.</p>
      <p style="color:#64748b; font-size:12px; margin-top: 32px;">
        If you didn't create this account, please contact us so we can secure it.
      </p>
      <p style="color:#2dd4bf; font-weight:bold; margin-top: 24px;">— The Beacon Team</p>
    </div>
    """
    send_email(user.email, subject, html)


def send_password_reset_email(user: User, reset_token: str):
    reset_link = f"{FRONTEND_BASE_URL}/reset-password.html?token={reset_token}"
    subject = "Beacon — Reset Your Password"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: auto; background:#0f172a; color:#e2e8f0; padding: 32px; border-radius: 16px;">
      <h1 style="color:#f43f5e; font-size: 22px;">Password Reset Request</h1>
      <p>Hi {user.username}, we received a request to reset your Beacon password.</p>
      <p>This link is valid for {RESET_TOKEN_EXPIRE_MINUTES} minutes and can only be used once.</p>
      <a href="{reset_link}"
         style="display:inline-block; margin: 20px 0; background:#0d9488; color:white; text-decoration:none;
                padding: 12px 24px; border-radius: 10px; font-weight:bold;">
        Reset My Password
      </a>
      <p style="font-size:12px; color:#64748b;">Or paste this link into your browser:<br>{reset_link}</p>
      <p style="color:#64748b; font-size:12px; margin-top: 24px;">
        If you didn't request this, you can safely ignore this email — your password will not change.
      </p>
    </div>
    """
    send_email(user.email, subject, html)


# ----------------------------------------------------------------------------
# Router
# ----------------------------------------------------------------------------

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/signup", status_code=201)
def signup(payload: SignupRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    normalized_email = payload.email.lower().strip()

    existing = db.query(User).filter(
        or_(
            User.username.ilike(payload.username),
            User.email.ilike(normalized_email),
            User.mobile_number == payload.mobile_number,
        )
    ).first()

    if existing:
        if existing.username.lower() == payload.username.lower():
            raise HTTPException(status_code=409, detail="That username is already taken.")
        if existing.email.lower() == normalized_email:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        raise HTTPException(status_code=409, detail="An account with this mobile number already exists.")

    user = User(
        username=payload.username,
        email=normalized_email,
        mobile_number=payload.mobile_number,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Fire-and-forget: this used to be a direct call here, which meant the
    # signup request blocked on a live SMTP handshake (DNS + TLS + login +
    # send) before the response could return -- easily several seconds, more
    # if the SMTP host is slow to respond. FastAPI runs background_tasks
    # AFTER the response is sent, so the user gets their token and is signed
    # in immediately; the welcome email goes out a moment later regardless.
    background_tasks.add_task(send_welcome_email, user)

    token = create_access_token(user)
    return {
        "status": "success",
        "message": "Account created. Check your email for a welcome message.",
        "access_token": token,
        "user": {"id": user.id, "username": user.username, "email": user.email},
    }


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    identifier = payload.identifier.strip().lower()
    user = db.query(User).filter(
        or_(User.username.ilike(identifier), User.email.ilike(identifier))
    ).first()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username/email or password.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been deactivated.")

    token = create_access_token(user)
    return {
        "status": "success",
        "access_token": token,
        "user": {"id": user.id, "username": user.username, "email": user.email},
    }


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    normalized_email = payload.email.lower().strip()
    user = db.query(User).filter(User.email.ilike(normalized_email)).first()

    # Always return a generic response to avoid leaking which emails are registered.
    generic_response = {
        "status": "success",
        "message": "If that email is registered, a password reset link has been sent.",
    }

    if not user:
        return generic_response

    reset_token = secrets.token_urlsafe(32)
    entry = PasswordResetToken(
        token=reset_token,
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
    )
    db.add(entry)
    db.commit()

    background_tasks.add_task(send_password_reset_email, user, reset_token)
    return generic_response


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    entry = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == payload.token
    ).first()

    if not entry or entry.used:
        raise HTTPException(status_code=400, detail="This reset link is invalid or has already been used.")

    expires_at = entry.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="This reset link has expired. Please request a new one.")

    user = db.query(User).filter(User.id == entry.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account no longer exists.")

    user.password_hash = hash_password(payload.new_password)
    entry.used = True
    db.commit()

    return {"status": "success", "message": "Password updated. You can now log in."}


@router.get("/me")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "mobile_number": current_user.mobile_number,
        "created_at": current_user.created_at.isoformat(),
    }


@router.get("/check-availability")
def check_availability(username: Optional[str] = None, email: Optional[str] = None,
                        mobile_number: Optional[str] = None, db: Session = Depends(get_db)):
    """Lightweight live-check used by the signup form to flag duplicates as the user types."""
    result = {}
    if username:
        result["username_taken"] = db.query(User).filter(User.username.ilike(username)).first() is not None
    if email:
        result["email_taken"] = db.query(User).filter(User.email.ilike(email)).first() is not None
    if mobile_number:
        result["mobile_taken"] = db.query(User).filter(User.mobile_number == mobile_number).first() is not None
    return result