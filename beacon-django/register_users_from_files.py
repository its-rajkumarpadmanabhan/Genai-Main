import os
import re
import sys
import datetime
import django

# Setup Django Environment
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.models import DoctorProfile, PatientProfile, CaretakerProfile

User = get_user_model()

def clean_cell(cell):
    if not cell:
        return ""
    text = cell.strip()
    # Strip markdown link syntax: [text](url) -> text or url if text is email
    match = re.search(r'\[([^\]]+)\]\([^)]+\)', text)
    if match:
        text = match.group(1).strip()
    return text

def clean_doctor_name(name):
    if not name:
        return ""
    cleaned = re.sub(r'^(Dr\.|DR\.|Dr|DR)\s*', '', name, flags=re.IGNORECASE).strip()
    return cleaned

def parse_markdown_table(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return []

    rows = []
    current_headers = []
    
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line_str = line.strip()
            if not line_str.startswith('|') or not line_str.endswith('|'):
                continue
            
            # Split cells
            cells = [clean_cell(c) for c in line_str.split('|')[1:-1]]
            if not cells or all(c == '' for c in cells):
                continue
            
            # Skip separator lines like |---|---|
            if all(set(c) <= set('-: ') for c in cells):
                continue
            
            # Check if this line is a header line (e.g. contains 'email' or 'name' or 'username' or 'patient id')
            lower_cells = [c.lower() for c in cells]
            if 'name' in lower_cells or 'username' in lower_cells or 'patient id' in lower_cells:
                current_headers = lower_cells
                continue

            if not current_headers:
                current_headers = lower_cells
                continue

            # Zip into dict
            row_dict = {}
            for idx, h in enumerate(current_headers):
                if idx < len(cells):
                    row_dict[h] = cells[idx]
            rows.append(row_dict)

    return rows


def register_doctors():
    doc_file = os.path.join('templates', 'doctors list.txt')
    records = parse_markdown_table(doc_file)
    print(f"\n--- Registering {len(records)} Doctors from {doc_file} ---")
    
    count_created = 0
    count_updated = 0

    for r in records:
        raw_name = r.get('name', '').strip()
        if not raw_name:
            continue
        
        name = clean_doctor_name(raw_name)
        phone = r.get('phone number', '').strip()
        email = r.get('email id', '').strip().lower()
        password = r.get('password', '').strip() or 'Doctor@123'
        license_no = r.get('license no.', '').strip()
        exp_str = r.get('experience', '').strip()
        fee_str = r.get('consultation fee', '').strip()
        hours = r.get('available hours', '').strip() or '09:00 AM - 05:00 PM'
        location = r.get('location', '').strip()
        languages = r.get('languages', '').strip()
        spec = r.get('specialization', '').strip() or 'General Physician'

        # Extract numerical experience years
        exp_years = 0
        exp_match = re.search(r'\d+', exp_str)
        if exp_match:
            exp_years = int(exp_match.group(0))

        # Extract fee amount
        fee = 0.0
        fee_match = re.search(r'\d+', fee_str.replace(',', ''))
        if fee_match:
            fee = float(fee_match.group(0))

        # Check or create user
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': name,
                'mobile_number': phone,
                'role': 'doctor',
                'is_active': True
            }
        )
        user.username = name
        user.mobile_number = phone
        user.role = 'doctor'
        user.set_password(password)
        user.save()

        # Check or create DoctorProfile
        profile, p_created = DoctorProfile.objects.get_or_create(
            user=user,
            defaults={
                'full_name': name,
                'experience_years': exp_years,
                'location': location,
                'major_department': spec,
                'languages_speak': languages,
                'license_number': license_no,
                'consultation_fee': fee,
                'available_hours': hours,
                'phone_number': phone,
                'availability_status': 'Active Today'
            }
        )
        profile.full_name = name
        profile.experience_years = exp_years
        profile.location = location
        profile.major_department = spec
        profile.languages_speak = languages
        profile.license_number = license_no
        profile.consultation_fee = fee
        profile.available_hours = hours
        profile.phone_number = phone
        profile.save()

        if created or p_created:
            count_created += 1
        else:
            count_updated += 1

    print(f"Doctors process complete: {count_created} created, {count_updated} updated.")


def register_patients():
    pat_file = os.path.join('templates', 'patient list.txt')
    records = parse_markdown_table(pat_file)
    print(f"\n--- Registering {len(records)} Patients from {pat_file} ---")

    count_created = 0
    count_updated = 0

    for r in records:
        pat_code = r.get('patient id', '').strip()
        name = r.get('name', '').strip()
        if not name:
            continue
        
        password = r.get('password', '').strip() or 'Patient@123'
        dob_str = r.get('date of birth', '').strip()
        gender = r.get('gender', '').strip()
        email = r.get('email address', '').strip().lower()
        marital = r.get('marital status', '').strip()
        location = r.get('location', '').strip()
        languages = r.get('languages', '').strip()
        phone = r.get('phone number', '').strip()
        insurance = r.get('insurance', '').strip()
        em_contact = r.get('emergency contact', '').strip()
        notes = r.get('medical history / notes', '').strip()

        # Parse dob
        dob = None
        if dob_str:
            try:
                dob = datetime.datetime.strptime(dob_str, '%Y-%m-%d').date()
            except Exception:
                dob = None

        # Parse emergency contact: Name (+91 9999999999)
        em_name = em_contact
        em_phone = ""
        em_match = re.search(r'([^(]+)\s*\(([^)]+)\)', em_contact)
        if em_match:
            em_name = em_match.group(1).strip()
            em_phone = em_match.group(2).strip()

        # User
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': name,
                'mobile_number': phone,
                'role': 'patient',
                'is_active': True
            }
        )
        user.username = name
        user.mobile_number = phone
        user.role = 'patient'
        user.set_password(password)
        user.save()

        # PatientProfile
        profile, p_created = PatientProfile.objects.get_or_create(
            user=user,
            defaults={
                'patient_code': pat_code,
                'full_name': name,
                'dob': dob,
                'gender': gender,
                'marital_status': marital,
                'location': location,
                'languages': languages,
                'phone_number': phone,
                'email': email,
                'insurance_details': insurance,
                'emergency_contact_name': em_name,
                'emergency_contact_phone': em_phone,
                'medical_history_notes': notes
            }
        )
        profile.full_name = name
        profile.dob = dob
        profile.gender = gender
        profile.marital_status = marital
        profile.location = location
        profile.languages = languages
        profile.phone_number = phone
        profile.email = email
        profile.insurance_details = insurance
        profile.emergency_contact_name = em_name
        profile.emergency_contact_phone = em_phone
        profile.medical_history_notes = notes
        if pat_code:
            profile.patient_code = pat_code
        profile.save()

        if created or p_created:
            count_created += 1
        else:
            count_updated += 1

    print(f"Patients process complete: {count_created} created, {count_updated} updated.")


def register_caretakers():
    car_file = os.path.join('templates', 'caretaker.txt')
    records = parse_markdown_table(car_file)
    print(f"\n--- Registering {len(records)} Caretakers from {car_file} ---")

    count_created = 0
    count_updated = 0

    for r in records:
        raw_name = r.get('name', '') or r.get('username', '')
        raw_name = raw_name.strip().replace('_', ' ').title()
        if not raw_name:
            continue

        email = r.get('email address', '').strip().lower()
        mobile = r.get('mobile number', '').strip() or r.get('phone number', '').strip()
        password = r.get('password', '').strip() or 'Caretaker@123'
        dob_str = r.get('date of birth', '').strip()
        exp_str = r.get('experience', '').strip()
        fee_str = r.get('daily fee', '').strip()
        phone = r.get('phone number', '').strip() or mobile
        location = r.get('location', '').strip()
        languages = r.get('languages', '').strip()
        license_no = r.get('license / cert', '').strip()
        hours = r.get('available hours', '').strip() or '24/7 Available'
        gender = r.get('available gender', '').strip()

        # Parse dob
        dob = None
        if dob_str:
            try:
                dob = datetime.datetime.strptime(dob_str, '%Y-%m-%d').date()
            except Exception:
                dob = None

        # Experience
        exp_years = 0
        exp_match = re.search(r'\d+', exp_str)
        if exp_match:
            exp_years = int(exp_match.group(0))

        # Fee
        fee = 0.0
        fee_match = re.search(r'\d+', fee_str.replace(',', '').replace('₹', ''))
        if fee_match:
            fee = float(fee_match.group(0))

        # User
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                'username': raw_name,
                'mobile_number': phone,
                'role': 'caretaker',
                'is_active': True
            }
        )
        user.username = raw_name
        user.mobile_number = phone
        user.role = 'caretaker'
        user.set_password(password)
        user.save()

        # CaretakerProfile
        profile, p_created = CaretakerProfile.objects.get_or_create(
            user=user,
            defaults={
                'full_name': raw_name,
                'dob': dob,
                'gender': gender,
                'experience_years': exp_years,
                'location': location,
                'languages': languages,
                'license_number': license_no,
                'consultation_fee': fee,
                'available_hours': hours,
                'phone_number': phone
            }
        )
        profile.full_name = raw_name
        profile.dob = dob
        profile.gender = gender
        profile.experience_years = exp_years
        profile.location = location
        profile.languages = languages
        profile.license_number = license_no
        profile.consultation_fee = fee
        profile.available_hours = hours
        profile.phone_number = phone
        profile.save()

        if created or p_created:
            count_created += 1
        else:
            count_updated += 1

    print(f"Caretakers process complete: {count_created} created, {count_updated} updated.")


if __name__ == '__main__':
    register_doctors()
    register_patients()
    register_caretakers()
    print("\n--- ALL DATASETS REGISTERED SUCCESSFULLY ---")
