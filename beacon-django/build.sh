#!/usr/bin/env bash
# Exit on error
set -o errexit

echo "==> Installing dependencies..."
pip install -r requirements.txt

echo "==> Collecting static files..."
python manage.py collectstatic --noinput

echo "==> Running database migrations..."
python manage.py migrate

echo "==> Registering default users & profiles..."
python register_users_from_files.py || true

echo "==> Seeding initial appointments..."
python seed_upcoming_patient_and_caretaker_appointments.py || true

echo "==> Build completed successfully!"
