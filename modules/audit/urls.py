from django.urls import path

from . import views

urlpatterns = [
    path("auditoria", views.AuditLogViewSet.as_view({"get": "list"}), name="auditoria"),
    path("auditoria/acciones", views.audit_actions, name="auditoria-acciones"),
    path("auditoria/<uuid:pk>", views.AuditLogViewSet.as_view({"get": "retrieve"}), name="auditoria-detalle"),
]
