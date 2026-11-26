---
name: servo-recovery
description: Recover an STS3215 that shows 0 V / 10 °C / voltage alarm with the supply on, model 9.255, limits 65535, or that stops answering after a firmware flash. Use when the bench header says "Settings wiped" or "NO MOTOR POWER" while the 12 V supply is on, when Connect fails after a flash, or when the user mentions FT SCServo Debug's Upgrade tab.
---

# Servo recovery (from docs/TROUBLESHOOTING.md, incident 2026-11-25)

**0.0 V, 10 °C and a voltage alarm while the supply is on means the settings area was wiped**, not that there is no power. Do not calibrate, flash or run anything until this is clear.

## Recognise the state

| Symptom | Firmware | Settings | Fix |
|---|---|---|---|
| about 12 V, model 9.3, header shows the firmware | OK | OK | none |
| 0 V, 10 °C, alarm 0x01, **model 9.255, limits 65535**, header "Settings wiped" | runs | **wiped** | B |
| 0 V, alarm, but model 9.3 and normal limits | runs | OK | check the 12 V supply, plug, current limit |
| no answer at any ID or baud; bootloader answers only right after power-on | **missing** | unknown | A, then B |

## A. Firmware missing

1. Only this servo on the bus; 12 V connection secure; FT SCServo Debug and the bench closed.
2. In `../../firmware/STS32XX-Firmware/STS32XX-Firmware/tools/flash` run `python flash.py recover --manifest factory/release.json --port COM7 --id 1`
   (RoboNine: `--manifest releases/custom/release.json`).
3. At "Power-cycle the isolated servo NOW": 12 V off, wait 3 s, on **once**, then touch nothing until it finishes (about 45 s for the factory image).
4. If it stops at a block number, power was lost: secure the connection and run again (the bootloader is never overwritten).
5. A failed check at the end with `status alarm 0x01` usually means wiped settings: go to B.

## B. Settings wiped: restore from a backup

The bench saves a settings snapshot before every flash (`../../firmware/.../backups/settings/`). Bench → **Change firmware** → **Restore servo settings** → pick the newest healthy backup of this servo → type `RESTORE`.
Torque goes off, differing registers are written and read back (voltage limits first), settings locked, servo restarts. Then redo **3 Centre** and **4 Positions**.

## Prevention

Switch firmware only from the bench. Never use FT SCServo Debug's Upgrade tab. Keep the servo alone on the bus while flashing and do not touch anything during the flash.
After recovery write an entry in `../../docs/observations/06-incidents-and-lessons.md`.
