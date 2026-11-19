from django.urls import path

from . import views

urlpatterns = [
    path("clientes", views.ClienteListCreateView.as_view(), name="clientes"),
    path("clientes/<uuid:pk>", views.ClienteDetailView.as_view(), name="cliente-detalle"),
    path("clientes/<uuid:pk>/manual-marca", views.ManualMarcaView.as_view(), name="cliente-manual"),
]
