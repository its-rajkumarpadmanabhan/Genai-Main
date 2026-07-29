import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import CaretakerProfile, PatientProfile, CaretakerRequest
from django.db.models import Count

User = get_user_model()

def enforce_limits():
    print("=== AUDITING & ENFORCING MAX 1 ACCEPTED CARE PER CARETAKER ===")

    caretakers = list(User.objects.filter(role='caretaker'))
    patients = list(User.objects.filter(role='patient'))
    print(f"Loaded {len(caretakers)} Caretakers and {len(patients)} Patients.")

    unlinked_count = 0

    # 1. Enforce max 1 active patient per caretaker
    for c in caretakers:
        reqs = list(CaretakerRequest.objects.filter(caretaker=c, status='accepted').order_by('-created_at'))
        if len(reqs) > 1:
            # Keep the newest one accepted, convert older ones to unlinked
            keep = reqs[0]
            for old_r in reqs[1:]:
                old_r.status = 'unlinked'
                old_r.save()
                unlinked_count += 1

    # 2. Enforce max 1 active caretaker per patient
    for p in patients:
        reqs = list(CaretakerRequest.objects.filter(patient=p, status='accepted').order_by('-created_at'))
        if len(reqs) > 1:
            keep = reqs[0]
            for old_r in reqs[1:]:
                old_r.status = 'unlinked'
                old_r.save()
                unlinked_count += 1

    # 3. Sync PatientProfile.assigned_caretaker
    for p_prof in PatientProfile.objects.all():
        active_req = CaretakerRequest.objects.filter(patient=p_prof.user, status='accepted').order_by('-created_at').first()
        if active_req:
            if p_prof.assigned_caretaker != active_req.caretaker:
                p_prof.assigned_caretaker = active_req.caretaker
                p_prof.save()
        else:
            if p_prof.assigned_caretaker is not None:
                p_prof.assigned_caretaker = None
                p_prof.save()

    print(f"Re-aligned {unlinked_count} duplicate accepted connections into 'unlinked' status.")

    # 4. Verify maximum accepted count across all caretakers
    c_counts = CaretakerRequest.objects.filter(status='accepted').values('caretaker_id').annotate(cnt=Count('id'))
    max_c_count = max([item['cnt'] for item in c_counts]) if c_counts else 0

    p_counts = CaretakerRequest.objects.filter(status='accepted').values('patient_id').annotate(cnt=Count('id'))
    max_p_count = max([item['cnt'] for item in p_counts]) if p_counts else 0

    print(f"Verification Results:")
    print(f"  - Max Accepted Patient Count per Caretaker: {max_c_count} (Target: <= 1)")
    print(f"  - Max Accepted Caretaker Count per Patient: {max_p_count} (Target: <= 1)")

    if max_c_count <= 1 and max_p_count <= 1:
        print("SUCCESS: ALL CARETAKER PROFILES STRICTLY ENFORCE MAX 1 ACCEPTED CARE!")
    else:
        print("WARNING: CONFLICT DETECTED IN CARETAKER ASSIGNMENT COUNTS!")

if __name__ == '__main__':
    enforce_limits()
