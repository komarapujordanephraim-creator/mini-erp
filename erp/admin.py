from django.contrib import admin

from .models import Invoice, InvoiceLine, Party, POLine, Product, PurchaseOrder, StockMove

admin.site.register([Product, Party])


@admin.register(StockMove)
class MoveAdmin(admin.ModelAdmin):
    list_display = ("at", "product", "qty", "kind", "ref", "by")
    def has_change_permission(self, request, obj=None):  # the ledger is append-only
        return False
    def has_delete_permission(self, request, obj=None):
        return False


class InvLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "customer", "status", "issued_on", "due_on")
    inlines = [InvLineInline]


class POLineInline(admin.TabularInline):
    model = POLine
    extra = 0


@admin.register(PurchaseOrder)
class POAdmin(admin.ModelAdmin):
    list_display = ("number", "supplier", "status")
    inlines = [POLineInline]
