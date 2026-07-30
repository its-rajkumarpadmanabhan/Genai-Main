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
        import sys
        print('=' * 70)
        try:
            print(f'[DEV MODE — SMTP NOT CONFIGURED] Email to: {to_email}')
            print(f'Subject: {subject}')
            print(html_body)
        except Exception:
            try:
                sys.stdout.buffer.write(f'[DEV MODE — SMTP NOT CONFIGURED] Email to: {to_email}\n'.encode('utf-8'))
                sys.stdout.buffer.write(f'Subject: {subject}\n'.encode('utf-8'))
                sys.stdout.buffer.write(html_body.encode('utf-8'))
                sys.stdout.buffer.write(b'\n')
            except Exception:
                enc = sys.stdout.encoding or 'utf-8'
                print(f'[DEV MODE — SMTP NOT CONFIGURED] Email to: {to_email}'.encode(enc, errors='replace').decode(enc))
                print(f'Subject: {subject}'.encode(enc, errors='replace').decode(enc))
                print(html_body.encode(enc, errors='replace').decode(enc))
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
    
    role_cap = user.role.capitalize()
    mobile = user.mobile_number or 'Not provided'
    
    html = f"""
    <div style="font-family:'Inter',Arial,sans-serif;max-width:560px;margin:auto;
                background:#0f172a;color:#e2e8f0;padding:32px;border-radius:16px;">
      <h1 style="color:#2dd4bf;font-size:22px;">Welcome, {user.username} 👋</h1>
      <p>Your Beacon account has been created successfully.</p>
      
      <div style="background:#1e293b;border-radius:12px;padding:20px;margin-top:20px;margin-bottom:20px;
                  border:1px solid #334155;">
        <h3 style="color:#2dd4bf;font-size:15px;margin-top:0;margin-bottom:12px;border-bottom:1px solid #334155;padding-bottom:8px;">
          Your Account Details
        </h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <tr>
            <td style="padding:8px 0;color:#94a3b8;width:140px;">Username</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{user.username}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">Email</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{user.email}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">Mobile Number</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{mobile}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">Role</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{role_cap}</td>
          </tr>
        </table>
      </div>

      <p style="color:#94a3b8;font-size:13px;">
        If you're ever in a crisis, call or text <strong style="color:#2dd4bf;">988</strong> — no app needed.
      </p>
      
      <p style="color:#64748b;font-size:12px;text-align:center;margin-top:20px;border-top:1px solid #334155;padding-top:12px;">
        Beacon Recovery Platform — Your Emergency & Prevention Assistant
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


def _send_appointment_notification_emails(appointment, email_type='accepted'):
    """
    Send appointment confirmation or rejection/cancellation emails to both
    the patient AND their assigned caretaker (if any).

    email_type: 'accepted' | 'rejected' | 'cancelled_by_doctor'
    """
    from .models import DoctorProfile, PatientProfile, CaretakerProfile
    from django.contrib.auth import get_user_model
    User = get_user_model()

    # ── Reload appointment from DB to get latest data ────────────────────
    from .models import Appointment
    try:
        appointment = Appointment.objects.select_related(
            'patient', 'doctor', 'caretaker'
        ).get(id=appointment.id)
    except Appointment.DoesNotExist:
        return

    # ── Gather appointment details (fresh from DB) ───────────────────────
    doc_profile = DoctorProfile.objects.filter(user=appointment.doctor).first()
    doc_name = (doc_profile.full_name if (doc_profile and doc_profile.full_name)
                else appointment.doctor.username)
    hospital = doc_profile.hospital_name if doc_profile else 'Not specified'
    location = doc_profile.location if doc_profile else 'Not specified'
    mode_str = ('🟢 Online (Video Call)' if appointment.appointment_type == 'online'
                else '📍 Offline (In-Person Clinic Visit)')

    pat_profile = PatientProfile.objects.filter(user=appointment.patient).first()
    patient_name = (pat_profile.full_name if (pat_profile and pat_profile.full_name)
                    else appointment.patient.username)

    # ── Collect recipients (patient + caretaker if assigned) ─────────────
    #    Always query fresh from DB to get the LATEST email addresses
    recipients = []

    # Patient email — re-read from DB to get latest
    fresh_patient = User.objects.filter(id=appointment.patient.id).first()
    patient_email = None
    if pat_profile and pat_profile.email:
        patient_email = pat_profile.email
    elif fresh_patient and fresh_patient.email:
        patient_email = fresh_patient.email
    if patient_email:
        recipients.append({'name': patient_name, 'email': patient_email, 'role': 'patient'})

    # Caretaker email (if assigned) — re-read from DB to get latest
    if pat_profile and pat_profile.assigned_caretaker_id:
        fresh_caretaker = User.objects.filter(id=pat_profile.assigned_caretaker_id).first()
        if fresh_caretaker and fresh_caretaker.email:
            ct_profile = CaretakerProfile.objects.filter(user=fresh_caretaker).first()
            caretaker_name = (ct_profile.full_name if (ct_profile and ct_profile.full_name)
                              else fresh_caretaker.username)
            recipients.append({'name': caretaker_name, 'email': fresh_caretaker.email, 'role': 'caretaker'})

    # Also check if appointment was booked by a different caretaker
    if appointment.caretaker_id and appointment.caretaker_id != getattr(pat_profile, 'assigned_caretaker_id', None):
        fresh_booker = User.objects.filter(id=appointment.caretaker_id).first()
        if fresh_booker and fresh_booker.email and not any(r['email'] == fresh_booker.email for r in recipients):
            ct_prof = CaretakerProfile.objects.filter(user=fresh_booker).first()
            ct_name = (ct_prof.full_name if (ct_prof and ct_prof.full_name)
                       else fresh_booker.username)
            recipients.append({'name': ct_name, 'email': fresh_booker.email, 'role': 'caretaker'})

    if not recipients:
        return

    # ── Build and send emails ────────────────────────────────────────────
    for recipient in recipients:
        if email_type == 'accepted':
            _send_accepted_email(recipient, appointment, doc_name, hospital,
                                 location, mode_str, patient_name)
        else:
            _send_rejected_email(recipient, appointment, doc_name, patient_name,
                                 email_type)


def _send_accepted_email(recipient, appointment, doc_name, hospital,
                         location, mode_str, patient_name):
    """Send a styled HTML appointment-accepted email."""
    role_label = 'Caretaker' if recipient['role'] == 'caretaker' else 'Patient'
    patient_note = (f'<p style="color:#94a3b8;font-size:13px;margin-top:8px;">'
                    f'Patient: <strong style="color:#2dd4bf;">{patient_name}</strong></p>'
                    if recipient['role'] == 'caretaker' else '')

    subject = f'✅ Appointment Confirmed — Dr. {doc_name}'
    html = f"""
    <div style="font-family:'Inter',Arial,sans-serif;max-width:560px;margin:auto;
                background:#0f172a;color:#e2e8f0;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:20px;">
        <span style="font-size:36px;">✅</span>
      </div>
      <h1 style="color:#2dd4bf;font-size:20px;text-align:center;margin-bottom:6px;">
        Appointment Confirmed
      </h1>
      <p style="text-align:center;color:#94a3b8;font-size:13px;margin-top:0;">
        Dear {recipient['name']} ({role_label}), your appointment has been accepted.
      </p>
      {patient_note}

      <div style="background:#1e293b;border-radius:12px;padding:20px;margin-top:20px;
                  border:1px solid #334155;">
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <tr>
            <td style="padding:8px 0;color:#94a3b8;width:140px;">👨‍⚕️ Doctor</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">Dr. {doc_name}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">🏥 Hospital</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{hospital}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">📍 Location</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{location}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">📅 Date</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{appointment.appointment_date}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">🕐 Time Slot</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{appointment.time_slot}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">📋 Mode</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{mode_str}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">📝 Reason</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{appointment.reason or 'General Consultation'}</td>
          </tr>
        </table>
      </div>

      <p style="color:#64748b;font-size:12px;text-align:center;margin-top:20px;">
        Beacon Recovery Platform — Your Emergency & Prevention Assistant
      </p>
    </div>
    """
    send_email(recipient['email'], subject, html)


def _send_rejected_email(recipient, appointment, doc_name, patient_name,
                         email_type):
    """Send a styled HTML appointment-rejected/cancelled email."""
    role_label = 'Caretaker' if recipient['role'] == 'caretaker' else 'Patient'
    patient_note = (f'<p style="color:#94a3b8;font-size:13px;margin-top:8px;">'
                    f'Patient: <strong style="color:#fbbf24;">{patient_name}</strong></p>'
                    if recipient['role'] == 'caretaker' else '')

    if email_type == 'cancelled_by_doctor':
        action_word = 'Cancelled'
        icon = '🚫'
        color = '#f97316'
        reason_text = 'The doctor has cancelled this appointment (doctor may be on leave).'
    else:
        action_word = 'Rejected'
        icon = '❌'
        color = '#f43f5e'
        reason_text = 'Unfortunately, the doctor was unable to accept this appointment request.'

    subject = f'{icon} Appointment {action_word} — Dr. {doc_name}'
    html = f"""
    <div style="font-family:'Inter',Arial,sans-serif;max-width:560px;margin:auto;
                background:#0f172a;color:#e2e8f0;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:20px;">
        <span style="font-size:36px;">{icon}</span>
      </div>
      <h1 style="color:{color};font-size:20px;text-align:center;margin-bottom:6px;">
        Appointment {action_word}
      </h1>
      <p style="text-align:center;color:#94a3b8;font-size:13px;margin-top:0;">
        Dear {recipient['name']} ({role_label}),
      </p>
      {patient_note}

      <div style="background:#1e293b;border-radius:12px;padding:20px;margin-top:20px;
                  border:1px solid #334155;">
        <p style="color:#e2e8f0;font-size:14px;margin:0 0 14px 0;">{reason_text}</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <tr>
            <td style="padding:8px 0;color:#94a3b8;width:140px;">👨‍⚕️ Doctor</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">Dr. {doc_name}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">📅 Date</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{appointment.appointment_date}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">🕐 Time Slot</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{appointment.time_slot}</td>
          </tr>
        </table>
      </div>

      <p style="text-align:center;margin-top:18px;font-size:13px;color:#94a3b8;">
        You can browse other available doctors and book a new appointment from your dashboard.
      </p>

      <p style="color:#64748b;font-size:12px;text-align:center;margin-top:20px;">
        Beacon Recovery Platform — Your Emergency & Prevention Assistant
      </p>
    </div>
    """
    send_email(recipient['email'], subject, html)


def send_appointment_accepted_email(appointment):
    """Public helper: send accepted notification to patient + caretaker in a background thread."""
    threading.Thread(
        target=_send_appointment_notification_emails,
        args=(appointment, 'accepted'),
        daemon=True,
    ).start()


def send_appointment_rejected_email(appointment, email_type='rejected'):
    """Public helper: send rejected/cancelled notification to patient + caretaker in a background thread."""
    threading.Thread(
        target=_send_appointment_notification_emails,
        args=(appointment, email_type),
        daemon=True,
    ).start()


def send_account_deleted_email(user_data: dict) -> None:
    """Send an HTML email confirmation after account deletion."""
    subject = 'Account Deleted — Beacon'
    
    role_cap = user_data.get('role', 'user').capitalize()
    username = user_data.get('username', '')
    email = user_data.get('email', '')
    mobile = user_data.get('mobile_number') or 'Not provided'
    
    details_html = f"""
      <tr>
        <td style="padding:8px 0;color:#94a3b8;width:140px;">Username</td>
        <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{username}</td>
      </tr>
      <tr>
        <td style="padding:8px 0;color:#94a3b8;">Email</td>
        <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{email}</td>
      </tr>
      <tr>
        <td style="padding:8px 0;color:#94a3b8;">Mobile Number</td>
        <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{mobile}</td>
      </tr>
      <tr>
        <td style="padding:8px 0;color:#94a3b8;">Role</td>
        <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{role_cap}</td>
      </tr>
    """
    
    profile_details = user_data.get('profile_details', {})
    if profile_details:
        for key, val in profile_details.items():
            if val is not None and val != '':
                display_key = key.replace('_', ' ').title()
                details_html += f"""
                  <tr>
                    <td style="padding:8px 0;color:#94a3b8;">{display_key}</td>
                    <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{val}</td>
                  </tr>
                """
                
    html = f"""
    <div style="font-family:'Inter',Arial,sans-serif;max-width:560px;margin:auto;
                background:#0f172a;color:#e2e8f0;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:20px;">
        <span style="font-size:36px;">🗑️</span>
      </div>
      <h1 style="color:#f43f5e;font-size:20px;text-align:center;margin-bottom:6px;">
        Account Deleted Successfully
      </h1>
      <p style="text-align:center;color:#94a3b8;font-size:13px;margin-top:0;">
        Dear {username}, your Beacon account has been permanently deleted.
      </p>

      <div style="background:#1e293b;border-radius:12px;padding:20px;margin-top:20px;
                  border:1px solid #334155;">
        <h3 style="color:#2dd4bf;font-size:15px;margin-top:0;margin-bottom:12px;border-bottom:1px solid #334155;padding-bottom:8px;">
          Deleted Account Details
        </h3>
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          {details_html}
        </table>
      </div>

      <p style="text-align:center;margin-top:18px;font-size:13px;color:#94a3b8;">
        We're sorry to see you go. Thank you for using Beacon.
      </p>

      <p style="color:#64748b;font-size:12px;text-align:center;margin-top:20px;">
        Beacon Recovery Platform — Your Emergency & Prevention Assistant
      </p>
    </div>
    """
    send_email(email, subject, html)


def trigger_account_deleted_email(user_data: dict) -> None:
    """Send account deletion email in a background thread."""
    threading.Thread(
        target=send_account_deleted_email,
        args=(user_data,),
        daemon=True,
    ).start()


def send_login_confirmation_email(user_username: str, user_email: str, user_role: str) -> None:
    """Send a login confirmation email to the user's email."""
    from datetime import datetime
    subject = 'Login Confirmation — Beacon'
    login_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    html = f"""
    <div style="font-family:'Inter',Arial,sans-serif;max-width:560px;margin:auto;
                background:#0f172a;color:#e2e8f0;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:20px;">
        <span style="font-size:36px;">🔑</span>
      </div>
      <h1 style="color:#2dd4bf;font-size:20px;text-align:center;margin-bottom:6px;">
        New Login Detected
      </h1>
      <p style="text-align:center;color:#94a3b8;font-size:13px;margin-top:0;">
        Dear {user_username}, we detected a successful login to your Beacon account.
      </p>

      <div style="background:#1e293b;border-radius:12px;padding:20px;margin-top:20px;
                  border:1px solid #334155;">
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
          <tr>
            <td style="padding:8px 0;color:#94a3b8;width:140px;">Username</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{user_username}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">Email</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{user_email}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">Role</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{user_role.capitalize()}</td>
          </tr>
          <tr>
            <td style="padding:8px 0;color:#94a3b8;">Time</td>
            <td style="padding:8px 0;color:#f8fafc;font-weight:600;">{login_time} UTC</td>
          </tr>
        </table>
      </div>

      <p style="text-align:center;margin-top:18px;font-size:13px;color:#94a3b8;">
        If this wasn't you, please secure your account immediately or contact support.
      </p>

      <p style="color:#64748b;font-size:12px;text-align:center;margin-top:20px;">
        Beacon Recovery Platform — Your Emergency & Prevention Assistant
      </p>
    </div>
    """
    send_email(user_email, subject, html)


def trigger_login_confirmation_email(user) -> None:
    """Send login confirmation email in a background thread."""
    threading.Thread(
        target=send_login_confirmation_email,
        args=(user.username, user.email, user.role),
        daemon=True,
    ).start()


def send_signup_otp_email(user, otp_code: str) -> None:
    """Send the 4-digit OTP for email verification during signup."""
    subject = 'Beacon — Verify Your Email Address'
    html = f"""
    <div style="font-family:'Inter',Arial,sans-serif;max-width:560px;margin:auto;
                background:#0f172a;color:#e2e8f0;padding:32px;border-radius:16px;">
      <div style="text-align:center;margin-bottom:20px;">
        <span style="font-size:36px;">📧</span>
      </div>
      <h1 style="color:#2dd4bf;font-size:20px;text-align:center;margin-bottom:6px;">
        Verify Your Email
      </h1>
      <p style="text-align:center;color:#94a3b8;font-size:13px;margin-top:0;">
        Dear {user.username}, use the OTP below to complete your Beacon registration.
      </p>

      <div style="background:#1e293b;border-radius:12px;padding:28px 20px;margin:24px 0;
                  border:1px solid #334155;text-align:center;">
        <p style="color:#94a3b8;font-size:13px;margin:0 0 10px 0;">Your One-Time Password</p>
        <span style="font-size:40px;font-weight:700;letter-spacing:12px;color:#2dd4bf;">
          {otp_code}
        </span>
        <p style="color:#64748b;font-size:12px;margin:14px 0 0 0;">
          This OTP expires in <strong style="color:#fbbf24;">10 minutes</strong>.
        </p>
      </div>

      <p style="text-align:center;color:#94a3b8;font-size:13px;">
        If you did not attempt to sign up for Beacon, please ignore this email.
      </p>

      <p style="color:#64748b;font-size:12px;text-align:center;margin-top:20px;
                border-top:1px solid #334155;padding-top:12px;">
        Beacon Recovery Platform — Your Emergency &amp; Prevention Assistant
      </p>
    </div>
    """
    send_email(user.email, subject, html)
