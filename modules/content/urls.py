from django.urls import path

from . import views

urlpatterns = [
    path("clientes/actuales", views.EmpresasActualesView.as_view(), name="empresas-actuales"),
    path("clientes/<uuid:pk>/cronograma", views.CronogramaView.as_view(), name="cronograma"),
    path("piezas", views.PiezaListCreateView.as_view(), name="piezas"),
    path("piezas/<uuid:pk>", views.PiezaDetailView.as_view(), name="pieza-detalle"),
    path("piezas/<uuid:pk>/archivos", views.ArchivoCreateView.as_view(), name="pieza-archivos"),
    path("piezas/<uuid:pk>/archivos/orden", views.ArchivoOrdenView.as_view(), name="pieza-archivos-orden"),
    path(
        "piezas/<uuid:pk>/archivos/<uuid:archivo_id>", views.ArchivoDetailView.as_view(), name="pieza-archivo"
    ),
    path("piezas/<uuid:pk>/archivo-editable", views.ArchivoEditableView.as_view(), name="pieza-editable"),
    path("piezas/<uuid:pk>/enviar-revision", views.EnviarRevisionView.as_view(), name="pieza-enviar"),
    path("piezas/<uuid:pk>/aprobar", views.AprobarView.as_view(), name="pieza-aprobar"),
    path("piezas/<uuid:pk>/solicitar-cambios", views.SolicitarCambiosView.as_view(), name="pieza-cambios"),
    path("piezas/<uuid:pk>/revisiones", views.RevisionesView.as_view(), name="pieza-revisiones"),
    path("archivos/<uuid:archivo_id>", views.ArchivoDownloadView.as_view(), name="archivo-descarga"),
]
