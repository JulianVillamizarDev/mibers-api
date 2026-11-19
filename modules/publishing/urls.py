from django.urls import path

from . import views

urlpatterns = [
    path("public/media/<uuid:archivo_id>", views.PublicMediaView.as_view(), name="public-media"),
    path(
        "piezas/<uuid:pk>/programacion", views.ProgramacionPreviewView.as_view(), name="programacion-preview"
    ),
    path("piezas/<uuid:pk>/programar", views.ProgramarView.as_view(), name="programar"),
    path(
        "piezas/<uuid:pk>/cancelar-programacion",
        views.CancelarProgramacionView.as_view(),
        name="cancelar-programacion",
    ),
    path(
        "piezas/<uuid:pk>/publicaciones", views.PiezaPublicacionesView.as_view(), name="pieza-publicaciones"
    ),
    path("publicaciones", views.TareaListView.as_view(), name="publicaciones"),
    path("publicaciones/cumplimiento", views.CumplimientoView.as_view(), name="publicaciones-cumplimiento"),
    path("publicaciones/<uuid:pk>", views.TareaDetailView.as_view(), name="publicacion"),
    path("publicaciones/<uuid:pk>/confirmar", views.ConfirmarView.as_view(), name="publicacion-confirmar"),
    path("publicaciones/<uuid:pk>/fallar", views.FallarView.as_view(), name="publicacion-fallar"),
    path("publicaciones/<uuid:pk>/reintentar", views.ReintentarView.as_view(), name="publicacion-reintentar"),
]
