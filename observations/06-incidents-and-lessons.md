# Incidents and lessons

## 1. Servo settings area wiped after a RoboNine flash (2026-11-25, 16:02-16:50)

* **Symptom:** 0.0 V, 10 °C, voltage alarm 0x01, model 9.255, limits 65535, position 0-15 with the 12 V supply **on**; later no answer at any ID / baud, bootloader only after a power cycle.
* **Cause:** the persistent settings registers (4-39 and 78-86) were erased to 0xFF; with both voltage limits at 25.5 V the alarm never clears. Not proven what erased them. Most likely suspect: **FT SCServo Debug's Upgrade tab** run against the RoboNine firmware ("No firmware detected" after it erased memory). The flash report showed the settings intact right after the RoboNine flash.
* **Fix:** `flash.py recover` with the factory image (263/263 blocks, servo alone on the bus), then the 40 settings registers restored from the backup the bench took before the flash. Result: 12.0 V, 32 °C, model 9.3, limits restored.
* **Rules that came out of it** (also in the bench TROUBLESHOOTING.md and CLAUDE.md):
  1. **0 V with the supply on is a wiped settings area until proven otherwise**, not "no power".
  2. Switch firmware **only from the bench** (*Change firmware*); it backs up the settings first. Never use FT SCServo Debug's Upgrade tab.
  3. Keep only this servo on the bus and the 12 V connection firm while flashing; do not touch anything for the ~45 s of a flash.
  4. Redo **Centre** and **Positions** after every flash.

## 2. Temperature sensor glitches (seen 2026-11-25, acted on 2026-11-26)

Single readings of 150 °C and 76 °C while the median is 36-37 °C stopped a Hold test at "76 °C ≥ 65 °C". Real heating is slow. **Rule:** a temperature limit is acted on only if the high value is confirmed (Hold: 1 s sustained; runs: three more reads over 0.4 s). Reports show the spike; do not treat it as heating. (v1.7.1)

## 3. First full run unusable: 89 of 97 cycles "stalled" (2026-11-25 15:17)

The servo stopped 4 counts from the target; the bench tolerance was 3. Adding the wobble step and re-calibrating (target 1615) removed it in the later runs. **Rule:** if more than 10 % of the first 20 cycles stall, stop and fix the set-up before running 100 cycles. (Open item in the bench NEXT-VERSION notes.)

## 4. A patch deleted 12 routes from the bench (2026-11-26, v1.6.0)

A text replacement meant for the camera-list route also removed the connect / scan / torque / move / calibration / e-stop / limits routes; Connect then answered "not found". They were rebuilt from the compiled `.pyc` of 25 Nov and checked: all 13 functions compile to the same bytecode as the originals. **Rules:** edit one function at a time with exact replacements; keep the code under git (it is: GitHub STS32_TESTBENCH_V1); Google Drive kept no earlier revision of `app.py`.

## 5. Camera switching crashed ("unknown OpenCV error", 2026-11-26)

Releasing a camera while its capture thread was reading it. Fixed: the new camera is opened and checked before the old one is stopped, and the capture thread owns its capture object. A camera that returns only a black picture with a spinner (the third camera, "FINGERS 1080") is flagged in the dropdown.

## 6. A mistaken inference I made, corrected here

On 2026-11-26 I said the RoboNine image on disk was a "local build" because its release label is `local-e44c0e6`. That was **not proven**: the label is the one inside RoboNine's release.json, the file was rewritten at 11:31 (matching a download by the bench) and its SHA-256 is identical to the file read on 2026-11-25. Treat the label as a name, not as evidence of origin. To be certain, download again from the bench and compare the SHA-256.

## 7. Test data deleted on request (2026-11-26)

After the new tabs were checked, 24 trial runs recorded after the v1.7.0 push (16 Hold & PID, 4 empty T3, 4 three-cycle trials) and their reports were deleted by the owner's decision. Google Drive keeps deleted files in its trash for 30 days. The T4.1a runs, including the long Feetech and RoboNine runs, were not touched.
