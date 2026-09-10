class NullRepository:
    backend_name = "none"

    def __init__(self, reason="not_configured"):
        self.reason = reason

    @property
    def configured(self):
        return False

    def save_diagnosis(self, _record):
        return {"success": False, "persistent": False, "reason": self.reason}

    def save_feedback(self, _record):
        return {"success": False, "persistent": False, "reason": self.reason}

    def check_rate_limit(self, *_args):
        return None
