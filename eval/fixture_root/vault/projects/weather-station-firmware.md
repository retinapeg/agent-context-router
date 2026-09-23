---
id: project-weather-station-firmware
title: "Weather station firmware"
created: 2026-08-15T09:00:00+01:00
updated: 2026-08-15T09:00:00+01:00
provenance: synthetic eval fixture
---
# Weather station firmware

## Objective and checkpoint
Microcontroller firmware that logs temperature, humidity and wind every minute.

## Current state
- Sensors read correctly.
- Deep sleep drains the battery faster than expected.

## Next action
Measure current draw in deep sleep with the radio off (marker WX-19).
