from django.urls import include, path

urlpatterns = [
    path("api/", include("analytics.api.urls")),
]

# Unmatched paths and unexpected errors answer with the same JSON envelope as the rest of the API.
handler404 = "analytics.api.exception_handler.not_found"
handler500 = "analytics.api.exception_handler.server_error"
