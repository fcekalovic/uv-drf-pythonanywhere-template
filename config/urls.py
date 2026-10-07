"""URL configuration for the project."""

from django.contrib import admin
from django.urls import path

from core.views import DeployView, HealthCheckView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", HealthCheckView.as_view(), name="health-check"),
    path("deploy/", DeployView.as_view(), name="deploy"),
]
