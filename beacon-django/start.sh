#!/usr/bin/env bash
# Exit on error
set -o errexit

echo "==> Running database migrations on startup..."
python manage.py migrate --noinput

echo "==> Registering default users and seeding records..."
python register_users_from_files.py || true
python seed_upcoming_patient_and_caretaker_appointments.py || true

echo "==> Starting Gunicorn web server..."
exec gunicorn beacon.wsgi:application --bind 0.0.0.0:$PORT --workers 4
