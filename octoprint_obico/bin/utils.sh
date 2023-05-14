#!/bin/bash -e

is_raspberry_pi() {
  if grep 'Raspberry Pi' /sys/firmware/devicetree/base/model >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

debian_release() {
  cat /etc/debian_version | cut -d '.' -f1
}

debian_variant() {
  echo $( debian_release ).$( getconf LONG_BIT )-bit
}
