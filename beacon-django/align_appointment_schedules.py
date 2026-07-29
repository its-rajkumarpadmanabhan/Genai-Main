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

User = get_user_model()

def align_schedules():
    print("=== RE-ALIGNING ALL APPOINTMENTS IN DATABASE ===")
    
    time_slots = [
        '09:00 AM', '09:30 AM', '10:00 AM', '10:30 AM', '11:00 AM', '11:30 AM',
        '12:00 PM', '02:00 PM', '02:30 PM', '03:00 PM', '03:30 PM', '04:00 PM', '04:30 PM', '05:00 PM'
    ]

    all_apts = list(Appointment.objects.all().order_by('appointment_date', 'id'))
    print(f"Total Appointments to re-align: {len(all_apts)}")

    # We track:
    # 1. doctor_occupied: (doctor_id, appointment_date, time_slot) -> True
    # 2. patient_occupied: (patient_id, appointment_date, time_slot) -> True
    # 3. patient_dept_day: (patient_id, appointment_date, department_name) -> True

    doctor_occupied = set()
    patient_occupied = set()
    patient_dept_day = set()

    # Pre-cache doctor departments
    doc_depts = {}
    for dp in DoctorProfile.objects.all():
        doc_depts[dp.user_id] = (dp.major_department or 'General Medicine').strip().lower()

    apts_to_update = []
    apts_to_delete = []

    for apt in all_apts:
        d_id = apt.doctor_id
        p_id = apt.patient_id
        a_date = apt.appointment_date
        dept = doc_depts.get(d_id, 'general medicine')

        # Check if patient already visited this specialty today
        dept_key = (p_id, a_date, dept)
        if dept_key in patient_dept_day:
            # Drop duplicate specialty on same day to enforce 1 visit per condition/specialty per day
            apts_to_delete.append(apt.id)
            continue

        # Find a clean time slot for this doctor and patient on a_date
        assigned_slot = None
        for slot in time_slots:
            d_key = (d_id, a_date, slot)
            p_key = (p_id, a_date, slot)
            if d_key not in doctor_occupied and p_key not in patient_occupied:
                assigned_slot = slot
                break

        if assigned_slot:
            apt.time_slot = assigned_slot
            doctor_occupied.add((d_id, a_date, assigned_slot))
            patient_occupied.add((p_id, a_date, assigned_slot))
            patient_dept_day.add(dept_key)
            apts_to_update.append(apt)
        else:
            # If all slots full on this date, shift appointment date by +1 or +2 days
            shifted_date = a_date + datetime.timedelta(days=random.randint(1, 7))
            apt.appointment_date = shifted_date
            shift_dept_key = (p_id, shifted_date, dept)

            for slot in time_slots:
                d_key = (d_id, shifted_date, slot)
                p_key = (p_id, shifted_date, slot)
                if d_key not in doctor_occupied and p_key not in patient_occupied:
                    assigned_slot = slot
                    break

            if assigned_slot:
                apt.time_slot = assigned_slot
                doctor_occupied.add((d_id, shifted_date, assigned_slot))
                patient_occupied.add((p_id, shifted_date, assigned_slot))
                patient_dept_day.add(shift_dept_key)
                apts_to_update.append(apt)
            else:
                apts_to_delete.append(apt.id)

    print(f"Updating {len(apts_to_update)} aligned appointments...")
    Appointment.objects.bulk_update(apts_to_update, ['appointment_date', 'time_slot'], batch_size=1000)

    if apts_to_delete:
        print(f"Deleting {len(apts_to_delete)} conflicting duplicate appointments...")
        Appointment.objects.filter(id__in=apts_to_delete).delete()

    print("=== RE-ALIGNMENT COMPLETE ===")

if __name__ == '__main__':
    align_schedules()
