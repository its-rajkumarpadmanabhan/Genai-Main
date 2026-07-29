"""
Beacon URL Configuration
All routes preserved exactly as the original FastAPI app.
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView
from django.views.decorators.cache import never_cache

urlpatterns = [
    # Django admin
    path('admin/', admin.site.urls),

    # ── Auth API (/api/auth/*) ─────────────────────────────────────────────
    path('api/auth/', include('accounts.urls')),

    # ── Core API (/api/*) ─────────────────────────────────────────────────
    path('api/', include('core.urls')),

    # ── HTML Template routes ───────────────────────────────────────────────
    # Root → main app (app.html checks beacon_token, redirects to login if missing)
    path('', never_cache(TemplateView.as_view(template_name='app.html')), name='home'),
    path('app', never_cache(TemplateView.as_view(template_name='app.html')), name='app_clean'),
    path('app.html', never_cache(TemplateView.as_view(template_name='app.html')), name='app'),

    # Auth pages
    path('login', TemplateView.as_view(template_name='login.html'), name='login_clean'),
    path('login.html', TemplateView.as_view(template_name='login.html'), name='login'),
    path('signup', TemplateView.as_view(template_name='signup.html'), name='signup_clean'),
    path('signup.html', TemplateView.as_view(template_name='signup.html'), name='signup'),
    path('forgot-password', TemplateView.as_view(template_name='forgot-password.html'), name='forgot-password_clean'),
    path('forgot-password.html', TemplateView.as_view(template_name='forgot-password.html'), name='forgot-password'),
    path('reset-password', TemplateView.as_view(template_name='reset-password.html'), name='reset-password_clean'),
    path('reset-password.html', TemplateView.as_view(template_name='reset-password.html'), name='reset-password'),

    # Profile & Dashboard pages
    path('admin-dashboard', never_cache(TemplateView.as_view(template_name='admin-dashboard.html')), name='admin-dashboard_clean'),
    path('admin-dashboard.html', never_cache(TemplateView.as_view(template_name='admin-dashboard.html')), name='admin-dashboard'),
    path('caretaker-dashboard', never_cache(TemplateView.as_view(template_name='caretaker-dashboard.html')), name='caretaker-dashboard_clean'),
    path('caretaker-dashboard.html', never_cache(TemplateView.as_view(template_name='caretaker-dashboard.html')), name='caretaker-dashboard'),
    path('doctor-caretaker-list', never_cache(TemplateView.as_view(template_name='doctor caretaker list.html')), name='doctor-caretaker-list_clean'),
    path('doctor-caretaker-list.html', never_cache(TemplateView.as_view(template_name='doctor caretaker list.html')), name='doctor-caretaker-list'),
    path('doctor-patients-list', never_cache(TemplateView.as_view(template_name='doctor-patients-list.html')), name='doctor-patients-list_clean'),
    path('doctor-patients-list.html', never_cache(TemplateView.as_view(template_name='doctor-patients-list.html')), name='doctor-patients-list'),
    path('doctor-profile', never_cache(TemplateView.as_view(template_name='doctor-profile.html')), name='doctor-profile_clean'),
    path('doctor-profile.html', never_cache(TemplateView.as_view(template_name='doctor-profile.html')), name='doctor-profile'),
    path('patient-profile', never_cache(TemplateView.as_view(template_name='patient-profile.html')), name='patient-profile_clean'),
    path('patient-profile.html', never_cache(TemplateView.as_view(template_name='patient-profile.html')), name='patient-profile'),
    path('patient-appointments', never_cache(TemplateView.as_view(template_name='patient-appointments.html')), name='patient-appointments_clean'),
    path('patient-appointments.html', never_cache(TemplateView.as_view(template_name='patient-appointments.html')), name='patient-appointments'),
    path('voice-timeline', never_cache(TemplateView.as_view(template_name='voice-timeline.html')), name='voice-timeline_clean'),
    path('voice-timeline.html', never_cache(TemplateView.as_view(template_name='voice-timeline.html')), name='voice-timeline'),
]
