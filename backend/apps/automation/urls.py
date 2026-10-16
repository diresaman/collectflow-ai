from django.urls import path

from . import views

urlpatterns = [
    path("automation/run/", views.RunAutomationView.as_view(), name="automation-run"),
    path("automation-runs/<int:pk>/", views.AutomationRunDetailView.as_view(), name="automation-run-detail"),
    path("automation/workflows/current/", views.CurrentWorkflowView.as_view(), name="workflow-current"),
    path("automation/workflows/<int:pk>/", views.WorkflowDetailView.as_view(), name="workflow-detail"),
    path("automation/workflows/<int:pk>/steps/", views.WorkflowStepsView.as_view(), name="workflow-steps"),
    path(
        "automation/workflows/<int:pk>/pending-actions/",
        views.WorkflowPendingActionsView.as_view(),
        name="workflow-pending-actions",
    ),
    path("automation/workflows/<int:pk>/finalize/", views.FinalizeWorkflowView.as_view(), name="workflow-finalize"),
    path(
        "automation/workflows/<int:pk>/items/<int:item_id>/leave-unsent/",
        views.LeaveUnsentView.as_view(),
        name="workflow-leave-unsent",
    ),
]
