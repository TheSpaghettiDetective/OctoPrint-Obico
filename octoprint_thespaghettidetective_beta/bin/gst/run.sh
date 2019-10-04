#!/bin/bash -e

GST_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

LD_LIBRARY_PATH=$GST_DIR/lib GST_PLUGIN_SCANNER=$GST_DIR/lib/gstreamer-1.0 GST_PLUGIN_SYSTEM_PATH=$GST_DIR/lib/gstreamer-1.0 GST_PLUGIN_PATH=$GST_DIR/lib/gstreamer-1.0 GST_OMX_CONFIG_DIR=$GST_DIR/etc/xdg $GST_DIR/bin/gst-launch-1.0 -v fdsrc ! h264parse ! rtph264pay config-interval=1 pt=96 ! udpsink host=127.0.0.1 port=8004


#LD_LIBRARY_PATH=$GST_DIR/lib GST_PLUGIN_SCANNER=$GST_DIR/lib/gstreamer-1.0 GST_PLUGIN_SYSTEM_PATH=$GST_DIR/lib/gstreamer-1.0 GST_PLUGIN_PATH=$GST_DIR/lib/gstreamer-1.0 GST_OMX_CONFIG_DIR=$GST_DIR/etc/xdg $GST_DIR/bin/gst-launch-1.0 v4l2src device=/dev/video0 ! "video/x-raw,width=640,height=480,framerate=10/1" ! tee name=t ! queue ! videorate ! video/x-raw,framerate=4/1 ! jpegenc ! multipartmux boundary=spionisto ! tcpserversink host=127.0.0.1 port=14499 t. ! queue ! videoconvert ! omxh264enc target-bitrate=2000000 control-rate=2 interval-intraframes=10 periodicty-idr=10 ! "video/x-h264,profile=baseline" ! rtph264pay ! udpsink host=127.0.0.1 port=8004
