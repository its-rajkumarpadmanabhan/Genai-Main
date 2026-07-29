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
    Appointment, CaretakerRequest, MedicalDocument, EmergencyAlert
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
        user.plain_password = data['password']
        user.save()

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


class UserProfilePictureUploadView(APIView):
    """POST /api/auth/profile/picture — Upload/update profile picture for any authenticated user"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        profile_picture = request.data.get('profile_picture')
        if not profile_picture:
            return Response({'detail': 'No profile picture provided.'}, status=status.HTTP_400_BAD_REQUEST)

        request.user.profile_picture = profile_picture
        request.user.save()
        return Response({
            'status': 'success',
            'message': 'Profile picture updated successfully.',
            'profile_picture': request.user.profile_picture
        })


class PatientVisitedDoctorsView(APIView):
    """GET /api/auth/patient/visited-doctors — Returns all doctors visited/booked by patient with stats"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['patient', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Patient access required.'}, status=status.HTTP_403_FORBIDDEN)

        import datetime
        from .models import Appointment, DoctorProfile

        patient_apts = Appointment.objects.filter(patient=request.user).order_by('-appointment_date', '-created_at')

        results = []
        seen_doctor_ids = set()
        today = datetime.date.today()

        for apt in patient_apts:
            doc_user = apt.doctor
            if doc_user.id in seen_doctor_ids:
                continue
            seen_doctor_ids.add(doc_user.id)

            d_profile = DoctorProfile.objects.filter(user=doc_user).first()
            
            # Fetch all appointments for this patient & doctor pair
            pair_apts = Appointment.objects.filter(patient=request.user, doctor=doc_user)
            total_visits = pair_apts.filter(status__in=['completed', 'accepted']).count()
            if total_visits == 0:
                total_visits = pair_apts.count()

            upcoming_cnt = pair_apts.filter(appointment_date__gte=today, status__in=['pending', 'accepted']).count()

            # Find last visited appointment
            last_apt = pair_apts.filter(status='completed').order_by('-appointment_date', '-created_at').first()
            if not last_apt:
                last_apt = pair_apts.order_by('-appointment_date', '-created_at').first()

            last_visited_str = "No prior visits"
            if last_apt and last_apt.appointment_date:
                last_visited_str = f"{last_apt.appointment_date}"
                if last_apt.time_slot:
                    last_visited_str += f" at {last_apt.time_slot}"

            raw_name = d_profile.full_name if (d_profile and d_profile.full_name) else doc_user.username
            doc_name = raw_name

            doc_item = {
                'doctor_id': doc_user.id,
                'doctor_profile_id': d_profile.id if d_profile else doc_user.id,
                'full_name': doc_name,
                'doctor_code': d_profile.doctor_code if d_profile else 'DOC-000',
                'major_department': d_profile.major_department if d_profile else 'General Medicine',
                'hospital_name': d_profile.hospital_name if d_profile else 'Not specified',
                'location': d_profile.location if d_profile else 'Not specified',
                'profile_picture': doc_user.profile_picture,
                'rating_avg': d_profile.rating_avg if d_profile else 5.0,
                'reviews_count': d_profile.reviews_count if d_profile else 0,
                'total_visits_count': total_visits,
                'upcoming_appointments_count': upcoming_cnt,
                'last_visited_date_time': last_visited_str
            }
            results.append(doc_item)

        return Response(results)


# ── Admin User Management ────────────────────────────────────────────────────
class AdminStatsView(APIView):
    """GET /api/admin/stats — returns genuine live database metrics & analytics"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != 'admin' and not request.user.is_staff:
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        import datetime
        from django.db.models import Avg, Count
        from .models import CustomUser, DoctorProfile, PatientProfile, CaretakerProfile, Appointment, Review

        patients_count = CustomUser.objects.filter(role='patient').count()
        doctors_count = CustomUser.objects.filter(role='doctor').count()
        caretakers_count = CustomUser.objects.filter(role='caretaker').count()
        inactive_count = CustomUser.objects.filter(is_active=False).count()

        # Signups by month (last 6 months)
        months = []
        patient_signups = []
        doctor_signups = []
        caretaker_signups = []
        today = datetime.date.today()

        for i in range(5, -1, -1):
            m_year = today.year
            m_month = today.month - i
            while m_month <= 0:
                m_month += 12
                m_year -= 1
            m_date = datetime.date(m_year, m_month, 1)
            m_str = m_date.strftime('%b')
            months.append(m_str)

            p_cnt = CustomUser.objects.filter(role='patient', created_at__year=m_year, created_at__month=m_month).count()
            d_cnt = CustomUser.objects.filter(role='doctor', created_at__year=m_year, created_at__month=m_month).count()
            c_cnt = CustomUser.objects.filter(role='caretaker', created_at__year=m_year, created_at__month=m_month).count()

            patient_signups.append(p_cnt)
            doctor_signups.append(d_cnt)
            caretaker_signups.append(c_cnt)

        # Appointments per week (last 5 weeks)
        week_labels = []
        week_values = []
        for i in range(4, -1, -1):
            start_w = today - datetime.timedelta(days=(i+1)*7)
            end_w = today - datetime.timedelta(days=i*7)
            w_label = f"Wk {5-i}"
            w_cnt = Appointment.objects.filter(appointment_date__gte=start_w, appointment_date__lt=end_w).count()
            week_labels.append(w_label)
            week_values.append(w_cnt)

        # Doctors by specialty (major_department)
        dept_qs = DoctorProfile.objects.values('major_department').annotate(cnt=Count('id')).order_by('-cnt')
        by_dept = {}
        for item in dept_qs:
            dept_name = item['major_department'] or 'General Medicine'
            by_dept[dept_name] = item['cnt']
        if not by_dept:
            by_dept = {'General Medicine': doctors_count}

        # Patients by location
        loc_qs = PatientProfile.objects.values('location').annotate(cnt=Count('id')).order_by('-cnt')[:6]
        by_location = {}
        for item in loc_qs:
            loc_name = item['location'] or 'Not specified'
            by_location[loc_name] = item['cnt']
        if not by_location:
            by_location = {'Primary Location': patients_count}

        # Genuine reviews & ratings
        reviews_qs = Review.objects.all().order_by('-created_at')
        reviews_list = []
        for r in reviews_qs:
            target_name = "System"
            target_type = "doctor"
            if r.doctor:
                target_name = r.doctor.full_name or r.doctor.user.username
                target_type = "doctor"
            elif r.caretaker:
                target_name = r.caretaker.full_name or r.caretaker.user.username
                target_type = "caretaker"

            reviews_list.append({
                'id': r.id,
                'reviewer': r.user.username,
                'targetType': target_type,
                'target': target_name,
                'rating': r.rating,
                'text': r.comment or '',
                'date': r.created_at.strftime('%d %b %Y')
            })

        avg_rating = Review.objects.aggregate(Avg('rating'))['rating__avg'] or 5.0
        doc_avg = DoctorProfile.objects.aggregate(Avg('rating_avg'))['rating_avg__avg'] or 5.0
        car_avg = CaretakerProfile.objects.aggregate(Avg('rating_avg'))['rating_avg__avg'] or 5.0

        # Monthly Patients per Doctor Breakdown (last 6 months)
        doctor_monthly_patients = []
        doc_users = CustomUser.objects.filter(role='doctor').order_by('id')[:50]  # Top doctors
        
        for d_user in doc_users:
            d_prof = getattr(d_user, 'doctor_profile', None)
            d_apts = Appointment.objects.filter(doctor=d_user)
            total_unique_all_time = d_apts.values('patient_id').distinct().count()

            m_stats = []
            for i in range(5, -1, -1):
                m_year = today.year
                m_month = today.month - i
                while m_month <= 0:
                    m_month += 12
                    m_year -= 1
                m_date = datetime.date(m_year, m_month, 1)
                m_label = m_date.strftime('%b %Y')

                m_apts = d_apts.filter(appointment_date__year=m_year, appointment_date__month=m_month)
                m_unique = m_apts.values('patient_id').distinct().count()
                m_total = m_apts.count()

                m_stats.append({
                    'month_label': m_label,
                    'year': m_year,
                    'month': m_month,
                    'unique_patients': m_unique,
                    'total_appointments': m_total
                })

            raw_name = d_prof.full_name if (d_prof and d_prof.full_name) else d_user.username
            doc_name = re.sub(r'^(Dr\.|DR\.|Dr|DR)\s*', '', raw_name, flags=re.IGNORECASE).strip()

            doctor_monthly_patients.append({
                'doctor_id': d_user.id,
                'doctor_name': doc_name,
                'doctor_code': d_prof.doctor_code if d_prof else 'DOC-000',
                'major_department': d_prof.major_department if d_prof else 'General Medicine',
                'hospital_name': d_prof.hospital_name if d_prof else 'Hospital / Clinic',
                'location': d_prof.location if d_prof else 'Not specified',
                'total_unique_patients_all_time': total_unique_all_time,
                'monthly_stats': m_stats
            })

        return Response({
            'patients_count': patients_count,
            'doctors_count': doctors_count,
            'caretakers_count': caretakers_count,
            'inactive_count': inactive_count,
            'appointments_count': Appointment.objects.count(),
            'signup_data': {
                'months': months,
                'patients': patient_signups,
                'doctors': doctor_signups,
                'caretakers': caretaker_signups,
            },
            'appt_week_data': {
                'labels': week_labels,
                'values': week_values,
            },
            'by_dept': by_dept,
            'by_location': by_location,
            'reviews': reviews_list,
            'reviews_count': len(reviews_list),
            'avg_rating': round(float(avg_rating), 1),
            'doctor_avg_rating': round(float(doc_avg), 1),
            'caretaker_avg_rating': round(float(car_avg), 1),
            'doctor_monthly_patients': doctor_monthly_patients
        })


class AdminUserProfileView(APIView):
    """GET /api/admin/users/<int:user_id>/profile — Admin views complete user profile details"""
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        if request.user.role != 'admin' and not request.user.is_staff:
            return Response({'detail': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        target_user = CustomUser.objects.filter(id=user_id).first()
        if not target_user:
            return Response({'detail': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        user_data = {
            'id': target_user.id,
            'username': target_user.username,
            'email': target_user.email,
            'mobile_number': target_user.mobile_number,
            'role': target_user.role,
            'plain_password': target_user.plain_password or '••••••••',
            'profile_picture': target_user.profile_picture,
            'is_active': target_user.is_active,
            'created_at': target_user.created_at.isoformat() if target_user.created_at else None,
            'profile_details': {}
        }

        if target_user.role == 'patient':
            from .serializers import PatientProfileSerializer
            p_prof = getattr(target_user, 'patient_profile', None)
            if p_prof:
                user_data['profile_details'] = PatientProfileSerializer(p_prof).data
        elif target_user.role == 'doctor':
            from .serializers import DoctorProfileSerializer
            d_prof = getattr(target_user, 'doctor_profile', None)
            if d_prof:
                user_data['profile_details'] = DoctorProfileSerializer(d_prof).data
        elif target_user.role == 'caretaker':
            from .serializers import CaretakerProfileSerializer
            c_prof = getattr(target_user, 'caretaker_profile', None)
            if c_prof:
                user_data['profile_details'] = CaretakerProfileSerializer(c_prof).data

        return Response(user_data)


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

        from .models import DoctorProfile, PatientProfile, CaretakerProfile

        new_username = request.data.get('username')
        new_mobile = request.data.get('mobile_number')
        new_role = request.data.get('role')

        if new_username:
            user.username = new_username
            PatientProfile.objects.filter(user=user).update(full_name=new_username)
            DoctorProfile.objects.filter(user=user).update(full_name=new_username)
            CaretakerProfile.objects.filter(user=user).update(full_name=new_username)

        if 'email' in request.data:
            user.email = request.data['email']

        if new_mobile is not None:
            user.mobile_number = new_mobile
            PatientProfile.objects.filter(user=user).update(phone_number=new_mobile)
            DoctorProfile.objects.filter(user=user).update(phone_number=new_mobile)
            CaretakerProfile.objects.filter(user=user).update(phone_number=new_mobile)

        if new_role:
            user.role = new_role
            if new_role == 'patient':
                PatientProfile.objects.get_or_create(user=user, defaults={'full_name': user.username, 'phone_number': user.mobile_number})
            elif new_role == 'doctor':
                DoctorProfile.objects.get_or_create(user=user, defaults={'full_name': user.username, 'phone_number': user.mobile_number})
            elif new_role == 'caretaker':
                CaretakerProfile.objects.get_or_create(user=user, defaults={'full_name': user.username, 'phone_number': user.mobile_number})

        if 'is_active' in request.data:
            user.is_active = bool(request.data['is_active'])

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
        profile, _ = PatientProfile.objects.get_or_create(
            user=request.user,
            defaults={'full_name': request.user.username, 'phone_number': request.user.mobile_number}
        )
        if not profile.full_name:
            profile.full_name = request.user.username
            profile.save()
        if not profile.phone_number and request.user.mobile_number:
            profile.phone_number = request.user.mobile_number
            profile.save()
        if not profile.email and request.user.email:
            profile.email = request.user.email
            profile.save()
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

    def delete(self, request, doc_id=None):
        from .models import MedicalDocument
        target_id = doc_id or request.data.get('doc_id') or request.query_params.get('doc_id')
        if not target_id:
            return Response({'detail': 'Document ID is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        deleted_count, _ = MedicalDocument.objects.filter(id=target_id, patient=request.user).delete()
        if deleted_count > 0:
            return Response({'status': 'success', 'message': 'Medical document deleted successfully.'})
        return Response({'detail': 'Document not found or access denied.'}, status=status.HTTP_404_NOT_FOUND)


# ── Directory & Caretaker Request Views ──────────────────────────────────────
class DirectoryListView(APIView):
    """GET /api/directory/search?type=doctor|caretaker&location=&gender=&dept="""
    permission_classes = [AllowAny]

    def get(self, request):
        dir_type = request.query_params.get('type')
        location = request.query_params.get('location', '').strip().lower()
        gender = request.query_params.get('gender', '').strip().lower()
        dept = request.query_params.get('dept', '').strip().lower()
        experience = request.query_params.get('experience', '').strip()

        from .models import DoctorProfile, CaretakerProfile
        from .serializers import DoctorProfileSerializer, CaretakerProfileSerializer

        def filter_experience(queryset, val):
            if val == '1-3':
                return queryset.filter(experience_years__range=(1, 3))
            elif val == '4-6':
                return queryset.filter(experience_years__range=(4, 6))
            elif val == '7+':
                return queryset.filter(experience_years__gte=7)
            return queryset

        from django.db.models import Q

        if dir_type == 'doctor':
            qs = DoctorProfile.objects.all()
            if location:
                qs = qs.filter(Q(location__icontains=location) | Q(state__icontains=location) | Q(hospital_name__icontains=location))
            if gender:
                qs = qs.filter(gender__iexact=gender)
            if dept:
                qs = qs.filter(major_department__icontains=dept)
            if experience:
                qs = filter_experience(qs, experience)
            return Response(DoctorProfileSerializer(qs, many=True).data)
        elif dir_type == 'caretaker':
            qs = CaretakerProfile.objects.all()
            if location:
                qs = qs.filter(location__icontains=location)
            if gender:
                qs = qs.filter(gender__iexact=gender)
            if experience:
                qs = filter_experience(qs, experience)
            return Response(CaretakerProfileSerializer(qs, many=True).data)
        else:
            d_qs = DoctorProfile.objects.all()
            c_qs = CaretakerProfile.objects.all()
            if location:
                d_qs = d_qs.filter(Q(location__icontains=location) | Q(state__icontains=location) | Q(hospital_name__icontains=location))
                c_qs = c_qs.filter(location__icontains=location)
            if gender:
                d_qs = d_qs.filter(gender__iexact=gender)
                c_qs = c_qs.filter(gender__iexact=gender)
            if dept:
                d_qs = d_qs.filter(major_department__icontains=dept)
            if experience:
                d_qs = filter_experience(d_qs, experience)
                c_qs = filter_experience(c_qs, experience)
            return Response({
                'doctors': DoctorProfileSerializer(d_qs, many=True).data,
                'caretakers': CaretakerProfileSerializer(c_qs, many=True).data
            })


class CaretakerRequestView(APIView):
    """POST /api/patient/caretaker-request"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import CaretakerRequest, CaretakerProfile
        caretaker_id = request.data.get('caretaker_id')
        
        caretaker = User.objects.filter(id=caretaker_id, role='caretaker').first()
        if not caretaker:
            c_profile = CaretakerProfile.objects.filter(id=caretaker_id).first()
            if c_profile:
                caretaker = c_profile.user
        
        if not caretaker:
            c_profile = CaretakerProfile.objects.filter(user_id=caretaker_id).first()
            if c_profile:
                caretaker = c_profile.user

        if not caretaker:
            return Response({'detail': 'Caretaker not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Check if patient already has an active caretaker
        from .models import PatientProfile
        p_profile = PatientProfile.objects.filter(user=request.user).first()
        if p_profile and p_profile.assigned_caretaker:
            return Response({'detail': 'You already have an active caretaker assigned. You cannot send new requests until you unlink the current caretaker.'}, status=status.HTTP_400_BAD_REQUEST)

        # Check if target caretaker already has an active patient
        active_patient_for_caretaker = PatientProfile.objects.filter(assigned_caretaker=caretaker).first()
        if active_patient_for_caretaker:
            return Response({'detail': 'This caretaker already has an active patient under care. A caretaker can only have 1 active patient at a time.'}, status=status.HTTP_400_BAD_REQUEST)

        # Enforce limit of Max 5 pending caretaker requests
        active_and_pending_count = CaretakerRequest.objects.filter(
            patient=request.user,
            status='pending'
        ).count()
        if active_and_pending_count >= 5:
            return Response({'detail': 'You cannot send requests to more than 5 caretakers at a time.'}, status=status.HTTP_400_BAD_REQUEST)

        req_obj = CaretakerRequest.objects.filter(patient=request.user, caretaker=caretaker).first()
        if not req_obj:
            req_obj = CaretakerRequest.objects.create(patient=request.user, caretaker=caretaker, status='pending')
        else:
            req_obj.status = 'pending'
            req_obj.save()

        return Response({'status': 'success', 'request_id': req_obj.id, 'status_text': 'pending'})



class AppointmentBookingView(APIView):
    """POST /api/appointments/book"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import Appointment, DoctorProfile
        doctor_id = request.data.get('doctor_id')
        
        doctor = User.objects.filter(id=doctor_id, role='doctor').first()
        if not doctor:
            d_profile = DoctorProfile.objects.filter(id=doctor_id).first()
            if d_profile:
                doctor = d_profile.user
        
        if not doctor:
            d_profile = DoctorProfile.objects.filter(user_id=doctor_id).first()
            if d_profile:
                doctor = d_profile.user

        if not doctor:
            return Response({'detail': 'Doctor not found.'}, status=status.HTTP_404_NOT_FOUND)

        app_date = request.data.get('appointment_date')
        time_slot = request.data.get('time_slot', '10:00 AM')
        reason = request.data.get('reason', '')
        booked_by = request.data.get('booked_by', 'patient')
        appointment_type = request.data.get('appointment_type', 'offline')

        patient_user = request.user
        if booked_by == 'caretaker' and request.data.get('patient_id'):
            patient_user = User.objects.filter(id=request.data.get('patient_id')).first() or request.user

        # Enforce rule: Appointments can only be booked for the current week (Monday to Sunday)
        if app_date:
            import datetime
            today_d = datetime.date.today()
            start_of_week = today_d - datetime.timedelta(days=today_d.weekday())
            end_of_week = start_of_week + datetime.timedelta(days=6)

            parsed_d = None
            if isinstance(app_date, str):
                try:
                    parsed_d = datetime.datetime.strptime(app_date, '%Y-%m-%d').date()
                except ValueError:
                    parsed_d = None
            else:
                parsed_d = app_date

            if parsed_d:
                if parsed_d < start_of_week or parsed_d > end_of_week:
                    return Response({
                        'detail': f'Appointments can only be booked for the current week ({start_of_week.strftime("%d %b %Y")} to {end_of_week.strftime("%d %b %Y")}). Booking for next week, next month, or future years is not allowed.'
                    }, status=status.HTTP_400_BAD_REQUEST)

        # Enforce rule: 1 active appointment per doctor per day
        if app_date:
            active_apt = Appointment.objects.filter(
                patient=patient_user,
                doctor=doctor,
                appointment_date=app_date,
                status__in=['pending', 'accepted', 'completed']
            ).first()
            if active_apt:
                doc_p = DoctorProfile.objects.filter(user=doctor).first()
                d_name = doc_p.full_name if (doc_p and doc_p.full_name) else doctor.username
                return Response({
                    'detail': f'You already have an active appointment scheduled with Dr. {d_name} on {app_date}. You can only book a new appointment on this day if your current appointment is cancelled or missed.'
                }, status=status.HTTP_400_BAD_REQUEST)

        # Enforce rule: Patient cannot attend multiple appointments at exact same date and time slot
        if app_date and time_slot:
            patient_time_conflict = Appointment.objects.filter(
                patient=patient_user,
                appointment_date=app_date,
                time_slot=time_slot,
                status__in=['pending', 'accepted', 'completed']
            ).first()
            if patient_time_conflict:
                return Response({
                    'detail': f'You already have another appointment scheduled at {time_slot} on {app_date}. A patient cannot attend multiple doctor appointments at the same time.'
                }, status=status.HTTP_400_BAD_REQUEST)

        # Enforce rule: Doctor cannot have 2 patients booked at exact same date and time slot
        if app_date and time_slot:
            doc_time_conflict = Appointment.objects.filter(
                doctor=doctor,
                appointment_date=app_date,
                time_slot=time_slot,
                status__in=['pending', 'accepted', 'completed']
            ).first()
            if doc_time_conflict:
                return Response({
                    'detail': f'This time slot ({time_slot}) is already booked for this doctor on {app_date}. Please choose a different available time slot.'
                }, status=status.HTTP_400_BAD_REQUEST)

        # Enforce rule: Patient cannot visit multiple doctors for same medical condition/specialty on same day
        if app_date:
            target_doc_prof = DoctorProfile.objects.filter(user=doctor).first()
            target_dept = (target_doc_prof.major_department or 'General Medicine').strip().lower() if target_doc_prof else 'general medicine'

            same_day_apts = Appointment.objects.filter(
                patient=patient_user,
                appointment_date=app_date,
                status__in=['pending', 'accepted', 'completed']
            )

            for ex_apt in same_day_apts:
                ex_doc_prof = DoctorProfile.objects.filter(user=ex_apt.doctor).first()
                ex_dept = (ex_doc_prof.major_department or 'General Medicine').strip().lower() if ex_doc_prof else 'general medicine'

                same_reason = (reason and ex_apt.reason and reason.strip().lower() == ex_apt.reason.strip().lower())
                same_dept = (ex_dept == target_dept)

                if same_reason or same_dept:
                    dept_display = target_doc_prof.major_department if (target_doc_prof and target_doc_prof.major_department) else 'this specialty'
                    return Response({
                        'detail': f'You already have an appointment scheduled for {dept_display} on {app_date}. A patient cannot visit multiple doctors for the same medical condition or specialty on the same day.'
                    }, status=status.HTTP_400_BAD_REQUEST)

        apt = Appointment.objects.create(
            patient=patient_user,
            doctor=doctor,
            booked_by=booked_by,
            caretaker=request.user if booked_by == 'caretaker' else None,
            appointment_date=app_date,
            time_slot=time_slot,
            reason=reason,
            appointment_type=appointment_type,
            status='pending'
        )
        return Response({'status': 'success', 'appointment_id': apt.id}, status=status.HTTP_201_CREATED)


class AppointmentCancelView(APIView):
    """POST /api/auth/appointments/<int:apt_id>/cancel — Patient or Caretaker cancels an appointment"""
    permission_classes = [IsAuthenticated]

    def post(self, request, apt_id):
        from .models import Appointment, PatientProfile
        apt = Appointment.objects.filter(id=apt_id).first()
        if not apt:
            return Response({'detail': 'Appointment not found.'}, status=status.HTTP_404_NOT_FOUND)

        is_patient = (apt.patient == request.user)
        is_caretaker = (apt.caretaker == request.user)
        if not is_caretaker:
            p_prof = PatientProfile.objects.filter(user=apt.patient, assigned_caretaker=request.user).first()
            if p_prof:
                is_caretaker = True

        if not (is_patient or is_caretaker or request.user.role in ['admin', 'doctor'] or request.user.is_staff):
            return Response({'detail': 'You do not have permission to cancel this appointment.'}, status=status.HTTP_403_FORBIDDEN)

        if apt.status in ['cancelled_by_patient', 'cancelled_by_doctor', 'rejected']:
            return Response({'detail': 'Appointment is already cancelled.'}, status=status.HTTP_400_BAD_REQUEST)

        apt.status = 'cancelled_by_patient'
        apt.is_call_active = False
        apt.save()

        return Response({
            'status': 'success',
            'message': 'Appointment cancelled successfully.',
            'appointment_id': apt.id,
            'new_status': apt.status
        })


# ── Doctor Profile & Appointments ────────────────────────────────────────────
class DoctorProfileView(APIView):
    """GET/PUT /api/doctor/profile"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import DoctorProfile
        from .serializers import DoctorProfileSerializer
        profile, _ = DoctorProfile.objects.get_or_create(
            user=request.user,
            defaults={'full_name': request.user.username, 'phone_number': request.user.mobile_number}
        )
        if not profile.full_name:
            profile.full_name = request.user.username
            profile.save()
        if not profile.phone_number and request.user.mobile_number:
            profile.phone_number = request.user.mobile_number
            profile.save()
        from .models import Review
        data = DoctorProfileSerializer(profile).data
        reviews_qs = Review.objects.filter(doctor=profile).order_by('-created_at')
        rev_list = []
        for r in reviews_qs:
            rev_list.append({
                'id': r.id,
                'reviewer_name': r.user.username,
                'rating': r.rating,
                'comment': r.comment or '',
                'date': r.created_at.strftime('%d %b %Y') if r.created_at else 'Recent'
            })
        data['reviews'] = rev_list
        return Response(data)

    def put(self, request):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import DoctorProfile, Appointment
        from .serializers import DoctorProfileSerializer
        import datetime
        profile, _ = DoctorProfile.objects.get_or_create(user=request.user)
        old_availability = profile.availability_status
        for k, v in request.data.items():
            if hasattr(profile, k):
                setattr(profile, k, v)
        profile.save()
        
        if old_availability != 'On Leave Today' and profile.availability_status == 'On Leave Today':
            today = datetime.date.today()
            today_apts = Appointment.objects.filter(
                doctor=request.user,
                appointment_date=today,
                status__in=['pending', 'accepted']
            )
            for apt in today_apts:
                apt.status = 'cancelled_by_doctor'
                apt.save()
                
        return Response(DoctorProfileSerializer(profile).data)


class DoctorAppointmentsView(APIView):
    """GET /api/doctor/appointments, POST status (accept/reject), POST checkup (completed/missed)"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import Appointment
        from .serializers import AppointmentSerializer
        from django.db.models import Case, When, Value, IntegerField
        apts = Appointment.objects.filter(doctor=request.user).order_by('-appointment_date', '-created_at')
        return Response(AppointmentSerializer(apts, many=True).data)

    def post(self, request):
        from .models import Appointment
        apt_id = request.data.get('appointment_id')
        new_status = request.data.get('status')
        apt = Appointment.objects.filter(id=apt_id, doctor=request.user).first()
        if not apt:
            return Response({'detail': 'Appointment not found.'}, status=status.HTTP_404_NOT_FOUND)

        if new_status in ['accepted', 'rejected', 'completed', 'missed']:
            old_status = apt.status
            apt.status = new_status
            if new_status in ['completed', 'missed', 'rejected']:
                apt.is_call_active = False
            if request.data.get('doctor_notes'):
                apt.doctor_notes = request.data.get('doctor_notes')
            if 'prescription_pdf' in request.data:
                apt.prescription_pdf = request.data.get('prescription_pdf')
            if 'prescription_name' in request.data:
                apt.prescription_name = request.data.get('prescription_name')
            apt.save()

            if new_status == 'accepted' and old_status != 'accepted':
                # Fetch details for the email
                from django.core.mail import send_mail
                from .models import DoctorProfile
                
                doc_profile = DoctorProfile.objects.filter(user=apt.doctor).first()
                doc_name = doc_profile.full_name if (doc_profile and doc_profile.full_name) else apt.doctor.username
                hospital = doc_profile.hospital_name if doc_profile else "Not specified"
                location = doc_profile.location if doc_profile else "Not specified"
                patient_name = apt.patient.username
                recipient_email = apt.patient.email

                # Fetch patient profile name and email if available
                from .models import PatientProfile
                pat_profile = PatientProfile.objects.filter(user=apt.patient).first()
                if pat_profile:
                    if pat_profile.full_name:
                        patient_name = pat_profile.full_name
                    if pat_profile.email:
                        recipient_email = pat_profile.email

                subject = f"Appointment Confirmed: Dr. {doc_name}"
                mode_str = "Online (Video Call)" if apt.appointment_type == "online" else "Offline (In-Person Clinic Visit)"
                message = (
                    f"Dear {patient_name},\n\n"
                    f"Your appointment request has been accepted. Here are the confirmation details:\n\n"
                    f"Doctor: Dr. {doc_name}\n"
                    f"Hospital: {hospital}\n"
                    f"Location: {location}\n"
                    f"Date: {apt.appointment_date}\n"
                    f"Time Slot: {apt.time_slot}\n"
                    f"Appointment Type: {mode_str}\n"
                    f"Reason for Visit: {apt.reason or 'General Consultation'}\n\n"
                    f"Thank you,\n"
                    f"Beacon Health Support"
                )
                try:
                    send_mail(
                        subject,
                        message,
                        None, # Uses DEFAULT_FROM_EMAIL
                        [recipient_email],
                        fail_silently=False,
                    )
                except Exception as e:
                    print("Error sending appointment confirmation email:", e)

            return Response({'status': 'success', 'new_status': apt.status})
        return Response({'detail': 'Invalid status.'}, status=status.HTTP_400_BAD_REQUEST)


class AppointmentStartCallView(APIView):
    """POST /api/auth/appointments/<int:apt_id>/start-call"""
    permission_classes = [IsAuthenticated]

    def post(self, request, apt_id):
        from .models import Appointment
        apt = Appointment.objects.filter(id=apt_id, doctor=request.user).first()
        if not apt:
            return Response({'detail': 'Appointment not found or you are not the doctor.'}, status=status.HTTP_404_NOT_FOUND)
        apt.is_call_active = True
        apt.save()
        return Response({'status': 'success', 'is_call_active': True})


class AppointmentEndCallView(APIView):
    """POST /api/auth/appointments/<int:apt_id>/end-call"""
    permission_classes = [IsAuthenticated]

    def post(self, request, apt_id):
        from .models import Appointment
        apt = Appointment.objects.filter(id=apt_id, doctor=request.user).first()
        if not apt:
            return Response({'detail': 'Appointment not found or you are not the doctor.'}, status=status.HTTP_404_NOT_FOUND)
        apt.is_call_active = False
        apt.save()
        return Response({'status': 'success', 'is_call_active': False})


class AppointmentToggleCaretakerCallView(APIView):
    """POST /api/auth/appointments/<int:apt_id>/toggle-caretaker-call"""
    permission_classes = [IsAuthenticated]

    def post(self, request, apt_id):
        from .models import Appointment
        apt = Appointment.objects.filter(id=apt_id, doctor=request.user).first()
        if not apt:
            return Response({'detail': 'Appointment not found or you are not the doctor.'}, status=status.HTTP_404_NOT_FOUND)
        
        apt.is_caretaker_added_to_call = not apt.is_caretaker_added_to_call
        apt.save()
        return Response({
            'status': 'success',
            'is_caretaker_added_to_call': apt.is_caretaker_added_to_call
        })


class AppointmentJoinVideoCallView(APIView):
    """GET /api/auth/appointments/<int:apt_id>/join-video-call"""
    permission_classes = [AllowAny]

    def get(self, request, apt_id):
        from .models import Appointment
        from rest_framework_simplejwt.authentication import JWTAuthentication
        from django.shortcuts import redirect
        from django.http import HttpResponseForbidden

        # 1. Authenticate user
        user = None
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        token = request.GET.get('token')

        if auth_header:
            try:
                validated_token = JWTAuthentication().get_validated_token(auth_header.split()[1])
                user = JWTAuthentication().get_user(validated_token)
            except Exception:
                pass
        elif token:
            try:
                validated_token = JWTAuthentication().get_validated_token(token)
                user = JWTAuthentication().get_user(validated_token)
            except Exception:
                pass

        if not user or not user.is_authenticated:
            return HttpResponseForbidden("Authentication credentials were not provided or are invalid.")

        # 2. Retrieve appointment
        apt = Appointment.objects.filter(id=apt_id).first()
        if not apt:
            return HttpResponseForbidden("Appointment not found.")

        # 3. Check access permissions
        is_doctor = (apt.doctor == user)
        is_patient = (apt.patient == user)
        
        # Check if user is the assigned caretaker of the patient
        is_caretaker = False
        if apt.caretaker == user:
            is_caretaker = True
        elif hasattr(apt.patient, 'patient_profile') and apt.patient.patient_profile.assigned_caretaker == user:
            is_caretaker = True

        if is_doctor:
            # Doctor can always join/initiate
            pass
        elif is_patient:
            # Patient can only join if the doctor started the call
            if not apt.is_call_active:
                return HttpResponseForbidden("This video call session has not been started by the doctor yet.")
        elif is_caretaker:
            # Caretaker can join if call is active
            if not apt.is_call_active:
                return HttpResponseForbidden("This video call session has not been started by the doctor yet.")
        else:
            return HttpResponseForbidden("You do not have permission to join this video call session.")

        # 4. Redirect to Jitsi Meet if authorized
        meeting_url = f"https://meet.jit.si/BeaconRoom-{apt.id}"
        return redirect(meeting_url)


class DoctorPatientsListView(APIView):
    """GET /api/doctor/patients — Returns patients with appointments for this doctor, with stats and caretaker details."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        import datetime
        from .models import Appointment, PatientProfile, CaretakerProfile

        all_doc_apts = Appointment.objects.filter(doctor=request.user).order_by('-appointment_date', '-created_at')

        results = []
        seen_patient_ids = set()
        today = datetime.date.today()

        for apt in all_doc_apts:
            patient_user = apt.patient
            if patient_user.id in seen_patient_ids:
                continue
            seen_patient_ids.add(patient_user.id)

            p_profile = PatientProfile.objects.filter(user=patient_user).first()
            
            # Fetch all appointments for this specific doctor & patient pair
            patient_apts = Appointment.objects.filter(doctor=request.user, patient=patient_user)
            total_apts = patient_apts.count()
            online_cnt = patient_apts.filter(appointment_type='online').count()
            offline_cnt = patient_apts.exclude(appointment_type='online').count()
            upcoming_cnt = patient_apts.filter(appointment_date__gte=today, status__in=['pending', 'accepted']).count()

            # Find last appointment (most recent past or completed appointment, or latest appointment)
            last_apt = patient_apts.filter(appointment_date__lt=today).order_by('-appointment_date', '-created_at').first()
            if not last_apt:
                last_apt = patient_apts.order_by('-appointment_date', '-created_at').first()
            
            last_apt_str = "No prior visits"
            if last_apt and last_apt.appointment_date:
                last_apt_str = f"{last_apt.appointment_date}"
                if last_apt.time_slot:
                    last_apt_str += f" at {last_apt.time_slot}"

            caretaker_data = None
            if p_profile and p_profile.assigned_caretaker:
                c_user = p_profile.assigned_caretaker
                c_profile = CaretakerProfile.objects.filter(user=c_user).first()
                caretaker_data = {
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

            patient_item = {
                'patient_id': patient_user.id,
                'username': patient_user.username,
                'email': patient_user.email,
                'mobile_number': patient_user.mobile_number,
                'full_name': (p_profile.full_name if (p_profile and p_profile.full_name) else patient_user.username),
                'patient_code': p_profile.patient_code if p_profile else 'PAT-000',
                'dob': str(p_profile.dob) if (p_profile and p_profile.dob) else 'Not provided',
                'gender': p_profile.gender if p_profile else 'Not provided',
                'location': p_profile.location if p_profile else 'Not provided',
                'languages': p_profile.languages if p_profile else 'Not provided',
                'insurance_details': p_profile.insurance_details if p_profile else 'Not provided',
                'emergency_contact': f"{p_profile.emergency_contact_name or ''} ({p_profile.emergency_contact_phone or ''})".strip() if p_profile else 'Not provided',
                'medical_history_notes': p_profile.medical_history_notes if p_profile else 'No medical condition specified yet',
                'appointment_status': apt.status,
                'appointment_date': str(apt.appointment_date) if apt.appointment_date else 'TBD',
                'reason': apt.reason or 'General consultation',
                'total_appointments': total_apts,
                'online_appointments_count': online_cnt,
                'offline_appointments_count': offline_cnt,
                'upcoming_appointments_count': upcoming_cnt,
                'last_appointment_date_time': last_apt_str,
                'assigned_caretaker': caretaker_data
            }
            results.append(patient_item)

        return Response(results)


class DoctorPatientRecordsView(APIView):
    """GET /api/doctor/patients/<id>/records — read-only access for booked patients and assigned caretakers"""
    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        from .models import Appointment, MedicalDocument, PatientProfile
        from .serializers import MedicalDocumentSerializer
        
        has_apt = Appointment.objects.filter(doctor=request.user, patient_id=patient_id).exists()
        is_caretaker = PatientProfile.objects.filter(user_id=patient_id, assigned_caretaker=request.user).exists()
        is_self = (request.user.id == patient_id)

        if not (has_apt or is_caretaker or is_self or request.user.role == 'admin' or request.user.is_staff):
            return Response({'detail': 'Access allowed for assigned patients/caretakers only.'}, status=status.HTTP_403_FORBIDDEN)

        docs = MedicalDocument.objects.filter(patient_id=patient_id)
        return Response(MedicalDocumentSerializer(docs, many=True).data)


class DoctorSinglePatientView(APIView):
    """GET /api/doctor/patients/<int:patient_id> — Returns detailed patient profile for Doctor Modal view."""
    permission_classes = [IsAuthenticated]

    def get(self, request, patient_id):
        if request.user.role not in ['doctor', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Doctor access required.'}, status=status.HTTP_403_FORBIDDEN)

        from .models import Appointment, PatientProfile, CaretakerProfile

        patient_user = User.objects.filter(id=patient_id).first()
        if not patient_user:
            return Response({'detail': 'Patient not found.'}, status=status.HTTP_404_NOT_FOUND)

        p_profile = PatientProfile.objects.filter(user=patient_user).first()
        
        caretaker_data = None
        if p_profile and p_profile.assigned_caretaker:
            c_user = p_profile.assigned_caretaker
            c_profile = CaretakerProfile.objects.filter(user=c_user).first()
            caretaker_data = {
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

        last_apt = Appointment.objects.filter(doctor=request.user, patient=patient_user).order_by('-created_at').first()

        res_data = {
            'patient_id': patient_user.id,
            'username': patient_user.username,
            'email': patient_user.email,
            'mobile_number': patient_user.mobile_number,
            'full_name': (p_profile.full_name if (p_profile and p_profile.full_name) else patient_user.username),
            'patient_code': p_profile.patient_code if p_profile else 'PAT-000',
            'dob': str(p_profile.dob) if (p_profile and p_profile.dob) else 'Not provided',
            'gender': p_profile.gender if p_profile else 'Not provided',
            'marital_status': p_profile.marital_status if p_profile else 'Not provided',
            'location': p_profile.location if p_profile else 'Not provided',
            'languages': p_profile.languages if p_profile else 'Not provided',
            'insurance_details': p_profile.insurance_details if p_profile else 'Not provided',
            'emergency_contact': f"{p_profile.emergency_contact_name or ''} ({p_profile.emergency_contact_phone or ''})".strip() if p_profile else 'Not provided',
            'medical_history_notes': p_profile.medical_history_notes if p_profile else 'No medical condition specified yet',
            'appointment_status': last_apt.status if last_apt else 'accepted',
            'appointment_date': str(last_apt.appointment_date) if (last_apt and last_apt.appointment_date) else 'TBD',
            'reason': last_apt.reason if last_apt else 'General consultation',
            'assigned_caretaker': caretaker_data
        }
        return Response(res_data)


# ── Caretaker Profile & Requests ─────────────────────────────────────────────
class CaretakerProfileView(APIView):
    """GET/PUT /api/caretaker/profile"""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['caretaker', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Caretaker access required.'}, status=status.HTTP_403_FORBIDDEN)
        from .models import CaretakerProfile
        from .serializers import CaretakerProfileSerializer
        profile, _ = CaretakerProfile.objects.get_or_create(
            user=request.user,
            defaults={'full_name': request.user.username, 'phone_number': request.user.mobile_number}
        )
        if not profile.full_name:
            profile.full_name = request.user.username
            profile.save()
        if not profile.phone_number and request.user.mobile_number:
            profile.phone_number = request.user.mobile_number
            profile.save()
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
    """GET/POST /api/caretaker/requests — returns care requests & connected patient doctor appointments."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['caretaker', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Caretaker access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        from .models import CaretakerRequest, Appointment, PatientProfile
        from .serializers import CaretakerRequestSerializer, AppointmentSerializer
        from django.db.models import Q

        # 1. Fetch Caretaker Requests (Patient -> Caretaker connection requests)
        reqs = CaretakerRequest.objects.filter(caretaker=request.user).order_by('-created_at')
        req_data = CaretakerRequestSerializer(reqs, many=True).data
        for r in req_data:
            r['item_type'] = 'caretaker_request'

        # 2. Fetch Doctor Appointments for assigned connected patients & appointments booked by this caretaker
        assigned_patients = PatientProfile.objects.filter(assigned_caretaker=request.user).values_list('user_id', flat=True)
        apts = Appointment.objects.filter(
            Q(caretaker=request.user) | Q(patient_id__in=assigned_patients)
        ).order_by('-appointment_date', '-time_slot', '-created_at')

        apt_data = AppointmentSerializer(apts, many=True).data
        for a in apt_data:
            a['item_type'] = 'doctor_appointment'

        # Return both categorized requests and doctor appointments
        return Response({
            'requests': req_data,
            'appointments': apt_data
        })

    def post(self, request):
        from .models import CaretakerRequest, PatientProfile
        req_id = request.data.get('request_id')
        new_status = request.data.get('status')
        creq = CaretakerRequest.objects.filter(id=req_id, caretaker=request.user).first()
        if not creq:
            return Response({'detail': 'Request not found.'}, status=status.HTTP_404_NOT_FOUND)

        if new_status in ['accepted', 'rejected']:
            if new_status == 'accepted':
                # Enforce limit: max 1 active patient
                active_count = PatientProfile.objects.filter(assigned_caretaker=request.user).count()
                if active_count >= 1:
                    return Response({'detail': 'You cannot accept more than 1 patient under your care. Please unlink your current patient before accepting another.'}, status=status.HTTP_400_BAD_REQUEST)

            creq.status = new_status
            creq.save()
            if new_status == 'accepted':
                PatientProfile.objects.filter(user=creq.patient).update(assigned_caretaker=request.user)
            return Response({'status': 'success', 'new_status': creq.status})
        return Response({'detail': 'Invalid status.'}, status=status.HTTP_400_BAD_REQUEST)



class CaretakerEditRequestView(APIView):
    """POST /api/caretaker/patient-edit-request — Caretaker edits patient profile details."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role not in ['caretaker', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Caretaker access required.'}, status=status.HTTP_403_FORBIDDEN)

        from .models import CaretakerEditRequest, PatientProfile
        patient_id = request.data.get('patient_id')
        
        patient_profile = PatientProfile.objects.filter(user_id=patient_id).first()
        if not patient_profile:
            patient_profile = PatientProfile.objects.filter(id=patient_id).first()

        if not patient_profile:
            return Response({'detail': 'Patient profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        if patient_profile.assigned_caretaker != request.user and request.user.role != 'admin':
            return Response({'detail': 'You can only edit profiles of your assigned patients.'}, status=status.HTTP_403_FORBIDDEN)

        updatable_fields = [
            'full_name', 'dob', 'gender', 'marital_status', 'phone_number', 
            'location', 'languages', 'insurance_details', 'emergency_contact_name', 
            'emergency_contact_phone', 'medical_history_notes'
        ]
        for field in updatable_fields:
            if field in request.data:
                val = request.data[field]
                if val == '' and field == 'dob':
                    val = None
                setattr(patient_profile, field, val)
        
        patient_profile.save()

        changes = request.data.get('proposed_changes', 'Patient profile updated by caretaker')
        reason = request.data.get('reason', 'Caretaker edit')
        edit_req = CaretakerEditRequest.objects.create(
            caretaker=request.user,
            patient=patient_profile.user,
            proposed_changes=changes,
            reason=reason,
            status='approved'
        )

        return Response({'status': 'success', 'message': 'Patient profile updated successfully.', 'edit_request_id': edit_req.id})


class CaretakerPatientsListView(APIView):
    """GET /api/caretaker/patients — Returns active and past patients separated by assignment status."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in ['caretaker', 'admin'] and not request.user.is_staff:
            return Response({'detail': 'Caretaker access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        from .models import PatientProfile, CaretakerRequest
        active_profiles = PatientProfile.objects.filter(assigned_caretaker=request.user)
        
        unlinked_reqs = CaretakerRequest.objects.filter(caretaker=request.user, status='unlinked').values_list('patient_id', flat=True)
        unlinked_profiles = PatientProfile.objects.filter(user_id__in=unlinked_reqs).exclude(assigned_caretaker=request.user)

        active_results = []
        for p in active_profiles:
            active_results.append({
                'patient_id': p.user.id,
                'username': p.user.username,
                'email': p.user.email,
                'mobile_number': p.user.mobile_number,
                'full_name': p.full_name or p.user.username,
                'patient_code': p.patient_code or 'PAT-000',
                'dob': str(p.dob) if p.dob else 'Not provided',
                'gender': p.gender or 'Not provided',
                'marital_status': p.marital_status or 'Not provided',
                'location': p.location or 'Not provided',
                'languages': p.languages or 'Not provided',
                'insurance_details': p.insurance_details or 'Not provided',
                'emergency_contact': f"{p.emergency_contact_name or ''} ({p.emergency_contact_phone or ''})".strip() or 'Not provided',
                'medical_history_notes': p.medical_history_notes or 'No notes provided',
                'assignment_status': 'active'
            })

        past_results = []
        for p in unlinked_profiles:
            # Last working date: when caretaker request was unlinked
            creq = CaretakerRequest.objects.filter(caretaker=request.user, patient=p.user, status='unlinked').order_by('-created_at').first()
            last_work_str = creq.created_at.strftime('%d %b %Y') if (creq and creq.created_at) else 'Recent'

            # Visited doctors with total appointments
            from django.db.models import Count
            from .models import Appointment
            doc_apts = Appointment.objects.filter(patient=p.user).values('doctor').annotate(total_cnt=Count('id')).order_by('-total_cnt')
            visited_doctors = []
            for item in doc_apts:
                d_user = CustomUser.objects.filter(id=item['doctor']).first()
                if d_user:
                    d_prof = getattr(d_user, 'doctor_profile', None)
                    raw_name = d_prof.full_name if (d_prof and d_prof.full_name) else d_user.username
                    doc_name = re.sub(r'^(Dr\.|DR\.|Dr|DR)\s*', '', raw_name, flags=re.IGNORECASE).strip()
                    dept = d_prof.major_department if d_prof else 'General Medicine'
                    last_visit_apt = Appointment.objects.filter(patient=p.user, doctor=d_user).order_by('-appointment_date').first()
                    last_date_str = str(last_visit_apt.appointment_date) if last_visit_apt else 'N/A'
                    visited_doctors.append({
                        'doctor_id': d_user.id,
                        'doctor_name': doc_name,
                        'major_department': dept,
                        'total_appointments': item['total_cnt'],
                        'last_visit_date': last_date_str
                    })

            past_results.append({
                'patient_id': p.user.id,
                'username': p.user.username,
                'email': p.user.email,
                'mobile_number': p.user.mobile_number,
                'full_name': p.full_name or p.user.username,
                'patient_code': p.patient_code or 'PAT-000',
                'dob': str(p.dob) if p.dob else 'Not provided',
                'gender': p.gender or 'Not provided',
                'marital_status': p.marital_status or 'Not provided',
                'location': p.location or 'Not provided',
                'languages': p.languages or 'Not provided',
                'insurance_details': p.insurance_details or 'Not provided',
                'emergency_contact': f"{p.emergency_contact_name or ''} ({p.emergency_contact_phone or ''})".strip() or 'Not provided',
                'medical_history_notes': p.medical_history_notes or 'No notes provided',
                'last_working_date': last_work_str,
                'visited_doctors': visited_doctors,
                'assignment_status': 'unlinked'
            })

        return Response({
            'active': active_results,
            'past': past_results
        })


class RemoveCaretakerAssignmentView(APIView):
    """POST /api/auth/caretaker/unlink — Allows patient or caretaker to remove/unlink their care assignment."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .models import PatientProfile, CaretakerRequest
        user = request.user

        if user.role == 'patient':
            p_profile = PatientProfile.objects.filter(user=user).first()
            if p_profile and p_profile.assigned_caretaker:
                caretaker_user = p_profile.assigned_caretaker
                p_profile.assigned_caretaker = None
                p_profile.save()

                CaretakerRequest.objects.filter(patient=user, caretaker=caretaker_user).update(status='unlinked')
                return Response({'status': 'success', 'message': 'Caretaker removed from your profile.'})
            return Response({'detail': 'No assigned caretaker found to remove.'}, status=status.HTTP_400_BAD_REQUEST)

        elif user.role in ['caretaker', 'admin'] or user.is_staff:
            patient_id = request.data.get('patient_id')
            p_profile = PatientProfile.objects.filter(user_id=patient_id).first()
            if not p_profile:
                p_profile = PatientProfile.objects.filter(id=patient_id).first()

            if not p_profile:
                return Response({'detail': 'Patient profile not found.'}, status=status.HTTP_404_NOT_FOUND)

            if p_profile.assigned_caretaker == user or user.role == 'admin' or user.is_staff:
                caretaker_user = p_profile.assigned_caretaker or user
                p_profile.assigned_caretaker = None
                p_profile.save()

                CaretakerRequest.objects.filter(patient=p_profile.user, caretaker=caretaker_user).update(status='unlinked')
                return Response({'status': 'success', 'message': f'Patient {p_profile.full_name or p_profile.user.username} removed from your care assignment.'})
            return Response({'detail': 'You are not assigned to this patient.'}, status=status.HTTP_403_FORBIDDEN)

        return Response({'detail': 'Action not permitted.'}, status=status.HTTP_403_FORBIDDEN)


class PatientAppointmentsView(APIView):
    """GET /api/patient/appointments — returns doctor appointments for connected patient & caretaker."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import Appointment, PatientProfile
        from .serializers import AppointmentSerializer
        from django.db.models import Q
        
        from django.db.models import Case, When, Value, IntegerField
        user = request.user
        if user.role == 'patient':
            apts = Appointment.objects.filter(patient=user)
        elif user.role == 'caretaker':
            assigned_patients = PatientProfile.objects.filter(assigned_caretaker=user).values_list('user_id', flat=True)
            apts = Appointment.objects.filter(
                Q(caretaker=user) | Q(patient_id__in=assigned_patients)
            )
        else:
            apts = Appointment.objects.filter(patient=user)
            
        apts = apts.order_by('-appointment_date', '-time_slot', '-created_at')
        return Response(AppointmentSerializer(apts, many=True).data)


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
                doc_profile = DoctorProfile.objects.filter(user_id=target_id).first()
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
                car_profile = CaretakerProfile.objects.filter(user_id=target_id).first()
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


# ── Emergency Alerts ────────────────────────────────────────────────────────
class CreateEmergencyAlertView(APIView):
    """POST /api/auth/emergency-alerts — Patient creates an emergency alert from Voice AI."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role != 'patient':
            return Response({'detail': 'Only patients can create emergency alerts.'}, status=status.HTTP_403_FORBIDDEN)

        severity = request.data.get('severity', 'moderate')
        condition_summary = request.data.get('condition_summary', '')
        ai_advice = request.data.get('ai_advice', '')
        detected_specialty = request.data.get('detected_specialty', '')
        patient_query = request.data.get('patient_query', '')
        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')

        try:
            latitude = float(latitude) if latitude else None
            longitude = float(longitude) if longitude else None
        except (ValueError, TypeError):
            latitude = longitude = None

        # Only save alerts for medical concerns (not low/general queries)
        if severity == 'low':
            return Response({'status': 'skipped', 'message': 'Non-medical query, not saved.'})

        alert = EmergencyAlert.objects.create(
            patient=request.user,
            severity=severity,
            condition_summary=condition_summary,
            ai_advice=ai_advice,
            detected_specialty=detected_specialty,
            patient_query=patient_query,
            latitude=latitude,
            longitude=longitude,
        )

        # Check if patient has an assigned caretaker and mark for notification
        try:
            patient_profile = PatientProfile.objects.get(user=request.user)
            if patient_profile.assigned_caretaker:
                alert.caretaker_notified = True
                alert.save()
        except PatientProfile.DoesNotExist:
            pass

        return Response({
            'status': 'success',
            'alert_id': alert.id,
            'severity': alert.severity,
            'message': 'Emergency alert recorded.'
        }, status=status.HTTP_201_CREATED)


class PatientEmergencyAlertsView(APIView):
    """GET /api/auth/patient/emergency-alerts — Patient views their own alert history or caretaker views assigned patient history."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import PatientProfile, EmergencyAlert
        user = request.user
        patient_id = request.query_params.get('patient_id')

        if patient_id:
            # Check if logged-in caretaker is assigned to the patient
            is_assigned = PatientProfile.objects.filter(user_id=patient_id, assigned_caretaker=user).exists()
            if not is_assigned and user.id != int(patient_id) and user.role != 'admin':
                return Response({'detail': 'Access denied. You must be the assigned caretaker to view health timeline.'}, status=status.HTTP_403_FORBIDDEN)
            alerts = EmergencyAlert.objects.filter(patient_id=patient_id).order_by('-created_at')[:50]
        else:
            if user.role not in ('patient', 'admin'):
                return Response({'detail': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)
            alerts = EmergencyAlert.objects.filter(patient=user).order_by('-created_at')[:50]

        data = []
        for a in alerts:
            data.append({
                'id': a.id,
                'severity': a.severity,
                'condition_summary': a.condition_summary,
                'ai_advice': a.ai_advice,
                'detected_specialty': a.detected_specialty,
                'patient_query': a.patient_query,
                'latitude': a.latitude,
                'longitude': a.longitude,
                'created_at': a.created_at.isoformat(),
            })
        return Response(data)



class CaretakerEmergencyAlertsView(APIView):
    """GET /api/auth/caretaker/emergency-alerts — Caretaker gets alerts for their assigned patients."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role != 'caretaker':
            return Response({'detail': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

        # Get all patients assigned to this caretaker
        assigned_patients = PatientProfile.objects.filter(assigned_caretaker=request.user)
        patient_user_ids = [p.user_id for p in assigned_patients]

        # unseen_only param for polling new notifications
        unseen_only = request.query_params.get('unseen', 'false').lower() == 'true'

        alerts_qs = EmergencyAlert.objects.filter(
            patient_id__in=patient_user_ids,
            severity__in=['critical', 'urgent', 'moderate'],
        ).order_by('-created_at')

        if unseen_only:
            alerts_qs = alerts_qs.filter(caretaker_seen=False)

        alerts = alerts_qs[:30]
        data = []
        for a in alerts:
            # Get patient profile info
            try:
                pp = PatientProfile.objects.get(user_id=a.patient_id)
                patient_name = pp.full_name or a.patient.username
                patient_code = pp.patient_code
                patient_phone = pp.phone_number or a.patient.mobile_number
                patient_location = pp.location or 'Not provided'
            except PatientProfile.DoesNotExist:
                patient_name = a.patient.username
                patient_code = ''
                patient_phone = a.patient.mobile_number
                patient_location = 'Not provided'

            data.append({
                'id': a.id,
                'severity': a.severity,
                'condition_summary': a.condition_summary,
                'ai_advice': a.ai_advice,
                'detected_specialty': a.detected_specialty,
                'patient_query': a.patient_query,
                'patient_name': patient_name,
                'patient_code': patient_code,
                'patient_phone': patient_phone,
                'patient_location': patient_location,
                'latitude': a.latitude,
                'longitude': a.longitude,
                'caretaker_seen': a.caretaker_seen,
                'created_at': a.created_at.isoformat(),
            })
        return Response(data)

    def post(self, request):
        """Mark alerts as seen."""
        if request.user.role != 'caretaker':
            return Response({'detail': 'Access denied.'}, status=status.HTTP_403_FORBIDDEN)

        alert_ids = request.data.get('alert_ids', [])
        if alert_ids:
            assigned_patients = PatientProfile.objects.filter(assigned_caretaker=request.user)
            patient_user_ids = [p.user_id for p in assigned_patients]
            EmergencyAlert.objects.filter(
                id__in=alert_ids,
                patient_id__in=patient_user_ids
            ).update(caretaker_seen=True)

        return Response({'status': 'success', 'message': 'Alerts marked as seen.'})
