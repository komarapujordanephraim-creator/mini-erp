from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import services
from .models import Invoice, InvoiceLine, Party, POLine, Product, PurchaseOrder, StockMove

User = get_user_model()


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("boss", "b@example.com", "pw-12345-test", is_staff=True)
        self.p = Product.objects.create(sku="NB", name="Notebook", unit_price=Decimal("10.00"), unit_cost=Decimal("6.00"), reorder_level=3)
        self.cust = Party.objects.create(name="Cust", kind=Party.CUSTOMER)
        self.sup = Party.objects.create(name="Sup", kind=Party.SUPPLIER)

    def stock_in(self, qty):
        StockMove.objects.create(product=self.p, qty=qty, kind=StockMove.ADJUST, note="test")

    def invoice(self, qty, price="10.00"):
        inv = Invoice.objects.create(customer=self.cust, tax_rate=Decimal("18.00"))
        InvoiceLine.objects.create(invoice=inv, product=self.p, description="Notebook", qty=qty, unit_price=Decimal(price))
        return inv


class StockAndInvoiceTests(Base):
    def test_stock_is_sum_of_ledger(self):
        self.stock_in(10)
        StockMove.objects.create(product=self.p, qty=-4, kind=StockMove.SALE)
        self.assertEqual(self.p.stock, 6)

    def test_totals_and_rounding(self):
        inv = self.invoice(3, "33.33")
        self.assertEqual(inv.subtotal, Decimal("99.99"))
        self.assertEqual(inv.tax, Decimal("18.00"))          # 17.9982 rounds half-up to 18.00
        self.assertEqual(inv.total, Decimal("117.99"))

    def test_issue_takes_stock_numbers_and_due_date(self):
        self.stock_in(10)
        inv = services.issue_invoice(self.invoice(4), self.user, today=date(2026, 1, 10))
        self.assertEqual(inv.number, "INV-0001")
        self.assertEqual(inv.due_on, date(2026, 1, 25))
        self.assertEqual(self.p.stock, 6)

    def test_cannot_oversell_and_nothing_changes(self):
        self.stock_in(2)
        inv = self.invoice(5)
        with self.assertRaises(services.ERPError):
            services.issue_invoice(inv, self.user)
        inv.refresh_from_db()
        self.assertEqual(inv.status, Invoice.DRAFT)
        self.assertIsNone(inv.number)
        self.assertEqual(self.p.stock, 2)

    def test_two_lines_same_product_counted_together(self):
        self.stock_in(5)
        inv = self.invoice(3)
        InvoiceLine.objects.create(invoice=inv, product=self.p, description="Notebook", qty=3, unit_price=Decimal("10"))
        with self.assertRaises(services.ERPError):
            services.issue_invoice(inv, self.user)

    def test_numbers_are_sequential(self):
        self.stock_in(10)
        a = services.issue_invoice(self.invoice(1), self.user)
        b = services.issue_invoice(self.invoice(1), self.user)
        self.assertEqual((a.number, b.number), ("INV-0001", "INV-0002"))

    def test_void_returns_stock_once_and_paid_cannot_be_voided(self):
        self.stock_in(10)
        inv = services.issue_invoice(self.invoice(4), self.user)
        services.void_invoice(inv, self.user)
        self.assertEqual(self.p.stock, 10)
        with self.assertRaises(services.ERPError):
            services.void_invoice(inv, self.user)
        inv2 = services.issue_invoice(self.invoice(1), self.user)
        services.mark_paid(inv2)
        with self.assertRaises(services.ERPError):
            services.void_invoice(inv2, self.user)

    def test_adjustment_rules(self):
        self.stock_in(3)
        with self.assertRaises(services.ERPError):
            services.adjust_stock(self.p, -5, "count", self.user)
        with self.assertRaises(services.ERPError):
            services.adjust_stock(self.p, 2, "  ", self.user)
        services.adjust_stock(self.p, -1, "damaged", self.user)
        self.assertEqual(self.p.stock, 2)


class PurchaseTests(Base):
    def test_po_flow_receives_once(self):
        po = services.new_po(self.sup)
        self.assertEqual(po.number, "PO-0001")
        with self.assertRaises(services.ERPError):
            services.place_po(po)                                   # no lines
        POLine.objects.create(po=po, product=self.p, qty=12, unit_cost=Decimal("6.00"))
        with self.assertRaises(services.ERPError):
            services.receive_po(po, self.user)                      # not ordered yet
        services.place_po(po)
        services.receive_po(po, self.user)
        self.assertEqual(self.p.stock, 12)
        with self.assertRaises(services.ERPError):
            services.receive_po(po, self.user)
        self.assertEqual(self.p.stock, 12)
        self.assertEqual(PurchaseOrder.objects.get().total, Decimal("72.00"))


class ViewTests(Base):
    def test_staff_only(self):
        for name in ("dashboard", "products", "invoices", "pos"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.client.force_login(User.objects.create_user("joe", "j@example.com", "pw-12345-test"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)
        self.client.force_login(self.user)
        for name in ("dashboard", "products", "invoices", "pos"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_actions_need_post(self):
        self.client.force_login(self.user)
        inv = self.invoice(1)
        self.assertEqual(self.client.get(reverse("invoice_action", args=[inv.pk, "issue"])).status_code, 405)

    def test_issued_invoice_cannot_be_edited(self):
        self.stock_in(5)
        inv = services.issue_invoice(self.invoice(1), self.user)
        self.client.force_login(self.user)
        self.client.post(reverse("invoice", args=[inv.pk]), {"product": self.p.pk, "qty": 3})
        self.client.post(reverse("invoice_action", args=[inv.pk, "remove-line"]), {"line": inv.lines.first().pk})
        self.assertEqual(inv.lines.count(), 1)
        self.assertEqual(inv.lines.first().qty, 1)

    def test_line_price_is_snapshot(self):
        self.client.force_login(self.user)
        inv = Invoice.objects.create(customer=self.cust, tax_rate=Decimal("18"))
        self.client.post(reverse("invoice", args=[inv.pk]), {"product": self.p.pk, "qty": 2})
        Product.objects.filter(pk=self.p.pk).update(unit_price=Decimal("99"))
        self.assertEqual(inv.lines.get().unit_price, Decimal("10.00"))

    def test_print_only_for_numbered_invoices(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("invoice_print", args=[self.invoice(1).pk])).status_code, 404)

    def test_dashboard_lists_low_stock(self):
        self.stock_in(2)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("dashboard")), "NB")

    def test_seed_twice(self):
        call_command("seed_demo", verbosity=0)
        n = Invoice.objects.count()
        call_command("seed_demo", verbosity=0)
        self.assertEqual(Invoice.objects.count(), n)
