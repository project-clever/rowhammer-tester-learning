#!/bin/bash

export TARGET=zcu104
export IP_ADDRESS=192.168.1.50
export UDP_PORT=1234

source venv/bin/activate
litex_server --udp --udp-ip ${IP_ADDRESS} --udp-port ${UDP_PORT} &

wait -n 

exit $?




