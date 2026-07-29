import os
import sys
import datetime
import django
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import DoctorProfile, Appointment

User = get_user_model()
today = datetime.date(2026, 7, 29)

print("=== VERIFYING SCHEDULING CONFLICT RULES ===")
print("Total Active Appointments in Database:", Appointment.objects.count())

apts = Appointment.objects.all()

pat_slots = defaultdict(int)
doc_slots = defaultdict(int)
pat_depts = defaultdict(int)

doc_depts = {}
for dp in DoctorProfile.objects.all():
    doc_depts[dp.user_id] = (dp.major_department or 'General Medicine').strip().lower()

for a in apts:
    p_slot_key = (a.patient_id, a.appointment_date, a.time_slot)
    d_slot_key = (a.doctor_id, a.appointment_date, a.time_slot)
    
    dept = doc_depts.get(a.doctor_id, 'general medicine')
    p_dept_key = (a.patient_id, a.appointment_date, dept)

    pat_slots[p_slot_key] += 1
    doc_slots[d_slot_key] += 1
    pat_depts[p_dept_key] += 1

pat_slot_conflicts = sum(1 for k, v in pat_slots.items() if v > 1)
doc_slot_conflicts = sum(1 for k, v in doc_slots.items() if v > 1)
pat_dept_conflicts = sum(1 for k, v in pat_depts.items() if v > 1)

print(f"  - Patient Time-Slot Overlaps: {pat_slot_conflicts} (Target: 0)")
print(f"  - Doctor Time-Slot Overlaps: {doc_slot_conflicts} (Target: 0)")
print(f"  - Patient Same-Day Specialty Conflicts: {pat_dept_conflicts} (Target: 0)")

if pat_slot_conflicts == 0 and doc_slot_conflicts == 0 and pat_dept_conflicts == 0:
    print("\nSUCCESS: ALL APPOINTMENTS COMPLY WITH ZERO SCHEDULING CONFLICTS!")
else:
    print("\nWARNING: CONFLICTS REMAIN!")
