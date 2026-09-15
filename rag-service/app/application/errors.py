"""Application-owned failures; HTTP adapters decide their response codes."""

from .gate import State


class GateBusy(RuntimeError):
    def __init__(self, state: State):
        self.state = state
        super().__init__('当前操作暂不可用')


class MutationFailed(RuntimeError):
    def __init__(self):
        super().__init__('资料变更未能确认完成，问答已暂停，请先检查恢复状态')


class RecoveryFailed(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__('索引一致性未通过检查，请停服后执行索引恢复')


class QuestionFailed(RuntimeError):
    def __init__(self):
        super().__init__('问答服务暂时不可用')
