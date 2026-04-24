#!/usr/bin/env bash
# Corre todos los check_m*.sh y reporta resumen.
DIR="$(dirname "$0")"
TOTAL=0; PASSED=0
for script in "$DIR"/check_m*.sh; do
  TOTAL=$((TOTAL+1))
  echo ""
  echo "=========================================="
  echo "▶ $(basename $script)"
  echo "=========================================="
  if bash "$script"; then
    PASSED=$((PASSED+1))
  fi
done
echo ""
echo "=========================================="
echo "RESUMEN: $PASSED/$TOTAL módulos pasan"
echo "=========================================="
