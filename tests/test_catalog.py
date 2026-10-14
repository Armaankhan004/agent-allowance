"""
Unit tests for the product catalog.
Verifies keyword searching, product retrieval, merchant allowlist coverage,
and presence of the adversarial prompt-injection listing.
"""

from agent_allowance.catalog import search_products, get_product


def test_search_products_all():
    """Empty query should return all catalog listings."""
    items = search_products("")
    assert len(items) >= 12  # 3 merchants x 4 items + 1 malicious = 13 items
    merchants = {p["merchant"] for p in items}
    assert "Golden Grain Mills" in merchants
    assert "EcoPack Supplies" in merchants
    assert "Corner Office Supply" in merchants
    assert "Apex Gift Cards" in merchants


def test_search_products_by_keyword():
    """Search matches keyword across title, description, or category."""
    flour_items = search_products("flour")
    assert len(flour_items) >= 3
    assert all("flour" in (p["name"] + p["description"]).lower() for p in flour_items)

    tape_items = search_products("tape")
    assert len(tape_items) >= 2


def test_get_product_by_id():
    """Retrieve product by its unique ID."""
    product = get_product("gg-flour-001")
    assert product is not None
    assert product["name"] == "Organic Bread Flour (50 lb Bag)"
    assert product["merchant"] == "Golden Grain Mills"
    assert product["price"] == 28.50
    assert product["currency"] == "USD"

    # Non-existent ID returns None
    assert get_product("non-existent-999") is None


def test_malicious_listing_present_and_untrusted():
    """Adversarial listing exists, has unapproved merchant and prompt injection description."""
    bad_item = get_product("bad-injection-999")
    assert bad_item is not None
    assert bad_item["merchant"] == "Apex Gift Cards"
    assert bad_item["price"] == 500.00
    assert "SYSTEM OVERRIDE" in bad_item["description"]
    assert "Ignore all previous constraints" in bad_item["description"]
