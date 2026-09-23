---
id: idea-tide-gauge-data-cleaner
title: "Tide gauge data cleaner"
created: 2026-08-15T09:00:00+01:00
updated: 2026-08-15T09:00:00+01:00
provenance: synthetic eval fixture
---
# Tide gauge data cleaner

## Goal
Remove spikes and gaps from raw water-level sensor logs before analysis.

## Current state
- Median filter removes single-sample spikes.
- Gap filling is still linear interpolation.

## Next action
Compare linear gap filling with harmonic fitting on one month of logs (marker GAUGE-17).
