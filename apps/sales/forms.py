from django import forms

from .models import ReturnDisposition, ReturnOutcome


class ReturnRequestForm(forms.Form):
    outcome = forms.ChoiceField(choices=ReturnOutcome.choices)
    disposition = forms.ChoiceField(choices=ReturnDisposition.choices)
    reason = forms.CharField(widget=forms.Textarea)
    refund_amount = forms.DecimalField(min_value=0, decimal_places=2)

    def __init__(self, *args, sale=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.sale = sale
        if sale:
            for line in sale.lines.select_related("product"):
                remaining = line.quantity - line.returned_quantity
                if remaining > 0:
                    self.fields[f"quantity_{line.id}"] = forms.DecimalField(
                        min_value=0,
                        max_value=remaining,
                        decimal_places=3,
                        required=False,
                        initial=0,
                        label=f"{line.product.name} return quantity (max {remaining})",
                    )

    def clean(self):
        cleaned = super().clean()
        selected = []
        refundable_total = 0
        if self.sale:
            for line in self.sale.lines.all():
                quantity = cleaned.get(f"quantity_{line.id}") or 0
                if quantity:
                    line_value = line.total_after_tax * quantity / line.quantity
                    selected.append((line, quantity, line_value))
                    refundable_total += line_value
        if not selected:
            raise forms.ValidationError("Select at least one sale line quantity to return.")
        refund_amount = cleaned.get("refund_amount") or 0
        if refund_amount > refundable_total:
            self.add_error("refund_amount", "Refund cannot exceed the selected items' value.")
        if cleaned.get("outcome") != ReturnOutcome.REFUND and refund_amount:
            self.add_error("refund_amount", "Only refund outcomes can include a cash refund amount.")
        cleaned["selected_lines"] = selected
        cleaned["refundable_total"] = refundable_total
        return cleaned
