"""
BEACON — Email Delivery
Mirrors auth.py send_email / send_welcome_email / send_password_reset_email.
Uses Django's email backend (SMTP in production, console in dev).
"""

import threading

from django.conf import settings
from django.core.mail import EmailMessage


def send_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an HTML email. In dev mode (no SMTP config), prints to console."""
    if not settings.EMAIL_ENABLED:
        print('=' * 70)
        print(f'[DEV MODE — SMTP NOT CONFIGURED] Email to: {to_email}')
        print(f'Subject: {subject}')
        print(html_body)
        print('=' * 70)
        return False

    try:
        msg = EmailMessage(
            subject=subject,
            body=html_body,
            from_email=f'{settings.SMTP_FROM_NAME} <{settings.DEFAULT_FROM_EMAIL}>',
            to=[to_email],
        )
        msg.content_subtype = 'html'
        msg.send(fail_silently=False)
        return True
    except Exception as exc:
        print(f'[EMAIL ERROR] Failed to send to {to_email}: {exc}')
        return False


def send_welcome_email(user) -> None:
    """Send a welcome email after signup (runs in a background thread)."""
    subject = 'Welcome to Beacon — Your Emergency & Prevention Assistant'
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: auto;
                background:#0f172a; color:#e2e8f0; padding: 32px; border-radius: 16px;">
      <h1 style="color:#2dd4bf; font-size: 22px;">Welcome, {user.username} 👋</h1>
      <p>Your Beacon account has been created successfully.</p>
      <p style="color:#94a3b8; font-size: 13px;">
        If you're ever in a crisis, call or text <strong style="color:#2dd4bf;">988</strong> — no app needed.
      </p>
    </div>
    """
    send_email(user.email, subject, html)


def send_password_reset_email(user, reset_token: str) -> None:
    """Send a password-reset email (runs in a background thread)."""
    reset_link = f'{settings.FRONTEND_BASE_URL}/reset-password.html?token={reset_token}'
    subject = 'Beacon — Reset Your Password'
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: auto;
                background:#0f172a; color:#e2e8f0; padding: 32px; border-radius: 16px;">
      <h1 style="color:#f43f5e; font-size: 22px;">Password Reset Request</h1>
      <p>Click the button below to set a new password. This link expires in <strong>30 minutes</strong>.</p>
      <a href="{reset_link}"
         style="display:inline-block; margin: 20px 0; background:#0d9488;
                color:white; padding: 12px 24px; border-radius: 10px;
                text-decoration:none; font-weight:bold;">
        Reset Password
      </a>
      <p style="color:#94a3b8; font-size: 12px;">
        If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    """
    send_email(user.email, subject, html)
