#!/bin/bash

# docker volume /home/sumfenaipc/sumfenpro/docker/RemoteV2/media/project:/mnt/odm
export ODM_DATA_DIR=/home/sumfenaipc/sumfenpro/docker/RemoteV2/media/project
export MOUNT_USB_DIR=/mnt/usb

# docker volume /home/sumfenaipc/sumfenpro/picture:/mnt/usb
export USB_DATA_DIR=/home/sumfenaipc/sumfenpro/picture
export MOUNT_ODM_DIR=/mnt/odm


docker-compose build && UID=$(id -u) GID=$(id -g) docker-compose up -d