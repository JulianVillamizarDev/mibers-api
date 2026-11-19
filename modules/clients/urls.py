from django.urls import path

from . import views

urlpatterns = [
    path("clientes", views.ClienteListCreateView.as_view(), name="clientes"),
    path("clientes/<uuid:pk>", views.ClienteDetailView.as_view(), name="cliente-detalle"),
    path(
        "clientes/<uuid:pk>/desactivar",
        views.ClienteStateView.as_view(activate=False),
        name="cliente-desactivar",
    ),
    path(
        "clientes/<uuid:pk>/reactivar",
        views.ClienteStateView.as_view(activate=True),
        name="cliente-reactivar",
    ),
    path("clientes/<uuid:pk>/manual-marca", views.ManualMarcaView.as_view(), name="cliente-manual"),
    path("clientes/<uuid:pk>/contactos", views.ContactoListCreateView.as_view(), name="cliente-contactos"),
    path(
        "clientes/<uuid:pk>/contactos/<uuid:contacto_id>",
        views.ContactoDetailView.as_view(),
        name="cliente-contacto",
    ),
]
