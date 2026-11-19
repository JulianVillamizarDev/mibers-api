from django.urls import path

from . import views

urlpatterns = [
    path("clientes/actuales", views.EmpresasActualesView.as_view(), name="empresas-actuales"),
]
