#!/bin/bash

while true; do
  sleep 5
  gst_line=$(top -b -n 1 | grep gst-launch-1.0 | sed 's/ \+/:/g')
  if [ -z "$gst_line" ]; then
    continue
  fi

  if [ $(echo $gst_line | cut -d : -f 7) -gt $1 ]; then
      kill $(echo $gst_line | cut -d : -f 2)
  fi
done
