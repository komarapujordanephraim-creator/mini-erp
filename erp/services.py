"""All stock and document changes go through here, each in one transaction."""
from collections import Counter
from datetime import date, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Invoice, Product, PurchaseOrder, Sequence, StockMove


class ERPError(Exception):
    pass


def next_number(prefix: str) -> str:
    seq, _ = Sequence.objects.select_for_update().get_or_create(name=prefix)
    seq.value += 1
    seq.save(update_fields=["value"])
    return "%s-%04d" % (prefix, seq.value)


@transaction.atomic
def new_po(supplier) -> PurchaseOrder:
    return PurchaseOrder.objects.create(number=next_number("PO"), supplier=supplier)


@transaction.atomic
def place_po(po: PurchaseOrder) -> None:
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.DRAFT:
        raise ERPError("Only a draft purchase order can be placed.")
    if not po.lines.exists():
        raise ERPError("Add at least one line first.")
    po.status = PurchaseOrder.ORDERED
    po.save(update_fields=["status"])


@transaction.atomic
def receive_po(po: PurchaseOrder, user) -> None:
    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status != PurchaseOrder.ORDERED:
        raise ERPError("Only an ordered purchase order can be received (this one is %s)." % po.get_status_display().lower())
    for line in po.lines.select_related("product"):
        StockMove.objects.create(product=line.product, qty=line.qty, kind=StockMove.RECEIPT, ref=po.number, by=user)
    po.status, po.received_at = PurchaseOrder.RECEIVED, timezone.now()
    po.save(update_fields=["status", "received_at"])


@transaction.atomic
def issue_invoice(inv: Invoice, user, today: date = None) -> Invoice:
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if inv.status != Invoice.DRAFT:
        raise ERPError("Only a draft invoice can be issued.")
    lines = list(inv.lines.select_related("product"))
    if not lines:
        raise ERPError("Add at least one line first.")
    need = Counter()
    for l in lines:
        need[l.product_id] += l.qty
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=need)}
    short = ["%s (have %d, need %d)" % (p.sku, p.stock, need[pk]) for pk, p in products.items() if p.stock < need[pk]]
    if short:
        raise ERPError("Not enough stock: " + ", ".join(short))
    inv.number = next_number("INV")
    for l in lines:
        StockMove.objects.create(product=l.product, qty=-l.qty, kind=StockMove.SALE, ref=inv.number, by=user)
    today = today or timezone.localdate()
    inv.status, inv.issued_on = Invoice.ISSUED, today
    inv.due_on = today + timedelta(days=settings.ERP_PAYMENT_DAYS)
    inv.save()
    return inv


@transaction.atomic
def mark_paid(inv: Invoice, today: date = None) -> None:
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if inv.status != Invoice.ISSUED:
        raise ERPError("Only an issued invoice can be marked paid.")
    inv.status, inv.paid_on = Invoice.PAID, today or timezone.localdate()
    inv.save(update_fields=["status", "paid_on"])


@transaction.atomic
def void_invoice(inv: Invoice, user) -> None:
    inv = Invoice.objects.select_for_update().get(pk=inv.pk)
    if inv.status != Invoice.ISSUED:
        raise ERPError("Only an issued, unpaid invoice can be voided. Paid invoices need a credit note.")
    for l in inv.lines.select_related("product"):
        StockMove.objects.create(product=l.product, qty=l.qty, kind=StockMove.RETURN, ref=inv.number, by=user)
    inv.status = Invoice.VOID
    inv.save(update_fields=["status"])


@transaction.atomic
def adjust_stock(product: Product, qty: int, note: str, user) -> None:
    product = Product.objects.select_for_update().get(pk=product.pk)
    if qty == 0:
        raise ERPError("Adjustment cannot be zero.")
    if not note.strip():
        raise ERPError("Give a reason for the adjustment.")
    if product.stock + qty < 0:
        raise ERPError("Stock cannot go below zero (have %d)." % product.stock)
    StockMove.objects.create(product=product, qty=qty, kind=StockMove.ADJUST, note=note.strip()[:200], by=user)
