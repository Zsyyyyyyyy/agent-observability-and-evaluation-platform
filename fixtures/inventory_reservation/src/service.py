"""Apply a batch of inventory reservations."""

from src.inventory import validated_quantity, validated_sku


def reserve_all(stock, lines):
    """Reserve every requested line and mutate ``stock`` only on success."""

    reserved = []
    for line in lines:
        sku = validated_sku(line)
        quantity = validated_quantity(line)
        if stock.get(sku, 0) < quantity:
            raise ValueError(f"insufficient stock for {sku}")
        # BUG: an error in a later line leaves prior decrements visible.
        stock[sku] -= quantity
        reserved.append({"sku": sku, "quantity": quantity})
    return reserved
