# Troubleshooting and servo recovery

## Incident 2026-11-25: settings area wiped after the RoboNine flash

**Servo:** STS3215, ID 1, 1 Mbaud, on COM7 (CH343 adapter), 12 V supply.

### Timeline

| Time | What happened |
|---|---|
| 15:50 | The servo read 0.0 V and 10 °C with a voltage alarm, on Feetech firmware 3.10. The cause was not found, possibly the supply. By 16:02 it read normally. |
| 16:02 | The bench backed up the settings (11.9 V, limits 1727..3164, no alarm). Then it flashed RoboNine v9 (`local-e44c0e6`). The flash report says **verified**: 403/403 blocks, `ARMC 9.0`, settings unchanged, 11.7 V afterwards. |
| 16:02–16:21 | FT SCServo Debug was used, including its **Upgrade** tab, which reported "No firmware detected". |
| 16:21 | A second bench upload stopped at the settings backup with `status alarm 0x01`. Nothing was written. |
| 16:28 | In the bench the servo read 0 V, 10 °C, position 15, model 9.255 and limits 65535. It was labelled "Feetech". **Set centre** wrote a wrong offset (1979). |
| 16:40 | The servo answered nothing at any ID or baud, but its bootloader answered right after a power-cycle. So the firmware no longer ran. |
| 16:45 | First `flash.py recover` with the factory 3.10 image. It failed at block 67/263, probably because power was interrupted. |
| 16:47 | Second recovery: 263/263 blocks. Its own check failed with `status alarm 0x01`. |
| 16:50 | Reading the registers showed the **settings area erased** (0xFF). The 40 settings registers were restored from the 16:02 backup, read back and locked. After a reboot: 12.0 V, 32 °C, model 9.3, limits 1727..3164, no alarm. |

### Root cause

The persistent settings registers (4–39 and 78–86) were erased to **0xFF**. Only the ID, the baud rate and the bad offset
were left. With minimum and maximum voltage both at 25.5 V, the voltage alarm never clears. The servo then refuses to
work and returns meaningless sensor values (0 V, 10 °C, position 0–15).

**What erased the settings is not proven.** The flash report shows they were intact right after the RoboNine flash.
The most likely cause is FT SCServo Debug's **Upgrade** tab run against the RoboNine firmware: it erases memory and then
fails with "No firmware detected". This would also explain why the firmware itself was gone afterwards. The RoboNine
firmware cannot be ruled out.

### Lesson: 0 V does not always mean the supply is off

The first diagnosis, "no motor power", was wrong. The supply was fine. **0.0 V, 10 °C and a voltage alarm while the
supply is on** is the sign of a wiped settings area.

## How to recognise each state

| Symptom | Firmware state | Settings state | Fix |
|---|---|---|---|
| About 12 V, model 9.3, header shows the firmware | OK | OK | none |
| 0 V, 10 °C, alarm 0x01, **model 9.255, limits 65535**, header "Settings wiped" | runs | **wiped** | B |
| 0 V, alarm, but model 9.3 and normal limits | runs | OK | check the 12 V supply, the plug and the current limit |
| No answer at any ID or baud. Bootloader answers only right after power-on | **missing** | unknown | A, then B if needed |

## A. Firmware missing: recovery

1. Keep only this servo on the bus. Secure the 12 V connection. Close FT SCServo Debug and the bench.
2. In `firmware/STS32XX-Firmware/STS32XX-Firmware/tools/flash`, run:
   `python flash.py recover --manifest factory/release.json --port COM7 --id 1`
   For RoboNine, use `--manifest releases/custom/release.json` instead.
3. When it says *Power-cycle the isolated servo NOW*: switch the 12 V off, wait 3 s, switch it on **once**. Then don't
   touch anything until it finishes (about 45 s for factory 3.10).
4. If it stops at a block number, power was lost. Secure the connection and run it again. The bootloader is never
   overwritten.
5. A failed check at the end with `status alarm 0x01` usually means the settings are wiped. Go to **B**.

## B. Settings wiped: restore from a backup

The bench saves a snapshot of the servo's settings before every firmware upload, in
`firmware/.../backups/settings/`. Each flash report also keeps one.

1. Bench → **Change firmware** → **Restore servo settings**.
2. Pick the newest healthy backup **of this servo**. The list shows model, limits and voltage limits, and wiped
   snapshots are never listed.
3. Type `RESTORE` and click **Restore settings**. Torque goes off, and each differing register is written and read
   back, voltage limits first. Settings are locked again, the servo restarts, and the result is checked.
4. Redo **3 Centre** and **4 Positions**.

RoboNine's `settings.py restore` cannot do this. It refuses while the alarm is active, and a wiped settings area keeps
the alarm active.

## Rules from now on

* Switch firmware **only from the bench** (Change firmware). **Never use FT SCServo Debug's Upgrade tab** while the
  servo runs RoboNine firmware.
* After every upload, check the Firmware tab: about 12 V, model 9.3, the expected firmware (`ARMC 9.0` for RoboNine).
  Do this before centring or running.
* Never centre or mark positions while the header is red ("Settings wiped" or "No motor power?"). The bench now
  refuses this (v1.4.2+).
* During any recovery: one power-cycle, a secure 12 V connection, hands off until it finishes.
* Keep `firmware/.../backups/` and don't delete old snapshots.
