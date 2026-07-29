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

def seed_today_appointments():
    print("=== SEEDING 2 TO 4 TODAY'S APPOINTMENTS PER PATIENT ===")

    patients = list(User.objects.filter(role='patient'))
    doctors = list(DoctorProfile.objects.all())

    print(f"Loaded {len(patients)} Patients and {len(doctors)} Doctors.")

    today = datetime.date(2026, 7, 29)

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

    # Delete existing Today's appointments to avoid duplicates
    Appointment.objects.filter(appointment_date=today).delete()

    doc_occupied_today = set()
    pat_occupied_today = set()

    apts_to_create = []

    for pat in patients:
        num_today = random.randint(2, 4)
        chosen_depts = random.sample(all_depts, min(num_today, len(all_depts)))

        for dept in chosen_depts:
            avail_docs = [d for d in docs_by_dept[dept]]
            random.shuffle(avail_docs)

            assigned_doc = None
            assigned_slot = None

            for d in avail_docs:
                for slot in time_slots:
                    d_key = (d.user_id, slot)
                    p_key = (pat.id, slot)
                    if d_key not in doc_occupied_today and p_key not in pat_occupied_today:
                        assigned_doc = d
                        assigned_slot = slot
                        break
                if assigned_doc:
                    break

            if assigned_doc and assigned_slot:
                doc_occupied_today.add((assigned_doc.user_id, assigned_slot))
                pat_occupied_today.add((pat.id, assigned_slot))

                reason_list = reasons_by_dept.get(dept, ['General Consultation'])
                reason_text = random.choice(reason_list)
                apt_type = random.choice(['offline', 'online', 'offline'])

                apts_to_create.append(Appointment(
                    patient=pat,
                    doctor=assigned_doc.user,
                    booked_by='patient',
                    appointment_date=today,
                    time_slot=assigned_slot,
                    reason=reason_text,
                    appointment_type=apt_type,
                    status='accepted'
                ))

    Appointment.objects.bulk_create(apts_to_create)
    print(f"Successfully created {len(apts_to_create)} Today's appointments across {len(patients)} patients!")

    # Summary verification per patient
    pat_today_counts = Appointment.objects.filter(appointment_date=today).values('patient_id').annotate(cnt=django.db.models.Count('id'))
    counts_list = [item['cnt'] for item in pat_today_counts]

    min_c = min(counts_list) if counts_list else 0
    max_c = max(counts_list) if counts_list else 0
    avg_c = round(sum(counts_list) / len(counts_list), 2) if counts_list else 0

    print(f"Today's Appointments per Patient Stats: Min = {min_c}, Max = {max_c}, Avg = {avg_c}")

if __name__ == '__main__':
    seed_today_appointments()
