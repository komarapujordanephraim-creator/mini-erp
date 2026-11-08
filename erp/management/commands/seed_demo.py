"""Fictional demo company. Safe to run more than once."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from erp import services
from erp.models import Invoice, InvoiceLine, Party, POLine, Product

PRODUCTS = [
    ("NB-A5", "A5 notebook, 200 pages", "120.00", "70.00", 20),
    ("PEN-BLU", "Blue gel pen (box of 10)", "180.00", "110.00", 10),
    ("FILE-L", "Lever arch file", "95.00", "55.00", 15),
    ("PAPER-A4", "A4 copier paper, 500 sheets", "320.00", "240.00", 25),
    ("STAPLER", "Heavy-duty stapler", "450.00", "290.00", 5),
]


class Command(BaseCommand):
    help = "Create a demo user and a fictional trading company's data."

    def handle(self, *args, **opts):
        User = get_user_model()
        user = User.objects.filter(username="demo").first() or User.objects.create_user(
            "demo", "demo@example.com", "demo12345", is_staff=True)
        if Product.objects.exists():
            return
        prods = {sku: Product.objects.create(sku=sku, name=n, unit_price=Decimal(p), unit_cost=Decimal(c), reorder_level=r)
                 for sku, n, p, c, r in PRODUCTS}
        sup = Party.objects.create(name="Sunrise Stationery Wholesale", kind=Party.SUPPLIER, email="orders@sunrise.example")
        c1 = Party.objects.create(name="Greenleaf School", kind=Party.CUSTOMER, gstin="29ABCDE1234F1Z5")
        c2 = Party.objects.create(name="Northwind Offices", kind=Party.CUSTOMER)
        po = services.new_po(sup)
        for sku, q in [("NB-A5", 120), ("PEN-BLU", 40), ("FILE-L", 30), ("PAPER-A4", 30), ("STAPLER", 8)]:
            POLine.objects.create(po=po, product=prods[sku], qty=q, unit_cost=prods[sku].unit_cost)
        services.place_po(po)
        services.receive_po(po, user)
        today = timezone.localdate()
        for cust, lines, days_ago, paid in [(c1, [("NB-A5", 60), ("PEN-BLU", 12)], 25, True),
                                            (c2, [("PAPER-A4", 18), ("STAPLER", 4)], 20, False),
                                            (c1, [("FILE-L", 20), ("NB-A5", 30)], 3, False)]:
            inv = Invoice.objects.create(customer=cust, tax_rate=Decimal(settings.ERP_TAX_RATE))
            for sku, q in lines:
                InvoiceLine.objects.create(invoice=inv, product=prods[sku], description=prods[sku].name, qty=q, unit_price=prods[sku].unit_price)
            services.issue_invoice(inv, user, today=today - timedelta(days=days_ago))
            if paid:
                services.mark_paid(inv, today - timedelta(days=days_ago - 5))
        draft = Invoice.objects.create(customer=c2, tax_rate=Decimal(settings.ERP_TAX_RATE))
        InvoiceLine.objects.create(invoice=draft, product=prods["PEN-BLU"], description=prods["PEN-BLU"].name, qty=5, unit_price=prods["PEN-BLU"].unit_price)
        if opts.get("verbosity", 1):
            self.stdout.write(self.style.SUCCESS("Demo ready. Log in as demo / demo12345"))
