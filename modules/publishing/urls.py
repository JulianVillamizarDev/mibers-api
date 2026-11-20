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
]
