from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import AdjustForm, LineForm, NewDocForm, POLineForm
from .models import Invoice, InvoiceLine, Party, POLine, Product, PurchaseOrder, StockMove, money

staff = staff_member_required(login_url="login")


def _ctx(**kw):
    kw.update(cur=settings.ERP_CURRENCY, company=settings.ERP_COMPANY)
    return kw


@staff
def dashboard(request):
    today = timezone.localdate()
    products = list(Product.objects.annotate(on_hand=Sum("moves__qty")))
    stock_value = money(sum(((p.on_hand or 0) * p.unit_cost for p in products), Decimal(0)))
    low = [p for p in products if (p.on_hand or 0) <= p.reorder_level]
    open_inv = list(Invoice.objects.filter(status=Invoice.ISSUED).prefetch_related("lines"))
    overdue = [i for i in open_inv if i.due_on and i.due_on < today]
    month_inv = Invoice.objects.filter(status__in=[Invoice.ISSUED, Invoice.PAID], issued_on__year=today.year,
                                       issued_on__month=today.month).prefetch_related("lines")
    return render(request, "erp/dashboard.html", _ctx(
        stock_value=stock_value, low=low, unpaid=money(sum((i.total for i in open_inv), Decimal(0))),
        overdue=overdue, overdue_total=money(sum((i.total for i in overdue), Decimal(0))),
        sales_month=money(sum((i.subtotal for i in month_inv), Decimal(0))), month=today.strftime("%B %Y"),
        moves=StockMove.objects.select_related("product", "by")[:10]))


@staff
def products(request):
    return render(request, "erp/products.html", _ctx(items=Product.objects.annotate(on_hand=Sum("moves__qty"))))


@staff
def product_detail(request, pk):
    p = get_object_or_404(Product, pk=pk)
    form = AdjustForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            services.adjust_stock(p, form.cleaned_data["qty"], form.cleaned_data["note"], request.user)
            messages.success(request, "Stock adjusted.")
            return redirect("product", pk=pk)
        except services.ERPError as e:
            messages.error(request, str(e))
    return render(request, "erp/product.html", _ctx(p=p, form=form, moves=p.moves.select_related("by")[:100]))


@staff
def invoices(request):
    form = NewDocForm(Party.CUSTOMER, request.POST or None)
    if request.method == "POST" and form.is_valid():
        inv = Invoice.objects.create(customer=form.cleaned_data["party"], tax_rate=Decimal(settings.ERP_TAX_RATE))
        return redirect("invoice", pk=inv.pk)
    return render(request, "erp/invoices.html", _ctx(items=Invoice.objects.select_related("customer").prefetch_related("lines"),
                                                     form=form, today=timezone.localdate()))


@staff
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
    form = LineForm(request.POST or None)
    if request.method == "POST":
        if inv.status != Invoice.DRAFT:
            messages.error(request, "Only draft invoices can be edited.")
        elif form.is_valid():
            p = form.cleaned_data["product"]
            InvoiceLine.objects.create(invoice=inv, product=p, description=p.name, qty=form.cleaned_data["qty"], unit_price=p.unit_price)
            return redirect("invoice", pk=pk)
    return render(request, "erp/invoice.html", _ctx(inv=inv, lines=inv.lines.select_related("product"), form=form))


@staff
@require_POST
def invoice_action(request, pk, action):
    inv = get_object_or_404(Invoice, pk=pk)
    try:
        if action == "issue":
            services.issue_invoice(inv, request.user)
        elif action == "paid":
            services.mark_paid(inv)
        elif action == "void":
            services.void_invoice(inv, request.user)
        elif action == "remove-line" and inv.status == Invoice.DRAFT:
            inv.lines.filter(pk=request.POST.get("line")).delete()
        else:
            raise services.ERPError("That action is not allowed now.")
        messages.success(request, "Done.")
    except services.ERPError as e:
        messages.error(request, str(e))
    return redirect("invoice", pk=pk)


@staff
def invoice_print(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk, status__in=[Invoice.ISSUED, Invoice.PAID, Invoice.VOID])
    return render(request, "erp/invoice_print.html", _ctx(inv=inv, lines=inv.lines.all()))


@staff
def purchase_orders(request):
    form = NewDocForm(Party.SUPPLIER, request.POST or None)
    if request.method == "POST" and form.is_valid():
        po = services.new_po(form.cleaned_data["party"])
        return redirect("po", pk=po.pk)
    return render(request, "erp/pos.html", _ctx(items=PurchaseOrder.objects.select_related("supplier").prefetch_related("lines"), form=form))


@staff
def po_detail(request, pk):
    po = get_object_or_404(PurchaseOrder.objects.select_related("supplier"), pk=pk)
    form = POLineForm(request.POST or None)
    if request.method == "POST":
        if po.status != PurchaseOrder.DRAFT:
            messages.error(request, "Only draft purchase orders can be edited.")
        elif form.is_valid():
            p = form.cleaned_data["product"]
            POLine.objects.create(po=po, product=p, qty=form.cleaned_data["qty"],
                                  unit_cost=form.cleaned_data["unit_cost"] if form.cleaned_data["unit_cost"] is not None else p.unit_cost)
            return redirect("po", pk=pk)
    return render(request, "erp/po.html", _ctx(po=po, lines=po.lines.select_related("product"), form=form))


@staff
@require_POST
def po_action(request, pk, action):
    po = get_object_or_404(PurchaseOrder, pk=pk)
    try:
        if action == "place":
            services.place_po(po)
        elif action == "receive":
            services.receive_po(po, request.user)
        else:
            raise services.ERPError("Unknown action.")
        messages.success(request, "Done.")
    except services.ERPError as e:
        messages.error(request, str(e))
    return redirect("po", pk=pk)
