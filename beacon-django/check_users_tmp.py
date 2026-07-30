from accounts.models import CustomUser
print('Doctors:', CustomUser.objects.filter(role='doctor', is_active=True).count())
print('Patients:', CustomUser.objects.filter(role='patient', is_active=True).count())
print('Caretakers:', CustomUser.objects.filter(role='caretaker', is_active=True).count())

# First 5 of each
print('\nFirst 5 Doctors:')
for u in CustomUser.objects.filter(role='doctor', is_active=True)[:5]:
    print(f'  {u.id} {u.username}')
print('\nFirst 5 Patients:')
for u in CustomUser.objects.filter(role='patient', is_active=True)[:5]:
    print(f'  {u.id} {u.username}')
print('\nFirst 5 Caretakers:')
for u in CustomUser.objects.filter(role='caretaker', is_active=True)[:5]:
    print(f'  {u.id} {u.username}')
