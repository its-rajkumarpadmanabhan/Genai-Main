"""
Beacon URL Configuration
All routes preserved exactly as the original FastAPI app.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

urlpatterns = [
    # Django admin
    path('admin/', admin.site.urls),

    # ── Auth API (/api/auth/*) ─────────────────────────────────────────────
    path('api/auth/', include('accounts.urls')),

    # ── Core API (/api/*) ─────────────────────────────────────────────────
    path('api/', include('core.urls')),

    # ── HTML Template routes ───────────────────────────────────────────────
    # Root → main app (app.html checks beacon_token, redirects to login.html if missing)
    path('', TemplateView.as_view(template_name='app.html'), name='home'),
    path('app.html', TemplateView.as_view(template_name='app.html'), name='app'),

    # Auth pages
    path('login.html', TemplateView.as_view(template_name='login.html'), name='login'),
    path('signup.html', TemplateView.as_view(template_name='signup.html'), name='signup'),
    path('forgot-password.html', TemplateView.as_view(template_name='forgot-password.html'), name='forgot-password'),
    path('reset-password.html', TemplateView.as_view(template_name='reset-password.html'), name='reset-password'),

    # Profile & Dashboard pages
    path('admin-dashboard.html', TemplateView.as_view(template_name='admin-dashboard.html'), name='admin-dashboard'),
    path('caretaker-dashboard.html', TemplateView.as_view(template_name='caretaker-dashboard.html'), name='caretaker-dashboard'),
    path('doctor-caretaker-list.html', TemplateView.as_view(template_name='doctor caretaker list.html'), name='doctor-caretaker-list'),
    path('doctor-profile.html', TemplateView.as_view(template_name='doctor-profile.html'), name='doctor-profile'),
    path('patient-profile.html', TemplateView.as_view(template_name='patient-profile.html'), name='patient-profile'),
]
