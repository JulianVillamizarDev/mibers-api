from django.conf import settings
from django.urls import include, path

urlpatterns = [
    path("", include("modules.core.urls")),
    path("api/v1/", include("modules.accounts.urls")),
    path("api/v1/", include("modules.audit.urls")),
    path("api/v1/", include("modules.clients.urls")),
    path("api/v1/", include("modules.content.urls")),
    path("api/v1/", include("modules.legal.urls")),
]

if settings.HAS_SPECTACULAR:
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path("api/schema", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    ]
