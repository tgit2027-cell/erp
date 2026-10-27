"""Separate disposable-only URLConf. Never included by foundation.urls."""
from django.urls import path
from django.apps import apps
from . import finance_lab_views as views

urlpatterns = [
    path("lab-ui/", views.ui),
    path("assets/<str:filename>", views.ui),
    path("lab/session/", views.session),
    path("lab/context/", views.context),
    path("lab/accounts/", views.accounts),
    path("lab/dimensions/", views.dimensions),
    path("lab/dimension-values/", views.dimension_values),
    path("lab/journals/", views.journals),
    path("lab/journals/<int:journal_id>/", views.journal),
    path("lab/journals/<int:journal_id>/preview/", views.recurring_preview),
]

if apps.is_installed("finance_allocation_lab"):
    from finance_allocation_lab import recovery
    from finance_allocation_lab import reversal_views
    from finance_allocation_lab import views as allocation_views
    from finance_allocation_lab import budget_views
    from finance_allocation_lab import reconciliation_views
    from finance_allocation_lab import expense_views
    from finance_allocation_lab import asset_views
    from finance_allocation_lab import forecast_views
    from finance_allocation_lab import invoice_views
    from finance_allocation_lab import supplier_adjustment_views
    from finance_allocation_lab import customer_invoice_views
    from finance_allocation_lab import customer_credit_views
    from finance_allocation_lab import supplier_advance_views
    urlpatterns += [
        path("lab-reversals-ui/", views.ui),
        path("lab/reversals/", reversal_views.reversals),
        path("lab/reversals/<int:draft_id>/", reversal_views.reversal),
        path("lab/reversal-sources/<int:journal_id>/<int:revision>/", reversal_views.source),
        path("lab-review-ui/", views.ui),
        path("lab/review/", recovery.review),
        path("lab/requests/<str:request_id>/", recovery.request_status),
        path("lab-supplier-advances-ui/", views.ui),
        path("lab/supplier-advances/", supplier_advance_views.advances),
        path("lab/supplier-advances/<int:draft_id>/", supplier_advance_views.advance),
        path("lab-customer-credits-ui/", views.ui),
        path("lab/customer-credits/", customer_credit_views.credits),
        path("lab/customer-credits/<int:draft_id>/", customer_credit_views.credit),
        path("lab-customer-invoices-ui/", views.ui),
        path("lab/customer-invoices/", customer_invoice_views.invoices),
        path("lab/customer-invoices/<int:draft_id>/", customer_invoice_views.invoice),
        path("lab-adjustments-ui/", views.ui),
        path("lab/adjustments/", supplier_adjustment_views.adjustments),
        path("lab/adjustments/<int:draft_id>/", supplier_adjustment_views.adjustment),
        path("lab-invoices-ui/", views.ui),
        path("lab/invoices/", invoice_views.invoices),
        path("lab/invoices/<int:draft_id>/", invoice_views.invoice),
        path("lab-forecasts-ui/", views.ui),
        path("lab/forecasts/", forecast_views.forecasts),
        path("lab/forecasts/<int:draft_id>/", forecast_views.forecast),
        path("lab-assets-ui/", views.ui),
        path("lab/assets/", asset_views.assets),
        path("lab/assets/<int:draft_id>/", asset_views.asset),
        path("lab-expense-ui/", views.ui),
        path("lab/expenses/", expense_views.proposals),
        path("lab/expenses/<int:proposal_id>/", expense_views.proposal),
        path("lab-reconciliation-ui/", views.ui),
        path("lab/reconciliations/", reconciliation_views.proposals),
        path("lab/reconciliations/<int:proposal_id>/", reconciliation_views.proposal),
        path("lab-budget-ui/", views.ui),
        path("lab/budgets/", budget_views.scenarios),
        path("lab/budgets/<int:scenario_id>/", budget_views.scenario),
        path("lab-allocation-ui/", views.ui),
        path("lab/allocations/", allocation_views.proposals),
        path("lab/allocations/<int:proposal_id>/", allocation_views.proposal),
    ]
