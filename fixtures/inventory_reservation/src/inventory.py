"""Low-level stock validation helpers."""


def validated_quantity(line):
    quantity = line.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    return quantity


def validated_sku(line):
    sku = line.get("sku")
    if not isinstance(sku, str) or not sku:
        raise ValueError("sku is required")
    return sku
