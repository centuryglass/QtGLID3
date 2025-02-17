"""A ComfyUI node used to encode latent image data for inpainting models."""
from typing import TypedDict, NotRequired, cast, Any

from src.api.comfyui.nodes.comfy_node import NodeConnection, ComfyNode

NODE_NAME = 'VAEEncodeForInpaint'


class VAEEncodeInpaintingInputs(TypedDict):
    """Latent image encoding input parameter object definition."""
    pixels: NotRequired[NodeConnection]  # raw image data, e.g. from LoadImage.
    vae: NotRequired[NodeConnection]  # VAE model used for encoding. May be baked-in to a regular SD model.
    mask: NotRequired[NodeConnection]  # Inpainting mask.
    grow_mask_by: int


class VAEEncodeInpaintingNode(ComfyNode):
    """A ComfyUI node used to encode images into latent image space for inpainting models."""

    # Connection keys:
    PIXELS = 'pixels'
    VAE = 'vae'
    MASK = 'mask'

    # Output indexes:
    IDX_LATENT = 0

    def __init__(self, grow_mask_by: int) -> None:
        connection_params = {
            VAEEncodeInpaintingNode.PIXELS,
            VAEEncodeInpaintingNode.VAE,
            VAEEncodeInpaintingNode.MASK
        }
        data: VAEEncodeInpaintingInputs = {
            'grow_mask_by': grow_mask_by
        }
        super().__init__(NODE_NAME, cast(dict[str, Any], data), connection_params, 1)
