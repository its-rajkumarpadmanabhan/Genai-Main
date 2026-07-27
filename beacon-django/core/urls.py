from django.urls import path

from .views import HealthView, NearestHospitalsView, VoiceInterventionView

# All paths are relative to /api/ (prefixed in beacon/urls.py)
urlpatterns = [
    path('health', HealthView.as_view(), name='health'),
    path('nearest-hospitals', NearestHospitalsView.as_view(), name='nearest-hospitals'),
    path('voice-intervention', VoiceInterventionView.as_view(), name='voice-intervention'),
]
