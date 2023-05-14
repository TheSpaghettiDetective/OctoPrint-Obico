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
  variant=$(getconf LONG_BIT 2>/dev/null)
  if [ -z "${variant}" ]; then
    variant=$(uname -m)
  else
    variant="${variant}bit"
  fi
  echo $( debian_release ).${variant}
}
