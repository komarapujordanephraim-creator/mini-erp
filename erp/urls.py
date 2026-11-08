from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("products/", views.products, name="products"),
    path("products/<int:pk>/", views.product_detail, name="product"),
    path("invoices/", views.invoices, name="invoices"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice"),
    path("invoices/<int:pk>/print/", views.invoice_print, name="invoice_print"),
    path("invoices/<int:pk>/<slug:action>/", views.invoice_action, name="invoice_action"),
    path("purchases/", views.purchase_orders, name="pos"),
    path("purchases/<int:pk>/", views.po_detail, name="po"),
    path("purchases/<int:pk>/<slug:action>/", views.po_action, name="po_action"),
]
