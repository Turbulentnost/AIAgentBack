from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.agents.procurement_manager_agent.schemas import (
    ComparisonWeights,
    QuoteComparison,
    QuoteScore,
    SupplierQuote,
)

_HUNDRED = Decimal("100")
_SCORE_QUANT = Decimal("0.01")


def _round(value: Decimal) -> Decimal:
    return value.quantize(_SCORE_QUANT, rounding=ROUND_HALF_UP)


def compare_quotes(
    quotes: list[SupplierQuote],
    weights: ComparisonWeights | None = None,
) -> QuoteComparison:
    """Score quotes deterministically; higher is better and ties are stable."""
    from datetime import UTC, datetime

    weights = weights or ComparisonWeights()
    eligible = [quote for quote in quotes if all(line.compliant for line in quote.lines)]
    if not eligible:
        return QuoteComparison(weights=weights, scores=[], generated_at=datetime.now(UTC))

    totals = {quote.quote_id: quote.total for quote in eligible}
    min_total = min(totals.values())
    max_delivery = max(
        max((line.delivery_days for line in quote.lines), default=0) for quote in eligible
    )
    denominator = weights.price + weights.delivery + weights.quality + weights.risk
    scores: list[QuoteScore] = []

    for quote in eligible:
        total = totals[quote.quote_id]
        price_score = _HUNDRED if total == 0 else _HUNDRED * min_total / total
        delivery_days = max((line.delivery_days for line in quote.lines), default=0)
        delivery_score = (
            _HUNDRED
            if max_delivery == 0
            else _HUNDRED * Decimal(max_delivery - delivery_days) / Decimal(max_delivery)
        )
        risk_score = _HUNDRED - quote.risk_score
        final = (
            price_score * weights.price
            + delivery_score * weights.delivery
            + quote.quality_score * weights.quality
            + risk_score * weights.risk
        ) / denominator
        scores.append(
            QuoteScore(
                quote_id=quote.quote_id,
                supplier_id=quote.supplier_id,
                total=_round(total),
                price_score=_round(price_score),
                delivery_score=_round(delivery_score),
                quality_score=_round(quote.quality_score),
                risk_score=_round(risk_score),
                final_score=_round(final),
            )
        )

    scores.sort(key=lambda item: (-item.final_score, item.total, item.quote_id))
    for rank, score in enumerate(scores, start=1):
        score.rank = rank
    return QuoteComparison(
        weights=weights,
        scores=scores,
        recommended_quote_id=scores[0].quote_id,
        generated_at=datetime.now(UTC),
    )


__all__ = ["compare_quotes"]
