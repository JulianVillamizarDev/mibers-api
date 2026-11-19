from django.urls import path

from . import views

urlpatterns = [
    path("clientes/actuales", views.EmpresasActualesView.as_view(), name="empresas-actuales"),
    path("piezas/<uuid:pk>/archivos", views.ArchivoCreateView.as_view(), name="pieza-archivos"),
    path("piezas/<uuid:pk>/archivos/orden", views.ArchivoOrdenView.as_view(), name="pieza-archivos-orden"),
    path(
        "piezas/<uuid:pk>/archivos/<uuid:archivo_id>", views.ArchivoDetailView.as_view(), name="pieza-archivo"
    ),
    path("piezas/<uuid:pk>/archivo-editable", views.ArchivoEditableView.as_view(), name="pieza-editable"),
    path("archivos/<uuid:archivo_id>", views.ArchivoDownloadView.as_view(), name="archivo-descarga"),
]
