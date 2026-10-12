from collections.abc import Callable, Iterable, Mapping
import signal
import threading
import time
from typing import Any

class LoopingInterruptibleThread(threading.Thread):

    def __init__(self, function: Callable[..., object], iterator, repetitions = 1, pause = 0):
            #self, group: None = None, target: Callable[..., object] | None = None, pause: int = 0, name: str | None = None, args: Iterable[Any] = ..., kwargs: Mapping[str, Any] | None = None, *, daemon: bool | None = None) -> None:
        super().__init__()
        self.stop_event = threading.Event()
        self.function = function
        self.iterator = iterator
        self.repetitions = repetitions
        self.pause = pause

        signal.signal(signal.SIGINT, self.handle_kb_interrupt)


    def handle_kb_interrupt(self, sig, frame):
        self.stop_event.set()

    def run(self):
        for item in self.iterator:
            for i in range(self.repetitions):
                if self.stop_event.is_set():
                    self.stop_event.clear()
                    print("\r", end="")
                    break
                self.function(item)
                time.sleep(self.pause)

def fun(x):
    print(x)

if __name__ == '__main__':
    n_iter = 10
    thread = LoopingInterruptibleThread(function=fun, iterator=[0,1,2], repetitions=2, pause=1)
    thread.start()
    thread.join()
    print('Program done')