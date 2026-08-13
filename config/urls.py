"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from apps.reports.views import activity_report, business_flow, dashboard, demo_access, help_center, product_features
from apps.reports.views import global_search, imei_history, module_overview
from apps.organizations.views import aged_stock_policy_update, agent_document_download, agent_document_upload, agent_dsa_create, agent_profile_decide, agent_profile_detail, branch_create, branch_update, invitation_accept, invitation_resend, invitation_revoke, location_create, location_update, membership_access_list, membership_access_update, register_organization, role_create, subscription_invoice_activate, tenant_user_create
from apps.accounts.views import mfa_setup, mfa_verify, recovery_codes_regenerate, security_settings, session_revoke, sessions_revoke_others
from apps.pos.views import cart_add, cart_complete, cart_detail, cart_remove, cash_movement_create, checkout, close_session, offline_invoice_sync, open_session, review_session, session_detail
from apps.purchasing.views import purchase_approve, purchase_create, purchase_detail, purchase_discrepancy_resolve, purchase_extract_document, purchase_receive, supplier_return_complete, supplier_return_create, supplier_return_detail
from apps.transfers.views import agent_allocation_create, agent_recall_create, transfer_approve, transfer_create, transfer_detail, transfer_discrepancy_resolve, transfer_dispatch, transfer_list, transfer_receive
from apps.sales.views import return_approve_complete, sale_add_payment, sale_detail, sale_request_return
from apps.expenses.views import expense_approve, expense_create
from apps.repairs.views import repair_create, repair_detail, repair_update, repair_use_part
from apps.notifications.views import notification_list, notification_read
from apps.integrations.views import etims_settings
from apps.reports.operational import operational_report
from apps.reports.advanced import exception_report
from apps.reports.retail import retail_analytics_report
from apps.reports.agent_network import agent_network_report
from apps.inventory.views import aged_stock_action_create, batch_serial_intake, device_search, stock_adjustment_complete, stock_adjustment_create, stock_adjustment_detail, stock_movement_reverse
from apps.commissions.views import commission_payout_approve, commission_payout_create, commission_payout_detail, commission_payout_pay
from apps.operations.views import access_request_create, approval_decide, approval_detail, approval_inbox, approval_policy_create, approval_policy_list, receivable_installment_schedule
from apps.catalog.views import brand_create, category_create, product_create, product_detail, product_toggle_active, product_update
from apps.contacts.views import contact_create, contact_detail, contact_statement, contact_toggle_active, contact_update
from config.views import health_live, health_ready

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("features/", product_features, name="product-features"),
    path("help/", help_center, name="help-center"),
    path("business-flow/", business_flow, name="business-flow"),
    path("demo/", demo_access, name="demo-access"),
    path("overview/<slug:module>/", module_overview, name="module-overview"),
    path("search/", global_search, name="global-search"),
    path("register/", register_organization, name="register-organization"),
    path("subscriptions/invoices/<uuid:invoice_id>/activate/", subscription_invoice_activate, name="subscription-invoice-activate"),
    path("users/new/", tenant_user_create, name="tenant-user-create"),
    path("invitations/<uuid:token>/accept/", invitation_accept, name="invitation-accept"),
    path("invitations/<uuid:invitation_id>/resend/", invitation_resend, name="invitation-resend"),
    path("invitations/<uuid:invitation_id>/revoke/", invitation_revoke, name="invitation-revoke"),
    path("users/access/", membership_access_list, name="membership-access-list"),
    path("users/access/<uuid:membership_id>/", membership_access_update, name="membership-access-update"),
    path("agents/<uuid:profile_id>/", agent_profile_detail, name="agent-profile-detail"),
    path("agents/<uuid:profile_id>/<slug:decision>/", agent_profile_decide, name="agent-profile-decide"),
    path("agents/<uuid:profile_id>/documents/upload/", agent_document_upload, name="agent-document-upload"),
    path("agents/dsas/new/", agent_dsa_create, name="agent-dsa-create"),
    path("agent-documents/<uuid:document_id>/download/", agent_document_download, name="agent-document-download"),
    path("branches/new/", branch_create, name="branch-create"),
    path("branches/<uuid:branch_id>/edit/", branch_update, name="branch-update"),
    path("locations/new/", location_create, name="location-create"),
    path("locations/<uuid:location_id>/edit/", location_update, name="location-update"),
    path("settings/aged-stock-policy/", aged_stock_policy_update, name="aged-stock-policy"),
    path("roles/new/", role_create, name="role-create"),
    path("pos/checkout/", checkout, name="pos-checkout"),
    path("pos/sessions/open/", open_session, name="session-open"),
    path("pos/cart/", cart_detail, name="pos-cart"),
    path("pos/cart/add/", cart_add, name="pos-cart-add"),
    path("pos/cart/lines/<uuid:line_id>/remove/", cart_remove, name="pos-cart-remove"),
    path("pos/cart/<uuid:sale_id>/complete/", cart_complete, name="pos-cart-complete"),
    path("pos/offline-queue/sync/", offline_invoice_sync, name="pos-offline-sync"),
    path("pos/sessions/<uuid:session_id>/", session_detail, name="session-detail"),
    path("pos/sessions/<uuid:session_id>/close/", close_session, name="session-close"),
    path("pos/sessions/<uuid:session_id>/cash-movements/", cash_movement_create, name="cash-movement-create"),
    path("pos/sessions/<uuid:session_id>/review/", review_session, name="session-review"),
    path("sales/<uuid:sale_id>/", sale_detail, name="sale-detail"),
    path("sales/<uuid:sale_id>/payment/", sale_add_payment, name="sale-add-payment"),
    path("sales/<uuid:sale_id>/return/", sale_request_return, name="sale-request-return"),
    path("returns/<uuid:return_id>/complete/", return_approve_complete, name="return-complete"),
    path("expenses/new/", expense_create, name="expense-create"),
    path("expenses/<uuid:expense_id>/approve/", expense_approve, name="expense-approve"),
    path("repairs/new/", repair_create, name="repair-create"),
    path("repairs/<uuid:ticket_id>/", repair_detail, name="repair-detail"),
    path("repairs/<uuid:ticket_id>/update/", repair_update, name="repair-update"),
    path("repairs/<uuid:ticket_id>/parts/", repair_use_part, name="repair-use-part"),
    path("notifications/", notification_list, name="notification-list"),
    path("notifications/<uuid:notification_id>/read/", notification_read, name="notification-read"),
    path("reports/imei-history/", imei_history, name="imei-history"),
    path("reports/operational/", operational_report, name="operational-report"),
    path("reports/retail-analytics/", retail_analytics_report, name="retail-analytics-report"),
    path("reports/agent-network/", agent_network_report, name="agent-network-report"),
    path("reports/exceptions/", exception_report, name="exception-report"),
    path("reports/activity/", activity_report, name="activity-report"),
    path("inventory/adjustments/new/", stock_adjustment_create, name="stock-adjustment-create"),
    path("inventory/aged-stock/<uuid:stock_unit_id>/actions/new/", aged_stock_action_create, name="aged-stock-action-create"),
    path("inventory/devices/search/", device_search, name="device-search"),
    path("inventory/adjustments/<uuid:adjustment_id>/", stock_adjustment_detail, name="stock-adjustment-detail"),
    path("inventory/adjustments/<uuid:adjustment_id>/complete/", stock_adjustment_complete, name="stock-adjustment-complete"),
    path("inventory/movements/<uuid:movement_id>/reverse/", stock_movement_reverse, name="stock-movement-reverse"),
    path("commissions/payouts/new/", commission_payout_create, name="commission-payout-create"),
    path("commissions/payouts/<uuid:payout_id>/", commission_payout_detail, name="commission-payout-detail"),
    path("commissions/payouts/<uuid:payout_id>/approve/", commission_payout_approve, name="commission-payout-approve"),
    path("commissions/payouts/<uuid:payout_id>/pay/", commission_payout_pay, name="commission-payout-pay"),
    path("approvals/", approval_inbox, name="approval-inbox"),
    path("approvals/<uuid:approval_id>/", approval_detail, name="approval-detail"),
    path("approvals/<uuid:approval_id>/decide/", approval_decide, name="approval-decide"),
    path("approval-policies/", approval_policy_list, name="approval-policy-list"),
    path("approval-policies/new/", approval_policy_create, name="approval-policy-create"),
    path("access-requests/new/", access_request_create, name="access-request-create"),
    path("integrations/etims/", etims_settings, name="etims-settings"),
    path("receivables/<uuid:receivable_id>/installments/", receivable_installment_schedule, name="receivable-installment-schedule"),
    path("catalog/categories/new/", category_create, name="category-create"),
    path("catalog/brands/new/", brand_create, name="brand-create"),
    path("catalog/products/new/", product_create, name="product-create"),
    path("catalog/products/<uuid:product_id>/", product_detail, name="product-detail"),
    path("catalog/products/<uuid:product_id>/edit/", product_update, name="product-update"),
    path("catalog/products/<uuid:product_id>/toggle-active/", product_toggle_active, name="product-toggle-active"),
    path("contacts/new/", contact_create, name="contact-create"),
    path("contacts/<uuid:contact_id>/", contact_detail, name="contact-detail"),
    path("contacts/<uuid:contact_id>/statement/", contact_statement, name="contact-statement"),
    path("contacts/<uuid:contact_id>/edit/", contact_update, name="contact-update"),
    path("contacts/<uuid:contact_id>/toggle-active/", contact_toggle_active, name="contact-toggle-active"),
    path("purchases/new/", purchase_create, name="purchase-create"),
    path("purchases/<uuid:order_id>/", purchase_detail, name="purchase-detail"),
    path("purchases/<uuid:order_id>/extract-document/", purchase_extract_document, name="purchase-extract-document"),
    path("purchases/<uuid:order_id>/approve/", purchase_approve, name="purchase-approve"),
    path("purchases/lines/<uuid:line_id>/receive/", purchase_receive, name="purchase-receive"),
    path("purchases/discrepancies/<uuid:discrepancy_id>/resolve/", purchase_discrepancy_resolve, name="purchase-discrepancy-resolve"),
    path("supplier-returns/new/", supplier_return_create, name="supplier-return-create"),
    path("supplier-returns/<uuid:return_id>/", supplier_return_detail, name="supplier-return-detail"),
    path("supplier-returns/<uuid:return_id>/complete/", supplier_return_complete, name="supplier-return-complete"),
    path("transfers/", transfer_list, name="transfer-list"),
    path("transfers/new/", transfer_create, name="transfer-create"),
    path("transfers/agent-allocation/new/", agent_allocation_create, name="agent-allocation-create"),
    path("transfers/agent-recall/new/", agent_recall_create, name="agent-recall-create"),
    path("transfers/<uuid:transfer_id>/", transfer_detail, name="transfer-detail"),
    path("transfers/<uuid:transfer_id>/approve/", transfer_approve, name="transfer-approve"),
    path("transfers/<uuid:transfer_id>/dispatch/", transfer_dispatch, name="transfer-dispatch"),
    path("transfers/<uuid:transfer_id>/receive/", transfer_receive, name="transfer-receive"),
    path("transfers/discrepancies/<uuid:discrepancy_id>/resolve/", transfer_discrepancy_resolve, name="transfer-discrepancy-resolve"),
    path("inventory/batch-serial-intake/", batch_serial_intake, name="batch-serial-intake"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password/change/", auth_views.PasswordChangeView.as_view(success_url="/accounts/security/"), name="password-change"),
    path(
        "accounts/password/reset/",
        auth_views.PasswordResetView.as_view(
            email_template_name="registration/password_reset_email.html",
            html_email_template_name="registration/password_reset_email_html.html",
            subject_template_name="registration/password_reset_subject.txt",
            success_url=reverse_lazy("password-reset-done"),
        ),
        name="password-reset",
    ),
    path("accounts/password/reset/done/", auth_views.PasswordResetDoneView.as_view(), name="password-reset-done"),
    path("accounts/password/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("accounts/password/reset/complete/", auth_views.PasswordResetCompleteView.as_view(), name="password-reset-complete"),
    path("accounts/security/", security_settings, name="security-settings"),
    path("accounts/mfa/setup/", mfa_setup, name="mfa-setup"),
    path("accounts/mfa/verify/", mfa_verify, name="mfa-verify"),
    path("accounts/mfa/recovery-codes/regenerate/", recovery_codes_regenerate, name="recovery-codes-regenerate"),
    path("accounts/sessions/revoke-others/", sessions_revoke_others, name="sessions-revoke-others"),
    path("accounts/sessions/<uuid:session_id>/revoke/", session_revoke, name="session-revoke"),
    path('admin/', admin.site.urls),
    path("ckeditor5/", include("django_ckeditor_5.urls")),
    path("health/live/", health_live, name="health-live"),
    path("health/ready/", health_ready, name="health-ready"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
