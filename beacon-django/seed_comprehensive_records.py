import os
import sys
import random
import datetime
import django

# Setup Django Environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import DoctorProfile, PatientProfile, CaretakerProfile, Appointment, CaretakerRequest

User = get_user_model()

def run_seed():
    print("=== FAST COMPREHENSIVE SEEDING PROCESS ===")
    
    today = datetime.date(2026, 7, 29)
    
    doctors = list(User.objects.filter(role='doctor'))
    patients = list(User.objects.filter(role='patient'))
    caretakers = list(User.objects.filter(role='caretaker'))

    print(f"Loaded Database Users -> Doctors: {len(doctors)}, Patients: {len(patients)}, Caretakers: {len(caretakers)}")

    if not doctors or not patients or not caretakers:
        print("Error: Missing users in database!")
        return

    time_slots = [
        '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM',
        '12:00 PM', '02:00 PM', '02:30 PM', '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM'
    ]

    reasons = [
        'Routine Health Checkup', 'Follow-up Consultation', 'Blood Pressure Evaluation',
        'Diabetes Progress Review', 'Chest Discomfort Checkup', 'Skin Rash Inspection',
        'Joint Pain & Mobility Review', 'General Wellness & Prescription Renewal',
        'Seasonal Allergy Management', 'Thyroid Function Review'
    ]

    types = ['offline', 'online']

    # 1. DOCTOR APPOINTMENTS
    print("\n--- Processing Doctor Appointments ---")
    apts_to_create = []

    for doc in doctors:
        # Check existing appointment stats for this doctor
        doc_apts = Appointment.objects.filter(doctor=doc)
        
        past_cnt = doc_apts.filter(appointment_date__lt=today).count()
        up_cnt = doc_apts.filter(appointment_date__gt=today).count()
        today_cnt = doc_apts.filter(appointment_date=today).count()

        num_patients = random.randint(6, min(12, len(patients)))
        doc_patients = random.sample(patients, num_patients)

        # Past (>= 15)
        needed_past = max(0, 16 - past_cnt)
        for _ in range(needed_past):
            pat = random.choice(doc_patients)
            days_ago = random.randint(1, 120)
            apt_date = today - datetime.timedelta(days=days_ago)
            slot = random.choice(time_slots)
            apts_to_create.append(Appointment(
                doctor=doc,
                patient=pat,
                booked_by='patient',
                appointment_date=apt_date,
                time_slot=slot,
                reason=random.choice(reasons),
                status='completed',
                appointment_type=random.choice(types),
                doctor_notes='Patient visited on schedule. Vital signs stable and prescribed routine medication.'
            ))

        # Upcoming (>= 10)
        needed_up = max(0, 11 - up_cnt)
        for _ in range(needed_up):
            pat = random.choice(doc_patients)
            days_ahead = random.randint(1, 60)
            apt_date = today + datetime.timedelta(days=days_ahead)
            slot = random.choice(time_slots)
            apts_to_create.append(Appointment(
                doctor=doc,
                patient=pat,
                booked_by='patient',
                appointment_date=apt_date,
                time_slot=slot,
                reason=random.choice(reasons),
                status='accepted',
                appointment_type=random.choice(types)
            ))

        # Today (>= 12)
        needed_today = max(0, 13 - today_cnt)
        used_slots = set(doc_apts.filter(appointment_date=today).values_list('time_slot', flat=True))
        for t_idx in range(needed_today):
            pat = random.choice(doc_patients)
            avail_slots = [s for s in time_slots if s not in used_slots]
            slot = random.choice(avail_slots) if avail_slots else f"{9 + (t_idx % 8):02d}:15 AM"
            used_slots.add(slot)
            apts_to_create.append(Appointment(
                doctor=doc,
                patient=pat,
                booked_by='patient',
                appointment_date=today,
                time_slot=slot,
                reason=random.choice(reasons),
                status=random.choice(['accepted', 'accepted', 'completed', 'pending']),
                appointment_type=random.choice(types)
            ))

    if apts_to_create:
        Appointment.objects.bulk_create(apts_to_create, batch_size=1000)
        print(f"Successfully bulk created {len(apts_to_create)} Appointments!")

    # 2. PATIENT CARETAKER CONNECTIONS & CARETAKER PAST CONNECTIONS
    print("\n--- Processing Patient & Caretaker Connections ---")
    reqs_to_create = []

    # Assign Senior DOBs to first 30 patients for >60yo verification
    for idx, pat in enumerate(patients):
        p_profile, _ = PatientProfile.objects.get_or_create(user=pat)
        
        if idx < 30 and (not p_profile.dob or (2026 - p_profile.dob.year) <= 60):
            p_profile.dob = datetime.date(random.randint(1950, 1963), random.randint(1, 12), random.randint(1, 28))
            p_profile.save()

        dob = p_profile.dob
        age = 40
        if dob:
            age = 2026 - dob.year

        target_past = random.randint(5, 8) if age > 60 else random.randint(2, 4)
        
        # Existing unlinked requests
        existing_cars = set(CaretakerRequest.objects.filter(patient=pat, status='unlinked').values_list('caretaker_id', flat=True))
        needed_past = max(0, target_past - len(existing_cars))

        if needed_past > 0:
            avail_cars = [c for c in caretakers if c.id not in existing_cars and c != p_profile.assigned_caretaker]
            chosen_past = random.sample(avail_cars, min(needed_past, len(avail_cars)))
            for car in chosen_past:
                reqs_to_create.append(CaretakerRequest(
                    patient=pat,
                    caretaker=car,
                    status='unlinked'
                ))
                existing_cars.add(car.id)

        # Assign active caretaker if missing
        if not p_profile.assigned_caretaker:
            assigned_car = random.choice(caretakers)
            p_profile.assigned_caretaker = assigned_car
            p_profile.save()
            CaretakerRequest.objects.get_or_create(
                patient=pat,
                caretaker=assigned_car,
                defaults={'status': 'accepted'}
            )

    # 3. VERIFY EVERY CARETAKER HAS AT LEAST 3-4 PAST CONNECTIONS
    for car in caretakers:
        car_past_cnt = CaretakerRequest.objects.filter(caretaker=car, status='unlinked').count()
        needed_car_past = max(0, random.randint(3, 4) - car_past_cnt)
        if needed_car_past > 0:
            assigned_pats = set(PatientProfile.objects.filter(assigned_caretaker=car).values_list('user_id', flat=True))
            existing_req_pats = set(CaretakerRequest.objects.filter(caretaker=car).values_list('patient_id', flat=True))
            cand_pats = [p for p in patients if p.id not in assigned_pats and p.id not in existing_req_pats]
            chosen_pats = random.sample(cand_pats, min(needed_car_past, len(cand_pats)))
            for p_user in chosen_pats:
                reqs_to_create.append(CaretakerRequest(
                    patient=p_user,
                    caretaker=car,
                    status='unlinked'
                ))

    if reqs_to_create:
        CaretakerRequest.objects.bulk_create(reqs_to_create, batch_size=500, ignore_conflicts=True)
        print(f"Successfully bulk created {len(reqs_to_create)} Caretaker Connections!")

    print("\n=== COMPREHENSIVE SEEDING COMPLETED IN FAST MODE ===")

if __name__ == '__main__':
    run_seed()
