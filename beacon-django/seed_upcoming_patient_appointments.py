import os
import sys
import random
import datetime
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import DoctorProfile, PatientProfile, Appointment
from collections import defaultdict

User = get_user_model()

def seed_upcoming_appointments():
    print("=== SEEDING 5 TO 8 UPCOMING APPOINTMENTS PER PATIENT ===")

    patients = list(User.objects.filter(role='patient'))
    doctors = list(DoctorProfile.objects.all())

    print(f"Loaded {len(patients)} Patients and {len(doctors)} Doctors.")

    today = datetime.date(2026, 7, 29)
    # Upcoming dates within current week (Thursday to Sunday)
    upcoming_dates = [
        today + datetime.timedelta(days=1), # 2026-07-30 (Thu)
        today + datetime.timedelta(days=2), # 2026-07-31 (Fri)
        today + datetime.timedelta(days=3), # 2026-08-01 (Sat)
        today + datetime.timedelta(days=4)  # 2026-08-02 (Sun)
    ]

    time_slots = [
        '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM',
        '12:00 PM', '02:00 PM', '02:30 PM', '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM'
    ]

    reasons_by_dept = {
        'Cardiology': ['Routine Cardiac Evaluation', 'ECG Follow-up', 'Blood Pressure Check', 'Heart Rhythm Monitoring'],
        'Neurology': ['Migraine Evaluation', 'Neurological Checkup', 'Memory & Nerve Health Review'],
        'Orthopedics': ['Joint Pain Consultation', 'Knee Mobility Review', 'Post-injury Evaluation'],
        'Pediatrics': ['Child Wellness Check', 'Growth & Immunity Review', 'Routine Vaccination Consult'],
        'General Medicine': ['General Health Checkup', 'Annual Medical Assessment', 'Fever & Fatigue Review'],
        'Dermatology': ['Skin Rash Evaluation', 'Allergy Assessment', 'Dermatological Review'],
        'Gastroenterology': ['Digestive Health Consultation', 'Gastric Symptom Evaluation'],
        'Pulmonology': ['Respiratory Checkup', 'Asthma Management Review'],
        'Ophthalmology': ['Vision Checkup', 'Eye Strain Evaluation'],
        'ENT': ['Ear & Throat Checkup', 'Sinus Pressure Evaluation']
    }

    # Group doctors by major department
    docs_by_dept = defaultdict(list)
    doc_profile_map = {}
    for d in doctors:
        dept = (d.major_department or 'General Medicine').strip()
        docs_by_dept[dept].append(d)
        doc_profile_map[d.user_id] = d

    all_depts = list(docs_by_dept.keys())

    # Delete existing upcoming appointments in the future to avoid conflicts
    Appointment.objects.filter(appointment_date__gt=today).delete()

    doc_occupied = set()
    pat_occupied = set()
    pat_dept_day = set()

    apts_to_create = []

    for pat in patients:
        num_upcoming = random.randint(5, 8)

        created_count = 0
        attempts = 0
        max_attempts = num_upcoming * 15

        while created_count < num_upcoming and attempts < max_attempts:
            attempts += 1
            a_date = random.choice(upcoming_dates)
            dept = random.choice(all_depts)

            dept_key = (pat.id, a_date, dept)
            if dept_key in pat_dept_day:
                continue

            avail_docs = [d for d in docs_by_dept[dept]]
            random.shuffle(avail_docs)

            assigned_doc = None
            assigned_slot = None

            for d in avail_docs:
                for slot in time_slots:
                    d_key = (d.user_id, a_date, slot)
                    p_key = (pat.id, a_date, slot)
                    if d_key not in doc_occupied and p_key not in pat_occupied:
                        assigned_doc = d
                        assigned_slot = slot
                        break
                if assigned_doc:
                    break

            if assigned_doc and assigned_slot:
                doc_occupied.add((assigned_doc.user_id, a_date, assigned_slot))
                pat_occupied.add((pat.id, a_date, assigned_slot))
                pat_dept_day.add(dept_key)

                reason_list = reasons_by_dept.get(dept, ['General Consultation'])
                reason_text = random.choice(reason_list)
                apt_type = random.choice(['offline', 'online', 'offline'])

                apts_to_create.append(Appointment(
                    patient=pat,
                    doctor=assigned_doc.user,
                    booked_by='patient',
                    appointment_date=a_date,
                    time_slot=assigned_slot,
                    reason=reason_text,
                    appointment_type=apt_type,
                    status='accepted'
                ))
                created_count += 1

    Appointment.objects.bulk_create(apts_to_create)
    print(f"Successfully created {len(apts_to_create)} Upcoming appointments across {len(patients)} patients!")

    # Summary verification per patient
    pat_upcoming_counts = Appointment.objects.filter(appointment_date__gt=today).values('patient_id').annotate(cnt=django.db.models.Count('id'))
    counts_list = [item['cnt'] for item in pat_upcoming_counts]

    min_c = min(counts_list) if counts_list else 0
    max_c = max(counts_list) if counts_list else 0
    avg_c = round(sum(counts_list) / len(counts_list), 2) if counts_list else 0

    print(f"Upcoming Appointments per Patient Stats: Min = {min_c}, Max = {max_c}, Avg = {avg_c}")

if __name__ == '__main__':
    seed_upcoming_appointments()
