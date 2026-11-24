from django.urls import path

from . import views, views_backup, views_monitor, views_results

app_name = "dashboard"

urlpatterns = [
    # Live lab monitor
    path("monitor/", views_monitor.MonitorIndexView.as_view(), name="monitor"),
    path("monitor/<int:pk>/", views_monitor.MonitorExamView.as_view(), name="monitor_exam"),
    path("monitor/<int:pk>/table/", views_monitor.MonitorTableView.as_view(), name="monitor_table"),
    path("monitor/<int:pk>/extend-all/", views_monitor.MonitorExtendAllView.as_view(), name="monitor_extend_all"),
    path("monitor/<int:pk>/submit-all/", views_monitor.MonitorForceSubmitAllView.as_view(), name="monitor_force_submit_all"),
    path("monitor/<int:pk>/unlock/<int:student_pk>/", views_monitor.MonitorUnlockView.as_view(), name="monitor_unlock"),
    path("monitor/<int:pk>/attempt/<int:attempt_pk>/extend/", views_monitor.MonitorExtendView.as_view(), name="monitor_extend"),
    path("monitor/<int:pk>/attempt/<int:attempt_pk>/submit/", views_monitor.MonitorForceSubmitView.as_view(), name="monitor_force_submit"),
    path("monitor/<int:pk>/attempt/<int:attempt_pk>/violations/", views_monitor.MonitorViolationsView.as_view(), name="monitor_violations"),
    # Results & analytics
    path("exams/<int:pk>/results/", views_results.ExamResultsView.as_view(), name="exam_results"),
    path("exams/<int:pk>/results/questions/", views_results.QuestionAnalysisView.as_view(), name="question_analysis"),
    path("exams/<int:pk>/results/publish/", views_results.PublishResultsView.as_view(), name="publish_results"),
    path("exams/<int:pk>/results/unpublish/", views_results.UnpublishResultsView.as_view(), name="unpublish_results"),
    path("exams/<int:pk>/results/export.<str:fmt>", views_results.ExportResultsView.as_view(), name="export_results"),
    path("exams/<int:pk>/results/scorecards.pdf", views_results.ScorecardsPdfView.as_view(), name="scorecards_pdf"),
    path("", views.DashboardHomeView.as_view(), name="home"),
    path("settings/", views.SiteSettingsView.as_view(), name="settings"),
    # Backup & restore (admins only)
    path("backup/", views_backup.BackupView.as_view(), name="backup"),
    path("backup/create/", views_backup.BackupCreateView.as_view(), name="backup_create"),
    path("backup/upload/", views_backup.BackupUploadView.as_view(), name="backup_upload"),
    path("backup/file/<str:name>/", views_backup.BackupDownloadView.as_view(), name="backup_download"),
    path("backup/file/<str:name>/delete/", views_backup.BackupDeleteView.as_view(), name="backup_delete"),
    path("backup/file/<str:name>/restore/", views_backup.BackupRestoreView.as_view(), name="backup_restore"),
    # Students
    path("students/", views.StudentListView.as_view(), name="student_list"),
    path("students/add/", views.StudentCreateView.as_view(), name="student_create"),
    path("students/upload/", views.StudentUploadView.as_view(), name="student_upload"),
    path("students/template.<str:fmt>", views.StudentTemplateDownloadView.as_view(), name="student_template"),
    path("students/slips/", views.LoginSlipsView.as_view(), name="login_slips"),
    path("students/regenerate-passwords/", views.ClassPasswordRegenerateView.as_view(), name="class_password_regenerate"),
    path("students/<int:pk>/unlock/", views.StudentUnlockView.as_view(), name="student_unlock"),
    path("students/<int:pk>/reset-password/", views.StudentResetPasswordView.as_view(), name="student_reset_password"),
    # Exams
    path("exams/", views.ExamListView.as_view(), name="exam_list"),
    path("exams/new/", views.ExamCreateView.as_view(), name="exam_create"),
    path("exams/<int:pk>/", views.ExamDetailView.as_view(), name="exam_detail"),
    path("exams/<int:pk>/edit/", views.ExamUpdateView.as_view(), name="exam_update"),
    path("exams/<int:pk>/toggle/", views.ExamToggleActiveView.as_view(), name="exam_toggle"),
    path("exams/<int:pk>/delete/", views.ExamDeleteView.as_view(), name="exam_delete"),
    # Questions
    path("exams/<int:exam_pk>/questions/new/", views.QuestionCreateView.as_view(), name="question_create"),
    path("exams/<int:exam_pk>/questions/upload/", views.QuestionUploadView.as_view(), name="question_upload"),
    path("questions/template.<str:fmt>", views.QuestionTemplateDownloadView.as_view(), name="question_template"),
    path("questions/<int:pk>/edit/", views.QuestionUpdateView.as_view(), name="question_update"),
    path("questions/<int:pk>/delete/", views.QuestionDeleteView.as_view(), name="question_delete"),
]
