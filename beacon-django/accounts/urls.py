from django.urls import path

from .views import (
    CheckAvailabilityView,
    ForgotPasswordView,
    LoginView,
    MeView,
    ResetPasswordView,
    SignupView,
    DeleteSelfProfileView,
    AdminStatsView,
    AdminUsersView,
    PatientProfileView,
    MedicalDocumentView,
    DirectoryListView,
    CaretakerRequestView,
    AppointmentBookingView,
    DoctorProfileView,
    DoctorAppointmentsView,
    DoctorPatientsListView,
    DoctorPatientRecordsView,
    DoctorSinglePatientView,
    CaretakerProfileView,
    CaretakerRequestsView,
    CaretakerEditRequestView,
    CaretakerPatientsListView,
    RemoveCaretakerAssignmentView,
    PatientAppointmentsView,
    SubmitReviewView,
    CreateEmergencyAlertView,
    PatientEmergencyAlertsView,
    CaretakerEmergencyAlertsView,
    AppointmentStartCallView,
    AppointmentEndCallView,
)

urlpatterns = [
    # Auth & Profile
    path('signup', SignupView.as_view(), name='auth-signup'),
    path('login', LoginView.as_view(), name='auth-login'),
    path('me', MeView.as_view(), name='auth-me'),
    path('forgot-password', ForgotPasswordView.as_view(), name='auth-forgot-password'),
    path('reset-password', ResetPasswordView.as_view(), name='auth-reset-password'),
    path('check-availability', CheckAvailabilityView.as_view(), name='auth-check-availability'),
    path('profile/delete', DeleteSelfProfileView.as_view(), name='auth-profile-delete'),

    # Admin
    path('admin/stats', AdminStatsView.as_view(), name='admin-stats'),
    path('admin/users', AdminUsersView.as_view(), name='admin-users'),

    # Patient
    path('patient/profile', PatientProfileView.as_view(), name='patient-profile-api'),
    path('patient/documents', MedicalDocumentView.as_view(), name='patient-documents-api'),
    path('patient/documents/<int:doc_id>', MedicalDocumentView.as_view(), name='patient-document-delete'),
    path('patient/appointments', PatientAppointmentsView.as_view(), name='patient-appointments-api'),
    path('patient/caretaker-request', CaretakerRequestView.as_view(), name='patient-caretaker-request'),

    # Doctor
    path('doctor/profile', DoctorProfileView.as_view(), name='doctor-profile-api'),
    path('doctor/appointments', DoctorAppointmentsView.as_view(), name='doctor-appointments-api'),
    path('doctor/patients', DoctorPatientsListView.as_view(), name='doctor-patients-list'),
    path('doctor/patients/<int:patient_id>', DoctorSinglePatientView.as_view(), name='doctor-single-patient'),
    path('doctor/patients/<int:patient_id>/records', DoctorPatientRecordsView.as_view(), name='doctor-patient-records'),

    # Caretaker
    path('caretaker/profile', CaretakerProfileView.as_view(), name='caretaker-profile-api'),
    path('caretaker/requests', CaretakerRequestsView.as_view(), name='caretaker-requests-api'),
    path('caretaker/patients', CaretakerPatientsListView.as_view(), name='caretaker-patients-list'),
    path('caretaker/patient-edit-request', CaretakerEditRequestView.as_view(), name='caretaker-patient-edit-request'),
    path('caretaker/unlink', RemoveCaretakerAssignmentView.as_view(), name='caretaker-unlink'),

    # Shared / Directory / Appointments / Reviews
    path('directory/search', DirectoryListView.as_view(), name='directory-search'),
    path('appointments/book', AppointmentBookingView.as_view(), name='appointments-book'),
    path('appointments/<int:apt_id>/start-call', AppointmentStartCallView.as_view(), name='appointment-start-call'),
    path('appointments/<int:apt_id>/end-call', AppointmentEndCallView.as_view(), name='appointment-end-call'),
    path('review', SubmitReviewView.as_view(), name='submit-review'),

    # Emergency Alerts
    path('emergency-alerts', CreateEmergencyAlertView.as_view(), name='create-emergency-alert'),
    path('patient/emergency-alerts', PatientEmergencyAlertsView.as_view(), name='patient-emergency-alerts'),
    path('caretaker/emergency-alerts', CaretakerEmergencyAlertsView.as_view(), name='caretaker-emergency-alerts'),
]
