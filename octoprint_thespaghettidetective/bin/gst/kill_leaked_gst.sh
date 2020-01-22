#!/bin/bash -x

while true; do
  sleep 5
  gst_line=$(top -b -n 1 | grep gst-launch-1.0 | sed 's/ \+/:/g')
  echo $gst_line
  if [ -z "$gst_line" ]; then
    continue
  fi

  if [ $(echo $gst_line | cut -d : -f 6) -gt $1 ]; then
      kill $(echo $gst_line | cut -d : -f 1)
  fi
done
