"use strict";

// Pure filtering and sorting for the house cards. A card is
// { city, rating, reviews, beachM, pending (not analysed yet), prices: { <people>: cents per person | null }, met: [aspect indexes] }.
// The state is { size, minPrice, maxPrice (reais per person), cities, maxBeachM, aspects, sort }.
const filters = (() => {
  const num = (value) => (typeof value === "number" && !Number.isNaN(value) ? value : null);

  function defaultState(size) {
    return { size, minPrice: null, maxPrice: null, cities: [], maxBeachM: null, aspects: [], sort: "price" };
  }

  function priceOf(card, state) {
    return num(card.prices[state.size]);
  }

  function matches(card, state) {
    const price = priceOf(card, state);
    if (state.minPrice !== null && !(price !== null && price >= state.minPrice * 100)) return false;
    if (state.maxPrice !== null && !(price !== null && price <= state.maxPrice * 100)) return false;
    if (state.cities.length > 0 && !state.cities.includes(card.city)) return false;
    const beach = num(card.beachM);
    if (state.maxBeachM !== null && !(beach !== null && beach <= state.maxBeachM)) return false;
    return state.aspects.every((index) => card.met.includes(index));
  }

  // direction 1: smaller first, -1: larger first; missing values always go last.
  function byValue(x, y, direction) {
    const a = num(x);
    const b = num(y);
    if (a === null && b === null) return 0;
    if (a === null) return 1;
    if (b === null) return -1;
    return direction * (a - b);
  }

  function compare(a, b, state) {
    if (a.pending !== b.pending) return a.pending ? 1 : -1; // unanalysed houses go last in every sort mode
    const price = byValue(priceOf(a, state), priceOf(b, state), 1);
    const rating = byValue(a.rating, b.rating, -1);
    if (state.sort === "rating") return rating || price;
    if (state.sort === "beach") return byValue(a.beachM, b.beachM, 1) || price;
    return price || rating;
  }

  return { defaultState, matches, compare };
})();

if (typeof module !== "undefined") module.exports = filters;
