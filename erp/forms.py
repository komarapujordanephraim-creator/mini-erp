from django import forms

from .models import Party, Product


class LineForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.all())
    qty = forms.IntegerField(min_value=1, max_value=100000)


class POLineForm(LineForm):
    unit_cost = forms.DecimalField(min_value=0, max_digits=10, decimal_places=2, required=False,
                                   help_text="Leave empty to use the product's cost")


class NewDocForm(forms.Form):
    party = forms.ModelChoiceField(queryset=Party.objects.none(), label="")

    def __init__(self, kind, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["party"].queryset = Party.objects.filter(kind=kind)
        self.fields["party"].label = "Customer" if kind == Party.CUSTOMER else "Supplier"


class AdjustForm(forms.Form):
    qty = forms.IntegerField(help_text="Positive to add, negative to remove")
    note = forms.CharField(max_length=200, help_text="Reason, e.g. 'stock count' or 'damaged'")
