FROM python:3.10-alpine
RUN apk --no-cache --update add npm build-base git bash
# --no-cache --update sudo openjdk13 apache-ant build-base bash busybox-extras libffi-dev tcpdump libpcap-dev iptables curl libpq-dev libc6-compat npm
# RUN pip3 install "scapy[basic]" pcapy-ng impacket sqlalchemy psycopg2 jsons pyyaml==5.3.1
RUN npm install -g pyright
# RUN curl -o /usr/bin/wait-for-it https://raw.githubusercontent.com/vishnubob/wait-for-it/master/wait-for-it.sh && chmod +x /usr/bin/wait-for-it
COPY . /code
WORKDIR /code
# RUN pyright --warnings
# WORKDIR /code
# RUN ant -f Mapper/build.xml dist
RUN python3 -m venv venv && pip install -r requirements.txt
CMD ./adapter-init.sh
# CMD iptables -A OUTPUT -p tcp --tcp-flags RST RST -j DROP && sleep 30 && wait-for-it implementation:44344 -s -- wait-for-it database:5432 -s -- 
# CMD python3 -u /code/adapter.py && export TARGET=zcu104 && export IP_ADDRESS=192.168.1.50
# CMD ping 10.2.9.57
