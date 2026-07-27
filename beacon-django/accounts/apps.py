from django.apps import AppConfig
from django.db.models.signals import post_migrate


def create_default_admin(sender, **kwargs):
    from accounts.models import CustomUser
    try:
        user, _ = CustomUser.objects.get_or_create(
            username='admin',
            defaults={
                'email': 'admin@hospitaltest.com',
                'role': 'admin',
                'is_staff': True,
                'is_superuser': True
            }
        )
        user.email = 'admin@hospitaltest.com'
        user.role = 'admin'
        user.is_staff = True
        user.is_superuser = True
        user.set_password('Admin@123')
        user.save()
    except Exception:
        pass


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'
    verbose_name = 'Beacon Accounts'

    def ready(self):
        post_migrate.connect(create_default_admin, sender=self)

