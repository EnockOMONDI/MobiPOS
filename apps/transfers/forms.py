from django import forms

from apps.catalog.models import Product
from apps.inventory.models import StockUnit
from apps.organizations.models import Location, LocationType
from apps.organizations.permissions import accessible_locations_for


class TransferForm(forms.Form):
    MAX_LINES = 5
    source = forms.ModelChoiceField(queryset=Location.objects.none())
    destination = forms.ModelChoiceField(queryset=Location.objects.none())
    product = forms.ModelChoiceField(queryset=Product.objects.none(), required=False)
    stock_unit = forms.ModelChoiceField(queryset=StockUnit.objects.none(), required=False, label="Serial / IMEI")
    selected_stock_units = forms.ModelMultipleChoiceField(
        queryset=StockUnit.objects.none(),
        required=False,
        label="Selected devices",
        widget=forms.CheckboxSelectMultiple,
    )
    quantity = forms.DecimalField(min_value=1, decimal_places=3, required=False)
    notes = forms.CharField(widget=forms.Textarea, required=False)

    def __init__(self, *args, organization=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            locations = accessible_locations_for(user, organization)
            self.fields["source"].queryset = locations
            self.fields["destination"].queryset = locations
            self.fields["product"].queryset = Product.objects.filter(organization=organization, is_stocked=True, is_active=True)
            self.fields["stock_unit"].queryset = StockUnit.objects.filter(
                organization=organization, status="available", location__in=locations
            )
            self.fields["selected_stock_units"].queryset = self.fields["stock_unit"].queryset.select_related("product", "location")
            for index in range(2, self.MAX_LINES + 1):
                self.fields[f"product_{index}"] = forms.ModelChoiceField(
                    queryset=self.fields["product"].queryset, required=False, label=f"Additional item {index}"
                )
                self.fields[f"stock_unit_{index}"] = forms.ModelChoiceField(
                    queryset=self.fields["stock_unit"].queryset, required=False, label=f"Item {index} serial / IMEI"
                )
                self.fields[f"quantity_{index}"] = forms.DecimalField(
                    min_value=1, decimal_places=3, required=False, label=f"Item {index} quantity"
                )
        self.additional_line_groups = [
            (self[f"product_{index}"], self[f"stock_unit_{index}"], self[f"quantity_{index}"])
            for index in range(2, self.MAX_LINES + 1)
        ] if organization else []

    def clean(self):
        cleaned = super().clean()
        source, destination = cleaned.get("source"), cleaned.get("destination")
        if source and destination and source == destination:
            raise forms.ValidationError("Source and destination must be different.")
        lines = []
        seen_serials = set()
        selected_stock_units = cleaned.get("selected_stock_units") or []
        for stock_unit in selected_stock_units:
            if not source or stock_unit.location_id != source.id:
                self.add_error("selected_stock_units", "Selected devices must be available at the source location.")
                continue
            if stock_unit.id in seen_serials:
                self.add_error("selected_stock_units", "This serial is already included.")
                continue
            seen_serials.add(stock_unit.id)
            lines.append({"product": stock_unit.product, "stock_unit": stock_unit, "quantity": 1})
        for index in range(1, self.MAX_LINES + 1):
            suffix = "" if index == 1 else f"_{index}"
            product = cleaned.get(f"product{suffix}")
            stock_unit = cleaned.get(f"stock_unit{suffix}")
            quantity = cleaned.get(f"quantity{suffix}")
            if not product:
                if stock_unit or quantity is not None:
                    self.add_error(f"product{suffix}", "Choose a product for this line.")
                continue
            if quantity is None:
                self.add_error(f"quantity{suffix}", "Enter a quantity.")
                continue
            if product.is_serialized:
                if not stock_unit or stock_unit.product_id != product.id or not source or stock_unit.location_id != source.id:
                    self.add_error(f"stock_unit{suffix}", "Select an available serial at the source location.")
                elif stock_unit.id in seen_serials:
                    self.add_error(f"stock_unit{suffix}", "This serial is already included.")
                if quantity != 1:
                    self.add_error(f"quantity{suffix}", "Serialized transfers move one unit per line.")
            elif stock_unit:
                self.add_error(f"stock_unit{suffix}", "Do not select a serial for a quantity-based product.")
            seen_serials.add(stock_unit.id if stock_unit else None)
            lines.append({"product": product, "stock_unit": stock_unit, "quantity": quantity})
        if not lines:
            raise forms.ValidationError("Select at least one device or enter a transfer line.")
        cleaned["lines"] = lines
        return cleaned


class AgentAllocationForm(forms.Form):
    source = forms.ModelChoiceField(queryset=Location.objects.none(), label="Source location")
    agent_location = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        label="Receiving agent",
        help_text="Only users with an active agent custody location are listed.",
    )
    selected_stock_units = forms.ModelMultipleChoiceField(
        queryset=StockUnit.objects.none(),
        label="Devices to allocate",
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(widget=forms.Textarea, required=False)
    complete_now = forms.BooleanField(
        required=False,
        initial=True,
        label="Approve, dispatch, and receive immediately",
        help_text="Owners and transfer managers can complete agent allocation in one step.",
    )

    def __init__(self, *args, organization=None, user=None, can_complete=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_complete = can_complete
        if organization:
            locations = accessible_locations_for(user, organization)
            self.fields["source"].queryset = locations.exclude(location_type=LocationType.AGENT)
            self.fields["agent_location"].queryset = (
                locations.filter(
                    location_type=LocationType.AGENT,
                    custodian_membership__isnull=False,
                )
                .select_related("custodian_membership__user", "branch")
                .order_by("custodian_membership__user__first_name", "custodian_membership__user__username")
            )
            self.fields["selected_stock_units"].queryset = (
                StockUnit.objects.filter(
                    organization=organization,
                    status="available",
                    location__in=self.fields["source"].queryset,
                )
                .select_related("product", "location")
                .order_by("product__name", "serial_number")
            )
        if not can_complete:
            self.fields["complete_now"].disabled = True
            self.fields["complete_now"].initial = False
            self.fields["complete_now"].help_text = "Requires transfer management permission or owner access."

    def clean(self):
        cleaned = super().clean()
        source = cleaned.get("source")
        agent_location = cleaned.get("agent_location")
        selected_stock_units = cleaned.get("selected_stock_units") or []
        if source and agent_location and source == agent_location:
            self.add_error("agent_location", "Source and receiving agent location must be different.")
        for stock_unit in selected_stock_units:
            if source and stock_unit.location_id != source.id:
                self.add_error("selected_stock_units", "Selected devices must be available at the source location.")
                break
        if cleaned.get("complete_now") and not self.can_complete:
            self.add_error("complete_now", "You do not have permission to complete the allocation immediately.")
        cleaned["lines"] = [
            {"product": stock_unit.product, "stock_unit": stock_unit, "quantity": 1}
            for stock_unit in selected_stock_units
        ]
        return cleaned


class AgentRecallForm(forms.Form):
    agent_location = forms.ModelChoiceField(
        queryset=Location.objects.none(),
        label="Agent custody location",
        help_text="Choose the agent or DSA location currently holding the devices.",
    )
    destination = forms.ModelChoiceField(queryset=Location.objects.none(), label="Return destination")
    selected_stock_units = forms.ModelMultipleChoiceField(
        queryset=StockUnit.objects.none(),
        label="Devices to recall",
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(widget=forms.Textarea, required=False)
    complete_now = forms.BooleanField(
        required=False,
        initial=True,
        label="Approve, dispatch, and receive immediately",
        help_text="Owners and transfer managers can complete agent recall in one step.",
    )

    def __init__(self, *args, organization=None, user=None, can_complete=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.can_complete = can_complete
        if organization:
            locations = accessible_locations_for(user, organization)
            agent_locations = locations.filter(
                location_type=LocationType.AGENT,
                custodian_membership__isnull=False,
            )
            self.fields["agent_location"].queryset = (
                agent_locations
                .select_related("custodian_membership__user", "branch")
                .order_by("custodian_membership__user__first_name", "custodian_membership__user__username")
            )
            self.fields["destination"].queryset = locations.exclude(location_type=LocationType.AGENT)
            self.fields["selected_stock_units"].queryset = (
                StockUnit.objects.filter(
                    organization=organization,
                    status="available",
                    location__in=agent_locations,
                )
                .select_related("product", "location", "location__custodian_membership__user")
                .order_by("location__custodian_membership__user__first_name", "product__name", "serial_number")
            )
        if not can_complete:
            self.fields["complete_now"].disabled = True
            self.fields["complete_now"].initial = False
            self.fields["complete_now"].help_text = "Requires transfer management permission or owner access."

    def clean(self):
        cleaned = super().clean()
        agent_location = cleaned.get("agent_location")
        destination = cleaned.get("destination")
        selected_stock_units = cleaned.get("selected_stock_units") or []
        if agent_location and destination and agent_location == destination:
            self.add_error("destination", "Source and destination must be different.")
        for stock_unit in selected_stock_units:
            if agent_location and stock_unit.location_id != agent_location.id:
                self.add_error("selected_stock_units", "Selected devices must be held by the chosen agent location.")
                break
        if cleaned.get("complete_now") and not self.can_complete:
            self.add_error("complete_now", "You do not have permission to complete the recall immediately.")
        cleaned["lines"] = [
            {"product": stock_unit.product, "stock_unit": stock_unit, "quantity": 1}
            for stock_unit in selected_stock_units
        ]
        return cleaned
