"""Seat allocation helpers (largest remainder / Hamilton method)."""


def largest_remainder(total: int, weights: list, floor: int = 1) -> list:
    """Allocate exactly ``total`` seats proportionally to ``weights``.

    Each item gets at least ``floor`` where possible. If ``total`` is smaller
    than the number of items, ``floor`` is lowered automatically.
    """
    k = len(weights)
    if k == 0:
        return []
    floor = max(0, min(floor, total // k))

    weight_sum = sum(weights)
    if weight_sum == 0:
        alloc = [floor] * k
        leftover = total - sum(alloc)
        for i in range(leftover):
            alloc[i % k] += 1
        return alloc

    base = [floor] * k
    rem = total - floor * k
    quotas = [rem * w / weight_sum for w in weights]
    alloc = [base[i] + int(quotas[i]) for i in range(k)]
    leftover = total - sum(alloc)
    order = sorted(range(k), key=lambda i: (-(quotas[i] - int(quotas[i])),
                                            -weights[i], i))
    for i in order[:leftover]:
        alloc[i] += 1
    assert sum(alloc) == total
    return alloc
