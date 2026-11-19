from django.urls import path

from . import views

urlpatterns = [
    path("metricas", views.MetricaListView.as_view(), name="metricas"),
    path("clientes/<uuid:pk>/metricas", views.ClienteMetricasView.as_view(), name="cliente-metricas"),
]
