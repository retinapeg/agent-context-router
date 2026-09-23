---
id: idea-recipe-scaler
title: "Recipe scaler"
created: 2026-08-15T09:00:00+01:00
updated: 2026-08-15T09:00:00+01:00
provenance: synthetic eval fixture
---
# Recipe scaler

## Goal
Scale a recipe to any number of servings and convert cups, grams and ounces.

## Current state
- Parser handles fractions like 1 1/2.
- Density table covers 40 ingredients.

## Next action
Handle ingredients with no density entry by asking for weight (marker RECIPE-08).
