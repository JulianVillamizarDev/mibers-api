from django.urls import path

from . import views

urlpatterns = [
    path("alertas", views.AlertaListView.as_view(), name="alertas"),
    path("alertas/<uuid:pk>/leer", views.AlertaReadView.as_view(), name="alerta-leer"),
    path("webhooks/whatsapp", views.WhatsAppWebhookView.as_view(), name="webhook-whatsapp"),
]
