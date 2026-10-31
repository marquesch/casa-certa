"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { defaultState, matches, compare } = require("../../casa_certa/web/static/filters.js");

const card = (overrides = {}) => ({
  city: "Alfa",
  rating: 4.5,
  reviews: 10,
  beachM: 100,
  prices: { 9: 40000, 13: 30000 },
  met: [0, 1],
  ...overrides,
});
const state = (overrides = {}) => ({ ...defaultState(13), ...overrides });

test("the default state matches everything", () => {
  assert.equal(matches(card(), state()), true);
  assert.equal(matches(card({ prices: {}, beachM: null, city: null }), state()), true);
});

test("price bounds are in reais per person at the selected size", () => {
  assert.equal(matches(card(), state({ minPrice: 300 })), true);
  assert.equal(matches(card(), state({ minPrice: 301 })), false);
  assert.equal(matches(card(), state({ maxPrice: 300 })), true);
  assert.equal(matches(card(), state({ maxPrice: 299 })), false);
  assert.equal(matches(card(), state({ size: 9, maxPrice: 350 })), false); // 400 at 9 people
});

test("a house without a price for the size is hidden only while a price bound is set", () => {
  const unpriced = card({ prices: { 9: 40000, 13: null } });
  assert.equal(matches(unpriced, state()), true);
  assert.equal(matches(unpriced, state({ minPrice: 0 })), false);
  assert.equal(matches(unpriced, state({ maxPrice: 1000 })), false);
});

test("cities: none selected means all, otherwise the city must be selected", () => {
  assert.equal(matches(card(), state({ cities: ["Beta"] })), false);
  assert.equal(matches(card(), state({ cities: ["Beta", "Alfa"] })), true);
  assert.equal(matches(card({ city: null }), state({ cities: ["Alfa"] })), false);
});

test("beach distance: up to a bound, and unknown only matches 'any'", () => {
  assert.equal(matches(card({ beachM: 100 }), state({ maxBeachM: 100 })), true);
  assert.equal(matches(card({ beachM: 101 }), state({ maxBeachM: 100 })), false);
  assert.equal(matches(card({ beachM: 0 }), state({ maxBeachM: 100 })), true);
  assert.equal(matches(card({ beachM: null }), state({ maxBeachM: 1000 })), false);
  assert.equal(matches(card({ beachM: null }), state()), true);
});

test("aspect checkboxes require every checked aspect to be met", () => {
  assert.equal(matches(card({ met: [0, 2] }), state({ aspects: [0] })), true);
  assert.equal(matches(card({ met: [0, 2] }), state({ aspects: [0, 1] })), false);
  assert.equal(matches(card({ met: [] }), state({ aspects: [] })), true);
});

test("sort by price: cheapest first at the selected size, unpriced last, then best rated", () => {
  const cheap = card({ prices: { 13: 20000 } });
  const dear = card({ prices: { 13: 50000 } });
  const none = card({ prices: { 13: null } });
  const tieHigh = card({ prices: { 13: 20000 }, rating: 4.9 });
  const sorted = [none, dear, cheap, tieHigh].sort((a, b) => compare(a, b, state()));
  assert.deepEqual(sorted, [tieHigh, cheap, dear, none]);
  const at9 = [card({ prices: { 9: 90000 } }), card({ prices: { 9: 10000 } })].sort((a, b) =>
    compare(a, b, state({ size: 9 })),
  );
  assert.equal(at9[0].prices[9], 10000);
});

test("sort by rating: best first, unrated last, then cheapest", () => {
  const best = card({ rating: 4.9 });
  const good = card({ rating: 4.5, prices: { 13: 10000 } });
  const good2 = card({ rating: 4.5, prices: { 13: 90000 } });
  const unrated = card({ rating: null });
  const sorted = [unrated, good2, best, good].sort((a, b) => compare(a, b, state({ sort: "rating" })));
  assert.deepEqual(sorted, [best, good, good2, unrated]);
});

test("sort by beach: nearest first, unknown last, then cheapest", () => {
  const front = card({ beachM: 0 });
  const near = card({ beachM: 50, prices: { 13: 10000 } });
  const near2 = card({ beachM: 50, prices: { 13: 90000 } });
  const unknown = card({ beachM: null });
  const sorted = [unknown, near2, front, near].sort((a, b) => compare(a, b, state({ sort: "beach" })));
  assert.deepEqual(sorted, [front, near, near2, unknown]);
});
