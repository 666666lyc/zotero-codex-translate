class BridgeError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def as_dict(self):
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


class Cancelled(BridgeError):
    def __init__(self):
        super().__init__("CANCELLED", "任务已取消，可以继续翻译。")
