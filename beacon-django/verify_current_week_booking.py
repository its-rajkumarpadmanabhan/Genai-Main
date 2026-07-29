import os
import sys
import datetime
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from django.test import RequestFactory
from django.contrib.auth import get_user_model
from accounts.views import AppointmentBookingView
from accounts.models import DoctorProfile

User = get_user_model()
patient = User.objects.filter(role='patient').first()
doctor_p = DoctorProfile.objects.first()
doctor = doctor_p.user

rf = RequestFactory()

today = datetime.date(2026, 7, 29)
start_of_week = today - datetime.timedelta(days=today.weekday())
end_of_week = start_of_week + datetime.timedelta(days=6)

next_week_date = (end_of_week + datetime.timedelta(days=2)).strftime('%Y-%m-%d')
next_month_date = (today + datetime.timedelta(days=35)).strftime('%Y-%m-%d')
current_week_date = today.strftime('%Y-%m-%d')

view = AppointmentBookingView.as_view(permission_classes=[])

print("=== VERIFYING CURRENT WEEK BOOKING RESTRICTION ===")

# Test 1: Next Week Date
req_next_week = rf.post('/api/appointments/book', data={
    'doctor_id': doctor.id,
    'appointment_date': next_week_date,
    'time_slot': '09:00 AM',
    'reason': 'Checkup'
}, content_type='application/json')
req_next_week.user = patient
resp_next_week = view(req_next_week)

print(f"Test 1 (Next Week Booking - {next_week_date}): Status = {resp_next_week.status_code}")
print("Response detail:", resp_next_week.data.get('detail'))

# Test 2: Next Month Date
req_next_month = rf.post('/api/appointments/book', data={
    'doctor_id': doctor.id,
    'appointment_date': next_month_date,
    'time_slot': '09:00 AM',
    'reason': 'Checkup'
}, content_type='application/json')
req_next_month.user = patient
resp_next_month = view(req_next_month)

print(f"\nTest 2 (Next Month Booking - {next_month_date}): Status = {resp_next_month.status_code}")
print("Response detail:", resp_next_month.data.get('detail'))

if resp_next_week.status_code == 400 and resp_next_month.status_code == 400:
    print("\nSUCCESS: OUT-OF-WEEK BOOKINGS ARE PROPERLY REJECTED WITH 400 BAD REQUEST!")
else:
    print("\nFAILURE: OUT-OF-WEEK BOOKINGS WERE NOT BLOCKED!")
