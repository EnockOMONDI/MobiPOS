from django import template

register = template.Library()


ACTION_LABELS = {
    "agent_document.downloaded": "Agent document downloaded",
    "agent_document.uploaded": "Agent document uploaded",
    "agent_profile.approve": "Agent profile approved",
    "agent_profile.created": "Agent profile created",
    "agent_profile.dsa_registered": "DSA registered",
    "agent_profile.reject": "Agent profile rejected",
    "agent_profile.suspend": "Agent profile suspended",
    "agent_profile.reactivate": "Agent profile reactivated",
    "auth.login": "User logged in",
    "auth.logout": "User logged out",
    "auth.login_failed": "Failed login attempt",
    "branch.created": "Branch created",
    "contact.created": "Contact created",
    "contact.updated": "Contact updated",
    "contact.archived": "Contact archived",
    "contact.restored": "Contact restored",
    "location.agent_custody_created": "Agent custody location created",
    "location.created": "Stock location created",
    "membership.access_updated": "User access updated",
    "product.updated": "Product updated",
    "role.created": "Role created",
    "user.created": "User created",
}

TARGET_LABELS = {
    "accounts.User": "User account",
    "catalog.Brand": "Brand",
    "catalog.Category": "Category",
    "catalog.Product": "Product",
    "contacts.Contact": "Contact",
    "inventory.StockMovement": "Stock movement",
    "inventory.StockUnit": "Device or serial unit",
    "organizations.AgentDocument": "Agent document",
    "organizations.AgentProfile": "Agent profile",
    "organizations.Branch": "Branch",
    "organizations.Location": "Stock location",
    "organizations.Membership": "User membership",
    "organizations.Role": "Role",
    "payments.Payment": "Payment",
    "pos.POSSession": "Cashier session",
    "purchasing.PurchaseOrder": "Purchase order",
    "sales.Sale": "Sale",
    "transfers.StockTransfer": "Stock transfer",
}


@register.filter
def business_action(value):
    if not value:
        return "Activity"
    if value in ACTION_LABELS:
        return ACTION_LABELS[value]
    return str(value).replace("_", " ").replace(".", " ").title()


@register.filter
def business_target(value):
    if not value:
        return "Record"
    if value in TARGET_LABELS:
        return TARGET_LABELS[value]
    label = str(value).split(".")[-1]
    return label.replace("_", " ").title()
