#!/bin/bash -e

is_raspberry_pi() {
  if grep 'Raspberry Pi' /sys/firmware/devicetree/base/model >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

