from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    """Runs a zero-argument callable on a background thread."""

    finished_ok = Signal(object)
    failed = Signal(str)
    output = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            result = self.fn()
            self.finished_ok.emit(result)

        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class WorkerRegistry:
    """Keeps background workers alive until they actually finish."""

    def __init__(self):
        self._workers = []

    def start(
        self,
        fn,
        on_ok=None,
        on_fail=None,
        on_output=None,
    ):
        worker = Worker(fn)

        self._workers.append(worker)

        def cleanup():
            if worker in self._workers:
                self._workers.remove(worker)

            worker.deleteLater()

        if on_ok:
            worker.finished_ok.connect(on_ok)

        if on_fail:
            worker.failed.connect(on_fail)

        if on_output:
            worker.output.connect(on_output)

        worker.finished.connect(cleanup)

        worker.start()

        return worker

    def shutdown(self, wait_ms=3000):
        """Wait for running workers during application shutdown."""

        for worker in list(self._workers):
            if not worker.wait(wait_ms):
                worker.terminate()
                worker.wait(1000)
