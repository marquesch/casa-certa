"""Text used in the generated spreadsheet, per language."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Labels:
    comparison_sheet: str
    details_sheet: str
    screened_out_sheet: str
    id: str
    link: str
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
    site_title: str
    trip_line: str
    updated: str
    section_complete: str
    section_incomplete: str
    count_of: str
    no_matches: str
    filters: str
    group_size: str
    price_range: str
    price_min: str
    price_max: str
    city: str
    beach_distance: str
    beach_any: str
    beach_up_to: str
    aspects_filter: str
    sort_by: str
    sort_price: str
    sort_rating: str
    sort_beach: str
    clear_filters: str
    details: str
    open_listing: str
    beach: str
    estimated: str
    no_analysis: str
    missing: str
    must_have: str
    status_met: str
    status_partial: str
    status_unmet: str
    status_unknown: str
    decimal_separator: str
    unlock_title: str
    passphrase: str
    unlock_button: str
    remember: str
    wrong_passphrase: str
    unlocking: str


PT_BR = Labels(
    comparison_sheet="Comparativo",
    details_sheet="Detalhes",
    screened_out_sheet="Descartadas",
    id="Id",
    link="Link",
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
    site_title="Casa Certa",
    trip_line="{dates} · {min} a {max} pessoas",
    updated="Atualizado em {date}",
    section_complete="Têm todos os essenciais",
    section_incomplete="Faltam essenciais",
    count_of="de",
    no_matches="Nenhuma casa com esses filtros.",
    filters="Filtros",
    group_size="Pessoas",
    price_range="Preço por pessoa (R$)",
    price_min="mínimo",
    price_max="máximo",
    city="Cidade",
    beach_distance="Distância da praia",
    beach_any="qualquer",
    beach_up_to="até {distance}",
    aspects_filter="Exigir",
    sort_by="Ordenar por",
    sort_price="Preço por pessoa",
    sort_rating="Nota",
    sort_beach="Distância da praia",
    clear_filters="Limpar filtros",
    details="Detalhes",
    open_listing="Abrir no Airbnb",
    beach="Praia",
    estimated="estimado",
    no_analysis="sem análise",
    missing="Falta",
    must_have="essencial",
    status_met="atende",
    status_partial="parcial",
    status_unmet="não atende",
    status_unknown="não confirmado",
    decimal_separator=",",
    unlock_title="Digite a senha para ver as casas",
    passphrase="Senha",
    unlock_button="Abrir",
    remember="Lembrar neste aparelho",
    wrong_passphrase="Senha incorreta",
    unlocking="Abrindo…",
)

EN = Labels(
    comparison_sheet="Comparison",
    details_sheet="Details",
    screened_out_sheet="Screened out",
    id="Id",
    link="Link",
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
    site_title="Casa Certa",
    trip_line="{dates} · {min} to {max} people",
    updated="Updated {date}",
    section_complete="Have every must-have",
    section_incomplete="Missing must-haves",
    count_of="of",
    no_matches="No house matches these filters.",
    filters="Filters",
    group_size="People",
    price_range="Price per person (R$)",
    price_min="minimum",
    price_max="maximum",
    city="City",
    beach_distance="Distance to the beach",
    beach_any="any",
    beach_up_to="up to {distance}",
    aspects_filter="Require",
    sort_by="Sort by",
    sort_price="Price per person",
    sort_rating="Rating",
    sort_beach="Distance to the beach",
    clear_filters="Clear filters",
    details="Details",
    open_listing="Open on Airbnb",
    beach="Beach",
    estimated="estimated",
    no_analysis="not analysed",
    missing="Missing",
    must_have="must-have",
    status_met="met",
    status_partial="partial",
    status_unmet="not met",
    status_unknown="unconfirmed",
    decimal_separator=".",
    unlock_title="Enter the passphrase to see the houses",
    passphrase="Passphrase",
    unlock_button="Open",
    remember="Remember on this device",
    wrong_passphrase="Wrong passphrase",
    unlocking="Opening…",
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
