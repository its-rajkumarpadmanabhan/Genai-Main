"""
BEACON — Custom User Model & Password Reset Token
Replaces SQLAlchemy models from auth.py with Django ORM.

Fields match the original exactly:
  - CustomUser: username (unique), email (unique), mobile_number (unique),
                password (bcrypt via Django PASSWORD_HASHERS), created_at, is_active
  - PasswordResetToken: token (unique), user, expires_at, used
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models


class CustomUserManager(BaseUserManager):
    """Custom manager so Django knows how to create users/superusers."""

    def create_user(self, username, email, mobile_number, password=None, **extra_fields):
        if not email:
            raise ValueError('Email is required.')
        if not username:
            raise ValueError('Username is required.')
        email = self.normalize_email(email)
        user = self.model(
            username=username,
            email=email,
            mobile_number=mobile_number,
            **extra_fields,
        )
        user.set_password(password)   # BCrypt via PASSWORD_HASHERS in settings.py
        user.save(using=self._db)
        return user

    def create_superuser(self, username, email, mobile_number, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(username, email, mobile_number, password, **extra_fields)


import random
import string

def generate_random_code(prefix):
    return f"{prefix}-" + ''.join(random.choices(string.digits, k=6))

class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Drop-in replacement for the SQLAlchemy User model from auth.py.
    Extended with role-based access for Admin, Doctor, Patient, and Caretaker.
    """
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('doctor', 'Doctor'),
        ('patient', 'Patient'),
        ('caretaker', 'Caretaker'),
    ]

    username = models.CharField(max_length=100, unique=False, db_index=True)
    email = models.EmailField(max_length=255, unique=True, db_index=True)
    mobile_number = models.CharField(max_length=20, null=True, blank=True, unique=False, db_index=True)
    plain_password = models.CharField(max_length=128, null=True, blank=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='patient', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = CustomUserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'mobile_number']

    class Meta:
        db_table = 'users'

    def __str__(self):
        return f"{self.username} ({self.role})"


class PatientProfile(models.Model):
    """Profile details for Patient users."""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='patient_profile')
    patient_code = models.CharField(max_length=30, unique=True, db_index=True, blank=True, null=True)
    full_name = models.CharField(max_length=100, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    marital_status = models.CharField(max_length=20, blank=True, null=True)
    location = models.CharField(max_length=255, blank=True, null=True)
    languages = models.CharField(max_length=255, blank=True, null=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    email = models.CharField(max_length=100, blank=True, null=True)
    insurance_details = models.TextField(blank=True, null=True)
    
    # Emergency Contact
    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, null=True)
    emergency_contact_relation = models.CharField(max_length=50, blank=True, null=True)
    
    medical_history_notes = models.TextField(blank=True, null=True)
    assigned_caretaker = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='cared_patients')

    class Meta:
        db_table = 'patient_profiles'

    def save(self, *args, **kwargs):
        if not self.patient_code:
            self.patient_code = generate_random_code('PAT')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"PatientProfile({self.patient_code} - {self.full_name or self.user.username})"


class DoctorProfile(models.Model):
    """Profile details for Doctor users."""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='doctor_profile')
    doctor_code = models.CharField(max_length=30, unique=True, db_index=True, blank=True, null=True)
    full_name = models.CharField(max_length=100, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    experience_years = models.IntegerField(default=0)
    location = models.CharField(max_length=255, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    hospital_name = models.CharField(max_length=255, blank=True, null=True)
    pincode = models.CharField(max_length=10, blank=True, null=True)
    major_department = models.CharField(max_length=100, blank=True, null=True)
    languages_speak = models.CharField(max_length=255, blank=True, null=True)
    license_number = models.CharField(max_length=50, blank=True, null=True)
    specialized_details = models.TextField(blank=True, null=True)
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    available_hours = models.CharField(max_length=100, default='09:00 AM - 05:00 PM')
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    rating_avg = models.FloatField(default=5.0)
    reviews_count = models.IntegerField(default=0)
    availability_status = models.CharField(max_length=30, default='Active Today')

    class Meta:
        db_table = 'doctor_profiles'

    def save(self, *args, **kwargs):
        if not self.doctor_code:
            self.doctor_code = generate_random_code('DOC')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"DoctorProfile({self.doctor_code} - {self.full_name or self.user.username})"


class CaretakerProfile(models.Model):
    """Profile details for Caretaker users."""
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='caretaker_profile')
    caretaker_code = models.CharField(max_length=30, unique=True, db_index=True, blank=True, null=True)
    full_name = models.CharField(max_length=100, blank=True, null=True)
    dob = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=20, blank=True, null=True)
    experience_years = models.IntegerField(default=0)
    location = models.CharField(max_length=255, blank=True, null=True)
    languages = models.CharField(max_length=255, blank=True, null=True)
    license_number = models.CharField(max_length=50, blank=True, null=True)
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    available_hours = models.CharField(max_length=100, default='24/7 Available')
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    rating_avg = models.FloatField(default=5.0)
    reviews_count = models.IntegerField(default=0)

    class Meta:
        db_table = 'caretaker_profiles'

    def save(self, *args, **kwargs):
        if not self.caretaker_code:
            self.caretaker_code = generate_random_code('CAR')
        super().save(*args, **kwargs)

    def __str__(self):
        return f"CaretakerProfile({self.full_name or self.user.username})"


class Review(models.Model):
    """Star review and rating submitted for a Doctor or Caretaker."""
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='submitted_reviews')
    doctor = models.ForeignKey(DoctorProfile, on_delete=models.CASCADE, null=True, blank=True, related_name='reviews')
    caretaker = models.ForeignKey(CaretakerProfile, on_delete=models.CASCADE, null=True, blank=True, related_name='reviews')
    rating = models.IntegerField(default=5)  # 1 to 5
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'reviews'


class MedicalDocument(models.Model):
    """Medical record / document files uploaded for a patient."""
    patient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='medical_documents')
    title = models.CharField(max_length=255)
    file_data = models.TextField(blank=True, null=True)  # base64 or stored URL
    file_name = models.CharField(max_length=255, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'medical_documents'

    def __str__(self):
        return f"Document({self.title} for {self.patient.username})"


class CaretakerRequest(models.Model):
    """Request sent by patient to choose a caretaker."""
    STATUS_CHOICES = [('pending', 'Pending'), ('accepted', 'Accepted'), ('rejected', 'Rejected')]
    patient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='sent_caretaker_requests')
    caretaker = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='received_caretaker_requests')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'caretaker_requests'


class CaretakerEditRequest(models.Model):
    """Request sent by caretaker to edit patient profile."""
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')]
    caretaker = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='edit_requests')
    patient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='profile_edit_requests')
    proposed_changes = models.TextField()
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'caretaker_edit_requests'


class Appointment(models.Model):
    """Appointments booked with Doctors by Patient or Caretaker."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('completed', 'Completed'),
        ('missed', 'Missed'),
    ]
    patient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='patient_appointments')
    doctor = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='doctor_appointments')
    booked_by = models.CharField(max_length=20, choices=[('patient', 'Patient'), ('caretaker', 'Caretaker')], default='patient')
    caretaker = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='booked_appointments')
    appointment_date = models.DateField()
    time_slot = models.CharField(max_length=50, default='10:00 AM')
    reason = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    appointment_type = models.CharField(max_length=20, default='offline') # 'online' or 'offline'
    prescription_pdf = models.TextField(blank=True, null=True) # base64 PDF
    prescription_name = models.CharField(max_length=255, blank=True, null=True)
    is_call_active = models.BooleanField(default=False)
    is_caretaker_added_to_call = models.BooleanField(default=False)
    doctor_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'appointments'


class PasswordResetToken(models.Model):
    """One-time password reset token — mirrors the original PasswordResetToken table."""

    token = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='reset_tokens')
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    class Meta:
        db_table = 'password_reset_tokens'

    def __str__(self):
        return f'ResetToken({self.user.username}, used={self.used})'


class EmergencyAlert(models.Model):
    """
    Records emergency/medical conditions detected by the Voice AI Engine.
    Stored in patient profile timeline and triggers caretaker notification.
    """
    SEVERITY_CHOICES = [
        ('critical', 'Critical Emergency'),
        ('urgent', 'Urgent Medical Need'),
        ('moderate', 'Moderate Medical Concern'),
        ('low', 'Low / General Inquiry'),
    ]
    patient = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='emergency_alerts')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default='moderate')
    condition_summary = models.TextField(help_text='AI-detected condition description')
    ai_advice = models.TextField(blank=True, null=True, help_text='Safety advice given by AI')
    detected_specialty = models.CharField(max_length=100, blank=True, null=True)
    patient_query = models.TextField(blank=True, null=True, help_text='What the patient said/asked')
    latitude = models.FloatField(blank=True, null=True)
    longitude = models.FloatField(blank=True, null=True)
    caretaker_notified = models.BooleanField(default=False)
    caretaker_seen = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'emergency_alerts'
        ordering = ['-created_at']

    def __str__(self):
        return f"EmergencyAlert({self.severity} - {self.patient.username} - {self.created_at})"
