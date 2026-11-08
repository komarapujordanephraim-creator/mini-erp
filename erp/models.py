from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum

TWO = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(x).quantize(TWO, rounding=ROUND_HALF_UP)


class Product(models.Model):
    sku = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=120)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    reorder_level = models.PositiveIntegerField(default=5)

    class Meta:
        ordering = ["sku"]

    def __str__(self):
        return "%s %s" % (self.sku, self.name)

    @property
    def stock(self) -> int:
        return self.moves.aggregate(s=Sum("qty"))["s"] or 0


class Party(models.Model):
    """A customer or a supplier."""
    CUSTOMER, SUPPLIER = "customer", "supplier"
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=[(CUSTOMER, "Customer"), (SUPPLIER, "Supplier")])
    email = models.EmailField(blank=True)
    gstin = models.CharField("GSTIN / tax id", max_length=20, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StockMove(models.Model):
    """The stock ledger. Stock is never stored, only summed from moves."""
    RECEIPT, SALE, RETURN, ADJUST = "receipt", "sale", "return", "adjust"
    KINDS = [(RECEIPT, "Purchase receipt"), (SALE, "Sale"), (RETURN, "Return (void)"), (ADJUST, "Adjustment")]
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="moves")
    qty = models.IntegerField()
    kind = models.CharField(max_length=10, choices=KINDS)
    ref = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=200, blank=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-at", "-id"]


class PurchaseOrder(models.Model):
    DRAFT, ORDERED, RECEIVED = "draft", "ordered", "received"
    STATUSES = [(DRAFT, "Draft"), (ORDERED, "Ordered"), (RECEIVED, "Received")]
    number = models.CharField(max_length=20, unique=True)
    supplier = models.ForeignKey(Party, on_delete=models.PROTECT, limit_choices_to={"kind": Party.SUPPLIER})
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    created_at = models.DateTimeField(auto_now_add=True)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-id"]

    @property
    def total(self) -> Decimal:
        return money(sum((l.qty * l.unit_cost for l in self.lines.all()), Decimal(0)))


class POLine(models.Model):
    po = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    qty = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def amount(self) -> Decimal:
        return money(self.qty * self.unit_cost)


class Invoice(models.Model):
    DRAFT, ISSUED, PAID, VOID = "draft", "issued", "paid", "void"
    STATUSES = [(DRAFT, "Draft"), (ISSUED, "Issued"), (PAID, "Paid"), (VOID, "Void")]
    number = models.CharField(max_length=20, unique=True, null=True, blank=True)  # given when issued
    customer = models.ForeignKey(Party, on_delete=models.PROTECT, limit_choices_to={"kind": Party.CUSTOMER})
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2)
    issued_on = models.DateField(null=True, blank=True)
    due_on = models.DateField(null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]

    @property
    def subtotal(self) -> Decimal:
        return money(sum((l.line_total for l in self.lines.all()), Decimal(0)))

    @property
    def tax(self) -> Decimal:
        return money(self.subtotal * self.tax_rate / 100)

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.tax


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    description = models.CharField(max_length=160)          # snapshot at the time of sale
    qty = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)  # snapshot

    @property
    def line_total(self) -> Decimal:
        return money(self.qty * self.unit_price)


class Sequence(models.Model):
    """Gap-free document numbers (INV-0001, PO-0001), assigned inside a transaction."""
    name = models.CharField(max_length=10, unique=True)
    value = models.PositiveIntegerField(default=0)
