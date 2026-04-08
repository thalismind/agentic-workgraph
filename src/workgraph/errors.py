class VersionMismatchError(RuntimeError):
    def __init__(self, *, run_version: str, current_version: str) -> None:
        self.run_version = run_version
        self.current_version = current_version
        super().__init__(
            f"Cannot resume run from version {run_version}; current workflow version is {current_version}"
        )
