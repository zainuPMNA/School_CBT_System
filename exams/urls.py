from django.urls import path

from . import views

app_name = "cbt"

urlpatterns = [
    path("<int:pk>/", views.instructions, name="instructions"),
    path("<int:pk>/start/", views.start, name="start"),
    path("<int:pk>/take/", views.take, name="take"),
    path("<int:pk>/submit/", views.submit, name="submit"),
    path("<int:pk>/submitted/", views.submitted, name="submitted"),
    path("<int:pk>/result/", views.result, name="result"),
    path("<int:pk>/result.pdf", views.result_pdf, name="result_pdf"),
    path("<int:pk>/api/sync/", views.api_sync, name="api_sync"),
    path("<int:pk>/api/save/", views.api_save, name="api_save"),
    path("<int:pk>/api/violation/", views.api_violation, name="api_violation"),
]
