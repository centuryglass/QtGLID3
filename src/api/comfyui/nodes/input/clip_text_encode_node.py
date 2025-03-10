"""A ComfyUI node used to encode text using CLIP."""
from typing import TypedDict, NotRequired, cast, Any

from src.api.comfyui.nodes.comfy_node import NodeConnection, ComfyNode

NODE_NAME = 'CLIPTextEncode'


class ClipTextInputs(TypedDict):
    """CLIP Text input parameter object definition."""
    text: str
    clip:  NotRequired[NodeConnection]


class ClipTextEncodeNode(ComfyNode):
    """A ComfyUI node used to encode text using CLIP."""

    # Connection keys:
    CLIP = 'clip'

    # Output indexes:
    IDX_CONDITIONING = 0

    def __init__(self, text: str) -> None:
        data: ClipTextInputs = {
            'text': text
        }
        super().__init__(NODE_NAME, cast(dict[str, Any], data), {ClipTextEncodeNode.CLIP}, 1)
