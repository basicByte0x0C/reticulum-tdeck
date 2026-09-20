#!/bin/bash
# ============================================================================
# M5LAUNCHER DEVICE ONLY.  READ-ONLY.
# ============================================================================
# Dump the T-Deck's on-flash partition table over JTAG and print it. Nothing is
# written to the device. Run this to CONFIRM the messenger lives at ota_0 /
# 0x1a0000 before flashing: this device runs M5Launcher, whose layout is NOT
# the vanilla one that build_firmware.sh / flash_tdeck.sh / flash_now.sh assume.
#
# Device must be in the JTAG loader (USB PID 0x1001) — e.g. sitting on the
# M5Launcher menu, not running the messenger.
#
#   ! bash tools/launcher_dump_parttable.sh
set -e
OOROOT="$HOME/.espressif/tools/openocd-esp32/v0.12.0-esp32-20260304/openocd-esp32"
OCD="$OOROOT/bin/openocd"
OCDS="$OOROOT/share/openocd/scripts"
GEN="$(cd "$(dirname "$0")" && pwd)/firmware_build/esp-idf/components/partition_table/gen_esp32part.py"
OUT="$(mktemp -t tdeck_pt).bin"

[ -x "$OCD" ] || { echo "ERROR: openocd 20260304 not found at $OCD"; exit 1; }

echo "=== Reading partition table (flash 0x8000, 0xc00 bytes) over JTAG — READ ONLY ==="
"$OCD" -s "$OCDS" -f board/esp32s3-builtin.cfg \
    -c "init" -c "reset halt" \
    -c "flash read_bank 0 $OUT 0x8000 0xc00" \
    -c "shutdown"

echo ""
echo "=== Parsed partition table ==="
python3 "$GEN" "$OUT"
echo ""
echo "=== EXPECT on this M5Launcher device: an app partition (label tdeckf / subtype ota_0)"
echo "    at offset 0x1a0000, app0 (the Launcher) at 0x10000, and vfs at 0x3a0000."
echo "    If tdeckf is NOT at 0x1a0000, STOP and tell Claude — do not flash. ==="
