from django.urls import path

from . import views

urlpatterns = [
    path("public/media/<uuid:archivo_id>", views.PublicMediaView.as_view(), name="public-media"),
]
