# MobiPOS Important Gaps and SYSCO Parity Implementation Plan

## Document Purpose

This document is the delivery plan for closing every important MobiPOS gap
identified through:

- The original `PROJECT_PLAN.md` architecture and business requirements.
- The comprehensive MobiPOS product audit.
- The completed P0 and current P1 implementation reports.
- A read-only review of 43 SYSCO POS navigation destinations, representative
  report outputs, and sales, purchase, transfer, commission, and user-management
  detail pages using multiple SYSCO roles.
- A source-level comparison against the current MobiPOS routes, models, forms,
  services, templates, permissions, settings, and tests.

The objective is not to reproduce SYSCO's defects or visual design. The
objective is to provide the same useful retail functionality with stronger
security, auditability, tenant isolation, usability, and operational controls.

This is an implementation plan only. No feature is considered complete merely
because it appears in this document.

---

## 1. Current Baseline

MobiPOS currently provides:

- Tenant-aware organizations, companies, branches, locations, memberships,
  multiple roles, permissions, invitations, subscriptions, and audit events.
- Catalog, customers and suppliers, serialized and quantity inventory,
  purchasing, transfers, POS, split payments, credit receivables, expenses,
  commissions, repairs, notifications, reports, and approvals.
- Append-only stock movements, stock-balance protection, serialized-stock
  lifecycle controls, return and refund workflows, cashier reconciliation, and
  tenant-scoped searches and registers.
- A grouped, role-aware navigation where locked destinations remain visible and
  can trigger access requests.
- PostgreSQL-only production settings, Render configuration, health checks,
  secure cookies, MFA foundations, Sentry support, and Redis/Celery boundaries.
- A clean automated baseline of 88 passing tests and a passing Django system
  check at the time this plan was written.

The current baseline must be preserved while the gaps below are implemented.

---

## 2. Delivery Principles

All implementation phases must follow these rules:

1. **No hidden authorization assumptions.** Navigation state, template
   visibility, and direct URL authorization must use the same permission policy.
2. **Locked actions remain visible.** Unauthorized actions are disabled and
   explain the required permission. Requestable permissions open the existing
   access-request workflow.
3. **No deletion of completed business records.** Corrections use reversals,
   adjustment records, approval decisions, and linked compensating movements.
4. **Branch and tenant scope is mandatory.** Reads, writes, exports, background
   tasks, imports, callbacks, and report totals must enforce organization and
   branch access.
5. **Sensitive financial information is separately controlled.** Cost prices,
   margins, company-wide totals, customer identifiers, and commission payments
   require explicit permissions.
6. **PostgreSQL compatibility from the first migration.** SQLite remains for
   local development; concurrency-critical acceptance tests run on PostgreSQL.
7. **Operational workflows stay outside Django admin.** Unfold remains the
   privileged configuration and support interface, not the daily staff UI.
8. **Large work is previewed before posting.** Imports, bulk stock changes,
   payouts, reconciliation, and corrections require validation and confirmation.
9. **Every workflow is auditable.** The actor, timestamp, reason, approval,
   before/after values, source record, and resulting ledger entries are retained.
10. **No feature advances without its acceptance gate.** Tests, migrations,
    permission checks, responsive behavior, and documentation are part of the
    feature, not follow-up work.

---

## 3. Target Navigation

The operational navigation will retain role-specific grouping while adding the
missing destinations.

```text
Overview
  Dashboard
  Approval Inbox
  Notifications

Sales & POS
  Open Register
  New Sale
  Active Carts
  Sales Register
  Payments
  Credit Sales
  Returns & Refunds
  Cashier Sessions

Inventory
  Products
  Categories
  Brands
  Units of Measure
  Taxes
  Price Lists
  Stock Levels
  Agent Stock
  Serialized Inventory
  IMEI History
  Aged Stock
  Stock Movements
  Stocktakes
  Stock Adjustments & Deductions
  Transfers
  Pending Receipts

Purchasing
  New Purchase
  Purchase Register
  Pending Receipts
  Purchase Imports
  Purchase Discrepancies
  Suppliers
  Supplier Statements
  Supplier Returns
  Payables & Settlements

Customers & Finance
  Customers
  Customer Statements
  Receivables & Installments
  Credit Agencies
  Expenses
  Expense Categories
  Commissions
  Commission Rules
  Commission Payouts

Repairs & Warranty
  New Repair
  Repair Board
  Warranty Claims
  Parts Usage

Reports
  Executive Summary
  Sales Reports
  Purchase Reports
  Inventory & Serial Reports
  Expense Reports
  Commission Reports
  Operational Accounting
  Exceptions & Aging
  Saved Reports

Administration
  Users & Access
  Roles & Permissions
  Companies
  Branches
  Locations
  Aging Policies
  Approval Policies
  Subscriptions
  Integrations
  Audit Explorer
  System Configuration
  Django Administration
```

Navigation destinations remain visible even when locked. Groups and default
landing pages remain role-specific, but permission denial must never be the only
control protecting a feature.

---

## 4. Phase P0: Authorization, Privacy, and Financial Correctness

P0 is reopened for the newly discovered scope. Earlier P0 hardening remains
valid, but the latest SYSCO comparison exposed additional permissions and
financial-visibility requirements that must be completed before new parity work.

### P0.1 Permission Taxonomy

Add explicit tenant permissions instead of overloading broad `view`, `add`, and
`change` permissions.

Minimum permission families:

| Domain | Required permissions |
| --- | --- |
| Sales | View own, view branch, view all, create, discount, override price, change date, reverse, return, approve return, export |
| Payments | Record, confirm, refund, approve refund, view references, export |
| Inventory | View quantity, view value, view agent custody, transfer, receive, reconcile, adjust, write off, reverse movement, manage aging, export |
| Purchasing | Create, approve, receive, change cost, resolve discrepancy, reverse, import, export, settle supplier |
| Credit | Create credit sale, approve credit, override limit, manage installments, view statements, export |
| Commissions | View own, view branch, view all, manage rules, approve payout, mark paid, reverse payout, export |
| Expenses | Create, view, approve, reject, mark paid, reverse, manage categories, export |
| Reports | View operational totals, view cost, view margin, view executive summary, export, schedule |
| Administration | Manage users, roles, companies, branches, locations, agencies, policies, integrations, audit |

Implementation requirements:

- Add custom model permissions through migrations where default Django
  permissions are insufficient.
- Expand `TENANT_ROLE_PERMISSION_CODES` and role-creation choices.
- Define role presets for Owner, Branch Manager, Inventory Manager, Purchasing,
  Cashier, Sales Agent, Finance, Technician, and Auditor.
- Preserve support for multiple roles; effective permissions are the union of
  active assigned roles.
- Separate organization-owner privileges from platform-admin privileges.
- Make high-risk permissions non-requestable where automatic delegation would
  be unsafe; those permissions require direct owner assignment.

### P0.2 Route and Object Authorization

Audit every route in `config/urls.py` and every view. For each route record:

- Authentication requirement.
- Organization requirement.
- Branch or location scope.
- View permission.
- Mutation permission.
- Approval requirement.
- MFA requirement.
- Export permission.

Correct known gaps:

- Protect sale, purchase, transfer, adjustment, repair, session, contact, and
  statement detail pages with their corresponding view permissions.
- Protect operational, retail, margin, cost, aging, and executive reports with
  explicit report permissions.
- Do not rely on UUID secrecy as authorization.
- Ensure platform-wide querysets are available only inside explicitly protected
  platform-admin views.
- Apply equivalent authorization to CSV, Excel, PDF, print, async export, and
  callback endpoints.
- Apply permission filtering to global search so a locked module cannot leak
  product, contact, sale, IMEI, repair, cost, or customer information through
  search results.
- Protect IMEI history and all dashboard drill-downs with the same permission
  used by their navigation destination.

### P0.2A Django Admin Isolation and Immutability

The source inspection confirmed that the shared `OrganizationOwnedAdmin`
currently scopes list querysets but does not fully scope add-form organization
choices, related foreign keys, or many-to-many fields. Most transactional models
also remain editable through generic admin registration.

Required corrections:

- Scope `organization`, company, branch, location, role, membership, user,
  product, contact, stock unit, sale, purchase, and other related admin fields to
  the administrator's authorized organization.
- Validate organization ownership again in `save_model`, `save_formset`, and
  `save_related`; never rely only on hidden or filtered form choices.
- Prevent cross-organization role and branch assignment through membership
  many-to-many fields.
- Make sales, sale lines, payments, refunds, stock balances, stock movements,
  purchase lines, transfer lines, commission accruals, payout lines, payables,
  receivables, integration events, and audit events read-only in admin after
  posting.
- Disable admin deletion for completed business, integration, accounting, stock,
  approval, and audit records. Use domain reversal/correction workflows.
- Restrict recovery codes, tracked sessions, integration payloads/responses, and
  other security-sensitive models to platform administrators.
- Add admin tests for list, add, change, related-field, many-to-many, and direct
  object access across two organizations.

### P0.3 Permission-Aware Actions

Create one reusable action-policy component used by navigation, register rows,
detail pages, dashboards, and modals.

Each action receives:

- Required permission.
- Whether it is requestable.
- Locked explanation.
- Approval requirement.
- Current record-state requirement.
- Optional MFA requirement.

Required behavior:

- Allowed action: normal enabled control.
- Requestable action: visible disabled control that opens the access-request
  dialog.
- Non-requestable action: visible disabled control explaining who can grant it.
- Invalid record state: disabled control explaining the required status.
- Server endpoint still performs full authorization independently of the UI.

Replace status-only action rendering in sales, purchase, transfer, commission,
expense, adjustment, return, and repair templates.

### P0.4 Financial and Personal Data Privacy

- Hide cost, gross margin, net margin, stock value, supplier cost, and company
  totals unless the user has the relevant financial permission.
- Default sales agents to their own commission data and own assigned stock.
- Mask customer and employee national IDs, phone numbers, payment references,
  and next-of-kin details in lists.
- Reveal full values only on authorized detail pages and audit sensitive-data
  access where appropriate.
- Prevent sensitive fields from appearing in exports without explicit export
  permission.
- Add organization-owned employee profile data instead of placing tenant-specific
  national IDs or employment details directly on the global user model.
- Never store or display administrator-created plaintext passwords. Continue
  using invitations and user-set passwords.

### P0.5 Dashboard and Report Correctness

- Correct today's purchase value to sum quantity multiplied by unit cost rather
  than treating quantity as monetary value.
- Add the missing Today's Purchases card and a permission-aware My Commissions
  card.
- Ensure every dashboard metric uses the same branch and status rules as its
  linked register.
- Add reconciliation tests proving dashboard totals equal report and ledger
  totals for the same scope and period.
- Restrict cost and margin cards independently from ordinary sales totals.
- Add date and branch filters to operational reports.

### P0.5A Payment and Provider-State Integrity

The current payment service confirms cash, M-Pesa, card, and bank allocations
through the same synchronous path. A typed provider reference must not be
treated as proof that money was received.

- Confirm cash immediately only inside an open, authorized cashier session.
- Keep M-Pesa payments pending until an authenticated/idempotent callback or
  reconciliation query confirms them.
- Keep card and bank payments pending until a configured provider response or an
  explicitly authorized manual confirmation.
- Require unique organization/provider transaction references and reject replay
  across sales, refunds, settlements, and subscription payments.
- Separate payment capture, confirmation, failure, cancellation, and refund
  transitions.
- Recalculate sale, receivable, installment, commission, and session totals only
  from confirmed allocations.
- Add dedicated permissions and audit events for manual payment confirmation and
  reconciliation.

### P0.5B Domain Reversal and State Integrity

The generic stock-movement reversal endpoint can currently reverse a movement
created by a sale, purchase, transfer, supplier return, or repair without
updating the parent transaction. This must be closed before live use.

- Allow generic movement reversal only for approved standalone stock adjustments
  that have no domain-owned compensation workflow.
- Route sale, purchase, transfer, supplier-return, repair, payment, commission,
  and cashier corrections through domain services that update every dependent
  state atomically.
- Add database and service constraints for non-negative balances, quantities,
  payment amounts, refund totals, and received/returned quantities.
- Make primary and secondary IMEI uniqueness rules explicit and prevent the same
  identifier from appearing in either serial field within an organization.
- Prevent duplicate open cashier sessions and duplicate active carts through
  database constraints appropriate for PostgreSQL and compatible validation for
  SQLite development.
- Add optional cart reservation/expiry so two cashiers receive immediate feedback
  when attempting to sell the same IMEI.
- Recompute sale status after refunds and partial returns instead of leaving a
  refunded sale incorrectly marked paid.

### P0.6 Security and Failure Handling

- Keep `DEBUG=False` and sanitized error pages in every production environment.
- Add regression tests proving exceptions never expose settings, cookies,
  headers, SQL, credentials, or stack traces to users.
- Add structured error identifiers for support without exposing internals.
- Ensure imports and reports reject malformed filter combinations with a normal
  validation message instead of server errors.
- Rate-limit sensitive exports, access requests, password recovery, and
  integration callbacks.
- Validate MFA and other `next` redirects with Django's allowed-host checks to
  prevent open redirects.
- Do not trust arbitrary client-supplied forwarding headers for audit/session IP
  attribution; use the configured Render proxy boundary.
- Self-host pinned HTMX and Alpine assets, or apply verified integrity metadata;
  do not depend on floating CDN versions in the production shell.
- Add a Content Security Policy compatible with Django, CKEditor, HTMX, Alpine,
  and Unfold.
- Replace broad exception-to-message rendering with sanitized user errors and
  request IDs while retaining server-side exception logging.
- Protect CSV/XLSX exports from spreadsheet formula injection.

### P0.7 Immutable Approval and Schedule History

The current approval service reuses a unique request row and resets its decision
fields when the same target is requested again. Installment schedule replacement
also deletes prior rows. Both behaviors lose operational history.

- Preserve each approval attempt and decision as an immutable record, with an
  optional link to the prior request or current approval subject.
- Store decision notes and the policy snapshot used at decision time.
- Prevent policy edits from rewriting the meaning of historical approvals.
- Version installment schedules instead of deleting paid, overdue, or previously
  approved installments.
- Preserve payment allocation history when a schedule changes.
- Add tests for repeated access, discount, credit, discrepancy, variance,
  correction, and payout requests.

### P0 Acceptance Gate

P0 is complete only when:

- Every route appears in an authorization matrix and has automated direct-URL
  tests for allowed and denied roles.
- Cross-tenant and cross-branch tests cover reads, writes, exports, and detail
  pages.
- Unauthorized financial fields are absent from HTML, CSV, Excel, PDF, and print
  output.
- Every visible operational action uses the shared action-policy component.
- Access-request creation, duplicate prevention, approval, rejection, and
  permission grant tests pass.
- Dashboard and report reconciliation fixtures produce matching totals.
- Django admin cannot create cross-tenant records or mutate/delete posted
  transactions.
- Provider references cannot confirm or duplicate non-cash payments without the
  correct confirmation workflow.
- Domain-owned stock movements cannot be reversed independently of their parent
  transaction.
- Repeated approvals and installment changes retain complete history.
- Full tests, system checks, migration checks, CSS build, static collection, and
  PostgreSQL authorization smoke tests pass.

Estimated effort: **8-12 engineering days**.

---

## 5. Phase P1: Complete Operational and SYSCO Functional Parity

P1 delivers all business-critical functionality observed in SYSCO while using
MobiPOS controls and architecture.

### P1.1 Agent Stock Custody

SYSCO treats sales agents as inventory custodians. MobiPOS currently stores
stock only at warehouse and POS locations.

Recommended design:

- Add `AGENT` to `LocationType`.
- Add an optional organization-scoped `custodian_membership` relationship to
  `Location`, with one active custody location per membership and same-branch,
  same-organization validation.
- Reuse the existing stock movement, balance, transfer, serial, branch, and
  ledger services instead of creating a second stock system.
- Provision or link an agent custody location when an authorized administrator
  enables stock custody for a user.

Required workflows:

- Allocate serialized and quantity stock to an agent.
- Transfer stock between warehouse, branch, POS, and agent custody locations.
- Require independent dispatch and receipt when configured.
- Recall stock from an agent.
- View current agent stock, value when permitted, aged stock, pending receipt,
  movement history, deductions, and discrepancies.
- Prevent selling a serial that is not held at the sale's permitted custody/POS
  location.
- Support manager and team-leader views over assigned agents.
- Include agent custody in dashboard metrics, global search, reports, stocktakes,
  and IMEI history.
- Allow an authorized cashier assigned to multiple POS locations to choose the
  active register/location explicitly instead of silently using the first
  assigned POS location.

Acceptance criteria:

- Every serial has one valid location or a documented in-transit/sold state.
- Concurrent allocation or sale cannot place one IMEI in conflicting custody.
- Agent users cannot view another agent's custody without branch/team permission.
- Dispatch, receipt, discrepancy, recall, and reversal produce balanced ledger
  movements and audit events.

### P1.1A Centralized IMEI and Serial Lifecycle Management

The source inspection confirmed that MobiPOS already has the correct foundation:
`StockUnit` is the central organization-scoped serialized inventory record,
`StockMovement` is append-only, transfers already point to existing stock units,
and IMEI history is searchable. The missing work is a complete operational UX
and stricter lifecycle metadata around that foundation.

Required model and validation improvements:

- Treat `StockUnit` as the single source of truth for every IMEI, serial number,
  barcode-tracked phone, and other serialized product.
- Add lifecycle metadata where missing: created by, last updated by, current
  custodian, current location, current status, intake batch, original supplier,
  warranty dates, and optional owner/assignee.
- Make primary and secondary serial uniqueness cross-field safe within an
  organization so the same identifier cannot exist as one item's primary IMEI
  and another item's secondary IMEI.
- Keep one and only one current custody state per serialized unit: warehouse,
  POS, agent/user custody, in transit, sold, returned, damaged, repair,
  written off, retired, or archived.
- Preserve full historical ownership, custody, and movement history through
  append-only stock movements and audit events.
- Prevent manual edits that would silently move, sell, assign, or retire a
  serialized item without a domain workflow.

Required search and selection workflows:

- Add a device search API scoped by organization, branch, location, status, and
  permission.
- Search by IMEI, secondary IMEI, serial number, barcode, SKU, model, product
  name, supplier code, current location, current custodian, assigned owner, and
  status.
- Use searchable multi-select tables for transfers and allocations instead of
  forcing users to re-enter identifiers.
- Support checkbox selection for bulk device movement and radio/single-select
  selection where a workflow accepts only one device.
- Show enough result context to avoid mistakes: product, model/variant, IMEI,
  secondary IMEI, status, current location, current custodian, age, and last
  movement reference.
- Disable unavailable or unauthorized devices with an explanation instead of
  hiding them from selection when doing so improves user understanding.

Required transfer and allocation workflows:

- Warehouse-to-warehouse, warehouse-to-POS, warehouse-to-agent, POS-to-agent,
  agent-to-agent, agent-to-warehouse, department, repair, and return movements
  must select existing stock units from the central inventory database.
- Users must not manually type IMEIs again for devices already in inventory
  except in scanner/search fields used to find and select those records.
- Transfers support selecting one or many devices, confirming the selected
  devices, then posting dispatch and receipt movements.
- Agent allocation uses the same transfer/custody engine, automatically updates
  the current custodian/location/status, and records the transaction reference.
- Batch allocation supports large device lists with validation before posting.
- Concurrency controls must prevent two users from allocating, transferring, or
  selling the same IMEI at the same time.

Required stock intake workflows:

- Keep manual multiline IMEI entry for small receipts.
- Add sequential scanner mode for large shipments: scan, validate, append row,
  mark duplicate/error inline, continue scanning without leaving the keyboard.
- Add CSV/XLSX upload with downloadable template, import preview, row-level
  validation errors, duplicate detection, partial-failure reporting, and final
  confirmation before posting stock.
- Support barcode and QR-code scanning where the scanner behaves as keyboard
  input.
- Create import job and import row records for auditability, retries, and
  support troubleshooting.
- On successful intake, create stock units, purchase receipt movements, audit
  events, and a success summary showing transaction ID, affected devices,
  accepted rows, rejected rows, actor, and timestamp.

Required audit events:

- Device creation and intake.
- Device edit/correction.
- Transfer request, approval, dispatch, receipt, discrepancy, and resolution.
- Warehouse allocation to agent or employee.
- Return, replacement, repair handoff, damage, write-off, retirement, archive,
  and status changes.
- Bulk import preview, confirmation, partial failure, retry, and cancellation.
- Every event stores actor, timestamp, organization, source and destination
  location/custodian where applicable, affected identifiers, previous values,
  new values, request ID, and system reference number.

Required validation and feedback:

- Duplicate IMEI or serial detected, including primary-vs-secondary conflicts.
- Device not found.
- Device already assigned, sold, in transit, damaged, retired, or otherwise
  unavailable for the requested action.
- Source or destination location/custodian not found or not permitted.
- Invalid status transition for the current workflow.
- Invalid file type, malformed rows, missing columns, bad quantities, or
  duplicate rows during import.
- Partial import failures produce a downloadable error report and preserve
  successfully validated rows for confirmation.
- Successful operations show transaction ID, number of affected devices,
  timestamp, source, destination, and next available action.

Acceptance criteria:

- A user can find any authorized device from one global search and from the
  inventory module without knowing its current location.
- Transfers and agent allocations can be completed by selecting existing device
  records, with no duplicate manual IMEI entry.
- Batch intake handles manual entry, scanner entry, and CSV/XLSX upload with
  preview and row-level errors.
- Every serialized-device lifecycle action has a linked stock movement and
  immutable audit event.
- Authorization, branch scope, tenant scope, and concurrency tests cover search,
  intake, transfer, allocation, sale, return, and repair handoff.

### P1.2 Catalog Masters and Product Structure

Add operational management for:

- Categories: list, create, edit, archive, restore, serial-tracking default.
- Brands: list, create, edit, archive, restore.
- Units of measure and precision.
- Reusable tax codes and rates with effective dates and active status.
- Product variants such as model, color, storage, RAM, and market/region.
- Multiple barcodes and supplier codes per variant.
- Organization and branch price lists with effective dates.
- Buying-price history without silently rewriting historical transaction costs.
- Selling-price override rules and approval thresholds.
- Product, variant, barcode, and price import with preview and validation.

Migration requirements:

- Backfill existing product tax rates into organization tax records.
- Backfill a default unit of measure.
- Preserve existing product UUIDs and transaction relationships.
- Keep existing SKUs and barcodes valid while migrating to child barcode and
  variant records.

### P1.3 Configurable Credit Agencies and Customer Credit

Replace the hard-coded credit-agency enum with an organization-owned
`CreditAgency` model containing:

- Name, code, status, contact details, settlement terms, notes, and optional
  integration identifier.
- Applicable branches, products/categories, and effective dates.
- Whether customer national ID, next of kin, deposit, reference, or approval is
  required.

Migration maps existing Watu, M-Kopa, OnFon, Mogo, and Other values to records
without losing historical sales.

Complete customer-credit workflows:

- Credit application/profile with limit, terms, status, risk notes, consent,
  and attachments when object storage is available.
- Credit limit and overdue-balance validation.
- Approval for limit override, overdue override, or incomplete agency data.
- Deposit plus credit split payment.
- Installment schedule, collections notes, reminders, aging, payment allocation,
  and customer statement.
- Agency receivable and settlement reporting where the agency, rather than the
  customer, owes the balance.

### P1.4 Commission Configuration and Settlement

Extend commission rules to support:

- Organization, company, branch, category, product, variant, credit agency, and
  sale-type scope.
- Agent, team-leader, and manager beneficiary roles.
- Fixed amount, percentage of revenue, percentage of margin, and tiered rules.
- Effective dates, priority, and conflict resolution.
- Eligibility after full payment, partial collection, agency confirmation, or
  an approved waiting period.
- Reversal or reduction after returns, refunds, cancelled credit, or written-off
  receivables.

Build operational screens for:

- Commission rule register and configuration.
- Branch/product commission matrix.
- Manager and team-leader commission settings.
- My Commissions.
- Branch and organization commission registers.
- Pending, approved, paid, reversed, and rejected payout batches.
- Payment references, payout statements, and export.

Commission evaluation must be deterministic, idempotent, auditable, and covered
by precedence tests.

### P1.5 Purchasing, Receiving, Imports, and Supplier Operations

Complete purchasing with:

- Dynamic purchase lines rather than a fixed five-line limit.
- Scanner-friendly IMEI entry and duplicate detection before submission.
- CSV/XLSX bulk import with downloadable template, preview, row-level errors,
  duplicate detection, idempotency key, and explicit confirmation.
- Import-job and import-row records for audit and retry.
- Partial receipt, final receipt, damaged/missing quantities, discrepancy
  approval, and replacement tracking.
- Printable purchase stock list showing products, serials, quantities, supplier,
  destination, and receipt status.
- Controlled purchase cost correction using a dedicated correction record,
  reason, approval, before/after value, payable adjustment, and margin impact.
- Purchase reversal only when dependent transactions permit it; otherwise use a
  supplier return or compensating correction.
- Supplier statements, invoices, payables, settlements, unapplied payments,
  returns, and aging.
- Purchases by agent, supplier, branch, product, and date.

### P1.6 Stocktakes, Deductions, and Aging

Build dedicated operational workflows for:

- Configurable deduction/adjustment categories such as damage, loss, repair,
  supplier return, correction, and write-off.
- Separate reason, evidence, quantity, serial, custody location, requester,
  approver, and resulting movement.
- Optional photo/document evidence after persistent object storage is enabled.
- Stocktake and cycle-count sessions with freeze/snapshot, blind count,
  variance, recount, approval, posting, and reconciliation.
- Replenishment recommendations based on reorder levels and branch demand.
- Configurable organization/category aging policies.
- Immutable stock-age review events rather than destructive age reset.
- Aging anchor derived from receipt or approved review, with full history.

### P1.7 Sales Corrections, Reversals, and Receipts

Complete controlled correction workflows:

- Whole-sale reversal with reason, approval, payment/refund handling, stock
  compensation, commission reversal, receivable correction, and audit linkage.
- Sale-date correction through a correction request, not direct editing.
- Customer and credit-context correction with before/after values and approval.
- Price and discount corrections only while a cart is open; completed-sale
  changes require reversal/correction records.
- Prevent reversal after conflicting returns, refunds, eTIMS acceptance, or
  settled agency payments unless a specific compensating workflow exists.

Receipt outputs:

- Full-page receipt.
- Thermal receipt optimized for 58/80 mm printers.
- Print CSS without application navigation.
- Shareable PDF or secure receipt link.
- Email/SMS/WhatsApp-ready delivery adapter without embedding provider logic in
  the sale view.
- Organization-configurable receipt logo, address, tax PIN, terms, footer, and
  eTIMS fields.

### P1.8 Dedicated Operational Reports

Build a report center with dedicated filters and permissions.

Required sales reports:

- Company, branch, agent, customer, product, category, sale type, credit agency,
  status, and date filters.
- Revenue, cost, gross margin, discount, tax, paid amount, and balance.

Required purchase reports:

- Supplier, purchasing user/agent, destination, product, category, status, and
  date filters.
- Ordered, received, damaged, missing, returned, cost, payable, and settlement
  totals.

Required inventory reports:

- Stock by location and agent.
- Active, reserved, in-transfer, sold, damaged, repair, returned, and written-off
  serials.
- IMEI lifecycle/history.
- Aged stock based on active policy.
- Stock movement, deduction, stocktake, and discrepancy registers.

Required finance reports:

- Expenses by category, branch, requester, approver, status, and date.
- Commission accrual and payment reports.
- Receivables, payables, installments, and aging.
- Executive summary: sales, cost, gross margin, expenses, commissions, net
  operational margin, receivables, payables, and stock value.

All reports must support:

- Branch-aware default scope.
- Explicit date ranges and validated filters.
- Drill-down from totals to source records.
- Pagination and query optimization.
- CSV and XLSX export.
- Print and PDF for approved report types.
- Background export jobs for large result sets.
- Saved filters in P2.

### P1.9 Company, User, and Operational Administration

Add operational company management:

- Legal name, trading name, code, tax PIN, address, contacts, receipt details,
  status, and company-level defaults.
- Company list, detail, edit, archive, and audit timeline.

Enhance user administration:

- Organization-owned employee profile with optional national ID, employee code,
  department/team, manager, employment status, and privacy controls.
- Role presets plus multiple custom roles.
- Multiple branch assignments and optional agent custody.
- Invitation, resend, revoke, suspend, reactivate, session revoke, MFA status,
  and last activity.
- Permission comparison and effective-access explanation.
- No administrator-generated passwords.
- Add a secure organization switcher for users with multiple active memberships;
  changing organizations must validate membership, clear permission caches,
  update session context, and create an audit event.
- Add owner email verification and an explicit onboarding status page covering
  plan selection, subscription invoice, payment, activation, and any blocked
  setup steps.

### P1 Acceptance Gate

P1 is complete only when:

- Every SYSCO capability listed in Sections P1.1-P1.9 has a MobiPOS workflow or
  an explicitly approved safer replacement.
- Agent custody, import, commission, correction, and report workflows are tested
  end to end.
- Schema migrations include reversible local tests and PostgreSQL staging
  rehearsal with production-like data volume.
- Historical sales, serials, commission accruals, and credit-agency values remain
  readable after migration.
- All reports reconcile to source transactions and respect cost/margin privacy.
- No completed transaction is edited or deleted in place.
- Full test, browser, mobile, accessibility, performance, and deployment smoke
  suites pass.

Estimated effort: **40-60 engineering days**.

---

## 6. Phase P2: World-Class Operations and Enterprise UX

P2 completes the broader important backlog that is not required for basic SYSCO
parity but is required for a high-quality SaaS product.

### P2.1 Role-Specific Dashboards

- Owner/executive dashboard: revenue, margin, cash, receivables, payables, stock
  value, exceptions, branch comparison, and integrations.
- Branch manager dashboard: branch sales, targets, approvals, variances, stock,
  aged inventory, staff, and exceptions.
- Sales agent dashboard: assigned stock, personal sales, targets, commission,
  pending receipts, and tasks.
- Inventory dashboard: low stock, transfers, receipts, aged serials, stocktakes,
  discrepancies, and write-offs.
- Finance dashboard: collections, aging, expenses, payouts, settlements, cash
  variance, and failed payments.
- Technician dashboard: repair queue, SLA, waiting parts, warranty decisions, and
  customer notifications.

### P2.2 POS Productivity

- Keyboard shortcuts for scanner-first operation.
- Command/search palette.
- Suspend, label, recall, transfer, and expire carts.
- Batch scan with immediate duplicate feedback.
- Customer quick search and richer inline customer creation.
- Configurable quick products and favorites.
- Clear offline indicator; offline selling remains excluded.
- Responsive POS layouts for desktop and tablet.

### P2.3 Saved Views, Exports, and Scheduled Reports

- User and role-shared saved filters.
- Configurable columns and sorting.
- Scheduled report generation and delivery.
- Export history, expiration, authorization re-check, and download audit.
- Charts with accessible tabular equivalents.
- Report annotations and period comparison.

### P2.4 Customer, Supplier, Expense, and Document Workspaces

- Customer/supplier document and consent management.
- Collections notes, promises to pay, disputes, and communication history.
- Supplier settlement workspace and unapplied-payment allocation.
- Expense categories, attachments, payment status, reversal, and reimbursement.
- S3-compatible object storage, virus scanning, file-size/type controls,
  organization paths, signed URLs, and retention policy.

### P2.5 Repairs and Warranty

- Intake inspection checklist and device condition.
- Customer-reported fault, technician diagnosis, estimate, approval, and SLA.
- Photos and attachments.
- Parts reservation and consumption.
- Warranty eligibility and external warranty provider decisions.
- Kanban-style repair board with accessible list alternative.
- Customer status notifications and completion acknowledgement.
- Repair profitability and turnaround reports.

### P2.6 Audit and Activity Explorer

- Search and filter by user, action, record, module, branch, risk, and date.
- Before/after metadata for controlled configuration changes.
- Privileged activity and suspicious-pattern views.
- Export permission and retention controls.
- Links from business records to their complete audit timeline.

### P2.7 Accessibility and Responsive Quality

- WCAG 2.2 AA target for operational pages.
- Full keyboard navigation, visible focus, skip links, logical focus return, and
  modal focus trapping.
- Screen-reader names for icon buttons and status controls.
- Non-color status indicators and verified contrast.
- Responsive card/table alternatives for narrow screens.
- Touch targets and scanner workflows tested on tablet layouts.
- Automated accessibility checks plus manual keyboard and screen-reader UAT.

### P2.8 Performance and Scale

- Define query budgets for dashboard, register, detail, POS, search, and report
  pages.
- Add indexes based on real filter patterns, including organization, branch,
  status, dates, serials, references, and ownership.
- PostgreSQL concurrency tests for IMEI sale, transfer receipt, purchase receipt,
  payment confirmation, commission generation, and import posting.
- Background report generation and notification delivery.
- Cache stable configuration and permission metadata with correct invalidation.
- Load-test targets for 100, 1,000, and 10,000 users and document required
  Render/Supabase service sizes.

### P2 Acceptance Gate

- Each target role passes scripted UAT without inaccessible dead ends.
- Core pages meet agreed query and response-time budgets on PostgreSQL staging.
- Desktop, tablet, and mobile workflows pass responsive checks.
- Accessibility checks and manual keyboard testing pass agreed thresholds.
- Saved reports, exports, attachments, notifications, and audit explorer enforce
  tenant, branch, and permission scope.
- Operations documentation and user guides cover all completed workflows.

Estimated effort: **25-40 engineering days**.

---

## 7. Parallel Production Integration Track

This track is mandatory for live payment and fiscal operation and must not be
confused with the existing adapter/outbox foundation.

### 7.1 M-Pesa Daraja

- Obtain approved credentials and registered callback URLs.
- Implement OAuth token handling, STK Push/customer payment initiation,
  callbacks, timeout handling, status query, C2B where required, and
  reconciliation.
- Verify callback signatures or provider controls available for the selected
  product.
- Use idempotency keys to prevent duplicate payment confirmation.
- Move provider network calls outside long-held database transactions. Claim an
  event with a short atomic lease, perform the external request, then atomically
  persist the result.
- Scope idempotency and provider references deliberately by organization,
  provider, event type, and external reference while preserving globally unique
  sale identifiers.
- Keep unmatched callbacks in a reconciliation queue.
- Add administrator alerts, retry policy, dead-letter workflow, and daily
  settlement reconciliation.
- Support both customer sale payments and SaaS subscription payments without
  mixing tenant payment data.

### 7.2 KRA eTIMS

- Confirm the applicable KRA integration method and certification requirements.
- Obtain taxpayer, branch, device, certificate, and production credentials.
- Implement taxpayer/product/tax mapping, invoice submission, credit notes,
  cancellations, response storage, receipt fields, and reconciliation.
- Queue submissions through the transactional outbox.
- Do not mark a transaction fiscally accepted until a real KRA response is
  stored.
- Prevent unsafe reversal after fiscal acceptance; issue the required credit
  note/cancellation workflow.
- Alert on rejected, delayed, duplicate, and unreconciled fiscal events.

### 7.3 Production Infrastructure

- Supabase/PostgreSQL database with backups and tested restore.
- Redis when workers and scheduled jobs are enabled.
- Render web, worker, and scheduler services without Docker.
- Production email provider, object storage, domain, HTTPS, Sentry, and alerts.
- Environment-variable validation and secret rotation procedure.
- Staging callback URLs and provider sandbox/certification environments.

### Integration Acceptance Gate

- Provider sandbox/certification tests pass with recorded evidence.
- Duplicate, delayed, reordered, malformed, and replayed callbacks are tested.
- Reconciliation totals match MobiPOS payments and fiscal records.
- Failure and recovery drills are completed.
- Business, finance, security, and provider sign-off is recorded before enabling
  live mode.

Estimated engineering effort: **15-30 days**, excluding provider approval and
certification waiting time.

---

## 8. Data Migration and Compatibility Plan

Each schema workstream must include:

1. Add nullable/new structures without breaking current reads.
2. Backfill in deterministic, idempotent data migrations.
3. Add dual-read or compatibility adapters where rollout requires it.
4. Verify counts and monetary reconciliation before constraints are tightened.
5. Add non-null and uniqueness constraints only after successful backfill.
6. Remove compatibility paths in a later migration after production evidence.

Mandatory migration reconciliations:

- Existing hard-coded credit-agency values to agency records.
- Product tax rates to tax-code records.
- Products to default units and variants.
- Existing barcodes to product-barcode records.
- Existing stock locations and optional agent custody locations.
- Existing commission rules and accrual links.
- Existing aged-stock timestamps to the first aging anchor.
- Existing secondary IMEIs and provider references through duplicate-detection
  reports before new uniqueness constraints are enabled.
- Existing approval requests and installment schedules into history-preserving
  structures.

No migration may recalculate historical sale prices, costs, taxes, commissions,
or payment allocations from current master data.

---

## 9. Comprehensive Test Plan

### Authorization and Isolation

- Permission matrix tests for every route and action.
- Multiple-role union tests.
- Tenant and branch isolation for HTML, exports, search, background jobs, and
  callbacks.
- Direct UUID access tests.
- Owner, platform-admin, delegated approver, auditor, and suspended-user tests.
- Sensitive-field masking and financial-visibility tests.
- Django admin cross-tenant add/change/many-to-many tests and posted-record
  immutability tests.
- Global-search and dashboard leakage tests for users without module access.

### Inventory and Concurrency

- Duplicate primary and secondary IMEI tests.
- Concurrent sale, allocation, transfer, receipt, return, repair, and write-off.
- Agent custody and recall tests.
- Stocktake variance and reconciliation tests.
- Append-only movement and compensating reversal tests.
- Quantity and serialized stock balance invariants.

### Sales, Credit, Payments, and Corrections

- Cash, M-Pesa, card, bank, credit, split tender, change, and overpayment.
- Credit limit, overdue override, agency requirement, installments, and
  allocation.
- Sale reversal, return, refund, exchange, repair disposition, and fiscal lock.
- Date/customer/credit correction approval and audit history.
- Thermal, full-page, PDF, and secure-link receipt rendering.
- Pending-to-confirmed provider transitions, duplicate reference rejection,
  callback replay, manual-confirm permission, and reconciliation.
- Multi-location cashier register selection and duplicate-open-session tests.

### Purchasing and Suppliers

- Dynamic order lines and duplicate-product validation.
- CSV/XLSX preview, row errors, duplicate IMEI, retry, and idempotency.
- Partial receipt, discrepancy, cost correction, supplier return, reversal, and
  payable adjustment.
- Supplier statement and settlement reconciliation.

### Commissions

- Rule priority across organization, branch, role, category, product, variant,
  agency, and sale type.
- Fixed, percentage, margin, and tier calculations.
- Collection eligibility and partial payment.
- Return/refund/write-off reversal.
- Payout approval, payment, duplicate prevention, and reconciliation.

### Reports

- Totals reconcile to source records for identical scope and dates.
- Cost/margin privacy.
- All filter combinations and invalid date handling.
- CSV/XLSX/PDF/print content and branch scope.
- Pagination, large exports, saved views, and scheduled reports.

### UX, Accessibility, and Performance

- Locked-action and access-request browser tests.
- Scanner and keyboard POS tests.
- Responsive tests at phone, tablet, laptop, and wide desktop sizes.
- Automated accessibility checks and manual keyboard scripts.
- Query-count budgets and PostgreSQL load/concurrency tests.

### Deployment and Recovery

- Production settings and environment validation.
- Migration rehearsal against a production-size staging copy.
- Render web/worker/scheduler smoke tests.
- Health, logging, alert, Sentry, callback, and background-job tests.
- Backup restoration and transaction reconciliation drill.

---

## 10. Delivery Sequence and Dependencies

Implementation order:

1. P0 permission taxonomy, authorization matrix, privacy, and report correctness.
   This includes admin isolation, provider-state integrity, immutable approval
   history, redirect/CDN/CSP hardening, and domain reversal controls.
2. Catalog masters and configurable agencies because later transactions depend
   on them.
3. Agent custody and aging models because inventory, transfers, reports, and POS
   depend on their ownership semantics.
4. Commission-rule extensions and migration.
5. Purchasing imports, stocktakes, deductions, and supplier settlement.
6. Controlled sales/purchase correction and receipt workflows.
7. Dedicated report center and exports after transactional schemas stabilize.
8. Company/user administration and remaining operational screens.
9. P2 role dashboards, saved views, productivity, documents, repairs,
   accessibility, and scale.
10. Live M-Pesa/eTIMS activation only after provider certification and staging
    reconciliation.

Parallel work is allowed for UI components, test fixtures, documentation, and
provider coordination. Two workstreams must not independently modify shared
stock, payment, sale, or commission semantics without a single agreed service
contract.

---

## 11. Release and Documentation Requirements

Every release must update:

- `IMPLEMENTATION_STATUS.md`.
- The relevant P0/P1/P2 status report.
- `PRODUCTION_RUNBOOK.md` when operations change.
- `SECURITY.md` when permissions or sensitive data change.
- Demo-data generation for new required records and role presets.
- User-facing workflow documentation.
- Migration and rollback notes.

Every release must run:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
pytest
npm run css:build
python manage.py collectstatic --noinput
git diff --check
```

Concurrency-critical releases must additionally run against PostgreSQL staging.

---

## 12. Final Definition of Done

The important-gap program is complete only when:

- MobiPOS supports every verified SYSCO business capability through an equal or
  safer workflow.
- Agent custody, catalog masters, agencies, commissions, imports, deductions,
  corrections, receipts, reports, companies, users, and aging policies are
  operationally complete.
- Multiple roles and branch scope work consistently in navigation, pages,
  actions, exports, jobs, and APIs/callbacks.
- Cost, margin, PII, reversals, approvals, and audit data are protected by
  explicit permissions.
- M-Pesa and eTIMS are either certified and reconciled or visibly disabled; no
  placeholder response can appear live.
- PostgreSQL concurrency, load, backup/restore, security, accessibility, and
  role-based UAT gates pass.
- No known P0 or P1 defect remains open.
- Production launch is supported by documented operations, monitoring,
  incident response, and business sign-off.

Estimated total: **88-142 engineering days**, plus external provider approval,
certification, UAT, and infrastructure lead time.
