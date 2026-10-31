"""Text used in the generated spreadsheet, per language."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Labels:
    comparison_sheet: str
    details_sheet: str
    house: str
    rating: str
    reviews: str
    dealbreakers: str
    per_person_at: str
    price_basis: str
    original_price: str
    summary: str
    pros: str
    cons: str
    aspect: str
    status: str
    reason: str
    evidence: str
    confidence: str
    extra: str
    prices: str
    people: str
    total: str
    per_person: str
    not_available: str
    failed: str
    source_listing: str
    source_review: str
    source_extra: str


PT_BR = Labels(
    comparison_sheet="Comparativo",
    details_sheet="Detalhes",
    house="Casa",
    rating="Nota",
    reviews="Avaliações",
    dealbreakers="Eliminatórios",
    per_person_at="Por pessoa ({people})",
    price_basis="{dates}, hóspedes pagantes; bebês não contam",
    original_price="Preço original",
    summary="Resumo",
    pros="Pontos positivos",
    cons="Pontos negativos",
    aspect="Aspecto",
    status="Situação",
    reason="Motivo",
    evidence="Evidências",
    confidence="Confiança",
    extra="Notas extras",
    prices="Preços",
    people="Pessoas",
    total="Total",
    per_person="Por pessoa",
    not_available="n/d",
    failed="Falhou",
    source_listing="anúncio",
    source_review="avaliação",
    source_extra="nota extra",
)

EN = Labels(
    comparison_sheet="Comparison",
    details_sheet="Details",
    house="House",
    rating="Rating",
    reviews="Reviews",
    dealbreakers="Dealbreakers",
    per_person_at="Per person ({people})",
    price_basis="{dates}, paying guests; infants do not count",
    original_price="Original price",
    summary="Summary",
    pros="Pros",
    cons="Cons",
    aspect="Aspect",
    status="Status",
    reason="Reason",
    evidence="Evidence",
    confidence="Confidence",
    extra="Extra notes",
    prices="Prices",
    people="People",
    total="Total",
    per_person="Per person",
    not_available="n/a",
    failed="Failed",
    source_listing="listing",
    source_review="review",
    source_extra="extra note",
)

_LABELS = {"pt-BR": PT_BR, "en": EN}


def labels_for(language: str) -> Labels:
    """Exact match first, then by language prefix ('pt' -> 'pt-BR'), then English."""
    if language in _LABELS:
        return _LABELS[language]
    prefix = language.split("-")[0].lower()
    for key, labels in _LABELS.items():
        if key.split("-")[0].lower() == prefix:
            return labels
    return EN
