from itertools import combinations
from math import factorial


def compute_shapley(scores, weights):
    players = list(weights)
    n = len(players)

    def v(coalition):
        return sum(weights[p] * scores[p] for p in coalition)

    phi = {}
    for i in players:
        others = [p for p in players if p != i]
        total = 0.0
        for size in range(n):
            coef = factorial(size) * factorial(n - size - 1) / factorial(n)
            for S in combinations(others, size):
                total += coef * (v(S + (i,)) - v(S))
        phi[i] = total
    return phi


def influence_shares(phi):
    positive = {k: max(v, 0.0) for k, v in phi.items()}
    total = sum(positive.values())
    if total <= 0:
        return {k: 0.0 for k in phi}
    shares = {k: round(100.0 * v / total, 1) for k, v in positive.items()}

    if shares:
        largest = max(shares, key=shares.get)
        shares[largest] = round(shares[largest] + (100.0 - sum(shares.values())), 1)
    return shares
