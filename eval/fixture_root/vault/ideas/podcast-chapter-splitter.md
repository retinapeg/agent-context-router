---
id: idea-podcast-chapter-splitter
title: "Podcast chapter splitter"
created: 2026-08-15T09:00:00+01:00
updated: 2026-08-15T09:00:00+01:00
provenance: synthetic eval fixture
---
# Podcast chapter splitter

## Goal
Turn a two-hour episode into chapters with titles, using the transcript.

## Current state
- Transcripts from a speech-to-text model.
- Topic boundaries picked by hand for one episode.

## Next action
Try embedding-similarity boundaries against the hand-labelled episode (marker POD-29).
