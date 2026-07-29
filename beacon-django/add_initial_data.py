"""Script to add initial patient, doctor, and caretaker users to the database.
Run with: python manage.py runscript add_initial_data (if django-extensions installed) or directly via python add_initial_data.py
"""
import os
import django
import datetime

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'beacon.settings')
django.setup()

from accounts.models import CustomUser, PatientProfile, DoctorProfile, CaretakerProfile

# Helper to create user and set role
def create_user(username, email, mobile, password, role):
    user = CustomUser.objects.create_user(username=username, email=email, mobile_number=mobile, password=password)
    user.role = role
    user.plain_password = password
    user.save()
    return user

# Patients
patients_data = [
    {
        "username": "devika_krishnan",
        "email": "devika.krishnan@patienttest.com",
        "mobile": "+91 9876501036",
        "password": "Patient@136",
        "dob": "1999-01-12",
        "gender": "Female",
        "marital_status": "Single",
        "location": "Kazhakkoottam, Trivandrum",
        "languages": "Malayalam, English",
        "insurance": "INS09UIYH78657670036GH",
        "emergency_name": "Mohan Krishnan",
        "emergency_phone": "9898901036",
    },
    {
        "username": "mohan_babu",
        "email": "mohan.babu@patienttest.com",
        "mobile": "+91 9876501037",
        "password": "Patient@137",
        "dob": "1985-04-08",
        "gender": "Male",
        "marital_status": "Married",
        "location": "Kottayam",
        "languages": "Malayalam, English",
        "insurance": "INS09UIYH78657670037GH",
        "emergency_name": "Latha Babu",
        "emergency_phone": "9898901037",
    },
]
for p in patients_data:
    user = create_user(p["username"], p["email"], p["mobile"], p["password"], "patient")
    PatientProfile.objects.create(
        user=user,
        full_name=p["username"].replace('_', ' ').title(),
        dob=datetime.datetime.strptime(p["dob"], "%Y-%m-%d").date(),
        gender=p["gender"],
        marital_status=p["marital_status"],
        location=p["location"],
        languages=p["languages"],
        phone_number=p["mobile"],
        email=p["email"],
        insurance_details=p["insurance"],
        emergency_contact_name=p["emergency_name"],
        emergency_contact_phone=p["emergency_phone"],
    )

# Doctors
doctors_data = [
    {
        "full_name": "Aditya Malhotra",
        "mobile": "+91 9876543326",
        "email": "aditya.malhotra@hospitaltest.com",
        "password": "Aditya@226",
        "license_number": "MCI-MH-2020-12026",
        "experience_years": 9,
        "consultation_fee": 800.00,
        "available_hours": "09:00 AM - 05:00 PM",
        "location": "Cardiology Wing, Block A",
        "languages": "English, Hindi",
        "specialized_details": "Cardiology",
    },
    {
        "full_name": "Bhavana Nair",
        "mobile": "+91 9876543327",
        "email": "bhavana.nair@hospitaltest.com",
        "password": "Bhavana@227",
        "license_number": "MCI-KL-2019-12027",
        "experience_years": 12,
        "consultation_fee": 900.00,
        "available_hours": "10:00 AM - 06:00 PM",
        "location": "Neurology Wing, Block B",
        "languages": "English, Malayalam",
        "specialized_details": "Neurology",
    },
]
for d in doctors_data:
    username = d["full_name"].lower().replace(' ', '_')
    user = create_user(username, d["email"], d["mobile"], d["password"], "doctor")
    DoctorProfile.objects.create(
        user=user,
        full_name=d["full_name"],
        experience_years=d["experience_years"],
        license_number=d["license_number"],
        consultation_fee=d["consultation_fee"],
        available_hours=d["available_hours"],
        location=d["location"],
        languages_speak=d["languages"],
        specialized_details=d["specialized_details"],
    )

# Caretakers
caretakers_data = [
    {
        "username": "vishnu",
        "email": "vishnu@hospitaltest.com",
        "mobile": "+91 9876543332",
        "password": "Care@182",
        "dob": "1994-11-26",
        "gender": "Male",
        "experience_years": 6,
        "consultation_fee": 340.00,
        "available_hours": "24/7 Available",
        "location": "Kakkanad, Kochi",
        "languages": "Malayalam, English",
        "license_number": "CCFD564YTHGYT182",
    },
    {
        "username": "yamuna",
        "email": "yamuna@hospitaltest.com",
        "mobile": "+91 9876543333",
        "password": "Care@183",
        "dob": "1999-01-31",
        "gender": "Female",
        "experience_years": 2,
        "consultation_fee": 180.00,
        "available_hours": "09:00 AM - 05:00 PM",
        "location": "Aluva, Kochi",
        "languages": "Malayalam, Tamil",
        "license_number": "CCFD564YTHGYT183",
    },
]
for c in caretakers_data:
    user = create_user(c["username"], c["email"], c["mobile"], c["password"], "caretaker")
    CaretakerProfile.objects.create(
        user=user,
        full_name=c["username"].title(),
        dob=datetime.datetime.strptime(c["dob"], "%Y-%m-%d").date(),
        gender=c["gender"],
        experience_years=c["experience_years"],
        license_number=c["license_number"],
        consultation_fee=c["consultation_fee"],
        available_hours=c["available_hours"],
        location=c["location"],
        languages=c["languages"],
        phone_number=c["mobile"],
    )

print('User data insertion completed.')
