import xml.etree.ElementTree as ET
import re

MAX_SIZE = 25.0  # pixels


def parse_length(value):
    """Extract numeric value from SVG length (e.g., '100px', '10cm')."""
    if value is None:
        return None
    match = re.match(r"([0-9.]+)", value)
    return float(match.group(1)) if match else None


def get_svg_size(root):
    """Determine SVG width and height from attributes or viewBox."""
    width = parse_length(root.get("width"))
    height = parse_length(root.get("height"))

    if width and height:
        return width, height

    viewbox = root.get("viewBox")
    if viewbox:
        parts = list(map(float, viewbox.strip().split()))
        if len(parts) == 4:
            _, _, w, h = parts
            return w, h

    raise ValueError("SVG has no usable size information.")


def resize_svg(input_file, output_file):
    tree = ET.parse(input_file)
    root = tree.getroot()

    width, height = get_svg_size(root)

    # Compute scale factor
    scale = min(MAX_SIZE / width, MAX_SIZE / height)

    new_width = width * scale
    new_height = height * scale

    # Set new dimensions
    root.set("width", f"{new_width}px")
    root.set("height", f"{new_height}px")

    # Ensure viewBox exists for proper scaling
    if not root.get("viewBox"):
        root.set("viewBox", f"0 0 {width} {height}")

    tree.write(output_file)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        print("Usage: python resize_svg.py input.svg output.svg")
        sys.exit(1)

    resize_svg(sys.argv[1], sys.argv[2])