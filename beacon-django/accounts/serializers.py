"""
BEACON — Auth Serializers
Validation rules are identical to auth.py (same regexes, same error messages).
"""

import re

from rest_framework import serializers


# ── Validation patterns (identical to auth.py) ────────────────────────────────
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_ .\'\-]{2,100}$')
MOBILE_RE = re.compile(r'^\+?[0-9]{7,15}$')
PASSWORD_RE = re.compile(r'^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$')


from .models import (
    CustomUser, PatientProfile, DoctorProfile, CaretakerProfile,
    MedicalDocument, CaretakerRequest, CaretakerEditRequest, Appointment
)


class SignupSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=100)
    email = serializers.EmailField()
    mobile_number = serializers.CharField(max_length=20)
    password = serializers.CharField(write_only=True)
    role = serializers.ChoiceField(choices=CustomUser.ROLE_CHOICES, default='patient')

    def validate_role(self, value):
        if value == 'admin':
            raise serializers.ValidationError('Admin accounts cannot be created via signup.')
        return value

    def validate_username(self, value):
        cleaned = value.strip()
        if len(cleaned) < 2 or len(cleaned) > 100:
            raise serializers.ValidationError('Name must be between 2 and 100 characters.')
        return cleaned

    def validate_mobile_number(self, value):
        cleaned = value.strip().replace(' ', '').replace('-', '')
        if not MOBILE_RE.match(cleaned):
            raise serializers.ValidationError(
                'Enter a valid mobile number (7-15 digits, optional leading +country code).'
            )
        return cleaned

    def validate_password(self, value):
        if not PASSWORD_RE.match(value):
            raise serializers.ValidationError(
                'Password needs 8+ characters with uppercase, lowercase, number, and special character.'
            )
        return value


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField()
    password = serializers.CharField(write_only=True)


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True)

    def validate_new_password(self, value):
        if not PASSWORD_RE.match(value):
            raise serializers.ValidationError(
                'Password needs 8+ characters with uppercase, lowercase, number, and special character.'
            )
        return value


class CustomUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomUser
        fields = ['id', 'username', 'email', 'mobile_number', 'role', 'is_active', 'created_at']


class PatientProfileSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    mobile_number = serializers.CharField(source='user.mobile_number', read_only=True)
    assigned_caretaker_details = serializers.SerializerMethodField()
    past_caretakers_history = serializers.SerializerMethodField()

    class Meta:
        model = PatientProfile
        fields = '__all__'

    def get_assigned_caretaker_details(self, obj):
        if not obj.assigned_caretaker:
            return None
        c_user = obj.assigned_caretaker
        c_profile = getattr(c_user, 'caretaker_profile', None)
        return {
            'caretaker_id': c_user.id,
            'username': c_user.username,
            'email': c_user.email,
            'full_name': c_profile.full_name if (c_profile and c_profile.full_name) else c_user.username,
            'phone_number': (c_profile.phone_number if c_profile else c_user.mobile_number) or 'Not provided',
            'location': c_profile.location if c_profile else 'Not provided',
            'experience_years': c_profile.experience_years if c_profile else 0,
            'consultation_fee': str(c_profile.consultation_fee) if c_profile else '0.00',
            'languages': c_profile.languages if c_profile else 'Not provided',
            'available_hours': c_profile.available_hours if c_profile else '24/7 Available',
            'license_number': c_profile.license_number if c_profile else 'Not provided',
        }

    def get_past_caretakers_history(self, obj):
        from .models import CaretakerRequest, CaretakerProfile
        past_reqs = CaretakerRequest.objects.filter(patient=obj.user, status='unlinked').order_by('-created_at')
        past_list = []
        for r in past_reqs:
            c_user = r.caretaker
            c_profile = CaretakerProfile.objects.filter(user=c_user).first()
            past_list.append({
                'caretaker_id': c_user.id,
                'full_name': c_profile.full_name if (c_profile and c_profile.full_name) else c_user.username,
                'phone_number': (c_profile.phone_number if c_profile else c_user.mobile_number) or 'Not provided',
                'location': c_profile.location if c_profile else 'Not provided',
                'unlinked_at': str(r.created_at).split(' ')[0]
            })
        return past_list


class DoctorProfileSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    mobile_number = serializers.CharField(source='user.mobile_number', read_only=True)

    class Meta:
        model = DoctorProfile
        fields = '__all__'


class CaretakerProfileSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source='user.id', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)
    mobile_number = serializers.CharField(source='user.mobile_number', read_only=True)

    class Meta:
        model = CaretakerProfile
        fields = '__all__'


class MedicalDocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicalDocument
        fields = '__all__'


class CaretakerRequestSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source='patient.username', read_only=True)
    caretaker_name = serializers.CharField(source='caretaker.username', read_only=True)

    class Meta:
        model = CaretakerRequest
        fields = '__all__'


class AppointmentSerializer(serializers.ModelSerializer):
    patient_name = serializers.CharField(source='patient.username', read_only=True)
    doctor_name = serializers.CharField(source='doctor.username', read_only=True)
    caretaker_name = serializers.SerializerMethodField()

    class Meta:
        model = Appointment
        fields = '__all__'

    def get_caretaker_name(self, obj):
        if obj.caretaker:
            from .models import CaretakerProfile
            c_prof = CaretakerProfile.objects.filter(user=obj.caretaker).first()
            return c_prof.full_name if (c_prof and c_prof.full_name) else obj.caretaker.username
        return None
