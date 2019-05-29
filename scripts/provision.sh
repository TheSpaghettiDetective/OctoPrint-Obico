#!/bin/bash

set -x
set -e

if [ -n "$1" ]; then
  WD="$1"
else
  WD=/vagrant
fi

sudo apt-get update
sudo apt-get -y install build-essential
sudo apt-get -y install cmake libjpeg8-dev
sudo apt-get -y --no-install-recommends install imagemagick ffmpeg libv4l-dev

cd /
if [ ! -d mjpg-streamer ]; then
    sudo rm -rf mjpg-streamer && sudo git clone https://github.com/kennethjiang/mjpg-streamer.git
fi
cd /mjpg-streamer/
sudo cp -r mjpg-streamer-experimental/* .
sudo make
sudo cp -r $WD/jpgs /mjpg-streamer/

sudo cp $WD/scripts/rc.local /etc/

version=1.3.11
sudo mkdir /octoprint && cd /octoprint
sudo curl -o octoprint.tar.gz -L https://github.com/foosel/OctoPrint/archive/${version}.tar.gz
sudo tar -xvf octoprint.tar.gz --strip 1
# RUN pip install --upgrade pip
# Workaround for a pip version mismatch
# RUN apt-get update && apt-get remove -f python-pip && apt-get install -y python-pip && apt-get remove -f python-pip easy_install -U pip
#RUN pip install setuptools
sudo apt-get -y install python-pip
sudo pip install -r requirements.txt
sudo python setup.py install

cd /vagrant
mkdir -p data
sudo python setup.py develop
if [ ! -f data/config.yaml ]; then cp octoprint-config.yaml data/config.yaml; fi
