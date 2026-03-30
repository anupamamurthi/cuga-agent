"""
Dummy product catalog tools — registered via CatalogPlugin.on_tools_build.

These are plain LangChain tools; they have no dependency on cuga internals.
"""
from __future__ import annotations

from langchain_core.tools import tool


CATALOG = {
    "SKU-001": {"name": "Wireless Keyboard",  "price": 49.99, "stock": 42},
    "SKU-002": {"name": "USB-C Hub",           "price": 29.99, "stock": 7},
    "SKU-003": {"name": "Noise-Cancel Headset","price": 129.99,"stock": 0},
    "SKU-004": {"name": "Ergonomic Mouse",     "price": 39.99, "stock": 18},
}


@tool
def lookup_product(sku: str) -> dict:
    """
    Return name, price, and stock count for a product by SKU.
    Returns an error dict if the SKU is not found.
    """
    product = CATALOG.get(sku.upper())
    if not product:
        return {"error": f"SKU '{sku}' not found. Available: {list(CATALOG.keys())}"}
    return {"sku": sku.upper(), **product}


@tool
def list_products() -> list[dict]:
    """Return all products in the catalog with their SKU, name, price, and stock."""
    return [{"sku": sku, **info} for sku, info in CATALOG.items()]


@tool
def check_inventory(sku: str) -> dict:
    """
    Return stock availability for a product by SKU.
    Returns status as one of: 'In Stock', 'Low Stock', 'Out of Stock'.
    """
    product = CATALOG.get(sku.upper())
    if not product:
        return {"error": f"SKU '{sku}' not found."}
    stock = product["stock"]
    if stock == 0:
        status = "Out of Stock"
    elif stock < 10:
        status = "Low Stock"
    else:
        status = "In Stock"
    return {"sku": sku.upper(), "stock": stock, "status": status}
