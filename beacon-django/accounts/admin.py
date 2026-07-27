from django.contrib import admin
from django.contrib.auth import get_user_model
from .models import PasswordResetToken

User = get_user_model()


@admin.register(User)
class CustomUserAdmin(admin.ModelAdmin):
    list_display = ('id', 'username', 'email', 'mobile_number', 'is_active', 'created_at')
    search_fields = ('username', 'email', 'mobile_number')
    list_filter = ('is_active', 'is_staff')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)


@admin.register(PasswordResetToken)
class PasswordResetTokenAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'expires_at', 'used')
    list_filter = ('used',)
    ordering = ('-expires_at',)
