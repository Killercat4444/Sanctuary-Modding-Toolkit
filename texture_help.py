"""Shared text for texture field tooltips and the adjacent help buttons."""

TEXTURE_INPUT_HELP = {
    "ALBEDO": (
        "Albedo PNG: Base Color / Diffuse",
        (
            "Use the base-color or diffuse PNG: the unit's paint and surface colors.",
            "Do not put ambient occlusion (AO), a mask, or a normal map here.",
            "Prefer color without baked lighting; it must match the model's UVs.",
            "The Albedo Alpha setting separately controls the team-color mask.",
        ),
    ),
    "MASK": (
        "Mask / AO PNG",
        (
            "AO / Grayscale mode: use your grayscale ambient-occlusion (AO) PNG.",
            "White means unoccluded; darker areas represent crevices/occlusion.",
            "Sanctuary RGBA mode: use a packed mask, not a diffuse/color texture.",
            "Packed channels: R = metallic, G = AO, B = emission, A = smoothness.",
            "A roughness map alone is not a ready-to-use Sanctuary packed mask.",
        ),
    ),
    "NORMAL": (
        "Normal PNG: Tangent-Space Normal",
        (
            "Use the tangent-space RGB normal PNG, usually purple/blue.",
            "It describes small surface details and must match the model's UVs.",
            "Do not use a grayscale height/bump map, AO map, or diffuse texture.",
            "A normal map does not add geometry or smooth the model's silhouette.",
        ),
    ),
}


def texture_input_help(slot, mask_mode=None):
    title, lines = TEXTURE_INPUT_HELP.get(slot, TEXTURE_INPUT_HELP["ALBEDO"])
    if slot == "MASK" and mask_mode == "AO_ONLY":
        lines = (
            "Current mode: AO / Grayscale. Choose an ambient-occlusion PNG.",
            "Do not put the diffuse/base-color texture in this field.",
            "White means unoccluded; darker areas represent crevices/occlusion.",
            "The converter sets metallic/emission to zero and uses Default Smoothness.",
            "For an already packed mask, change Mask Input to Sanctuary RGBA.",
        )
    elif slot == "MASK" and mask_mode == "SANCTUARY_RGBA":
        lines = (
            "Current mode: Sanctuary RGBA. Choose an already packed mask PNG.",
            "R = metallic, G = AO, B = emission, A = smoothness.",
            "Do not use a diffuse/base-color PNG or an ordinary roughness map alone.",
            "For a grayscale AO bake, change Mask Input to AO / Grayscale.",
        )
    return title, lines


def texture_input_description(slot, mask_mode=None):
    return "\n".join(texture_input_help(slot, mask_mode)[1])
