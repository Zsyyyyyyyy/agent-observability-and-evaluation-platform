def parse_record(row):
    identifier = row.get("id")
    amount = row.get("amount")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("id is required")
    if not isinstance(amount, int) or amount < 0:
        raise ValueError("amount must be a non-negative integer")
    return {"id": identifier, "amount": amount}
