#!/bin/bash -e

BIN_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

_term() { 
  kill -TERM "$child" 2>/dev/null
}

trap _term SIGTERM

nice $BIN_DIR/ffmpeg $* &

child=$! 
wait "$child"
