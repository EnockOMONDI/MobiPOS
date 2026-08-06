import csv
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.catalog.models import Brand, Category, Product
from apps.catalog.permissions import can_view_product_costs
from apps.contacts.models import Contact
from apps.commissions.models import CommissionAccrual, CommissionPayout
from apps.expenses.models import Expense
from apps.integrations.models import FiscalDevice, FiscalDocument, IntegrationEvent
from apps.inventory.models import SerialStatus, StockAdjustment, StockBalance, StockMovement, StockUnit
from apps.operations.models import ApprovalRequest, Payable, Receivable
from apps.organizations.models import AgentProfile, Branch, Location, LocationType, Membership, Organization, Role, SubscriptionInvoice
from apps.organizations.permissions import accessible_branches_for, organization_permission_required, user_has_organization_permission
from apps.payments.models import Payment, Refund
from apps.pos.models import POSSession
from apps.purchasing.models import PurchaseDiscrepancy, PurchaseOrder, PurchaseStatus, SupplierReturn
from apps.repairs.models import RepairTicket
from apps.sales.models import Sale, SaleReturn, SaleStatus
from apps.transfers.models import StockTransfer, TransferDiscrepancy, TransferStatus
from .exporting import build_simple_pdf, safe_csv_row


BRANCH_LOOKUPS = {
    Branch: "id",
    AgentProfile: "branch",
    Location: "branch",
    StockUnit: "location__branch",
    StockBalance: "location__branch",
    StockMovement: "location__branch",
    StockAdjustment: "location__branch",
    PurchaseOrder: "destination__branch",
    SupplierReturn: "line__order__destination__branch",
    PurchaseDiscrepancy: "line__order__destination__branch",
    Sale: "location__branch",
    Payment: "sale__location__branch",
    Expense: "branch",
    CommissionAccrual: "sale__location__branch",
    RepairTicket: "branch",
    Receivable: "sale__location__branch",
    Payable: "purchase_order__destination__branch",
    SaleReturn: "sale__location__branch",
    Refund: "payment__sale__location__branch",
    POSSession: "location__branch",
}

LANDING_FEATURE_CARDS = [
    "POS sales",
    "IMEI inventory",
    "Batch Excel intake",
    "Warehouse transfers",
    "Agent allocation",
    "Stock recall",
    "Credit sales",
    "Commissions",
    "Repairs and warranty",
    "Activity reports",
    "Role permissions",
    "Operational dashboards",
]

LANDING_ROADMAP_ITEMS = [
    "M-Pesa reconciliation",
    "eTIMS invoicing",
    "BI dashboards",
    "Stock forecasting",
    "Mobile apps",
    "Accounting APIs",
    "AI insights",
    "Barcode and QR",
    "Franchise control",
    "CRM loyalty",
]

HELP_TOPICS = [
    {
        "section": "Business Structure",
        "title": "Organization",
        "summary": "The main business account in MobiPOS.",
        "example": "Kipekee Demo or Nairobi Mobile Hub is the organization that owns the staff, branches, stock, sales and reports.",
        "steps": [
            "A business owner creates the organization from the signup page.",
            "MobiPOS creates the first company, main branch and starter location so the owner can begin setup.",
            "All users, products, stock movements, purchases and sales stay inside that organization.",
        ],
        "why": "This keeps one business separated from another and gives the owner one place to manage the whole operation.",
    },
    {
        "section": "Business Structure",
        "title": "Company",
        "summary": "The registered business entity inside the organization.",
        "example": "Nairobi Mobile Hub Limited can be the company under the Nairobi Mobile Hub organization.",
        "steps": [
            "The company is selected when creating branches.",
            "Company details can later support branding, tax setup, reports and official documents.",
            "A growing group can add more companies if it operates more than one registered business.",
        ],
        "why": "It helps separate legal or operating entities while keeping management under one platform.",
    },
    {
        "section": "Business Structure",
        "title": "Branch",
        "summary": "A branch is one shop, outlet or operating office.",
        "example": "Mombasa Shop, Nairobi CBD and Westlands are branches.",
        "steps": [
            "Open Branches and create a branch with a short code such as MSA, CBD or WES.",
            "Assign staff to the branch when adding users.",
            "Create locations under the branch for warehouse, POS counter, repair bench or agent custody.",
        ],
        "why": "The owner can compare stock, sales, expenses and staff activity by branch.",
    },
    {
        "section": "Business Structure",
        "title": "Location",
        "summary": "A location is the exact stock-holding point under a branch.",
        "example": "Mombasa POS Counter, Mombasa Warehouse, Repair Bench and Agent Custody are locations.",
        "steps": [
            "Create a branch first.",
            "Open Locations and create the stock point under that branch.",
            "Choose the location type: warehouse, POS, repair, agent custody or in transit.",
            "Receive, transfer, sell or repair stock from the correct location.",
        ],
        "why": "You know not only which branch has stock, but exactly where the item is sitting.",
    },
    {
        "section": "Business Structure",
        "title": "Warehouse",
        "summary": "A warehouse is a location used to hold stock before it goes to branches, POS counters or agents.",
        "example": "Main Warehouse receives phones from suppliers before sending them to Westlands Branch.",
        "steps": [
            "Create a warehouse location under the correct branch.",
            "Use New Purchase to receive stock into that warehouse.",
            "Use Transfers to dispatch stock from the warehouse to another location.",
        ],
        "why": "Warehouse control prevents stock from moving directly into sales without accountability.",
    },
    {
        "section": "Business Structure",
        "title": "POS Location",
        "summary": "A POS location is where cashiers sell stock to customers.",
        "example": "Nairobi CBD POS Counter is where a cashier sells phones and accessories.",
        "steps": [
            "Create the POS location under a branch.",
            "Assign cashiers to the branch.",
            "Open a POS session and sell products from that location.",
        ],
        "why": "Sales reduce stock from the correct counter and make cashier reconciliation easier.",
    },
    {
        "section": "Products and Inventory",
        "title": "Product",
        "summary": "A product is an item the business buys, tracks or sells.",
        "example": "Samsung A15, iPhone 13, Type-C Charger and Screen Protector are products.",
        "steps": [
            "Open Add Product and enter the product name, SKU, category, brand, buying price and selling price.",
            "Tick the serialized option if each unit must be tracked individually.",
            "Use the product in purchases, stock intake, transfers, POS, repairs and reports.",
        ],
        "why": "One clean product record prevents different staff from using different names for the same item.",
    },
    {
        "section": "Products and Inventory",
        "title": "Serialized Product",
        "summary": "A serialized product is an item where every individual unit must be tracked separately.",
        "example": "If you buy 10 Samsung A15 phones, each phone has its own IMEI. They are not treated as one general quantity.",
        "steps": [
            "Create the product on the Add Product page.",
            "Tick the serialized checkbox because each unit needs its own identity.",
            "Purchase or receive stock from a supplier on the New Purchase page.",
            "Enter, paste or upload the IMEI or serial numbers during receiving or batch intake.",
            "MobiPOS creates one tracked stock unit for each IMEI or serial number.",
            "When the phone is transferred or sold, the cashier selects the exact IMEI.",
        ],
        "why": "The business can trace the exact phone from supplier purchase to warehouse, branch, agent, customer, return or repair.",
    },
    {
        "section": "Products and Inventory",
        "title": "Non-Serialized Product",
        "summary": "A non-serialized product is tracked by quantity only.",
        "example": "Phone covers, chargers, earphones and screen protectors are usually counted by quantity.",
        "steps": [
            "Create the product on the Add Product page without ticking serialized.",
            "Receive the quantity from a supplier during purchase receiving.",
            "Transfer or sell the product by quantity instead of selecting individual IMEIs.",
            "MobiPOS updates the stock balance for that product and location.",
        ],
        "why": "It keeps accessory stock fast and simple without forcing staff to enter unnecessary serial numbers.",
    },
    {
        "section": "Products and Inventory",
        "title": "IMEI",
        "summary": "An IMEI is a unique number used to identify one mobile phone or cellular device.",
        "example": "Samsung A15 with IMEI 356789123456789 is one specific device.",
        "steps": [
            "Mark the phone product as serialized.",
            "Enter or upload the IMEI during purchase receiving or Batch IMEI Intake.",
            "Search the IMEI from global search, device search, transfer selection or POS.",
            "MobiPOS prevents the same IMEI from being sold twice in the same organization.",
        ],
        "why": "IMEI tracking protects against stock loss, duplicate sales, warranty confusion and disputes with customers or agents.",
    },
    {
        "section": "Products and Inventory",
        "title": "Serial Number",
        "summary": "A serial number is a unique identifier for one specific unit. An IMEI is one type of serial number.",
        "example": "A router may have a serial number instead of an IMEI.",
        "steps": [
            "Create the product as serialized.",
            "Enter the serial number as the primary identifier or use the secondary serial field where needed.",
            "Use device search to find the unit later by serial, product, SKU or location.",
        ],
        "why": "It gives the business unit-level control for devices that are not phones.",
    },
    {
        "section": "Products and Inventory",
        "title": "Batch IMEI Intake",
        "summary": "Batch intake lets the warehouse register many IMEIs or serial numbers at once.",
        "example": "A supplier sends 80 phones and a spreadsheet with all IMEIs.",
        "steps": [
            "Open Batch Serial Intake.",
            "Choose the product and receiving location.",
            "Paste IMEIs, upload a CSV file or upload an Excel workbook.",
            "Review accepted rows and row-level errors.",
            "MobiPOS creates stock units and stock movement records for the valid devices.",
        ],
        "why": "Large deliveries can be registered quickly while still catching duplicate or invalid identifiers.",
    },
    {
        "section": "Products and Inventory",
        "title": "Stock Movement",
        "summary": "A stock movement is a recorded change in stock caused by buying, transferring, selling, returning or adjusting stock.",
        "example": "Receiving 20 phones into the warehouse creates stock-in movements.",
        "steps": [
            "Complete a purchase, transfer, sale, return or adjustment.",
            "MobiPOS records the movement with product, location, quantity and user context.",
            "Reports and IMEI history use those movements to explain what happened.",
        ],
        "why": "The owner can see why stock changed instead of only seeing today’s balance.",
    },
    {
        "section": "Products and Inventory",
        "title": "Stock Ledger",
        "summary": "The stock ledger is the permanent history of stock movements.",
        "example": "A phone can show received from supplier, transferred to branch, allocated to agent and sold to customer.",
        "steps": [
            "MobiPOS writes ledger entries when stock actions are completed.",
            "Completed movements are not edited directly.",
            "Corrections are handled through reversals or new corrective movements.",
        ],
        "why": "It gives management a trustworthy record for audits and disputes.",
    },
    {
        "section": "Purchasing and Suppliers",
        "title": "Supplier",
        "summary": "A supplier is a person or company the business buys stock from.",
        "example": "Safaricom Dealer, Phone Importer Ltd or Opening Stock Supplier can be suppliers.",
        "steps": [
            "Open Add Supplier from Contacts or from the New Purchase setup prompt.",
            "Enter supplier name, phone, email and Tax number/PIN where available.",
            "Select the supplier when creating a purchase order.",
            "Supplier purchases, returns and payables appear on the supplier statement.",
        ],
        "why": "Good supplier records help track what was bought, who supplied it and what the business still owes.",
    },
    {
        "section": "Purchasing and Suppliers",
        "title": "Purchase Order",
        "summary": "A purchase order records stock being bought or received from a supplier.",
        "example": "The business buys 15 Samsung A15 phones and 50 chargers from one supplier.",
        "steps": [
            "Open New Purchase.",
            "Select the supplier and destination location.",
            "Add the products, quantities and unit cost.",
            "Attach a supplier invoice, delivery note, CSV, image, PDF or Excel file if available.",
            "Approve and receive the order so stock enters the selected location.",
        ],
        "why": "Purchases connect supplier documents, received stock, cost prices and supplier balances.",
    },
    {
        "section": "Purchasing and Suppliers",
        "title": "Receiving Stock",
        "summary": "Receiving confirms what actually arrived from the supplier.",
        "example": "You ordered 20 phones but only 18 arrived.",
        "steps": [
            "Open the purchase order.",
            "Receive each purchase line into the destination location.",
            "For serialized products, enter or upload the IMEIs received.",
            "If the quantity differs from the order, MobiPOS records a discrepancy.",
        ],
        "why": "The business pays for and reports what actually arrived, not just what was ordered.",
    },
    {
        "section": "Purchasing and Suppliers",
        "title": "Supplier Return",
        "summary": "A supplier return sends stock back to the supplier.",
        "example": "A new phone arrives damaged and must be returned to the supplier.",
        "steps": [
            "Open Supplier Return.",
            "Select the related purchase line and stock unit where needed.",
            "Enter the quantity and reason.",
            "Complete the return so stock and supplier balances are updated.",
        ],
        "why": "Returns stay linked to the original purchase instead of disappearing from stock without explanation.",
    },
    {
        "section": "Transfers and Custody",
        "title": "Stock Transfer",
        "summary": "A transfer moves stock from one location to another.",
        "example": "Move 5 phones from Main Warehouse to Westlands POS Counter.",
        "steps": [
            "Open Transfers and create a new transfer.",
            "Select the source location and destination location.",
            "Search products, IMEIs or serial numbers and select the stock being moved.",
            "Dispatch the transfer from the source location.",
            "The receiving side confirms receipt and records any discrepancy.",
        ],
        "why": "Transfers show who moved stock, where it came from, where it went and whether everything arrived.",
    },
    {
        "section": "Transfers and Custody",
        "title": "Dispatch",
        "summary": "Dispatch confirms stock has left the sending location.",
        "example": "The warehouse dispatches three selected IMEIs to TRM Branch.",
        "steps": [
            "Create or approve the transfer.",
            "Confirm dispatch once the physical stock leaves the source location.",
            "The stock changes to an in-transit state until the destination receives it.",
        ],
        "why": "It separates stock that is still in the warehouse from stock already on the way.",
    },
    {
        "section": "Transfers and Custody",
        "title": "Transfer Discrepancy",
        "summary": "A discrepancy records a mismatch between what was sent and what was received.",
        "example": "Warehouse sends 5 phones, but the branch confirms only 4 arrived.",
        "steps": [
            "The receiving user enters the actual received quantity or devices.",
            "MobiPOS records the discrepancy.",
            "A manager reviews and resolves the issue through the discrepancy workflow.",
        ],
        "why": "Missing or extra stock is visible immediately instead of becoming an unexplained loss later.",
    },
    {
        "section": "Transfers and Custody",
        "title": "Agent Allocation",
        "summary": "Agent allocation assigns stock to an agent or DSA custodian.",
        "example": "An agent receives two phones to sell in the field.",
        "steps": [
            "Create or approve the agent profile.",
            "Create an agent custody location if it does not already exist.",
            "Use Agent Allocation to select existing stock units by IMEI or product.",
            "MobiPOS updates the stock custodian and records the movement.",
        ],
        "why": "The owner can see which agent is holding which exact devices.",
    },
    {
        "section": "Sales and Payments",
        "title": "Customer",
        "summary": "A customer is the person or business buying from you.",
        "example": "A walk-in customer buys a charger, or John Mwangi buys a phone on credit.",
        "steps": [
            "Create a customer from Contacts or directly during POS where the flow allows it.",
            "Use the customer during POS sales, credit sales, returns and statements.",
            "Customer balances appear through receivables and payments.",
        ],
        "why": "Customer records help with credit follow-up, statements, warranties and repeat business.",
    },
    {
        "section": "Sales and Payments",
        "title": "POS Cart",
        "summary": "The POS cart is where a cashier prepares a sale before checkout.",
        "example": "The cashier adds one Samsung phone and one screen protector to the cart.",
        "steps": [
            "Open or continue a POS session.",
            "Search the product by name, SKU, barcode, IMEI or serial number.",
            "For serialized products, select the exact IMEI.",
            "For non-serialized products, enter the quantity.",
            "Proceed to checkout and record payments.",
        ],
        "why": "The cart prevents accidental sale of the wrong IMEI and keeps every sale organized.",
    },
    {
        "section": "Sales and Payments",
        "title": "Sale",
        "summary": "A sale is the completed transaction between the business and a customer.",
        "example": "Invoice S-1001 sells one Samsung A15 and one charger.",
        "steps": [
            "Create the cart in POS.",
            "Choose customer details or use walk-in customer.",
            "Record payment or credit details.",
            "Complete the cart so stock moves out and the receipt is created.",
        ],
        "why": "Completed sales connect stock, payment, cashier session, customer and receipt in one record.",
    },
    {
        "section": "Sales and Payments",
        "title": "Payment",
        "summary": "A payment records money received from the customer.",
        "example": "A customer pays KES 10,000 by M-Pesa and KES 2,500 by cash.",
        "steps": [
            "At checkout, choose cash, M-Pesa, card, bank or credit.",
            "Enter the amount and any provider reference where available.",
            "For split payment, add more than one payment method against the same sale.",
            "MobiPOS totals payments against the sale balance.",
        ],
        "why": "The business can reconcile cashier money, M-Pesa references, bank payments and credit balances.",
    },
    {
        "section": "Sales and Payments",
        "title": "Split Payment",
        "summary": "A split payment uses more than one payment method for one sale.",
        "example": "The customer pays part by M-Pesa and part in cash.",
        "steps": [
            "Create the sale in POS.",
            "Enter the first payment method and amount.",
            "Add another payment method for the remaining amount.",
            "Complete the sale when the payment total matches the required amount or record the balance as credit.",
        ],
        "why": "It matches how customers actually pay and keeps one clean receipt instead of multiple fake sales.",
    },
    {
        "section": "Sales and Payments",
        "title": "Credit Sale",
        "summary": "A credit sale lets the customer take goods now and pay later.",
        "example": "A customer takes a KES 20,000 phone, pays KES 5,000 today and will pay KES 15,000 later.",
        "steps": [
            "Select the customer in POS.",
            "Choose credit sale or leave an outstanding balance during checkout.",
            "MobiPOS creates a receivable for the unpaid amount.",
            "Record future payments against the customer or sale until the balance is cleared.",
        ],
        "why": "Credit stays visible, controlled and collectible instead of sitting in a notebook.",
    },
    {
        "section": "Credit and Balances",
        "title": "Receivable",
        "summary": "A receivable is money a customer owes the business.",
        "example": "A customer still owes KES 15,000 after a credit phone sale.",
        "steps": [
            "Complete a credit sale.",
            "MobiPOS creates a receivable with original amount, outstanding amount and due date.",
            "Use installments or payments to reduce the outstanding balance.",
            "Review receivables in reports and customer statements.",
        ],
        "why": "Owners can follow up debt and avoid selling more credit to customers who are already overdue.",
    },
    {
        "section": "Credit and Balances",
        "title": "Payable",
        "summary": "A payable is money the business owes a supplier.",
        "example": "The business receives stock worth KES 300,000 but has not yet paid the supplier.",
        "steps": [
            "Create and receive a purchase order.",
            "MobiPOS records supplier balance when payment is due.",
            "Supplier statements show outstanding payables.",
        ],
        "why": "The business can plan supplier payments and avoid losing track of debts.",
    },
    {
        "section": "People and Access",
        "title": "Role",
        "summary": "A role is a job profile that gives a user a set of permissions.",
        "example": "Cashier, Manager, Inventory Officer, Agent and Technician are roles.",
        "steps": [
            "The owner opens Users and Access.",
            "Create a staff user and tick one or more roles.",
            "Assign the branches where the user should work.",
            "MobiPOS combines the selected roles into that user’s access profile.",
        ],
        "why": "Staff get enough access to do their job without opening sensitive business functions unnecessarily.",
    },
    {
        "section": "People and Access",
        "title": "Permission",
        "summary": "A permission controls whether a user can view, create, edit, approve or export a specific business function.",
        "example": "A cashier may sell products but not approve stock discrepancies.",
        "steps": [
            "Permissions are attached to roles.",
            "When a user clicks a locked action, MobiPOS can show an access request instead of hiding the action.",
            "An owner or manager reviews access needs and updates the user’s roles or permissions.",
        ],
        "why": "It supports accountability while keeping the app easy to discover.",
    },
    {
        "section": "People and Access",
        "title": "Agent",
        "summary": "An agent is a field seller or custodian who can hold business stock outside a normal branch counter.",
        "example": "An agent receives two phones from the warehouse to sell to customers.",
        "steps": [
            "Create the user or agent profile.",
            "Upload ID or onboarding documents where required.",
            "Approve the agent profile.",
            "Allocate stock to the agent custody location.",
            "Track sales, recalls and stock balances for that agent.",
        ],
        "why": "Field stock remains visible and accountable even when it leaves the shop.",
    },
    {
        "section": "People and Access",
        "title": "DSA / Sub-Agent",
        "summary": "A DSA is a direct sales agent linked under a supervising agent or manager.",
        "example": "An approved agent registers a DSA who sells under their supervision.",
        "steps": [
            "Create the DSA profile from the agent workflow.",
            "Attach ID details and supporting documents.",
            "Link the DSA to the supervising agent.",
            "Approve before allocating stock or recognizing field activity.",
        ],
        "why": "The organization can mirror real sales hierarchies while still controlling accountability.",
    },
    {
        "section": "Repairs and Warranty",
        "title": "Warranty",
        "summary": "Warranty records whether a product is covered after sale.",
        "example": "A phone has a 12-month warranty and returns with a screen issue after two months.",
        "steps": [
            "Set warranty details on the product where relevant.",
            "Use the sale or IMEI history to confirm the customer and purchase date.",
            "Create a repair ticket or return workflow based on the case.",
        ],
        "why": "Staff can handle after-sales issues without guessing whether the item qualifies.",
    },
    {
        "section": "Repairs and Warranty",
        "title": "Repair Ticket",
        "summary": "A repair ticket tracks a customer device or internal stock item being repaired.",
        "example": "A customer brings back a phone with charging problems.",
        "steps": [
            "Open New Repair Ticket.",
            "Select customer, branch, device details and issue description.",
            "Update ticket status as work progresses.",
            "Record repair part usage where parts are consumed.",
        ],
        "why": "Repairs become traceable work instead of informal promises between staff and customers.",
    },
    {
        "section": "Reports and Controls",
        "title": "Audit Trail",
        "summary": "The audit trail records important user actions across the system.",
        "example": "MobiPOS records that Enock created a supplier, approved an agent or uploaded an ID document.",
        "steps": [
            "Users perform actions such as creating contacts, uploading documents or approving profiles.",
            "MobiPOS records the actor, time, action and target record.",
            "Owners review activity from the dashboard or activity report.",
        ],
        "why": "It improves accountability and helps explain who did what and when.",
    },
    {
        "section": "Reports and Controls",
        "title": "Activity Report",
        "summary": "The activity report shows owner-visible actions across users and operations.",
        "example": "An owner can see user creation, supplier creation, agent approvals and document uploads.",
        "steps": [
            "Open Activity Report from Reports.",
            "Search or filter activity by user, action or date.",
            "Use the report to investigate operational changes.",
        ],
        "why": "Owners get visibility without checking each module one by one.",
    },
    {
        "section": "Reports and Controls",
        "title": "Supplier Statement",
        "summary": "A supplier statement summarizes supplier purchases, payments and balances.",
        "example": "A supplier statement shows purchases received and payables still owed.",
        "steps": [
            "Open the supplier profile.",
            "Click Statement.",
            "Review purchases, payables and totals.",
            "Download as PDF, Excel or CSV, or print the statement.",
        ],
        "why": "It makes supplier discussions and payment follow-up clearer.",
    },
    {
        "section": "Reports and Controls",
        "title": "Customer Statement",
        "summary": "A customer statement summarizes customer sales, payments and receivable balances.",
        "example": "A customer who bought on credit can be shown their outstanding balance.",
        "steps": [
            "Open the customer profile.",
            "Click Statement.",
            "Review sales, payments and receivables.",
            "Download or print the statement when needed.",
        ],
        "why": "It gives staff and customers one shared view of what is owed.",
    },
    {
        "section": "Setup and Integrations",
        "title": "Standard Receipt",
        "summary": "A standard receipt is the customer-friendly proof of sale printed from POS.",
        "example": "A cashier completes a sale and prints the normal receipt for the customer.",
        "steps": [
            "Complete the POS sale.",
            "Open the sale detail page.",
            "Click Print standard receipt.",
            "Give the customer the receipt immediately.",
        ],
        "why": "The customer gets quick proof of purchase even when tax integrations are not finalized.",
    },
    {
        "section": "Setup and Integrations",
        "title": "eTIMS Tax Receipt",
        "summary": "An eTIMS tax receipt is the compliant tax receipt after tax provider acceptance and signing.",
        "example": "A sale is submitted for tax signing and later receives the official tax receipt details.",
        "steps": [
            "Configure eTIMS setup for the organization and branch.",
            "Complete a sale that requires eTIMS handling.",
            "MobiPOS records an integration event for the tax submission.",
            "The final tax receipt becomes available after the provider accepts and signs the document.",
        ],
        "why": "It supports tax compliance while still allowing the business to print a normal receipt for customers.",
    },
    {
        "section": "Setup and Integrations",
        "title": "M-Pesa Reference",
        "summary": "An M-Pesa reference is the confirmation code or payment reference from an M-Pesa transaction.",
        "example": "A customer pays by M-Pesa and gives reference QH123ABC45.",
        "steps": [
            "At checkout, choose M-Pesa as the payment method.",
            "Enter the amount and provider reference if live confirmation is not connected yet.",
            "MobiPOS stores the reference against the payment.",
            "Reports use the payment record for reconciliation.",
        ],
        "why": "It reduces confusion when matching POS sales to M-Pesa statements.",
    },
    {
        "section": "Setup and Integrations",
        "title": "Offline Invoice Queue",
        "summary": "The offline queue is a controlled place for invoices created when connectivity is unreliable.",
        "example": "A cashier records a draft invoice while the internet is unstable, then syncs it later.",
        "steps": [
            "Create the sale carefully when the system indicates offline or sync risk.",
            "Keep the invoice as a controlled draft until it can be confirmed.",
            "Sync only once the system can safely verify stock, payments and invoice references.",
        ],
        "why": "It protects the business from duplicate invoices, duplicate IMEI sales and missing payment records.",
    },
]


PRODUCT_FEATURE_GROUPS = [
    {
        "title": "Business structure and access",
        "summary": "Give each company, branch, location and staff member a clear place in the business, without exposing another organization’s data.",
        "features": [
            {
                "name": "Multi-tenant business structure",
                "status": "complete",
                "what": "One platform can serve separate organizations, companies, branches and stock locations.",
                "how": "Every operational record is attached to an organization and, where relevant, a branch and location. Users only work inside the organization and branches assigned to them.",
                "scenario": "A retailer with Nairobi and Kisumu branches can review each branch separately while the owner sees the combined business picture.",
            },
            {
                "name": "Users, multiple roles and branch access",
                "status": "complete",
                "what": "A user can hold more than one role and receive access only to the branches they need.",
                "how": "Memberships link users to an organization, roles contribute permissions, and branch assignments limit operational data. Locked actions remain visible and open an access-request flow.",
                "scenario": "A branch manager can approve a transfer at their branch and also sell at the POS, without gaining access to another branch’s stock.",
            },
            {
                "name": "Grouped navigation and operational dashboards",
                "status": "complete",
                "what": "The working interface organizes stock, sales, people, money, repairs, approvals and reports into clear groups.",
                "how": "Navigation is role-aware while keeping unavailable actions visible. The dashboard changes its metrics and recent activity to the signed-in user’s permitted scope.",
                "scenario": "A cashier starts a sale quickly, while an owner opens the same system and sees sales, stock, approvals and activity summaries.",
            },
        ],
    },
    {
        "title": "Catalog, contacts and serialized inventory",
        "summary": "Create one reliable source for products, customers, suppliers and every tracked device.",
        "features": [
            {
                "name": "Products, brands and categories",
                "status": "complete",
                "what": "A product master holds the selling, cost, barcode, warranty, tax and tracking rules used across operations.",
                "how": "Products are created once, then selected in purchasing, intake, transfers, POS, repairs and reports. Products can be serialized for phones or quantity-based for accessories.",
                "scenario": "A Samsung phone is marked as serialized while a screen protector is sold by quantity, each following the correct workflow automatically.",
            },
            {
                "name": "Customers and suppliers",
                "status": "complete",
                "what": "Customer and supplier records provide the commercial identity behind sales, credit, purchasing and statements.",
                "how": "Contact profiles record type, contact details and active status; customer transactions feed receivables and supplier purchases feed payables and returns.",
                "scenario": "A repeat customer’s credit balance is visible before the cashier creates another credit sale, while purchasing can select the supplier for a new stock order.",
            },
            {
                "name": "IMEI and serial lifecycle tracking",
                "status": "complete",
                "what": "Each phone or tracked device has one central stock-unit record from receipt through transfer, allocation, sale, return or repair.",
                "how": "The system validates serial and IMEI values within the organization, prevents duplicates, stores current location and status, and provides IMEI history search.",
                "scenario": "An owner searches an IMEI and sees the device, its current or last location, movement history and related sale context instead of checking multiple spreadsheets.",
            },
            {
                "name": "Immutable stock movement ledger",
                "status": "complete",
                "what": "Completed stock movements are permanent business evidence rather than editable balances.",
                "how": "Purchases, transfers, allocations, sales, returns and adjustments post movement records. Completed entries cannot be edited or deleted; corrections are made as reversing movements.",
                "scenario": "When a transfer was posted to the wrong location, the manager records a reversal and the trail still shows both the original action and the correction.",
            },
            {
                "name": "Batch serial and IMEI intake",
                "status": "complete",
                "what": "Teams can add many serialized devices without creating one record at a time.",
                "how": "The intake screen accepts pasted serial or IMEI lines, CSV files and Excel workbooks. Each row is validated before stock is received, with valid items created and row-level issues reported.",
                "scenario": "A warehouse receives 120 phones, uploads the supplier spreadsheet, and immediately sees which IMEIs were accepted and which duplicates need attention.",
            },
            {
                "name": "Barcode and QR camera scanning",
                "status": "partial",
                "what": "Current forms are scanner-friendly and accept barcode, serial and IMEI text from hardware scanners.",
                "how": "A USB or Bluetooth scanner can type into focused identifier fields just like a keyboard. A dedicated in-browser phone-camera scanner is not yet built.",
                "scenario": "A warehouse operator can scan with a handheld scanner today; a field user cannot yet use the phone camera to scan a QR code directly inside MobiPOS.",
            },
        ],
    },
    {
        "title": "Purchasing, transfers and custody",
        "summary": "Move stock through the business with clear custody, confirmation and discrepancy control.",
        "features": [
            {
                "name": "Purchases, receiving and supplier returns",
                "status": "complete",
                "what": "Teams can order stock, approve an order, receive it, manage receiving differences and return items to suppliers.",
                "how": "Purchase lines retain ordered and received quantities. Receiving can flag a discrepancy, and supplier returns are linked back to the original purchase line.",
                "scenario": "A supplier delivers 48 phones against an order for 50. The receiver records 48, logs the shortfall and finance retains the correct payable context.",
            },
            {
                "name": "Warehouse, branch and agent transfers",
                "status": "complete",
                "what": "Existing stock can be searched and selected for controlled movement between locations, branches and agent custody.",
                "how": "Transfers use approval, dispatch and receipt stages. The receiving side confirms quantities, and discrepancies remain visible until resolved. Agent allocation and recall use the same controlled movement model.",
                "scenario": "A warehouse sends selected IMEIs to a branch, then allocates a subset to an agent. The owner can see the exact custodian at every stage.",
            },
            {
                "name": "Agent and DSA hierarchy",
                "status": "complete",
                "what": "Businesses can register agents and direct sales agents under their supervising agent when company policy allows it.",
                "how": "Agent profiles capture the hierarchy, verification state, documents and onboarding activity. Authorized users can approve, reject, allocate stock and recall stock.",
                "scenario": "A regional agent onboards a DSA, uploads ID evidence, waits for approval and then receives inventory only after the profile is approved.",
            },
            {
                "name": "National ID and onboarding documents",
                "status": "complete",
                "what": "Agent records can hold identification numbers, an image or document scan, photographs and supporting onboarding documents.",
                "how": "Documents are uploaded against the agent profile, and the audit trail records who created or changed the onboarding record. Direct camera capture is not a separate native scanning module.",
                "scenario": "A manager photographs an agent’s ID using the device camera upload control, attaches it to the profile and later downloads it during a compliance review.",
            },
        ],
    },
    {
        "title": "Sales, money and customer care",
        "summary": "Sell serialized and quantity stock, accept the payment mix customers use, and retain control after the sale.",
        "features": [
            {
                "name": "POS cart, checkout and payment methods",
                "status": "complete",
                "what": "The POS supports cart building, serialized items, accessories, receipts and recorded cash, M-Pesa, card, bank and credit payments.",
                "how": "Cashiers open a session, add product lines, complete the cart and record one or more payments. Split payment totals are tracked against the sale and the session can be reviewed and closed.",
                "scenario": "A customer pays KES 10,000 by M-Pesa and KES 2,500 in cash. The cashier records both methods against one phone sale and the session totals reconcile later.",
            },
            {
                "name": "Customer credit, receivables and installments",
                "status": "complete",
                "what": "Credit sales can be controlled by customer limits, then followed as outstanding receivables with scheduled installments.",
                "how": "A credit sale creates a receivable. Staff can record and schedule installments, while operational reports surface aging and outstanding balances.",
                "scenario": "A trusted customer takes a phone on credit. The business records the limit, schedules three monthly payments and checks overdue balances before approving new credit.",
            },
            {
                "name": "Returns, refunds, warranties and repairs",
                "status": "complete",
                "what": "The business can request and approve a line-level return, record refunds, track warranty cases and manage repair tickets with parts usage.",
                "how": "Returns remain tied to the original sale line. Repair tickets hold customer/device context and can record parts taken from stock, maintaining a traceable after-sales path.",
                "scenario": "A customer returns a faulty phone. Staff locate the sale, request the return, route it to warranty or repair, and retain the financial and stock history.",
            },
            {
                "name": "Live M-Pesa confirmation and reconciliation",
                "status": "partial",
                "what": "M-Pesa can be recorded as a payment method today, but live Daraja callbacks and automated confirmation are not connected.",
                "how": "The integration outbox and adapter foundation exist, but live credentials, callback handling, reconciliation and production monitoring must be implemented before M-Pesa is treated as automatically confirmed.",
                "scenario": "A cashier can record an M-Pesa reference now; the future live connection will validate the payment and reconcile it without manual checking.",
            },
        ],
    },
    {
        "title": "Control, reporting and scale",
        "summary": "Give owners a reliable view of actions, exceptions and the areas that require attention.",
        "features": [
            {
                "name": "Expenses, commissions and approvals",
                "status": "complete",
                "what": "Expenses, commission accruals and payout workflows have approval and payment controls.",
                "how": "The approval inbox handles configured approvals, while commission records connect to sales and payout status. Expenses and operational money movements appear in reports.",
                "scenario": "A salesperson earns commission from a paid sale, a manager approves the payout, and the owner can later see the approval and payment history.",
            },
            {
                "name": "Owner activity reporting and audit trail",
                "status": "complete",
                "what": "Authorized owners and platform administrators can review platform or organization activity, including actor summaries and login activity.",
                "how": "Audit events capture login, stock, user, transfer, sale, payment, approval, repair and integration events when they occur. The activity report filters and summarizes the recorded history.",
                "scenario": "An owner filters activity for Renny and sees login events, stock changes and access-related actions with time and organizational context.",
            },
            {
                "name": "Operational, retail, exception, IMEI and agent reports",
                "status": "complete",
                "what": "Reports cover operational totals, sales and retail signals, aging and exceptions, device history and agent-network performance.",
                "how": "Each report scopes data to the active organization and permitted branches, so managers see relevant data while owners maintain broader visibility.",
                "scenario": "Before a weekly meeting, the owner reviews aging stock, outstanding credit, pending receipts, agent stock and the IMEI history of an escalated device.",
            },
            {
                "name": "eTIMS, advanced BI, forecasting, loyalty and mobile apps",
                "status": "planned",
                "what": "These are important growth capabilities, but they are not production-complete features in the current release.",
                "how": "The platform has a foundation for integration events and background work, but live eTIMS invoicing, forecasting, CRM loyalty, native mobile apps and AI recommendations need dedicated delivery and validation.",
                "scenario": "A future owner dashboard will recommend purchase quantities using demand trends; today the team uses operational and retail reports to make that decision.",
            },
        ],
    },
]


def product_features(request):
    return render(request, "marketing/features.html", {"feature_groups": PRODUCT_FEATURE_GROUPS})


@login_required
def help_center(request):
    sections = []
    for topic in HELP_TOPICS:
        section = next((item for item in sections if item["name"] == topic["section"]), None)
        if section is None:
            section = {"name": topic["section"], "topics": []}
            sections.append(section)
        section["topics"].append(topic)
    return render(request, "help/center.html", {"help_sections": sections})


def _business_flow_organization(request):
    organization = getattr(request, "organization", None)
    if request.user.is_authenticated and organization:
        return organization
    return (
        Organization.objects.filter(slug="nairobi-mobile-hub").first()
        or Organization.objects.filter(status="active").order_by("created_at").first()
    )


def _demo_user_label(username, fallback):
    user = User.objects.filter(username=username).first()
    if not user:
        return fallback
    full_name = user.get_full_name().strip()
    return full_name or user.username.title()


def _flow_metric(value, singular, plural=None):
    label = singular if value == 1 else (plural or f"{singular}s")
    return f"{value} {label}"


def _business_flow_counts(organization):
    if not organization:
        return {
            "branches": 0,
            "locations": 0,
            "warehouse_locations": 0,
            "pos_locations": 0,
            "agent_locations": 0,
            "repair_locations": 0,
            "memberships": 0,
            "roles": 0,
            "agents": 0,
            "dsas": 0,
            "products": 0,
            "serialized_units": 0,
            "available_units": 0,
            "sold_units": 0,
            "stock_movements": 0,
            "purchases": 0,
            "purchase_discrepancies": 0,
            "supplier_returns": 0,
            "transfers": 0,
            "transfer_discrepancies": 0,
            "sales": 0,
            "credit_sales": 0,
            "payments": 0,
            "receivables": 0,
            "returns": 0,
            "refunds": 0,
            "commissions": 0,
            "commission_payouts": 0,
            "repairs": 0,
            "expenses": 0,
            "audit_events": 0,
            "login_events": 0,
        }
    return {
        "branches": Branch.objects.filter(organization=organization, is_active=True).count(),
        "locations": Location.objects.filter(organization=organization, is_active=True).count(),
        "warehouse_locations": Location.objects.filter(organization=organization, location_type=LocationType.WAREHOUSE).count(),
        "pos_locations": Location.objects.filter(organization=organization, location_type=LocationType.POS).count(),
        "agent_locations": Location.objects.filter(organization=organization, location_type=LocationType.AGENT).count(),
        "repair_locations": Location.objects.filter(organization=organization, location_type=LocationType.REPAIR).count(),
        "memberships": Membership.objects.filter(organization=organization, status="active").count(),
        "roles": Role.objects.filter(organization=organization, is_active=True).count(),
        "agents": AgentProfile.objects.filter(organization=organization, profile_type="agent").count(),
        "dsas": AgentProfile.objects.filter(organization=organization, profile_type="dsa").count(),
        "products": Product.objects.filter(organization=organization, is_active=True).count(),
        "serialized_units": StockUnit.objects.filter(organization=organization).count(),
        "available_units": StockUnit.objects.filter(organization=organization, status=SerialStatus.AVAILABLE).count(),
        "sold_units": StockUnit.objects.filter(organization=organization, status=SerialStatus.SOLD).count(),
        "stock_movements": StockMovement.objects.filter(organization=organization).count(),
        "purchases": PurchaseOrder.objects.filter(organization=organization).count(),
        "purchase_discrepancies": PurchaseDiscrepancy.objects.filter(organization=organization).count(),
        "supplier_returns": SupplierReturn.objects.filter(organization=organization).count(),
        "transfers": StockTransfer.objects.filter(organization=organization).count(),
        "transfer_discrepancies": TransferDiscrepancy.objects.filter(organization=organization).count(),
        "sales": Sale.objects.filter(organization=organization).exclude(status=SaleStatus.DRAFT).count(),
        "credit_sales": Sale.objects.filter(organization=organization, sale_channel="credit").count(),
        "payments": Payment.objects.filter(organization=organization).count(),
        "receivables": Receivable.objects.filter(organization=organization).count(),
        "returns": SaleReturn.objects.filter(organization=organization).count(),
        "refunds": Refund.objects.filter(organization=organization).count(),
        "commissions": CommissionAccrual.objects.filter(organization=organization).count(),
        "commission_payouts": CommissionPayout.objects.filter(organization=organization).count(),
        "repairs": RepairTicket.objects.filter(organization=organization).count(),
        "expenses": Expense.objects.filter(organization=organization).count(),
        "audit_events": AuditEvent.objects.filter(organization=organization).count(),
        "login_events": AuditEvent.objects.filter(organization=organization, action="auth.login").count(),
    }


def _demo_url(url):
    return f"{reverse('login')}?demo=brian&next={quote(url, safe='/?=&')}"


def _node(title, summary, metric, url_name, *, args=None, status="Implemented", query=""):
    url = reverse(url_name, args=args or [])
    if query:
        url = f"{url}?{query}"
    return {
        "title": title,
        "summary": summary,
        "metric": metric,
        "url": url,
        "demo_url": _demo_url(url),
        "status": status,
    }


def _business_flow_sections(counts):
    return [
        {
            "title": "Stock Enters The Business",
            "summary": "Purchasing creates the supplier record, receiving confirms what arrived, and batch intake turns delivered IMEIs into traceable stock.",
            "nodes": [
                _node("Supplier Purchase", "Create the supplier order and attach invoice evidence.", _flow_metric(counts["purchases"], "purchase"), "purchase-create"),
                _node("Receiving Check", "Record received items and raise supplier discrepancies when delivery is short or damaged.", _flow_metric(counts["purchase_discrepancies"], "discrepancy", "discrepancies"), "module-overview", args=["purchase-discrepancies"]),
                _node("Batch IMEI Intake", "Paste, upload CSV, or upload Excel serials and IMEIs for fast stock registration.", _flow_metric(counts["serialized_units"], "serialized device"), "batch-serial-intake"),
                _node("Supplier Return", "Send damaged or incorrect stock back with traceable supplier-return records.", _flow_metric(counts["supplier_returns"], "supplier return"), "supplier-return-create"),
            ],
        },
        {
            "title": "Stock Moves Across Branches And Custodians",
            "summary": "Devices move through warehouse, branch, POS, repair bench and agent custody without retyping identifiers.",
            "nodes": [
                _node("Device Search", "Find a phone by IMEI, serial, product, status, branch or custodian.", _flow_metric(counts["available_units"], "available unit"), "device-search"),
                _node("Branch Transfer", "Dispatch and receive stock between warehouse, branches and POS locations.", _flow_metric(counts["transfers"], "transfer"), "transfer-create"),
                _node("Agent Allocation", "Allocate selected devices to agent custody and retain ownership history.", _flow_metric(counts["agent_locations"], "agent custody location"), "agent-allocation-create"),
                _node("Discrepancy Resolution", "Resolve transfer differences through a controlled exception workflow.", _flow_metric(counts["transfer_discrepancies"], "transfer exception"), "exception-report"),
            ],
        },
        {
            "title": "Sales, Payments And Credit Are Controlled",
            "summary": "Cashiers sell serialized phones and accessories, record split payments, handle credit and keep sessions reconcilable.",
            "nodes": [
                _node("POS Cart", "Add products, reserve IMEIs, complete sales and print receipts.", _flow_metric(counts["sales"], "sale"), "pos-cart"),
                _node("Payments", "Record cash, M-Pesa, card, bank, credit and split-payment workflows.", _flow_metric(counts["payments"], "payment"), "module-overview", args=["payments"], status="Workflow level"),
                _node("Credit Follow-Up", "Track receivables, outstanding balances, limits and installment schedules.", _flow_metric(counts["receivables"], "receivable"), "operational-report"),
                _node("Returns And Refunds", "Request returns, approve outcomes and record refunds without deleting history.", _flow_metric(counts["returns"], "return"), "module-overview", args=["returns"]),
            ],
        },
        {
            "title": "After-Sales, People And Oversight Stay Visible",
            "summary": "Repairs, commissions, expenses, approvals and activity reports keep operational accountability visible to management.",
            "nodes": [
                _node("Repair Desk", "Track warranty and repair tickets with parts usage.", _flow_metric(counts["repairs"], "repair ticket"), "repair-create"),
                _node("Commission Payouts", "Accrue, approve and pay commission from qualifying sales.", _flow_metric(counts["commissions"], "commission accrual"), "commission-payout-create"),
                _node("Activity Report", "See login, user, stock, sale, approval and integration activity.", _flow_metric(counts["audit_events"], "audit event"), "activity-report"),
                _node("Owner Reports", "Review operations, retail analytics, agent stock, IMEI history and exceptions.", _flow_metric(counts["stock_movements"], "stock movement"), "operational-report"),
            ],
        },
    ]


def _business_role_flows(counts):
    roles = [
        {
            "role": "Owner",
            "slug": "owner-journey",
            "person": _demo_user_label("brian", "Brian"),
            "focus": "Owns the full business view: branches, money, stock risk, approvals, staff activity and growth decisions.",
            "proof": [
                _flow_metric(counts["branches"], "branch"),
                _flow_metric(counts["locations"], "location"),
                _flow_metric(counts["audit_events"], "audit event"),
                _flow_metric(counts["login_events"], "login event"),
            ],
            "steps": [
                ("Open dashboard", reverse("dashboard")),
                ("Review activity report", reverse("activity-report")),
                ("Check operational report", reverse("operational-report")),
                ("Inspect agent network", reverse("agent-network-report")),
                ("Trace IMEI history", reverse("imei-history")),
            ],
        },
        {
            "role": "Branch Manager",
            "slug": "manager-journey",
            "person": _demo_user_label("manager", "Mary"),
            "focus": "Controls branch execution: approvals, transfers, customer credit, cash discipline and exceptions.",
            "proof": [
                _flow_metric(counts["transfers"], "transfer"),
                _flow_metric(counts["purchase_discrepancies"], "purchase discrepancy", "purchase discrepancies"),
                _flow_metric(counts["receivables"], "receivable"),
            ],
            "steps": [
                ("Review approvals", reverse("approval-inbox")),
                ("Create stock transfer", reverse("transfer-create")),
                ("Resolve exceptions", reverse("exception-report")),
                ("Review branch report", reverse("operational-report")),
            ],
        },
        {
            "role": "Inventory Officer",
            "slug": "inventory-journey",
            "person": _demo_user_label("inventory", "Grace"),
            "focus": "Maintains inventory accuracy by receiving stock, uploading IMEIs, moving stock and correcting exceptions.",
            "proof": [
                _flow_metric(counts["serialized_units"], "serialized unit"),
                _flow_metric(counts["available_units"], "available unit"),
                _flow_metric(counts["stock_movements"], "ledger movement"),
            ],
            "steps": [
                ("Batch IMEI intake", reverse("batch-serial-intake")),
                ("Search devices", reverse("device-search")),
                ("Create adjustment", reverse("stock-adjustment-create")),
                ("Trace IMEI report", reverse("imei-history")),
            ],
        },
        {
            "role": "Cashier",
            "slug": "cashier-journey",
            "person": _demo_user_label("cashier", "Kevin"),
            "focus": "Runs the POS: opens a session, sells devices and accessories, records payments and reconciles cash.",
            "proof": [
                _flow_metric(counts["sales"], "sale"),
                _flow_metric(counts["payments"], "payment"),
                _flow_metric(counts["sold_units"], "sold device"),
            ],
            "steps": [
                ("Open POS session", reverse("session-open")),
                ("Use POS cart", reverse("pos-cart")),
                ("Checkout", reverse("pos-checkout")),
                ("Review sales", reverse("module-overview", args=["sales"])),
            ],
        },
        {
            "role": "Field Agent",
            "slug": "agent-journey",
            "person": _demo_user_label("agent", "Renny"),
            "focus": "Receives allocated devices, works with DSAs, completes field sales and remains accountable for custody.",
            "proof": [
                _flow_metric(counts["agents"], "agent"),
                _flow_metric(counts["agent_locations"], "agent custody location"),
                _flow_metric(counts["credit_sales"], "credit sale"),
            ],
            "steps": [
                ("View agent report", reverse("agent-network-report")),
                ("Allocate stock", reverse("agent-allocation-create")),
                ("Recall stock", reverse("agent-recall-create")),
                ("Register DSA", reverse("agent-dsa-create")),
            ],
        },
        {
            "role": "Direct Sales Agent",
            "slug": "dsa-journey",
            "person": _demo_user_label("dsa", "Faith"),
            "focus": "Supports field sales under an agent while the business keeps supervisor, customer and stock accountability.",
            "proof": [
                _flow_metric(counts["dsas"], "DSA"),
                _flow_metric(counts["credit_sales"], "credit workflow"),
                _flow_metric(counts["commissions"], "commission record"),
            ],
            "steps": [
                ("Review agent network", reverse("agent-network-report")),
                ("Create customer", reverse("contact-create")),
                ("Support POS sale", reverse("pos-cart")),
                ("Check commissions", reverse("module-overview", args=["commissions"])),
            ],
        },
        {
            "role": "Technician",
            "slug": "technician-journey",
            "person": _demo_user_label("technician", "Daniel"),
            "focus": "Handles warranty and repair tickets, uses parts from stock and updates repair outcomes.",
            "proof": [
                _flow_metric(counts["repairs"], "repair ticket"),
                _flow_metric(counts["repair_locations"], "repair location"),
                _flow_metric(counts["returns"], "after-sales return"),
            ],
            "steps": [
                ("Create repair ticket", reverse("repair-create")),
                ("Open repair register", reverse("module-overview", args=["repairs"])),
                ("Search IMEI", reverse("imei-history")),
                ("Review exceptions", reverse("exception-report")),
            ],
        },
    ]
    for role in roles:
        role["steps"] = [(label, _demo_url(url)) for label, url in role["steps"]]
    return roles


def _journey_step(label, detail, url_name, *, args=None, status="Complete"):
    url = reverse(url_name, args=args or [])
    return {"label": label, "detail": detail, "url": _demo_url(url), "status": status}


def _business_journeys(counts):
    return [
        {
            "slug": "full-business-flow",
            "title": "Full Business Flow",
            "audience": "Owners, directors and operations managers",
            "status": "Complete foundation",
            "purpose": "Shows how MobiPOS controls the journey from supplier purchase to warehouse, branch, agent, customer sale, repair, audit trail and management reporting.",
            "flow": ["Supplier", "Purchase", "Receiving", "IMEI Intake", "Warehouse", "Transfer", "POS/Agent", "Customer", "Reports"],
            "steps": [
                _journey_step("Create supplier purchase", "Record the supplier, destination, costs, attachment and purchase status before stock enters the business.", "purchase-create"),
                _journey_step("Receive and validate stock", "Confirm what arrived, flag supplier discrepancies and keep payables linked to the purchase.", "module-overview", args=["purchase-discrepancies"]),
                _journey_step("Register IMEIs in batch", "Use pasted scanner lines, CSV or Excel to create centralized serialized inventory records.", "batch-serial-intake"),
                _journey_step("Move stock with custody", "Transfer selected items to branches, POS locations, repair benches or agent custody without retyping IMEIs.", "transfer-create"),
                _journey_step("Sell, repair or report", "Complete POS sales, repair tickets, commissions and owner reports while stock and activity history remain traceable.", "pos-cart"),
            ],
            "notes": [
                f"{counts['serialized_units']} serialized devices and {counts['stock_movements']} stock movements in the current demo dataset.",
                "All critical inventory changes should produce audit and stock movement evidence.",
            ],
        },
        {
            "slug": "purchasing-intake",
            "title": "Purchasing And Stock Intake Journey",
            "audience": "Purchasing officers and warehouse teams",
            "status": "Complete with OCR boundary",
            "purpose": "Controls how new products and serialized devices enter the business and become available stock.",
            "flow": ["Supplier Order", "Invoice Upload", "Receive", "Discrepancy Check", "Batch IMEI Upload", "Available Stock"],
            "steps": [
                _journey_step("Create purchase", "Choose supplier, destination, purchase date, status and supporting document.", "purchase-create"),
                _journey_step("Extract document text", "CSV, text and Excel documents can be extracted for review. Scanned PDFs/images are marked for manual review until OCR is connected.", "module-overview", args=["purchases"], status="Partial"),
                _journey_step("Receive stock", "Record received quantities and create discrepancy records when delivery differs from the order.", "module-overview", args=["purchases"]),
                _journey_step("Upload IMEI batch", "Paste lines or upload CSV/Excel to validate duplicates and register devices.", "batch-serial-intake"),
            ],
            "notes": [
                "Real OCR for scanned supplier invoices is still pending.",
                "Excel/CSV intake is implemented and covered by tests.",
            ],
        },
        {
            "slug": "inventory-transfer",
            "title": "Inventory, Transfer And Custody Journey",
            "audience": "Inventory officers, branch managers and agents",
            "status": "Complete foundation",
            "purpose": "Keeps every phone traceable as it moves between warehouse, branch, POS, repair desk and field custody.",
            "flow": ["Search Device", "Select IMEIs", "Dispatch", "Receive", "Resolve Difference", "Update Custody"],
            "steps": [
                _journey_step("Search inventory", "Find products or devices by IMEI, serial, SKU, barcode, model, status, branch or custodian.", "device-search"),
                _journey_step("Create transfer", "Select source and destination, choose existing stock and dispatch it for receiving.", "transfer-create"),
                _journey_step("Allocate to agent", "Move selected devices into agent custody with ownership history.", "agent-allocation-create"),
                _journey_step("Resolve discrepancies", "Review missing, excess or damaged transfer items through exception reports.", "exception-report"),
            ],
            "notes": [
                "Manual IMEI re-entry should not be required during transfer selection.",
                "Discrepancies require review instead of silent stock edits.",
            ],
        },
        {
            "slug": "pos-payments",
            "title": "POS, Payments And Credit Journey",
            "audience": "Cashiers, branch managers and finance teams",
            "status": "Workflow complete; live providers pending",
            "purpose": "Supports day-to-day selling, split payments, customer credit, receipts and session reconciliation.",
            "flow": ["Open Session", "Scan Item", "Select IMEI", "Choose Customer", "Record Payment", "Receipt", "Reconcile"],
            "steps": [
                _journey_step("Open register", "Cashier starts a session at an assigned POS location.", "session-open"),
                _journey_step("Build cart", "Scan or type product, SKU, barcode, serial or IMEI. Serialized products require an available IMEI.", "pos-cart"),
                _journey_step("Record payment", "Capture cash, M-Pesa reference, card, bank, credit or split payment.", "pos-checkout", status="Workflow level"),
                _journey_step("Review sales", "Managers and owners review sales, payment status, returns and outstanding balances.", "module-overview", args=["sales"]),
            ],
            "notes": [
                "M-Pesa is recordable today, but live Daraja confirmation is not connected.",
                "Offline invoices are local drafts requiring online validation; production policy still needs a final decision.",
            ],
        },
        {
            "slug": "agent-dsa",
            "title": "Agent And DSA Journey",
            "audience": "Owners, managers, agents and direct sales agents",
            "status": "Complete foundation",
            "purpose": "Models field sales hierarchy, onboarding documents, stock allocation, recall and accountability.",
            "flow": ["Register Agent", "Upload ID", "Approve", "Register DSA", "Allocate Stock", "Sell/Return", "Report"],
            "steps": [
                _journey_step("Create agent profile", "Capture legal identity, phone, branch, supervising manager and verification status.", "tenant-user-create"),
                _journey_step("Upload ID documents", "Attach national ID or onboarding documents for compliance and review from an agent profile.", "agent-network-report"),
                _journey_step("Register DSA", "Link DSA profiles under supervising agents where business policy permits.", "agent-dsa-create"),
                _journey_step("Allocate and recall stock", "Move selected devices into and out of agent custody with audit records.", "agent-allocation-create"),
                _journey_step("Review network report", "Owner checks agent/DSA stock, activity and accountability.", "agent-network-report"),
            ],
            "notes": [
                "Direct camera capture is browser upload based, not a separate native scanning app.",
                "Advanced productivity recommendations remain future work.",
            ],
        },
        {
            "slug": "after-sales-audit",
            "title": "After-Sales, Audit And Reporting Journey",
            "audience": "Owners, technicians, finance users and auditors",
            "status": "Complete foundation",
            "purpose": "Keeps repairs, returns, refunds, expenses, commissions and staff activity visible after the original sale.",
            "flow": ["Return/Repair", "Parts Usage", "Refund/Commission", "Expense", "Audit Event", "Owner Report"],
            "steps": [
                _journey_step("Create repair ticket", "Track customer, device, warranty and repair status.", "repair-create"),
                _journey_step("Record returns/refunds", "Handle after-sales corrections without deleting completed sale history.", "module-overview", args=["returns"]),
                _journey_step("Approve/pay commissions", "Review commission accruals and payout status.", "commission-payout-create"),
                _journey_step("Review activity", "Owner checks logins, actions, privileged changes and suspicious activity.", "activity-report"),
                _journey_step("Open operational reports", "Use reports for stock, sales, aging, exceptions, IMEI history and agent visibility.", "operational-report"),
            ],
            "notes": [
                "Completed operational records should be corrected through reversals and approvals, not deletion.",
                "Sentry and production monitoring still require deployment configuration.",
            ],
        },
    ]


def business_flow(request):
    organization = _business_flow_organization(request)
    counts = _business_flow_counts(organization)
    return render(
        request,
        "marketing/business_flow.html",
        {
            "flow_organization": organization,
            "flow_counts": counts,
            "flow_sections": _business_flow_sections(counts),
            "role_flows": _business_role_flows(counts),
            "journeys": _business_journeys(counts),
        },
    )


def _scope_to_user_branches(queryset, model, request):
    branches = accessible_branches_for(request.user, request.organization)
    if model is StockTransfer:
        return queryset.filter(Q(source__branch__in=branches) | Q(destination__branch__in=branches)).distinct()
    lookup = BRANCH_LOOKUPS.get(model)
    return queryset.filter(**{f"{lookup}__in": branches}) if lookup else queryset


def _can_view_module(request, module):
    if module in SENSITIVE_MODULES and request.organization:
        membership = request.membership
        if request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner):
            return True
        return False
    permission = MODULE_PERMISSIONS.get(module)
    if permission and request.organization:
        from apps.organizations.permissions import user_has_organization_permission
        return user_has_organization_permission(request.user, request.organization, permission)
    return True


def _owner_activity_queryset(request):
    if request.user.is_superuser or request.user.is_platform_admin:
        return AuditEvent.objects.all()

    organization = getattr(request, "organization", None)
    membership = getattr(request, "membership", None)
    if not organization or not membership:
        raise PermissionDenied("Organization owner access is required.")
    if not membership.is_owner and not user_has_organization_permission(
        request.user, organization, "organizations.view_activity_report"
    ):
        raise PermissionDenied("Organization owner access is required.")

    member_user_ids = Membership.objects.filter(
        organization=organization,
        status="active",
    ).values("user_id")
    return AuditEvent.objects.filter(
        Q(organization=organization) | Q(organization__isnull=True, actor_id__in=member_user_ids)
    )


def _owner_setup_steps(organization):
    purchase_url = reverse("purchase-create")
    steps = [
        {
            "key": "branch",
            "title": "Confirm your branch",
            "description": "A branch represents a shop, outlet, office, or business unit.",
            "complete": Branch.objects.filter(organization=organization, is_active=True).exists(),
            "action_label": "Add branch",
            "action_url": reverse("branch-create"),
        },
        {
            "key": "location",
            "title": "Create selling and stock locations",
            "description": "Locations show where stock sits, such as a POS counter, warehouse, repair desk, or agent custody.",
            "complete": Location.objects.filter(organization=organization, is_active=True).exists(),
            "action_label": "Add location",
            "action_url": reverse("location-create"),
        },
        {
            "key": "users",
            "title": "Add your team",
            "description": "Create staff accounts and assign the branches and roles they should use.",
            "complete": Membership.objects.filter(organization=organization, status="active").count() > 1,
            "action_label": "Add user",
            "action_url": reverse("tenant-user-create"),
        },
        {
            "key": "supplier",
            "title": "Add a supplier",
            "description": "Supplier records make purchasing, payables, returns, and stock history traceable.",
            "complete": Contact.objects.filter(
                organization=organization,
                contact_type__in=("supplier", "both"),
                is_active=True,
            ).exclude(name__iexact="Opening Stock Supplier").exists(),
            "action_label": "Add supplier",
            "action_url": f"{reverse('contact-create')}?{urlencode({'type': 'supplier'})}",
        },
        {
            "key": "product",
            "title": "Add products",
            "description": "Create the phones, accessories, parts, and services your team will purchase and sell.",
            "complete": Product.objects.filter(organization=organization, is_active=True, is_purchasable=True).exists(),
            "action_label": "Add product",
            "action_url": reverse("product-create"),
        },
        {
            "key": "purchase",
            "title": "Create the first purchase",
            "description": "Record incoming supplier stock before receiving quantities or IMEIs.",
            "complete": PurchaseOrder.objects.filter(organization=organization).exists(),
            "action_label": "New purchase",
            "action_url": purchase_url,
        },
        {
            "key": "stock",
            "title": "Receive stock or IMEIs",
            "description": "Register available stock so the POS, transfers, and reports have reliable inventory.",
            "complete": StockUnit.objects.filter(organization=organization).exists()
            or StockBalance.objects.filter(organization=organization, quantity__gt=0).exists(),
            "action_label": "Batch IMEI intake",
            "action_url": reverse("batch-serial-intake"),
        },
    ]
    for index, step in enumerate(steps, start=1):
        step["number"] = index
    return steps


def dashboard(request):
    if not request.user.is_authenticated:
        return render(request, "marketing/landing.html", {
            "feature_cards": LANDING_FEATURE_CARDS,
            "roadmap_items": LANDING_ROADMAP_ITEMS,
        })
    organization = request.organization
    latest_activity = []
    if organization:
        branches = accessible_branches_for(request.user, organization)
        today = timezone.localdate()
        available_stock = StockUnit.objects.filter(
            organization=organization,
            location__branch__in=branches,
            status=SerialStatus.AVAILABLE,
        )
        sales = Sale.objects.filter(organization=organization, location__branch__in=branches)
        metrics = {
            "organizations": 1,
            "branches": branches.count(),
            "active_users": Membership.objects.filter(organization=organization, status="active").count(),
            "pending_approvals": ApprovalRequest.objects.filter(organization=organization, status="pending").count(),
            "sales_total": sales.aggregate(total=Sum("total"))["total"] or 0,
            "todays_sales_total": sales.filter(completed_at__date=today).aggregate(total=Sum("total"))["total"] or 0,
            "todays_sales_count": sales.filter(completed_at__date=today).count(),
            "todays_purchases_total": PurchaseOrder.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                created_at__date=today,
            ).aggregate(total=Sum(F("lines__quantity") * F("lines__unit_cost")))["total"] or 0,
            "pending_purchase_receipts": PurchaseOrder.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status__in=(PurchaseStatus.APPROVED, PurchaseStatus.PART_RECEIVED, PurchaseStatus.DISCREPANCY),
            ).count(),
            "pending_transfer_receipts": StockTransfer.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status=TransferStatus.IN_TRANSIT,
            ).count(),
            "active_carts": sales.filter(status="draft").count(),
            "aged_stock": available_stock.filter(created_at__lte=timezone.now() - timedelta(days=5)).count(),
            "unpaid_commissions": CommissionAccrual.objects.filter(
                organization=organization,
                sale__location__branch__in=branches,
                is_payable=True,
                paid_at__isnull=True,
            ).aggregate(total=Sum("amount"))["total"] or 0,
            "stock_units": available_stock.count(),
            "expenses_total": Expense.objects.filter(organization=organization, branch__in=branches).aggregate(total=Sum("amount"))["total"] or 0,
            "integration_failures": IntegrationEvent.objects.filter(organization=organization, status="failed").count(),
        }
        latest_sales = sales.select_related("customer", "agent").prefetch_related("lines__product", "lines__stock_unit").order_by("-created_at")[:8]
        pending_actions = {
            "purchase_receipts": PurchaseOrder.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status__in=(PurchaseStatus.APPROVED, PurchaseStatus.PART_RECEIVED, PurchaseStatus.DISCREPANCY),
            ).select_related("supplier", "destination")[:5],
            "transfer_receipts": StockTransfer.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status=TransferStatus.IN_TRANSIT,
            ).select_related("source", "destination")[:5],
        }
        membership = getattr(request, "membership", None)
        setup_steps = []
        if membership and membership.is_owner:
            setup_steps = _owner_setup_steps(organization)
        if request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner):
            latest_activity = _owner_activity_queryset(request).select_related("actor", "organization")[:8]
    elif request.user.is_platform_admin or request.user.is_superuser:
        metrics = {
            "organizations": Organization.objects.filter(status="active").count(),
            "branches": Branch.objects.filter(is_active=True).count(),
            "active_users": User.objects.filter(is_active=True).count(),
            "pending_approvals": Organization.objects.filter(status="pending").count(),
            "sales_total": Sale.objects.aggregate(total=Sum("total"))["total"] or 0,
            "stock_units": StockUnit.objects.filter(status="available").count(),
            "expenses_total": Expense.objects.aggregate(total=Sum("amount"))["total"] or 0,
            "integration_failures": IntegrationEvent.objects.filter(status="failed").count(),
            "todays_sales_total": 0,
            "todays_sales_count": 0,
            "todays_purchases_total": 0,
            "pending_purchase_receipts": 0,
            "pending_transfer_receipts": 0,
            "active_carts": 0,
            "aged_stock": 0,
            "unpaid_commissions": 0,
        }
        latest_sales = Sale.objects.select_related("customer", "agent").prefetch_related("lines__product", "lines__stock_unit").order_by("-created_at")[:8]
        pending_actions = {"purchase_receipts": [], "transfer_receipts": []}
        latest_activity = _owner_activity_queryset(request).select_related("actor", "organization")[:8]
        setup_steps = []
    else:
        metrics = dict.fromkeys(
            (
                "organizations", "branches", "active_users", "pending_approvals",
                "sales_total", "todays_sales_total", "todays_sales_count",
                "todays_purchases_total", "pending_purchase_receipts",
                "pending_transfer_receipts", "active_carts", "aged_stock",
                "unpaid_commissions", "stock_units", "expenses_total",
                "integration_failures",
            ),
            0,
        )
        latest_sales = []
        pending_actions = {"purchase_receipts": [], "transfer_receipts": []}
        setup_steps = []
    return render(request, "dashboard.html", {
        "metrics": metrics,
        "latest_sales": latest_sales,
        "pending_actions": pending_actions,
        "latest_activity": latest_activity,
        "owner_setup_steps": setup_steps,
        "owner_setup_complete_count": sum(1 for step in setup_steps if step["complete"]),
        "owner_setup_total": len(setup_steps),
    })


def demo_access(request):
    if request.user.is_authenticated:
        return render(request, "marketing/demo.html", {"already_signed_in": True})
    demo_accounts = [
        {
            "username": "brian",
            "password": "DemoPass123!",
            "role": "Owner demo",
            "organization": "Nairobi Mobile Hub",
            "best_for": "Full business owner view with Kenyan mobile retail demo data.",
        },
    ]
    return render(request, "marketing/demo.html", {"demo_accounts": demo_accounts})


@login_required
def activity_report(request):
    queryset = _owner_activity_queryset(request).select_related("actor", "organization")

    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    actor = request.GET.get("actor", "").strip()
    start = parse_date(request.GET.get("start", ""))
    end = parse_date(request.GET.get("end", ""))

    if query:
        queryset = queryset.filter(
            Q(action__icontains=query)
            | Q(message__icontains=query)
            | Q(target_type__icontains=query)
            | Q(target_id__icontains=query)
            | Q(actor__username__icontains=query)
            | Q(actor__email__icontains=query)
            | Q(actor__first_name__icontains=query)
            | Q(actor__last_name__icontains=query)
        )
    if action:
        queryset = queryset.filter(action=action)
    if actor:
        queryset = queryset.filter(actor_id=actor)
    if start:
        queryset = queryset.filter(created_at__date__gte=start)
    if end:
        queryset = queryset.filter(created_at__date__lte=end)

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="activity-report.csv"'
        writer = csv.writer(response)
        writer.writerow(["Time", "Actor", "Organization", "Action", "Target", "Message", "IP address"])
        for event in queryset.order_by("-created_at"):
            writer.writerow(safe_csv_row([
                event.created_at.isoformat(),
                event.actor.get_username() if event.actor else "System",
                event.organization.name if event.organization else "Platform",
                event.action,
                f"{event.target_type} {event.target_id}".strip(),
                event.message,
                event.ip_address or "",
            ]))
        return response

    scoped = queryset.order_by("-created_at")
    page_obj = Paginator(scoped, 25).get_page(request.GET.get("page"))
    available_actions = (
        _owner_activity_queryset(request)
        .exclude(action="")
        .values_list("action", flat=True)
        .distinct()
        .order_by("action")
    )
    available_actors = User.objects.filter(
        id__in=_owner_activity_queryset(request).exclude(actor__isnull=True).values("actor_id")
    ).order_by("first_name", "last_name", "username")
    actor_summary = (
        queryset.exclude(actor__isnull=True)
        .values("actor_id", "actor__first_name", "actor__last_name", "actor__username", "actor__email")
        .annotate(total=Count("id"), logins=Count("id", filter=Q(action="auth.login")))
        .order_by("-total")[:10]
    )
    action_summary = (
        queryset.values("action")
        .annotate(total=Count("id"))
        .order_by("-total", "action")[:12]
    )

    return render(request, "reports/activity.html", {
        "page_obj": page_obj,
        "events": page_obj.object_list,
        "query": query,
        "selected_action": action,
        "selected_actor": actor,
        "start": start,
        "end": end,
        "available_actions": available_actions,
        "available_actors": available_actors,
        "actor_summary": actor_summary,
        "action_summary": action_summary,
        "total_events": queryset.count(),
    })


MODULES = {
    "products": ("Products", Product, ("name", "sku", "selling_price", "is_serialized")),
    "contacts": ("Customers and suppliers", Contact, ("name", "contact_type", "phone_number", "credit_limit", "is_active")),
    "inventory": ("Serialized inventory", StockUnit, ("serial_number", "product", "status", "location")),
    "agent-stock": ("Agent stock custody", StockUnit, ("serial_number", "secondary_serial", "product", "status", "location")),
    "stock": ("Stock balances", StockBalance, ("product", "location", "quantity")),
    "movements": ("Stock movements", StockMovement, ("movement_type", "product", "location", "quantity", "reason")),
    "adjustments": ("Stock adjustments", StockAdjustment, ("number", "product", "location", "quantity", "status")),
    "purchases": ("Purchases", PurchaseOrder, ("number", "supplier", "destination", "status")),
    "supplier-returns": ("Supplier returns", SupplierReturn, ("number", "line", "quantity", "status", "reason")),
    "purchase-discrepancies": ("Purchase discrepancies", PurchaseDiscrepancy, ("number", "line", "damaged_quantity", "missing_quantity", "status")),
    "transfers": ("Stock transfers", StockTransfer, ("number", "source", "destination", "status")),
    "sales": ("Sales", Sale, ("number", "customer", "status", "total", "paid_total")),
    "payments": ("Payments", Payment, ("number", "method", "status", "amount", "provider_reference")),
    "expenses": ("Expenses", Expense, ("number", "category", "status", "amount", "incurred_on")),
    "commissions": ("Commissions", CommissionAccrual, ("agent", "sale", "amount", "is_payable")),
    "commission-payouts": ("Commission payouts", CommissionPayout, ("number", "agent", "period_start", "period_end", "amount", "status")),
    "repairs": ("Repairs", RepairTicket, ("number", "customer", "status", "quoted_amount")),
    "integrations": ("Integration events", IntegrationEvent, ("provider", "event_type", "status", "attempts")),
    "fiscal-devices": ("eTIMS devices", FiscalDevice, ("taxpayer_pin", "branch_office_id", "environment", "status")),
    "fiscal-documents": ("eTIMS tax receipts", FiscalDocument, ("internal_number", "document_type", "status", "etims_invoice_number")),
    "receivables": ("Receivables", Receivable, ("customer", "sale", "outstanding_amount", "due_on")),
    "payables": ("Payables", Payable, ("supplier", "purchase_order", "outstanding_amount", "due_on")),
    "approvals": ("Approval inbox", ApprovalRequest, ("request_type", "target_type", "status", "requested_by")),
    "returns": ("Returns", SaleReturn, ("number", "sale", "status", "refund_amount")),
    "refunds": ("Refunds", Refund, ("number", "payment", "status", "amount")),
    "subscriptions": ("Subscription invoices", SubscriptionInvoice, ("number", "subscription", "status", "amount", "due_on")),
    "sessions": ("Cashier sessions", POSSession, ("number", "location", "cashier", "status", "expected_cash", "variance")),
    "users": ("Organization users", Membership, ("user", "status", "is_owner")),
    "agents": ("Agent profiles", AgentProfile, ("legal_name", "profile_type", "status", "branch", "supervisor")),
    "branches": ("Branches", Branch, ("name", "code", "company", "is_active")),
    "locations": ("Locations", Location, ("name", "code", "branch", "location_type", "is_active")),
    "roles": ("Roles", Role, ("name", "code", "description", "is_active")),
    "aged-stock": ("Aged stock", StockUnit, ("serial_number", "product", "location", "status", "created_at")),
}
SENSITIVE_MODULES = {
    "approvals", "branches", "commission-payouts", "integrations", "fiscal-devices", "fiscal-documents", "locations",
    "roles", "subscriptions", "users", "agents",
}
MODULE_PERMISSIONS = {
    "products": "catalog.view_product",
    "contacts": "contacts.view_contact",
    "inventory": "inventory.view_stockunit",
    "agent-stock": "inventory.view_stockunit",
    "stock": "inventory.view_stockbalance",
    "movements": "inventory.view_stockmovement",
    "adjustments": "inventory.view_stockadjustment",
    "purchases": "purchasing.view_purchaseorder",
    "supplier-returns": "purchasing.view_supplierreturn",
    "purchase-discrepancies": "purchasing.view_purchasediscrepancy",
    "transfers": "transfers.view_stocktransfer",
    "sales": "sales.view_sale",
    "payments": "payments.view_payment",
    "expenses": "expenses.view_expense",
    "commissions": "commissions.view_commissionaccrual",
    "repairs": "repairs.view_repairticket",
    "receivables": "sales.view_sale",
    "payables": "purchasing.view_purchaseorder",
    "returns": "sales.view_salereturn",
    "refunds": "payments.view_refund",
    "sessions": "pos.view_possession",
    "aged-stock": "inventory.view_stockunit",
}
MODULE_DETAIL_URLS = {
    "products": "product-detail",
    "contacts": "contact-detail",
    "adjustments": "stock-adjustment-detail",
    "purchases": "purchase-detail",
    "supplier-returns": "supplier-return-detail",
    "transfers": "transfer-detail",
    "sales": "sale-detail",
    "commission-payouts": "commission-payout-detail",
    "repairs": "repair-detail",
    "sessions": "session-detail",
    "agents": "agent-profile-detail",
}

MODULE_CREATE_ACTIONS = {
    "branches": {"label": "Add branch", "url_name": "branch-create"},
    "locations": {"label": "Add location", "url_name": "location-create"},
    "contacts": {"label": "Add customer or supplier", "url_name": "contact-create"},
    "roles": {"label": "Add role", "url_name": "role-create"},
    "purchases": {"label": "New purchase", "url_name": "purchase-create"},
    "supplier-returns": {"label": "New supplier return", "url_name": "supplier-return-create"},
    "transfers": {"label": "New transfer", "url_name": "transfer-create"},
    "expenses": {"label": "New expense", "url_name": "expense-create"},
    "repairs": {"label": "New repair", "url_name": "repair-create"},
    "commission-payouts": {"label": "New payout", "url_name": "commission-payout-create"},
}


def _product_register(request, title):
    organization = request.organization
    can_view_costs = can_view_product_costs(request.user, organization)
    branches = accessible_branches_for(request.user, organization) if organization else Branch.objects.all()
    queryset = Product.objects.none()
    if organization:
        queryset = Product.objects.filter(organization=organization)
    elif request.user.is_platform_admin or request.user.is_superuser:
        queryset = Product.objects.all()

    balance_subquery = StockBalance.objects.filter(
        product=OuterRef("pk"),
        location__branch__in=branches,
    )
    available_unit_subquery = StockUnit.objects.filter(
        product=OuterRef("pk"),
        location__branch__in=branches,
        status=SerialStatus.AVAILABLE,
    )
    sold_unit_subquery = StockUnit.objects.filter(
        product=OuterRef("pk"),
        status=SerialStatus.SOLD,
    ).filter(
        Q(location__branch__in=branches)
        | Q(saleline__sale__location__branch__in=branches)
        | Q(movements__location__branch__in=branches)
    )
    if organization:
        balance_subquery = balance_subquery.filter(organization=organization)
        available_unit_subquery = available_unit_subquery.filter(organization=organization)
        sold_unit_subquery = sold_unit_subquery.filter(organization=organization)

    queryset = queryset.select_related("category", "brand").annotate(
        scoped_stock_total=Coalesce(
            Subquery(
                balance_subquery.values("product").annotate(total=Sum("quantity")).values("total")[:1],
                output_field=DecimalField(max_digits=14, decimal_places=3),
            ),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        available_serial_count=Coalesce(
            Subquery(
                available_unit_subquery.values("product").annotate(total=Count("id")).values("total")[:1],
                output_field=IntegerField(),
            ),
            Value(0),
            output_field=IntegerField(),
        ),
        sold_serial_count=Coalesce(
            Subquery(
                sold_unit_subquery.values("product").annotate(total=Count("id", distinct=True)).values("total")[:1],
                output_field=IntegerField(),
            ),
            Value(0),
            output_field=IntegerField(),
        ),
    )

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    tracking = request.GET.get("tracking", "").strip()
    stock_status = request.GET.get("stock_status", "").strip()
    category_id = request.GET.get("category", "").strip()
    brand_id = request.GET.get("brand", "").strip()

    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(sku__icontains=query)
            | Q(barcode__icontains=query)
            | Q(stock_units__serial_number__icontains=query)
            | Q(stock_units__secondary_serial__icontains=query)
        ).distinct()
    if status in {"active", "inactive"}:
        queryset = queryset.filter(is_active=status == "active")
    if tracking == "serialized":
        queryset = queryset.filter(is_serialized=True)
    elif tracking == "quantity":
        queryset = queryset.filter(is_serialized=False)
    if category_id:
        queryset = queryset.filter(category_id=category_id)
    if brand_id:
        queryset = queryset.filter(brand_id=brand_id)
    if stock_status == "in_stock":
        queryset = queryset.filter(scoped_stock_total__gt=0)
    elif stock_status == "out_of_stock":
        queryset = queryset.filter(scoped_stock_total__lte=0)
    elif stock_status == "low_stock":
        queryset = queryset.filter(is_stocked=True, scoped_stock_total__gt=0, scoped_stock_total__lte=F("reorder_level"))

    queryset = queryset.order_by("name", "sku")

    export_headers = [
        "Product", "SKU", "Barcode", "Category", "Brand", "Tracking",
        "Current stock", "Available IMEIs", "Sold IMEIs", "Selling price", "Active",
    ]
    if can_view_costs:
        export_headers.extend(["Cost price", "Margin amount", "Margin percent"])

    def product_export_rows():
        for product in queryset:
            row = [
                product.name,
                product.sku,
                product.barcode,
                product.category.name if product.category else "",
                product.brand.name if product.brand else "",
                "Serialized / IMEI" if product.is_serialized else "Quantity",
                product.scoped_stock_total,
                product.available_serial_count,
                product.sold_serial_count,
                product.selling_price,
                "Yes" if product.is_active else "No",
            ]
            if can_view_costs:
                margin_amount = product.selling_price - product.cost_price
                margin_percent = (margin_amount / product.selling_price * 100) if product.selling_price else 0
                row.extend([product.cost_price, margin_amount, f"{margin_percent:.2f}"])
            yield row

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="products.csv"'
        writer = csv.writer(response)
        writer.writerow(export_headers)
        for row in product_export_rows():
            writer.writerow(safe_csv_row(row))
        return response
    if request.GET.get("format") == "pdf":
        response = HttpResponse(
            build_simple_pdf(title="MobiPOS Products", headers=export_headers, rows=list(product_export_rows())),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = 'attachment; filename="products.pdf"'
        return response

    page_obj = Paginator(queryset, 25).get_page(request.GET.get("page"))
    page_products = list(page_obj.object_list)
    product_ids = [product.id for product in page_products]
    location_summaries = {}
    if product_ids:
        for balance in StockBalance.objects.filter(
            organization=organization,
            product_id__in=product_ids,
            location__branch__in=branches,
        ).select_related("location", "location__branch").order_by("location__branch__name", "location__name"):
            location_summaries.setdefault(balance.product_id, []).append(balance)

    rows = []
    for product in page_products:
        margin_amount = product.selling_price - product.cost_price
        margin_percent = (margin_amount / product.selling_price * 100) if product.selling_price else 0
        rows.append({
            "product": product,
            "detail_url": reverse("product-detail", args=[product.pk]),
            "stock_total": product.scoped_stock_total,
            "available_serial_count": product.available_serial_count,
            "sold_serial_count": product.sold_serial_count,
            "balances": location_summaries.get(product.id, [])[:4],
            "balance_count": len(location_summaries.get(product.id, [])),
            "is_low_stock": product.is_stocked and product.scoped_stock_total > 0 and product.scoped_stock_total <= product.reorder_level,
            "is_out_of_stock": product.is_stocked and product.scoped_stock_total <= 0,
            "margin_amount": margin_amount,
            "margin_percent": margin_percent,
            "values": [product.name, product.sku, product.selling_price, product.is_serialized],
        })

    base_queryset = Product.objects.filter(organization=organization) if organization else Product.objects.all()
    category_options = Category.objects.filter(organization=organization, is_active=True).order_by("name") if organization else Category.objects.none()
    brand_options = Brand.objects.filter(organization=organization, is_active=True).order_by("name") if organization else Brand.objects.none()
    balance_filters = {"product__in": queryset, "location__branch__in": branches}
    unit_filters = {"product__in": queryset, "location__branch__in": branches}
    if organization:
        balance_filters["organization"] = organization
        unit_filters["organization"] = organization
    metrics = {
        "products": queryset.count(),
        "available_stock": StockBalance.objects.filter(**balance_filters).aggregate(total=Sum("quantity"))["total"] or 0,
        "available_serials": StockUnit.objects.filter(**unit_filters, status=SerialStatus.AVAILABLE).count(),
        "low_stock": queryset.filter(is_stocked=True, scoped_stock_total__gt=0, scoped_stock_total__lte=F("reorder_level")).count(),
        "all_products": base_queryset.count(),
    }

    return render(request, "catalog/register.html", {
        "title": title,
        "rows": rows,
        "page_obj": page_obj,
        "query": query,
        "status": status,
        "tracking": tracking,
        "stock_status": stock_status,
        "selected_category": category_id,
        "selected_brand": brand_id,
        "category_options": category_options,
        "brand_options": brand_options,
        "metrics": metrics,
        "can_view_costs": can_view_costs,
        "can_add_product": user_has_organization_permission(request.user, organization, "catalog.add_product") if organization else request.user.is_superuser,
        "supports_status_filter": True,
        "has_detail_pages": True,
    })


@login_required
def module_overview(request, module):
    if module not in MODULES:
        raise Http404("Unknown module.")
    if module in SENSITIVE_MODULES and request.organization:
        membership = request.membership
        if not (request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner)):
            raise PermissionDenied("Organization owner access is required.")
    permission = MODULE_PERMISSIONS.get(module)
    if permission and request.organization:
        from apps.organizations.permissions import user_has_organization_permission
        if not user_has_organization_permission(request.user, request.organization, permission):
            raise PermissionDenied(f"Permission {permission} is required.")
    title, model, fields = MODULES[module]
    if module == "products":
        return _product_register(request, title)
    queryset = model.objects.none()
    if request.organization:
        queryset = _scope_to_user_branches(
            model.objects.filter(organization=request.organization), model, request
        ).order_by("-created_at")
    elif request.user.is_platform_admin or request.user.is_superuser:
        queryset = model.objects.all().order_by("-created_at")
    relation_field_names = {
        model_field.name for model_field in model._meta.fields if model_field.is_relation
    }
    related_fields = [field for field in fields if field in relation_field_names]
    if related_fields:
        queryset = queryset.select_related(*related_fields)
    query = request.GET.get("q", "").strip()
    if query:
        searchable_fields = [
            field.name for field in model._meta.fields
            if field.name in fields and field.get_internal_type() in {"CharField", "TextField", "EmailField", "SlugField"}
        ]
        search_filter = Q()
        for field in searchable_fields:
            search_filter |= Q(**{f"{field}__icontains": query})
        if searchable_fields:
            queryset = queryset.filter(search_filter)
    status = request.GET.get("status", "").strip()
    if status in {"active", "inactive"} and any(field.name == "is_active" for field in model._meta.fields):
        queryset = queryset.filter(is_active=status == "active")
    if module == "aged-stock":
        queryset = queryset.filter(status=SerialStatus.AVAILABLE, created_at__lte=timezone.now() - timedelta(days=5))
    if module == "agent-stock":
        queryset = queryset.filter(location__location_type=LocationType.AGENT)
    headers = [field.replace("_", " ").title() for field in fields]
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{module}.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(safe_csv_row(getattr(item, field) for field in fields) for item in queryset)
        return response
    if request.GET.get("format") == "pdf":
        rows_for_pdf = ([getattr(item, field) for field in fields] for item in queryset[:80])
        response = HttpResponse(
            build_simple_pdf(title=f"MobiPOS {title}", headers=headers, rows=list(rows_for_pdf)),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = f'attachment; filename="{module}.pdf"'
        return response
    page_obj = Paginator(queryset, 25).get_page(request.GET.get("page"))
    detail_url_name = MODULE_DETAIL_URLS.get(module)
    create_action = MODULE_CREATE_ACTIONS.get(module)
    if create_action:
        create_action = {
            **create_action,
            "url": reverse(create_action["url_name"]),
        }
    rows = [{
        "values": [getattr(item, field) for field in fields],
        "detail_url": reverse(detail_url_name, args=[item.pk]) if detail_url_name else "",
    } for item in page_obj]
    return render(
        request,
        "module_overview.html",
        {
            "title": title,
            "headers": headers,
            "rows": rows,
            "page_obj": page_obj,
            "query": query,
            "status": status,
            "supports_status_filter": any(field.name == "is_active" for field in model._meta.fields),
            "has_detail_pages": bool(detail_url_name),
            "create_action": create_action,
        },
    )


@login_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    results = []
    if query and (request.organization or request.user.is_platform_admin or request.user.is_superuser):
        organization_filter = {"organization": request.organization} if request.organization else {}
        branches = accessible_branches_for(request.user, request.organization) if request.organization else Branch.objects.all()
        searches = []
        if _can_view_module(request, "products"):
            searches.append(("Product", Product.objects.filter(**organization_filter).filter(Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query)), "product-detail"))
        if _can_view_module(request, "contacts"):
            searches.append(("Contact", Contact.objects.filter(**organization_filter).filter(Q(name__icontains=query) | Q(phone_number__icontains=query) | Q(email__icontains=query) | Q(tax_number__icontains=query)), "contact-detail"))
        if _can_view_module(request, "inventory"):
            searches.append(("Serial / IMEI", StockUnit.objects.filter(**organization_filter).filter(
                Q(location__branch__in=branches)
                | Q(saleline__sale__location__branch__in=branches)
                | Q(movements__location__branch__in=branches)
            ).filter(Q(serial_number__icontains=query) | Q(secondary_serial__icontains=query)).distinct(), "imei-history"))
        if _can_view_module(request, "sales"):
            searches.append(("Sale", Sale.objects.filter(**organization_filter, location__branch__in=branches, number__icontains=query), "sale-detail"))
        if _can_view_module(request, "repairs"):
            searches.append(("Repair", RepairTicket.objects.filter(**organization_filter, branch__in=branches, number__icontains=query), "repair-detail"))
        for label, queryset, url_name in searches:
            results.extend({
                "type": label,
                "label": str(item),
                "url": reverse(url_name, args=[item.pk]) if url_name and url_name != "imei-history" else (
                    f"{reverse('imei-history')}?q={item.serial_number}" if url_name == "imei-history" else ""
                ),
            } for item in queryset[:10])
    return render(request, "search_results.html", {"query": query, "results": results})


@login_required
@organization_permission_required("inventory.view_stockunit")
def imei_history(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    branches = accessible_branches_for(request.user, request.organization)
    units = StockUnit.objects.filter(
        organization=request.organization,
    ).filter(
        Q(location__branch__in=branches)
        | Q(saleline__sale__location__branch__in=branches)
        | Q(movements__location__branch__in=branches)
    ).select_related("product", "location", "location__branch").distinct()
    if query:
        units = units.filter(
            Q(serial_number__icontains=query)
            | Q(secondary_serial__icontains=query)
            | Q(product__name__icontains=query)
            | Q(product__sku__icontains=query)
        )
    valid_statuses = {choice.value for choice in SerialStatus}
    if status in valid_statuses:
        units = units.filter(status=status)
    units = units.order_by("product__name", "serial_number")
    unit = None
    movements = StockMovement.objects.none()
    sales = Sale.objects.none()
    purchases = PurchaseOrder.objects.none()
    if query and request.organization:
        unit = units.first()
        if unit:
            movements = StockMovement.objects.filter(
                organization=request.organization,
                stock_unit=unit,
            ).select_related("product", "location", "actor")[:50]
            sales = Sale.objects.filter(
                organization=request.organization,
                lines__stock_unit=unit,
                location__branch__in=branches,
            ).select_related("customer", "agent").distinct()
            purchases = PurchaseOrder.objects.filter(
                organization=request.organization,
                lines__product=unit.product,
                destination__branch__in=branches,
            ).select_related("supplier", "destination").distinct()[:10]
    page_obj = Paginator(units, 25).get_page(request.GET.get("page"))
    scoped_units = StockUnit.objects.filter(
        organization=request.organization,
    ).filter(
        Q(location__branch__in=branches)
        | Q(saleline__sale__location__branch__in=branches)
        | Q(movements__location__branch__in=branches)
    ).distinct()
    return render(request, "reports/imei_history.html", {
        "query": query,
        "status": status,
        "status_options": SerialStatus.choices,
        "units": page_obj.object_list,
        "page_obj": page_obj,
        "unit": unit,
        "movements": movements,
        "sales": sales,
        "purchases": purchases,
        "metrics": {
            "total": scoped_units.count(),
            "available": scoped_units.filter(status=SerialStatus.AVAILABLE).count(),
            "sold": scoped_units.filter(status=SerialStatus.SOLD).count(),
            "in_transfer": scoped_units.filter(status=SerialStatus.IN_TRANSFER).count(),
            "exceptions": scoped_units.filter(
                status__in=(SerialStatus.DAMAGED, SerialStatus.WARRANTY_REPAIR, SerialStatus.WRITTEN_OFF)
            ).count(),
        },
    })
