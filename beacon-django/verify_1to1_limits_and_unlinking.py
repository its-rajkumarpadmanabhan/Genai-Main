import os
import sys
import django

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from django.contrib.auth import get_user_model
from accounts.models import PatientProfile, CaretakerProfile, CaretakerRequest
from accounts.views import CaretakerRequestView, CaretakerRequestsView, RemoveCaretakerAssignmentView

User = get_user_model()
rf = APIRequestFactory()

print("=== VERIFYING 1-TO-1 LIMITS & REMOVAL WORKFLOW ===")

patient = User.objects.filter(role='patient', patient_profile__assigned_caretaker__isnull=False).first()
if not patient:
    print("No patient with assigned caretaker found.")
    sys.exit(0)

p_prof = patient.patient_profile
caretaker = p_prof.assigned_caretaker

print(f"Test Patient: {patient.username} (ID: {patient.id})")
print(f"Test Assigned Caretaker: {caretaker.username} (ID: {caretaker.id})")

# Test 1: Patient attempts to request another caretaker while already assigned
other_caretaker = User.objects.filter(role='caretaker').exclude(id=caretaker.id).first()
req_view = CaretakerRequestView.as_view()

r1 = rf.post('/api/patient/caretaker-request', {'caretaker_id': other_caretaker.id}, format='json')
force_authenticate(r1, user=patient)
resp1 = req_view(r1)

print(f"\nTest 1 (Patient attempts 2nd Caretaker Request): Status = {resp1.status_code}")
print("Response detail:", resp1.data.get('detail'))

# Test 2: Unlink Caretaker via RemoveCaretakerAssignmentView
unlink_view = RemoveCaretakerAssignmentView.as_view()
r2 = rf.post('/api/auth/caretaker/unlink', {}, format='json')
force_authenticate(r2, user=patient)
resp2 = unlink_view(r2)

print(f"\nTest 2 (Patient unlinks Caretaker): Status = {resp2.status_code}")
print("Response message:", resp2.data.get('message'))

p_prof.refresh_from_db()
print("Post-unlink Patient Assigned Caretaker:", p_prof.assigned_caretaker)

# Check CaretakerRequest status
creq = CaretakerRequest.objects.filter(patient=patient, caretaker=caretaker).order_by('-created_at').first()
print("Post-unlink CaretakerRequest status:", creq.status if creq else 'None')

if resp1.status_code == 400 and p_prof.assigned_caretaker is None and creq.status == 'unlinked':
    print("\nSUCCESS: 1-TO-1 LIMITS AND UNLINKING REMOVAL WORKFLOW VERIFIED SUCCESSFULLY!")
else:
    print("\nFAILURE IN VERIFICATION!")
