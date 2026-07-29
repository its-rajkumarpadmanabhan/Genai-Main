import os
import sys
import datetime
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import DoctorProfile, PatientProfile, CaretakerProfile, Appointment, CaretakerRequest

User = get_user_model()
today = datetime.date(2026, 7, 29)

print("=== DATABASE SEEDING VERIFICATION ===")
print("Total Appointments:", Appointment.objects.count())
print("Total Caretaker Requests:", CaretakerRequest.objects.count())
print("Past Caretaker Connections (Unlinked):", CaretakerRequest.objects.filter(status='unlinked').count())

# Verify Doctors
docs = User.objects.filter(role='doctor')
past_apts_per_doc = [Appointment.objects.filter(doctor=d, appointment_date__lt=today).count() for d in docs]
up_apts_per_doc = [Appointment.objects.filter(doctor=d, appointment_date__gt=today).count() for d in docs]
today_apts_per_doc = [Appointment.objects.filter(doctor=d, appointment_date=today).count() for d in docs]
unique_pats_per_doc = [Appointment.objects.filter(doctor=d).values('patient_id').distinct().count() for d in docs]

print(f"\nDoctors ({len(docs)} total):")
print(f"  - Min Past Appointments per Doctor: {min(past_apts_per_doc)} (Target >= 15)")
print(f"  - Min Upcoming Appointments per Doctor: {min(up_apts_per_doc)} (Target >= 10)")
print(f"  - Min Today Appointments per Doctor: {min(today_apts_per_doc)} (Target >= 12)")
print(f"  - Min Unique Patients per Doctor: {min(unique_pats_per_doc)} (Target >= 5)")

# Verify Patients
pats = PatientProfile.objects.all()
senior_past_cnts = []
std_past_cnts = []

for p in pats:
    dob = p.dob
    age = 40
    if dob:
        age = 2026 - dob.year
    
    cnt = CaretakerRequest.objects.filter(patient=p.user, status='unlinked').count()
    if age > 60:
        senior_past_cnts.append(cnt)
    else:
        std_past_cnts.append(cnt)

print(f"\nPatients ({len(pats)} total):")
print(f"  - Senior Patients (>60yo) Min Past Connections: {min(senior_past_cnts) if senior_past_cnts else 'N/A'}, Max: {max(senior_past_cnts) if senior_past_cnts else 'N/A'} (Target: 5 to 8)")
print(f"  - Standard Patients (<=60yo) Min Past Connections: {min(std_past_cnts) if std_past_cnts else 'N/A'} (Target >= 2)")

# Verify Caretakers
cars = User.objects.filter(role='caretaker')
car_past_cnts = [CaretakerRequest.objects.filter(caretaker=c, status='unlinked').count() for c in cars]

print(f"\nCaretakers ({len(cars)} total):")
print(f"  - Min Past Patient Connections per Caretaker: {min(car_past_cnts)} (Target >= 3 to 4)")

print("\n=== VERIFICATION COMPLETE ===")
