#!/bin/bash -x

while true; do
  sleep 5
  resMem=$(ps -o rss= $(pgrep gst-launch-1.0))
  if [ -z "$resMem" ]; then
    continue
  fi

  if [ $resMem -gt $1 ]; then
      kill $(pgrep gst-launch-1.0)
  fi
done
