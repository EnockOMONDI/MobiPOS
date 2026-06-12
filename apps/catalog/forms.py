from django import forms

from .models import Brand, Category, Product


class OrganizationFormMixin:
    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization


class CategoryForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ("name", "code")

    def clean_code(self):
        code = self.cleaned_data["code"]
        if Category.objects.filter(organization=self.organization, code=code).exists():
            raise forms.ValidationError("This category code is already in use.")
        return code


class BrandForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Brand
        fields = ("name",)

    def clean_name(self):
        name = self.cleaned_data["name"]
        if Brand.objects.filter(organization=self.organization, name__iexact=name).exists():
            raise forms.ValidationError("This brand name is already in use.")
        return name


class ProductForm(OrganizationFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        exclude = ("organization",)
        widgets = {"description": forms.Textarea}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, organization=organization, **kwargs)
        for name in ("warranty_days", "reorder_level", "cost_price", "selling_price", "tax_rate"):
            self.fields[name].required = False
        if organization:
            self.fields["category"].queryset = Category.objects.filter(organization=organization, is_active=True)
            self.fields["brand"].queryset = Brand.objects.filter(organization=organization, is_active=True)

    def clean_sku(self):
        sku = self.cleaned_data["sku"].upper()
        if Product.objects.filter(organization=self.organization, sku=sku).exists():
            raise forms.ValidationError("This product SKU is already in use.")
        return sku
