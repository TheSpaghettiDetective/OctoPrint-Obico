#!/bin/sh

FFMPEG=$1
TMP_DIR=$2

raspivid -t 0 -n -fps 20 -pf baseline -b 3000000 -w 960 -h 540 -o - | $FFMPEG -re -i - -c:v copy -bsf dump_extra -an -r 20 -f rtp rtp://0.0.0.0:8004?pkt_size=1300 -c:v copy -an -r 20 -f hls -hls_time 2 -hls_list_size 10 -hls_delete_threshold 10 -hls_flags split_by_time+delete_segments+second_level_segment_index -strftime 1 -hls_segment_filename $TMP_DIR/%s-%%d.ts -hls_segment_type mpegts $TMP_DIR/stream.m3u8
