"""Placeholder resource manager used by the development scheduler."""


class ResourceManager:
    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def start(self) -> None:  # pragma: no cover - no runtime logic
        pass

    def shutdown(self) -> None:  # pragma: no cover - no runtime logic
        pass
