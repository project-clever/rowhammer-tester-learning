import socket
import socketserver
import signal
import re
import sys
import json
from hw_executor import HammerAction, HwExecutor
# from typing import Optional
# import yaml

import logging

logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")




class Adapter:
    def __init__(self):
        self.localAddr: str = socket.gethostbyname(socket.gethostname())
        self.logger: logging.Logger = logging.getLogger("Adapter")
        self.logger.info("Creating hardware executor...")
        self.hw_exec = HwExecutor()
        
    
    def stop(self):
        self.hw_exec.stop()

    # def stop(self) -> None:
    #     self.tracker.stop()
    #     self.mapper.stop()

    # def reset(self) -> None:
    #     self.logger.info("Sending RESET...")
    #     self.handleQuery("RST(?,?,?)")
    #     self.mapper.reset()
    #     self.logger.info("RESET finished.")

    def handle_query(self, query: str) -> str:
        test = query.split()
        actions = [HammerAction.from_string(a_str) for a_str in test]
        return self.hw_exec.execute(actions)


class QueryRequestHandler(socketserver.StreamRequestHandler):
    def __init__(self, request, client_address, server):
        self.logger = logging.getLogger("Query Handler")
        socketserver.BaseRequestHandler.__init__(self, request, client_address, server)
        return

    def handle(self):
        while True:
            query = self.rfile.readline().strip().decode("utf-8").rstrip("\n")
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
    try:
        server.adapter.hw_exec.row_pattern='striped'
        server.serve_forever()
    except KeyboardInterrupt:
        server.logger.info("Shutting down.")
        server.adapter.hw_exec.stop()
