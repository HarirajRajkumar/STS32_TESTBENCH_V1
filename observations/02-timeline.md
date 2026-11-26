# Timeline (times are +05:30)

## 2026-11-25: first measurements on the Feetech factory firmware

| Time | What happened | Evidence |
|---|---|---|
| ~12:00 | Bench `sts3215-repeat-bench` ready (v1.x): T4.1a / T4.2b, Qwen gauge reading, firmware flasher from RoboNine's tools | repo history |
| 12:32 | First "full" run stopped after 1 cycle: no calibration (no centre, no limits, no camera box), target 108 | `data/T4.1a/2026-11-25_123227*` |
| 15:00-15:50 | Many 3-cycle trial runs to get calibration, camera rotation (180°) and reading right | `*_trial` runs |
| 15:17 | First usable run, 100 cycles, target 1757, no wobble: **89 of 97 cycles flagged stalled** (servo stopped 4 counts from the target against a 3-count tolerance); the statistics mix two populations | `…151700…` |
| 15:50 | Servo read 0 V / 10 °C with a voltage alarm on Feetech 3.10 (cause not found; normal again by 16:02) | TROUBLESHOOTING.md |
| 16:02 | Bench backed up the settings and flashed RoboNine v9; flash report "verified" (403/403 blocks, ARMC 9.0) | firmware backups |
| 16:02-16:21 | FT SCServo Debug used, including its **Upgrade** tab ("No firmware detected") | TROUBLESHOOTING.md |
| 16:28-16:50 | Servo dead (0 V, model 9.255, limits 65535); bootloader recovery with the factory image (263/263 blocks); the **settings area had been erased to 0xFF**; restored from the 16:02 backup: 12 V, 32 °C, model 9.3 | 06-incidents |
| 16:52 | Re-centred (2048), limits 1585..3152, positions: away 3162, gauge 1615, target 1615 | `meta.json` calib |
| 17:00-17:22 | **100 cycles**, wobble 10 × 62: no stalls, gauge mean 1.051 mm, sd 0.0101 mm | `…170001…` |
| 17:33-18:33 | 271 cycles (stopped): sd 0.0083 mm, no stalls | `…173304…` |
| 18:34-23:47 | **1,408 cycles in 5 h 13 min** (stopped): sd 0.0167 mm, 3 stalls, a 70-cycle disturbance near cycle 940-1010, drift −0.018 mm per 1,000 cycles | `…183449…` |
| evening | Analysis and plots; first HTML reports; daily report for 2026-11-25 | `docs/test_reports/repeatability/static-reports/` |

## 2026-11-26: RoboNine v9 on the same servo, and tooling

| Time | What happened | Evidence |
|---|---|---|
| 11:30 | Trial on Feetech 3.10 (3 cycles) | `…113046…` |
| 11:31-11:32 | RoboNine v9 downloaded by the bench and flashed (settings backed up first); servo now "RoboNine v9.0 (fw 3.10)" | `servo-1-20261126-113205-before-robonine.json` |
| 11:32 | RL trial (legacy): all 3 arrivals flagged stalled, encoder error 4 counts | `…113245…` |
| 11:33 | RE trial (extension): no stalls, encoder error 0 | `…113330…` |
| 11:34-16:55 | **RE run, 1,305 cycles in 5 h 20 min** (stopped): robust sd 0.0148 mm, plain sd 0.0424 mm (21 outliers), encoder error 0.00 ± 0.08 counts | `…113458…` |
| 17:44 | RE trial with camera index 1, new target 1891, limits 1861..2109 (camera and positions changed) | `…174454…` |
| 17:00-19:30 | Tool development: interactive plotly charts (1.6.0), Hold & PID (1.7.0), temperature-spike guard (1.7.1), report folders and library (1.8.0), T3 push test (1.9.0), Auto test T3.1 (1.10.0); all pushed to GitHub | git log |
| 18:01-18:54 | Trial runs of the new tabs (16 Hold & PID, 4 empty T3, 4 three-cycle trials); **deleted on request** after the tools were checked | 06-incidents |
| 19:30 | RoboNine article added to `software/.../docs/robonine_docs` as the reference | this folder |

## Next steps (not done yet)

1. Auto test (T3.1) on RE (now), then RL, then F, then F2; T4.2b in the same conditions.
2. Decide the load and lever for T3 with the beam; run T3 once.
3. Confirm the beam length and mass; check the gauge zero between conditions.
