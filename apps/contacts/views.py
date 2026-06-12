from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_permission_required

from .forms import ContactForm


@login_required
@organization_permission_required("contacts.add_contact")
@transaction.atomic
def contact_create(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        contact = form.save(commit=False)
        contact.organization = request.organization
        contact.save()
        record_audit_event(action="contact.created", actor=request.user, organization=request.organization, target=contact, request=request)
        return redirect("module-overview", module="contacts")
    return render(request, "contacts/create.html", {"form": form})
