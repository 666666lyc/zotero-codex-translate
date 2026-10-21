class BridgeError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False, details=None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details

    def as_dict(self):
        return {"code": self.code, "message": str(self), "retryable": self.retryable, **({"details": self.details} if self.details is not None else {})}


class Cancelled(BridgeError):
    def __init__(self):
        super().__init__("CANCELLED", "任务已取消，可以继续翻译。")
