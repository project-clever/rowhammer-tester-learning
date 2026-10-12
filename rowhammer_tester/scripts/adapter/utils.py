from collections.abc import Callable
import signal
import threading
import time


class LoopingInterruptibleThread(threading.Thread):
    """Run `function` over each item of `iterator`, `repetitions` times each.

    The loop can be stopped early by calling `stop()` from another thread, or
    by Ctrl-C if `install_sigint_handler=True` and the thread is created on the
    main thread. The SIGINT handler is not installed by default, because
    signal.signal() only works on the main thread and installing it silently
    hijacks the process-wide handler.
    """

    def __init__(self, function: Callable[..., object], iterator, repetitions=1,
                 pause=0, install_sigint_handler=False):
        super().__init__()
        self.stop_event = threading.Event()
        self.function = function
        self.iterator = iterator
        self.repetitions = repetitions
        self.pause = pause

        if install_sigint_handler:
            if threading.current_thread() is not threading.main_thread():
                raise RuntimeError(
                    'install_sigint_handler requires constructing the thread '
                    'on the main thread')
            signal.signal(signal.SIGINT, self._handle_kb_interrupt)

    def _handle_kb_interrupt(self, sig, frame):
        self.stop()

    def stop(self):
        self.stop_event.set()

    def run(self):
        for item in self.iterator:
            for _ in range(self.repetitions):
                if self.stop_event.is_set():
                    print("\r", end="")
                    return
                self.function(item)
                time.sleep(self.pause)


if __name__ == '__main__':
    def fun(x):
        print(x)

    thread = LoopingInterruptibleThread(function=fun, iterator=[0, 1, 2],
                                        repetitions=2, pause=1,
                                        install_sigint_handler=True)
    thread.start()
    thread.join()
    print('Program done')