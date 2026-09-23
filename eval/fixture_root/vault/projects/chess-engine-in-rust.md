---
id: project-chess-engine-in-rust
title: "Chess engine in Rust"
created: 2026-08-15T09:00:00+01:00
updated: 2026-08-15T09:00:00+01:00
provenance: synthetic eval fixture
---
# Chess engine in Rust

## Objective and checkpoint
A UCI engine that beats a 1500-rated bot.

## Current state
- Move generation passes perft to depth 5.
- Search has no transposition table.

## Next action
Add a transposition table and measure nodes per second (marker CHESS-61).
