import os
import re
import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model
from accounts.models import DoctorProfile, PatientProfile, CaretakerProfile

User = get_user_model()

class Command(BaseCommand):
    help = 'Imports doctors, patients, and caretakers from txt lists.'

    def handle(self, *args, **options):
        # 1. Paths
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        templates_dir = os.path.join(base_dir, 'templates')
        
        doctors_file = os.path.join(templates_dir, 'doctors list.txt')
        patients_file = os.path.join(templates_dir, 'patient list.txt')
        caretakers_file = os.path.join(templates_dir, 'caretaker.txt')

        # Run import processes
        self.import_doctors(doctors_file)
        self.import_patients(patients_file)
        self.import_caretakers(caretakers_file)

    def clean_email(self, val):
        if not val:
            return ''
        match = re.search(r'\[([^\]]+)\]', val)
        if match:
            return match.group(1).strip()
        return val.strip()

    def parse_markdown_table(self, filepath):
        if not os.path.exists(filepath):
            self.stdout.write(self.style.WARNING(f"File not found: {filepath}"))
            return []
            
        rows = []
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        headers = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith('|') and line.endswith('|'):
                parts = [p.strip() for p in line.split('|')[1:-1]]
                if all(c == '-' or c == ' ' or c == ':' for c in ''.join(parts)):
                    continue
                
                # Check if this line is header
                if any(h in ['Name', 'Patient ID', 'Phone Number', 'License No.'] for h in parts):
                    headers = parts
                elif headers:
                    if len(parts) == len(headers):
                        row_dict = dict(zip(headers, parts))
                        rows.append(row_dict)
                    elif len(parts) > len(headers):
                        row_dict = dict(zip(headers, parts[:len(headers)]))
                        rows.append(row_dict)
        return rows

    def import_doctors(self, filepath):
        self.stdout.write("Importing Doctors...")
        rows = self.parse_markdown_table(filepath)
        if not rows:
            self.stdout.write("No doctor rows parsed.")
            return

        created_count = 0
        skipped_count = 0

        for row in rows:
            name = row.get('Name') or row.get('Name ')
            phone = row.get('Phone Number') or row.get('Phone Number ')
            email = self.clean_email(row.get('Email ID') or row.get('Email ID '))
            password = row.get('Password') or row.get('Password ')
            license_no = row.get('License No.') or row.get('License No. ')
            experience = row.get('Experience') or row.get('Experience ')
            fee_str = row.get('Consultation Fee') or row.get('Consultation Fee ')
            hours = row.get('Available Hours') or row.get('Available Hours ')
            location = row.get('Location') or row.get('Location ')
            languages = row.get('Languages') or row.get('Languages ')
            specialization = row.get('Specialization') or row.get('Specialization ')

            if not email or not name:
                continue

            # Deduplication
            if User.objects.filter(email__iexact=email).exists():
                skipped_count += 1
                continue
                
            if license_no and DoctorProfile.objects.filter(license_number__iexact=license_no).exists():
                skipped_count += 1
                continue

            # Parse experience
            exp_years = 0
            if experience:
                match = re.search(r'\d+', experience)
                if match:
                    exp_years = int(match.group())

            # Parse fee
            fee = 0.0
            if fee_str:
                match = re.search(r'\d+', fee_str)
                if match:
                    fee = float(match.group())

            # Build username
            username = email.split('@')[0].lower().replace('.', ' ').replace('_', ' ')
            base_username = username
            idx = 1
            while User.objects.filter(username__iexact=username).exists():
                username = f"{base_username} {idx}"
                idx += 1

            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        mobile_number=phone,
                        password=password or 'DefaultPass123!',
                        role='doctor',
                        plain_password=password or 'DefaultPass123!'
                    )
                    DoctorProfile.objects.create(
                        user=user,
                        full_name=name,
                        experience_years=exp_years,
                        consultation_fee=fee,
                        available_hours=hours or '09:00 AM - 05:00 PM',
                        location=location or 'Not provided',
                        languages_speak=languages or 'English',
                        major_department=specialization or 'General Practitioner',
                        license_number=license_no or 'LIC-GEN-001'
                    )
                    created_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating doctor {name}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS(f"Finished Doctors. Created: {created_count}, Skipped (Duplicates): {skipped_count}"))

    def import_patients(self, filepath):
        self.stdout.write("Importing Patients...")
        rows = self.parse_markdown_table(filepath)
        if not rows:
            self.stdout.write("No patient rows parsed.")
            return

        created_count = 0
        skipped_count = 0

        for row in rows:
            patient_id = row.get('Patient ID')
            name = row.get('Name')
            password = row.get('Password')
            dob_str = row.get('Date of Birth')
            gender = row.get('Gender')
            email = self.clean_email(row.get('Email Address') or row.get('Email'))
            marital = row.get('Marital Status')
            location = row.get('Location')
            languages = row.get('Languages')
            phone = row.get('Phone Number')
            insurance = row.get('Insurance')
            emergency = row.get('Emergency Contact')
            medical_history = row.get('Medical History / Notes')

            if not email or not name:
                continue

            # Deduplication
            if User.objects.filter(email__iexact=email).exists():
                skipped_count += 1
                continue
                
            if patient_id and PatientProfile.objects.filter(patient_code__iexact=patient_id).exists():
                skipped_count += 1
                continue

            # Parse DOB
            dob = None
            if dob_str:
                try:
                    dob = datetime.datetime.strptime(dob_str.strip(), '%Y-%m-%d').date()
                except ValueError:
                    pass

            # Parse emergency contact details
            em_name = ''
            em_phone = ''
            if emergency:
                match = re.search(r'([^(]+)\(([^)]+)\)', emergency)
                if match:
                    em_name = match.group(1).strip()
                    em_phone = match.group(2).strip()
                else:
                    em_name = emergency

            # Build username
            username = email.split('@')[0].lower().replace('.', ' ').replace('_', ' ')
            base_username = username
            idx = 1
            while User.objects.filter(username__iexact=username).exists():
                username = f"{base_username} {idx}"
                idx += 1

            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        mobile_number=phone,
                        password=password or 'DefaultPass123!',
                        role='patient',
                        plain_password=password or 'DefaultPass123!'
                    )
                    PatientProfile.objects.create(
                        user=user,
                        patient_code=patient_id,
                        full_name=name,
                        dob=dob,
                        gender=gender or 'Not provided',
                        marital_status=marital or 'Not provided',
                        location=location or 'Not provided',
                        languages=languages or 'English',
                        phone_number=phone,
                        insurance_details=insurance or 'None',
                        emergency_contact_name=em_name,
                        emergency_contact_phone=em_phone,
                        medical_history_notes=medical_history or ''
                    )
                    created_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating patient {name}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS(f"Finished Patients. Created: {created_count}, Skipped (Duplicates): {skipped_count}"))

    def import_caretakers(self, filepath):
        self.stdout.write("Importing Caretakers...")
        rows = self.parse_markdown_table(filepath)
        if not rows:
            self.stdout.write("No caretaker rows parsed (file may be empty).")
            return

        created_count = 0
        skipped_count = 0

        for row in rows:
            name = row.get('Name') or row.get('Username') or row.get('Name ') or row.get('Username ')
            phone = row.get('Phone Number') or row.get('Phone Number ') or row.get('Mobile Number') or row.get('Mobile Number ')
            email = self.clean_email(row.get('Email Address') or row.get('Email Address ') or row.get('Email ID') or row.get('Email ID ') or row.get('Email'))
            password = row.get('Password') or row.get('Password ')
            license_no = row.get('License / Cert') or row.get('License / Cert ') or row.get('License No.') or row.get('License No. ') or row.get('Certificate No.')
            experience = row.get('Experience') or row.get('Experience ')
            fee_str = row.get('Daily Fee') or row.get('Daily Fee ') or row.get('Consultation Fee') or row.get('Consultation Fee ')
            hours = row.get('Available Hours') or row.get('Available Hours ')
            location = row.get('Location') or row.get('Location ')
            languages = row.get('Languages') or row.get('Languages ')
            gender = row.get('Available Gender') or row.get('Available Gender ') or row.get('Gender') or row.get('Gender ') or row.get('gender')

            if not email or not name:
                continue

            # Deduplication
            if User.objects.filter(email__iexact=email).exists():
                skipped_count += 1
                continue

            # Parse experience
            exp_years = 0
            if experience:
                match = re.search(r'\d+', experience)
                if match:
                    exp_years = int(match.group())

            # Parse fee
            fee = 0.0
            if fee_str:
                # Remove currency symbols or extra characters like /day
                cleaned_fee_str = fee_str.replace(',', '').replace('₹', '')
                match = re.search(r'\d+(?:\.\d+)?', cleaned_fee_str)
                if match:
                    fee = float(match.group())

            # Parse DOB
            dob_str = row.get('Date of Birth') or row.get('Date of Birth ')
            dob = None
            if dob_str:
                try:
                    dob = datetime.datetime.strptime(dob_str.strip(), '%Y-%m-%d').date()
                except ValueError:
                    pass

            # Build username
            username = email.split('@')[0].lower().replace('.', ' ').replace('_', ' ')
            base_username = username
            idx = 1
            while User.objects.filter(username__iexact=username).exists():
                username = f"{base_username} {idx}"
                idx += 1

            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        mobile_number=phone,
                        password=password or 'DefaultPass123!',
                        role='caretaker',
                        plain_password=password or 'DefaultPass123!'
                    )
                    CaretakerProfile.objects.create(
                        user=user,
                        full_name=name,
                        dob=dob,
                        gender=gender or 'Not provided',
                        experience_years=exp_years,
                        location=location or 'Not provided',
                        languages=languages or 'English',
                        license_number=license_no or 'LIC-CAR-001',
                        consultation_fee=fee,
                        available_hours=hours or '24/7 Available',
                        phone_number=phone
                    )
                    created_count += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error creating caretaker {name}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS(f"Finished Caretakers. Created: {created_count}, Skipped (Duplicates): {skipped_count}"))
