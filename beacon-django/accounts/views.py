"""
BEACON — Auth Views (Django REST Framework)
Replaces all routes from auth.py's FastAPI APIRouter.

Preserved API contract (same paths, same JSON response shapes):
  POST /api/auth/signup
  POST /api/auth/login
  GET  /api/auth/me
  POST /api/auth/forgot-password
  POST /api/auth/reset-password
  GET  /api/auth/check-availability
"""

import secrets
import threading
from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .emails import send_password_reset_email, send_welcome_email
from .models import (
    PasswordResetToken, DoctorProfile, PatientProfile, CaretakerProfile,
    Appointment, CaretakerRequest, MedicalDocument
)
from .serializers import (
    ForgotPasswordSerializer,
    LoginSerializer,
    ResetPasswordSerializer,
    SignupSerializer,
)

User = get_user_model()

RESET_TOKEN_EXPIRE_MINUTES = 30


# ── Helper ──────────────────────────────────────────────────────────────────
def get_tokens_for_user(user) -> str:
    """
    Creates a JWT access token with custom claims (username, email) so the
    frontend /api/auth/me response is fully populated.
    """
    refresh = RefreshToken.for_user(user)
    refresh['username'] = user.username
    refresh['email'] = user.email
    return str(refresh.access_token)


def _flatten_drf_errors(errors: dict) -> list:
    """Convert DRF field errors into a list of {msg: ...} dicts (FastAPI shape)."""
    detail = []
    for msgs in errors.values():
        for msg in msgs:
            detail.append({'msg': str(msg)})
    return detail


# ── Signup ───────────────────────────────────────────────────────────────────
class SignupView(APIView):
    """POST /api/auth/signup"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': _flatten_drf_errors(serializer.errors)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        data = serializer.validated_data
        normalized_email = data['email'].lower().strip()
        username = data['username']
        mobile = data['mobile_number']

        # Duplicate checks (only email and mobile must be unique)
        if User.objects.filter(email__iexact=normalized_email).exists():
            return Response(
                {'detail': 'An account with this email already exists.'},
                status=status.HTTP_409_CONFLICT,
            )
        if User.objects.filter(mobile_number=mobile).exists():
            return Response(
                {'detail': 'An account with this mobile number already exists.'},
                status=status.HTTP_409_CONFLICT,
            )

        role = data.get('role', 'patient')
        user = User.objects.create_user(
            username=username,
            email=normalized_email,
            mobile_number=mobile,
            password=data['password'],
            role=role,
        )

        # Create associated role profile
        if role == 'patient':
            PatientProfile.objects.get_or_create(user=user, defaults={'full_name': username, 'phone_number': mobile})
        elif role == 'doctor':
            DoctorProfile.objects.get_or_create(user=user, defaults={'full_name': username, 'phone_number': mobile})
        elif role == 'caretaker':
            CaretakerProfile.objects.get_or_create(user=user, defaults={'full_name': username, 'phone_number': mobile})

        # Send welcome email in a background thread (non-blocking)
        threading.Thread(target=send_welcome_email, args=(user,), daemon=True).start()

        token = get_tokens_for_user(user)
        return Response(
            {
                'status': 'success',
                'message': 'Account created successfully.',
                'access_token': token,
                'user': {'id': user.id, 'username': user.username, 'email': user.email, 'role': user.role},
            },
            status=status.HTTP_201_CREATED,
        )


# ── Login ────────────────────────────────────────────────────────────────────
class LoginView(APIView):
    """POST /api/auth/login"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid request.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        identifier = serializer.validated_data['identifier'].strip().lower()
        password = serializer.validated_data['password']

        # Allow login by username OR email (case-insensitive)
        user = User.objects.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)
        ).first()

        if not user:
            return Response(
                {'detail': 'No user account found. Please sign up to create a new profile.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.check_password(password):
            return Response(
                {'detail': 'Invalid password. Please check your credentials.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {'detail': 'This account has been deactivated.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        token = get_tokens_for_user(user)
        return Response({
            'status': 'success',
            'access_token': token,
            'user': {'id': user.id, 'username': user.username, 'email': user.email, 'role': user.role},
        })


# ── Me & Self Delete ─────────────────────────────────────────────────────────
class MeView(APIView):
    """GET /api/auth/me — returns current user profile"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'mobile_number': user.mobile_number,
            'role': user.role,
            'created_at': user.created_at.isoformat(),
        })


class DeleteSelfProfileView(APIView):
    """DELETE /api/auth/profile/delete — allows user to self-delete their profile"""
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = request.user
        user.delete()
        return Response({'status': 'success', 'message': 'Profile deleted successfully.'})


# ── Admin User Management ────────────────────────────────────────────────────
class AdminStatsView(APIView):
    """GET /api/admin/stats"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin' and not request.user.is_staff:
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        from .models import DoctorProfile, PatientProfile, CaretakerProfile, Appointment
        return Response({
            'doctors_count': DoctorProfile.objects.count(),
            'patients_count': PatientProfile.objects.count(),
            'caretakers_count': CaretakerProfile.objects.count(),
            'appointments_count': Appointment.objects.count(),
        })


class AdminUsersView(APIView):
    """GET/POST/PUT/DELETE /api/admin/users"""
    permission_classes = [IsAuthenticated]

    def _check_admin(self, user):
        return user.role == 'admin' or user.is_staff

    def get(self, request):
        if not self._check_admin(request.user):
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        users = User.objects.all().order_by('-created_at')
        users_data = []
        for u in users:
            users_data.append({
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'mobile_number': u.mobile_number,
                'role': u.role,
                'is_active': u.is_active,
                'created_at': u.created_at.isoformat() if u.created_at else None,
            })
        return Response(users_data)

    def post(self, request):
        if not self._check_admin(request.user):
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        username = request.data.get('username')
        email = request.data.get('email')
        mobile = request.data.get('mobile_number', '')
        password = request.data.get('password', 'DefaultPass123!')
        role = request.data.get('role', 'patient')

        if User.objects.filter(username__iexact=username).exists():
            return Response({'detail': 'Username already exists.'}, status=status.HTTP_400_BAD_REQUEST)

        u = User.objects.create_user(username=username, email=email, mobile_number=mobile, password=password, role=role)
        from .models import DoctorProfile, PatientProfile, CaretakerProfile
        if role == 'patient':
            PatientProfile.objects.get_or_create(user=u, defaults={'full_name': username})
        elif role == 'doctor':
            DoctorProfile.objects.get_or_create(user=u, defaults={'full_name': username})
        elif role == 'caretaker':
            CaretakerProfile.objects.get_or_create(user=u, defaults={'full_name': username})

        return Response({'status': 'success', 'user_id': u.id}, status=status.HTTP_201_CREATED)

    def put(self, request):
        if not self._check_admin(request.user):
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        user_id = request.data.get('user_id')
        user = User.objects.filter(id=user_id).first()
        if not user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        if 'username' in request.data: user.username = request.data['username']
        if 'email' in request.data: user.email = request.data['email']
        if 'mobile_number' in request.data: user.mobile_number = request.data['mobile_number']
        if 'role' in request.data: user.role = request.data['role']
        if 'is_active' in request.data: user.is_active = bool(request.data['is_active'])
        if 'password' in request.data and request.data['password']:
            user.set_password(request.data['password'])
        user.save()

        return Response({'status': 'success', 'message': 'User updated.'})

    def delete(self, request):
        if not self._check_admin(request.user):
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        user_id = request.data.get('user_id')
        User.objects.filter(id=user_id).delete()
        return Response({'status': 'success', 'message': 'User deleted.'})


# ── Patient Profile & Features ───────────────────────────────────────────────
class PatientProfileView(APIView):
    """GET/PUT /api/patient/profile"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['patient', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Patient access required.'}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = PatientProfile.objects.get_or_create(user=request.user, defaults={'full_name': request.user.username})
        from .serializers import PatientProfileSerializer
        return Response(PatientProfileSerializer(profile).data)

    def put(self, request):
        if request.user.role not in ['patient', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Patient access required.'}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = PatientProfile.objects.get_or_create(user=request.user)
        for k, v in request.data.items():
            if hasattr(profile, k):
                setattr(profile, k, v)
        profile.save()
        from .serializers import PatientProfileSerializer
        return Response(PatientProfileSerializer(profile).data)


class MedicalDocumentView(APIView):
    """GET/POST/DELETE /api/patient/documents"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import MedicalDocument
        from .serializers import MedicalDocumentSerializer
        docs = MedicalDocument.objects.filter(patient=request.user).order_by('-uploaded_at')
        return Response(MedicalDocumentSerializer(docs, many=True).data)

    def post(self, request):
        from .models import MedicalDocument
        from .serializers import MedicalDocumentSerializer
        doc = MedicalDocument.objects.create(
            patient=request.user,
            title=request.data.get('title', 'Medical Record'),
            file_data=request.data.get('file_data', ''),
            file_name=request.data.get('file_name', 'document.pdf'),
            notes=request.data.get('notes', '')
        )
        return Response(MedicalDocumentSerializer(doc).data, status=status.HTTP_201_CREATED)

    def delete(self, request):
        from .models import MedicalDocument
        doc_id = request.data.get('doc_id')
        MedicalDocument.objects.filter(id=doc_id, patient=request.user).delete()
        return Response({'status': 'success'})


# ── Directory & Caretaker Request Views ──────────────────────────────────────
class DirectoryListView(APIView):
    """GET /api/directory/search?type=doctor|caretaker&location=&gender=&dept="""
    permission_classes = [AllowAny]

    def get(self, request):
        dir_type = request.query_params.get('type', 'doctor')
        location = request.query_params.get('location', '').strip().lower()
        gender = request.query_params.get('gender', '').strip().lower()
        dept = request.query_params.get('dept', '').strip().lower()

        if dir_type == 'doctor':
            from .models import DoctorProfile
            from .serializers import DoctorProfileSerializer
            qs = DoctorProfile.objects.all()
            if location:
                qs = qs.filter(location__icontains=location)
            if gender:
                qs = qs.filter(gender__iexact=gender)
            if dept:
                qs = qs.filter(major_department__icontains=dept)
            return Response(DoctorProfileSerializer(qs, many=True).data)
        else:
            from .models import CaretakerProfile
            from .serializers import CaretakerProfileSerializer
            qs = CaretakerProfile.objects.all()
            if location:
                qs = qs.filter(location__icontains=location)
            if gender:
                qs = qs.filter(gender__iexact=gender)
            return Response(CaretakerProfileSerializer(qs, many=True).data)


class CaretakerRequestView(APIView):
    """POST /api/patient/caretaker-request"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import CaretakerRequest
        caretaker_id = request.data.get('caretaker_id')
        caretaker = User.objects.filter(id=caretaker_id, role='caretaker').first()
        if not caretaker:
            return Response({'detail': 'Caretaker not found.'}, status=status.HTTP_404_NOT_FOUND)

        req_obj, created = CaretakerRequest.objects.get_or_create(patient=request.user, caretaker=caretaker)
        return Response({'status': 'success', 'request_id': req_obj.id, 'status_text': req_obj.status})


class AppointmentBookingView(APIView):
    """POST /api/appointments/book"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import Appointment
        doctor_id = request.data.get('doctor_id')
        doctor = User.objects.filter(id=doctor_id, role='doctor').first()
        if not doctor:
            return Response({'detail': 'Doctor not found.'}, status=status.HTTP_404_NOT_FOUND)

        app_date = request.data.get('appointment_date')
        time_slot = request.data.get('time_slot', '10:00 AM')
        reason = request.data.get('reason', '')
        booked_by = request.data.get('booked_by', 'patient')

        apt = Appointment.objects.create(
            patient=request.user if booked_by == 'patient' else User.objects.get(id=request.data.get('patient_id')),
            doctor=doctor,
            booked_by=booked_by,
            caretaker=request.user if booked_by == 'caretaker' else None,
            appointment_date=app_date,
            time_slot=time_slot,
            reason=reason,
            status='pending'
        )
        return Response({'status': 'success', 'appointment_id': apt.id}, status=status.HTTP_201_CREATED)


# ── Doctor Profile & Appointments ────────────────────────────────────────────
class DoctorProfileView(APIView):
    """GET/PUT /api/doctor/profile"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import DoctorProfile
        from .serializers import DoctorProfileSerializer
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user, defaults={'full_name': request.user.username})
        return Response(DoctorProfileSerializer(profile).data)

    def put(self, request):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import DoctorProfile
        from .serializers import DoctorProfileSerializer
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        for k, v in request.data.items():
            if hasattr(profile, k):
                setattr(profile, k, v)
        profile.save()
        return Response(DoctorProfileSerializer(profile).data)


class DoctorAppointmentsView(APIView):
    """GET /api/doctor/appointments, POST status (accept/reject), POST checkup (completed/missed)"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import Appointment
        from .serializers import AppointmentSerializer
        apts = Appointment.objects.filter(doctor=request.user).order_by('-appointment_date')
        return Response(AppointmentSerializer(apts, many=True).data)

    def post(self, request):
        from .models import Appointment
        apt_id = request.data.get('appointment_id')
        new_status = request.data.get('status')
        apt = Appointment.objects.filter(id=apt_id, doctor=request.user).first()
        if not apt:
            return Response({'detail': 'Appointment not found.'}, status=status.HTTP_404_NOT_FOUND)

        if new_status in ['accepted', 'rejected', 'completed', 'missed']:
            apt.status = new_status
            if request.data.get('doctor_notes'):
                apt.doctor_notes = request.data.get('doctor_notes')
            apt.save()
            return Response({'status': 'success', 'new_status': apt.status})
        return Response({'detail': 'Invalid status.'}, status=status.HTTP_400_BAD_REQUEST)


class DoctorPatientRecordsView(APIView):
    """GET /api/doctor/patients/<id>/records — read-only access for booked patients"""
    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        from .models import Appointment, MedicalDocument
        from .serializers import MedicalDocumentSerializer
        has_apt = Appointment.objects.filter(doctor=request.user, patient_id=patient_id).exists()
        if not has_apt and request.user.role != 'admin':
            return Response({'detail': 'Access allowed for your patients only.'}, status=status.HTTP_403_FORBIDDEN)

        docs = MedicalDocument.objects.filter(patient_id=patient_id)
        return Response(MedicalDocumentSerializer(docs, many=True).data)


# ── Caretaker Profile & Requests ─────────────────────────────────────────────
class CaretakerProfileView(APIView):
    """GET/PUT /api/caretaker/profile"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['caretaker', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Caretaker access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import CaretakerProfile
        from .serializers import CaretakerProfileSerializer
        profile, _ = CaretakerProfile.objects.get_or_create(user=request.user, defaults={'full_name': request.user.username})
        return Response(CaretakerProfileSerializer(profile).data)

    def put(self, request):
        if request.user.role not in ['caretaker', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Caretaker access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import CaretakerProfile
        from .serializers import CaretakerProfileSerializer
        profile, _ = CaretakerProfile.objects.get_or_create(user=request.user)
        for k, v in request.data.items():
            if hasattr(profile, k):
                setattr(profile, k, v)
        profile.save()
        return Response(CaretakerProfileSerializer(profile).data)


class CaretakerRequestsView(APIView):
    """GET/POST /api/caretaker/requests"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['caretaker', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Caretaker access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import CaretakerRequest
        from .serializers import CaretakerRequestSerializer
        reqs = CaretakerRequest.objects.filter(caretaker=request.user).order_by('-created_at')
        return Response(CaretakerRequestSerializer(reqs, many=True).data)

    def post(self, request):
        from .models import CaretakerRequest, PatientProfile
        req_id = request.data.get('request_id')
        new_status = request.data.get('status')
        creq = CaretakerRequest.objects.filter(id=req_id, caretaker=request.user).first()
        if not creq:
            return Response({'detail': 'Request not found.'}, status=status.HTTP_404_NOT_FOUND)

        if new_status in ['accepted', 'rejected']:
            creq.status = new_status
            creq.save()
            if new_status == 'accepted':
                PatientProfile.objects.filter(user=creq.patient).update(assigned_caretaker=request.user)
            return Response({'status': 'success', 'new_status': creq.status})
        return Response({'detail': 'Invalid status.'}, status=status.HTTP_400_BAD_REQUEST)


class CaretakerEditRequestView(APIView):
    """POST /api/caretaker/patient-edit-request"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import CaretakerEditRequest
        patient_id = request.data.get('patient_id')
        changes = request.data.get('proposed_changes', '')
        reason = request.data.get('reason', '')
        
        edit_req = CaretakerEditRequest.objects.create(
            caretaker=request.user,
            patient_id=patient_id,
            proposed_changes=changes,
            reason=reason
        )
        return Response({'status': 'success', 'edit_request_id': edit_req.id}, status=status.HTTP_201_CREATED)


# ── Forgot Password ──────────────────────────────────────────────────────────
class ForgotPasswordView(APIView):
    """POST /api/auth/forgot-password"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': 'Invalid email address.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalized_email = serializer.validated_data['email'].lower().strip()

        # Generic response prevents email enumeration
        generic_response = {
            'status': 'success',
            'message': 'If that email is registered, a password reset link has been sent.',
        }

        user = User.objects.filter(email__iexact=normalized_email).first()
        if not user:
            return Response(generic_response)

        reset_token = secrets.token_urlsafe(32)
        PasswordResetToken.objects.create(
            token=reset_token,
            user=user,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_EXPIRE_MINUTES),
        )

        threading.Thread(
            target=send_password_reset_email,
            args=(user, reset_token),
            daemon=True,
        ).start()

        return Response(generic_response)


# ── Reset Password ───────────────────────────────────────────────────────────
class ResetPasswordView(APIView):
    """POST /api/auth/reset-password"""
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'detail': _flatten_drf_errors(serializer.errors)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        token_str = serializer.validated_data['token']
        new_password = serializer.validated_data['new_password']

        entry = PasswordResetToken.objects.filter(token=token_str).first()
        if not entry or entry.used:
            return Response(
                {'detail': 'This reset link is invalid or has already been used.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        expires_at = entry.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < datetime.now(timezone.utc):
            return Response(
                {'detail': 'This reset link has expired.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = entry.user
        user.set_password(new_password)
        user.save()
        entry.used = True
        entry.save()

        return Response({'status': 'success', 'message': 'Password updated. You can now log in.'})


# ── Check Availability ───────────────────────────────────────────────────────
class CheckAvailabilityView(APIView):
    """GET /api/auth/check-availability?username=&email=&mobile_number="""
    permission_classes = [AllowAny]

    def get(self, request):
        result = {}
        username = request.query_params.get('username')
        email = request.query_params.get('email')
        mobile_number = request.query_params.get('mobile_number')

        if username:
            result['username_taken'] = User.objects.filter(
                username__iexact=username
            ).exists()
        if email:
            result['email_taken'] = User.objects.filter(
                email__iexact=email
            ).exists()
        return Response(result)


# ── Star Reviews & Ratings ───────────────────────────────────────────────────
class SubmitReviewView(APIView):
    """POST /api/auth/review — submit a star rating & review for a doctor or caretaker"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import DoctorProfile, CaretakerProfile, Review
        target_type = request.data.get('target_type')  # 'doctor' or 'caretaker'
        target_id = request.data.get('target_id')
        rating = int(request.data.get('rating', 5))
        comment = request.data.get('comment', '').strip()

        if rating < 1 or rating > 5:
            return Response({'detail': 'Rating must be between 1 and 5 stars.'}, status=status.HTTP_400_BAD_REQUEST)

        if target_type == 'doctor':
            doc_profile = DoctorProfile.objects.filter(id=target_id).first()
            if not doc_profile:
                return Response({'detail': 'Doctor not found.'}, status=status.HTTP_404_NOT_FOUND)
            Review.objects.create(user=request.user, doctor=doc_profile, rating=rating, comment=comment)
            
            all_reviews = doc_profile.reviews.all()
            doc_profile.reviews_count = all_reviews.count()
            doc_profile.rating_avg = round(sum(r.rating for r in all_reviews) / doc_profile.reviews_count, 1)
            doc_profile.save()
            return Response({
                'status': 'success',
                'rating_avg': doc_profile.rating_avg,
                'reviews_count': doc_profile.reviews_count
            })
        elif target_type == 'caretaker':
            car_profile = CaretakerProfile.objects.filter(id=target_id).first()
            if not car_profile:
                return Response({'detail': 'Caretaker not found.'}, status=status.HTTP_404_NOT_FOUND)
            Review.objects.create(user=request.user, caretaker=car_profile, rating=rating, comment=comment)

            all_reviews = car_profile.reviews.all()
            car_profile.reviews_count = all_reviews.count()
            car_profile.rating_avg = round(sum(r.rating for r in all_reviews) / car_profile.reviews_count, 1)
            car_profile.save()
            return Response({
                'status': 'success',
                'rating_avg': car_profile.rating_avg,
                'reviews_count': car_profile.reviews_count
            })

        return Response({'detail': 'Invalid target type.'}, status=status.HTTP_400_BAD_REQUEST)
