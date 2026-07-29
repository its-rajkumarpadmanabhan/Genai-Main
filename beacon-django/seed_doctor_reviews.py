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
from accounts.models import DoctorProfile, PatientProfile, Appointment, Review
from django.db.models import Avg

User = get_user_model()

def seed_reviews():
    print("=== SEEDING PATIENT REVIEWS FOR DOCTORS ===")

    patients = list(User.objects.filter(role='patient'))
    doctors = list(DoctorProfile.objects.all())

    print(f"Loaded {len(patients)} Patients and {len(doctors)} DoctorProfiles.")

    if not patients or not doctors:
        print("Error: Missing patients or doctor profiles!")
        return

    comments_pool = [
        "Excellent consultation, diagnosed my condition accurately!",
        "Very polite and attentive doctor. Explained all instructions clearly.",
        "Prompt, professional, and compassionate medical care. Highly recommend!",
        "Great experience! The prescribed treatment worked wonderfully within days.",
        "Very experienced doctor. Listened patiently to all my concerns.",
        "Clean clinic environment and very clear medical guidance.",
        "Outstanding doctor! Provided clear dietary and prescription advice.",
        "Extremely satisfied with the treatment and follow-up care.",
        "Thorough medical evaluation and very polite demeanor.",
        "Very helpful guidance for my ongoing health condition.",
        "Impressive medical knowledge and warm patient care.",
        "Professional checkup. Answered all my questions in detail."
    ]

    reviews_to_create = []
    today = datetime.date(2026, 7, 29)

    for doc in doctors:
        # Get patients who had appointments with this doctor
        doc_pat_ids = list(Appointment.objects.filter(doctor=doc.user).values_list('patient_id', flat=True).distinct())
        if doc_pat_ids:
            doc_pats = list(User.objects.filter(id__in=doc_pat_ids))
        else:
            doc_pats = random.sample(patients, min(5, len(patients)))

        # Create 2 to 4 reviews per doctor
        num_reviews = random.randint(2, 4)
        chosen_pats = random.sample(doc_pats, min(num_reviews, len(doc_pats)))

        for pat in chosen_pats:

            rating = random.choice([5, 5, 5, 4, 4, 5])
            comment = random.choice(comments_pool)
            days_ago = random.randint(1, 120)

            reviews_to_create.append(Review(
                user=pat,
                doctor=doc,
                rating=rating,
                comment=comment
            ))

    # Bulk create reviews
    Review.objects.filter(doctor__isnull=False).delete()
    Review.objects.bulk_create(reviews_to_create)
    print(f"Created {len(reviews_to_create)} genuine Patient Reviews for Doctors!")

    # Recalculate rating_avg and reviews_count on DoctorProfiles
    print("\nRecalculating rating_avg and reviews_count for all Doctor Profiles...")
    for doc in doctors:
        d_reviews = doc.reviews.all()
        cnt = d_reviews.count()
        if cnt > 0:
            avg_r = round(float(d_reviews.aggregate(Avg('rating'))['rating__avg'] or 5.0), 1)
            doc.rating_avg = avg_r
            doc.reviews_count = cnt
        else:
            doc.rating_avg = 5.0
            doc.reviews_count = 0
        doc.save()

    print("=== PATIENT REVIEWS SEEDING COMPLETE ===")

if __name__ == '__main__':
    seed_reviews()
