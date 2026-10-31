"use strict";

(() => {
  const money = new Intl.NumberFormat(document.documentElement.lang || "pt-BR", { style: "currency", currency: "BRL" });
  const panel = document.getElementById("filters");
  const size = document.getElementById("f-size");
  const min = document.getElementById("f-min");
  const max = document.getElementById("f-max");
  const beach = document.getElementById("f-beach");
  const sort = document.getElementById("f-sort");
  const clear = document.getElementById("f-clear");
  const sections = [...document.querySelectorAll(".group")];

  const number = (value) => {
    const text = String(value).trim().replace(",", ".");
    return text === "" || Number.isNaN(Number(text)) ? null : Number(text);
  };

  function readCard(element) {
    const prices = {};
    for (const [key, value] of Object.entries(element.dataset)) {
      const match = /^price(\d+)$/.exec(key);
      if (match) prices[Number(match[1])] = number(value);
    }
    return {
      city: element.dataset.city || null,
      rating: number(element.dataset.rating ?? ""),
      reviews: number(element.dataset.reviews ?? ""),
      beachM: number(element.dataset.beach ?? ""),
      prices,
      met: element.dataset.met ? element.dataset.met.split(" ").map(Number) : [],
    };
  }

  const cards = [...document.querySelectorAll(".card")].map((element) => ({ element, data: readCard(element) }));
  const checked = (name) => [...document.querySelectorAll(`input[name="${name}"]:checked`)];

  function readState() {
    return {
      size: Number(size.value),
      minPrice: number(min.value),
      maxPrice: number(max.value),
      cities: checked("city").map((input) => input.value),
      maxBeachM: number(beach.value),
      aspects: checked("aspect").map((input) => Number(input.value)),
      sort: sort.value,
    };
  }

  function apply() {
    const state = readState();
    for (const section of sections) {
      const inSection = cards.filter((card) => section.contains(card.element));
      const visible = inSection
        .filter((card) => filters.matches(card.data, state))
        .sort((a, b) => filters.compare(a.data, b.data, state));
      const container = section.querySelector(".cards");
      for (const card of inSection) card.element.hidden = !visible.includes(card);
      for (const card of visible) container.append(card.element);
      const count = section.querySelector(".count");
      count.textContent = `(${visible.length} ${count.dataset.of} ${inSection.length})`;
      section.querySelector(".empty").hidden = visible.length > 0;
    }
    for (const card of cards) {
      const label = card.element.querySelector(".price-now");
      if (!label) continue;
      const cents = card.data.prices[state.size];
      label.textContent = typeof cents === "number" ? money.format(cents / 100) : label.dataset.na;
    }
  }

  function reset() {
    size.value = size.dataset.default;
    min.value = "";
    max.value = "";
    beach.value = "";
    sort.value = "price";
    for (const input of document.querySelectorAll("#filters input[type=checkbox]")) input.checked = false;
    apply();
  }

  panel.addEventListener("input", apply);
  panel.addEventListener("change", apply);
  clear.addEventListener("click", reset);
  if (window.matchMedia("(min-width: 48em)").matches) panel.open = true;
  apply();
})();
