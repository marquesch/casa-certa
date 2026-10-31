from sqlalchemy.ext.asyncio import AsyncSession

from casa_certa.db.models import House
from casa_certa.enums import AspectStatus, EvidenceSource
from casa_certa.i18n import labels_for
from casa_certa.web.model import build_site
from casa_certa.web.render import format_distance, render_page
from tests.fakes import make_trip
from tests.test_xlsx import analysed_house


def page(house: House, **trip_overrides: object) -> str:
    trip = make_trip(**trip_overrides)
    return render_page(build_site([house], trip), trip, labels_for(trip.language))


def test_distances_are_shown_in_meters_or_kilometers() -> None:
    labels = labels_for("pt-BR")
    assert format_distance(30, labels) == "30 m"
    assert format_distance(1000, labels) == "1 km"
    assert format_distance(1500, labels) == "1,5 km"
    assert format_distance(1500, labels_for("en")) == "1.5 km"


async def test_page_has_header_sections_filters_and_card_data(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)

    html = page(house)

    assert '<html lang="pt-BR">' in html
    assert 'name="robots" content="noindex"' in html
    assert "05/12\u201309/12 · 9 a 13 pessoas" in html
    assert "Atualizado em" in html
    assert 'id="complete"' in html and 'id="incomplete"' in html
    assert "Têm todos os essenciais" in html and "Faltam essenciais" in html
    assert 'class="count" data-of="de"' in html  # site.js reads the word from here when it updates the counts
    assert "Casa do Albatroz" in html
    assert 'data-city="Matinhos"' in html
    assert 'data-price9="40834"' in html
    assert 'data-price13=""' in html
    assert 'data-met="0 1"' in html and 'data-pending=""' in html
    assert 'id="f-size"' in html and 'id="f-min"' in html and 'id="f-beach"' in html and 'id="f-sort"' in html
    assert 'name="city" value="Matinhos"' in html
    assert 'name="aspect" value="0"' in html and 'name="aspect" value="1"' in html
    assert "★ Perto da praia" in html
    assert "https://www.airbnb.com.br/rooms/46397403" in html
    # listing links open in a new tab and never hand the page (or its address) to Airbnb
    assert html.count('href="https://www.airbnb.com.br/rooms/46397403" target="_blank" rel="noopener noreferrer"') == 2


async def test_a_house_missing_a_must_have_is_listed_under_the_second_section(
    session: AsyncSession, stored_house: House
) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.verdicts[0].status = AspectStatus.UNMET

    html = page(house)

    second = html[html.index('id="incomplete"') :]
    assert "Casa do Albatroz" in second
    assert "Falta" in second and "não atende" in second
    assert "Casa do Albatroz" not in html[: html.index('id="incomplete"')].split('id="complete"')[1]


async def test_beach_distance_is_labelled_as_an_estimate_with_its_quote(
    session: AsyncSession, stored_house: House
) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.beach_distance_m = 30
    house.analysis.beach_distance_source = EvidenceSource.REVIEW
    house.analysis.beach_distance_quote = "a 30 metros do mar"

    html = page(house)

    assert "30 m" in html and "estimado" in html and "a 30 metros do mar" in html
    assert 'data-beach="30"' in html


async def test_unknown_values_are_shown_as_not_available(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert 'data-beach=""' in page(house)
    assert "n/d" in page(house)


async def test_untrusted_text_is_escaped(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    assert house.analysis is not None
    house.analysis.verdicts[0].evidence[0].quote = "<script>alert(1)</script>"
    house.extra = "<b>x</b>"

    html = page(house)

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)" not in html
    assert "&lt;b&gt;x&lt;/b&gt;" in html


async def test_details_have_the_summary_pros_cons_evidence_and_the_price_table(
    session: AsyncSession, stored_house: House
) -> None:
    house = await analysed_house(session, stored_house)

    html = page(house)

    for text in (
        "Boa casa para o grupo",
        "Piscina privativa",
        "Sem ar-condicionado",
        "100 metros do mar",
        "R$ 3.675,00",
    ):
        assert text in html
    assert "O dono confirmou que a praia fica a 3 minutos a pé." in html


async def test_the_page_can_be_rendered_in_english(session: AsyncSession, stored_house: House) -> None:
    house = await analysed_house(session, stored_house)
    html = page(house, language="en")
    assert "Have every must-have" in html and "Missing must-haves" in html


async def test_an_unanalysed_card_is_marked_pending_for_the_script(session: AsyncSession, stored_house: House) -> None:
    html = page(stored_house)
    assert 'data-pending="1"' in html
