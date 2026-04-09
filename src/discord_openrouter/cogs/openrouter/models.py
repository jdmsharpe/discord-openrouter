from typing import Any, Protocol

from ...util import ChatSettings, Conversation, ModelInfo


class PermissionAwareChannel(Protocol):
    def permissions_for(self, member: Any) -> Any: ...


__all__ = ["ChatSettings", "Conversation", "ModelInfo", "PermissionAwareChannel"]
