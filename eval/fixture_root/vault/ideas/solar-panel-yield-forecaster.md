---
id: idea-solar-panel-yield-forecaster
title: "Solar panel yield forecaster"
created: 2026-08-15T09:00:00+01:00
updated: 2026-08-15T09:00:00+01:00
provenance: synthetic eval fixture
---
# Solar panel yield forecaster

## Goal
Predict next-day generation from a weather forecast and past inverter data.

## Current state
- One year of inverter data exported.
- Baseline: yesterday's output.

## Next action
Fit a regression on cloud cover and compare with the persistence baseline (marker SOLAR-71).
