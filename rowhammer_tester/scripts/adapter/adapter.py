import socket
import socketserver
import sys
import json
from test_runner import run_test

# from typing import Optional
# import yaml

import logging

logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")


class Adapter:
    def __init__(self):
        self.localAddr: str = socket.gethostbyname(socket.gethostname())
        self.logger: logging.Logger = logging.getLogger("Adapter")
        return

    # def stop(self) -> None:
    #     self.tracker.stop()
    #     self.mapper.stop()

    # def reset(self) -> None:
    #     self.logger.info("Sending RESET...")
    #     self.handleQuery("RST(?,?,?)")
    #     self.mapper.reset()
    #     self.logger.info("RESET finished.")

    def handle_query(self, query: str) -> str:
        actions = query.split()
        return run_test(actions)


class QueryRequestHandler(socketserver.StreamRequestHandler):
    def __init__(self, request, client_address, server):
        self.logger = logging.getLogger("Query Handler")
        socketserver.BaseRequestHandler.__init__(self, request, client_address, server)
        return

    def handle(self):
        while True:
            query = self.rfile.readline().strip().decode("utf-8").rstrip("\n")
            # TODO: reset action to signal start of repeated tests, so that
            #       some setting up can be done, eg opening the Wishbone connection
            #       (or keep it open?)
            if query != "":
                self.logger.info("Received query: " + query)
                if isinstance(self.server, AdapterServer):
                    answer = self.server.adapter.handle_query(query)
                    self.wfile.write(bytearray(json.dumps(answer) + "\n", "utf-8"))
            else:
                return
        # sys.exit(0)


class AdapterServer(socketserver.TCPServer):
    def __init__(self, config, handler_class=QueryRequestHandler):
        self.adapter = Adapter()
        self.logger = logging.getLogger("Server")
        self.logger.info("Initialising server...")
        socketserver.TCPServer.__init__(self, ("0.0.0.0", 4343), handler_class)
        return

    def handle_error(self, request, client_address):
        print("-" * 40, file=sys.stderr)
        print(
            "Exception occurred during processing of request from",
            client_address,
            file=sys.stderr,
        )
        import traceback

        traceback.print_exc()
        print("-" * 40, file=sys.stderr)
        print("Crashing...")
        sys.exit(1)


# def loadConfig(path):
#     with open(path, "r") as stream:
#         return yaml.safe_load(stream)["adapter"]


# config = loadConfig("/root/config.yaml")
server = AdapterServer(QueryRequestHandler)

if __name__ == "__main__":
    server.serve_forever()
