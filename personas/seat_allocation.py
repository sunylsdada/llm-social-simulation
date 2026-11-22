"""席位分配工具（最大余数法 / Hamilton 法）。"""


def largest_remainder(total: int, weights: list, floor: int = 1) -> list:
    """按 ``weights`` 比例分配恰好 ``total`` 个席位。

    每项尽量至少得到 ``floor`` 个席位；如果 ``total`` 小于项目数，
    会自动降低 ``floor``。
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
