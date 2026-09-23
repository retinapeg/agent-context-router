---
id: project-sparse-autoencoder-replication
title: "Sparse autoencoder replication"
created: 2026-08-15T09:00:00+01:00
updated: 2026-08-15T09:00:00+01:00
provenance: synthetic eval fixture
---
# Sparse autoencoder replication

## Objective and checkpoint
Replicate a dictionary-learning interpretability result on a two-layer transformer.

## Current state
- Activations cached for 10M tokens.
- First SAE trained; many dead features.

## Next action
Add feature resampling to reduce dead features (marker SAE-38).
