from django.urls import path

from . import views

urlpatterns = [
    path("legal/politica", views.PoliticaVigenteView.as_view(), name="politica-vigente"),
    path("legal/politicas", views.PoliticaListCreateView.as_view(), name="politicas"),
]
