#!/bin/bash -e

debian_release() {
  cat /etc/debian_version | cut -d '.' -f1
}

debian_variant() {
  echo $( board_id ).debian.$( debian_release ).$( getconf LONG_BIT )-bit
}

precompiled_dir() {
  local root="$1"
  local exact="${root}/$( debian_variant )"
  if [ -d "${exact}" ]; then
    echo "${exact}"
    return
  fi

  local current_ver bit board prefix suffix
  current_ver=$( debian_release )
  bit=$( getconf LONG_BIT )-bit
  board=$( board_id )
  prefix="${board}.debian."
  suffix=".${bit}"

  local best_le_ver=-1 best_le_dir=""
  local best_gt_ver=-1 best_gt_dir=""
  local d name ver
  if [ -d "${root}" ]; then
    for d in "${root}"/${prefix}*${suffix}; do
      [ -d "$d" ] || continue
      name="${d##*/}"
      ver="${name#${prefix}}"
      ver="${ver%${suffix}}"
      case "$ver" in
        ''|*[!0-9]*) continue;;
      esac
      if [ "$ver" -le "$current_ver" ]; then
        if [ "$ver" -gt "$best_le_ver" ]; then
          best_le_ver="$ver"; best_le_dir="$d"
        fi
      else
        if [ "$best_gt_ver" -lt 0 ] || [ "$ver" -lt "$best_gt_ver" ]; then
          best_gt_ver="$ver"; best_gt_dir="$d"
        fi
      fi
    done
  fi

  if [ -n "${best_le_dir}" ]; then
    echo "${best_le_dir}"
  elif [ -n "${best_gt_dir}" ]; then
    echo "${best_gt_dir}"
  else
    echo "${exact}"
  fi
}

board_id() {
    local model_file="/sys/firmware/devicetree/base/model"

    if [ -f "$model_file" ]; then
        if grep "Raspberry" $model_file >/dev/null; then
            echo "rpi"
        else
            echo "NA"
        fi
    else
        echo "NA"
    fi
}
