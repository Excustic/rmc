__author__ = "Michael Kushnir"

from rmscene.scene_items import PenColor
from rmscene.scene_items import Pen as PenEnum
"""
Convert .rm SceneTree to InkML-supported XML file.
"""
import logging
import typing as tp
from pathlib import Path
import string
from rmscene import SceneTree, read_tree, CrdtId
from rmscene import scene_items as si
from rmscene.text import TextDocument
from typing import List, Tuple
from .writing_tools import Pen, RM_PALETTE

# Initialize module logger
_logger = logging.getLogger(__name__)

SCREEN_WIDTH = 32767
SCREEN_HEIGHT = 32767
A4_HEIGHT_MM = 297
A4_WIDTH_MM = 210
ASPECT_RATIO = A4_WIDTH_MM / A4_HEIGHT_MM
# Explicit padding to avoid top-left collisions (e.g. page title)
X_PAD = 200
Y_PAD = 200
WIDTH_CONV_CONSTANT = 10
HEIGHT_CONV_CONSTANT = 10
PRESSURE_CONV_CONSTANT = 128
X_OFFSET = 148
Y_OFFSET = 512
min_x = min_y = max_x = max_y = 0

def scale(x: float, y: float) -> Tuple[int, int]:
    global min_x, max_x, min_y, max_y

    # 1. Calculate original ranges
    x_range = max_x - min_x
    y_range = max_y - min_y

    if x_range == 0:
        x_range = 1
    if y_range == 0:
        y_range = 1

    # 2. Apply A4 aspect correction
    raw_ratio = x_range / y_range
    if raw_ratio > ASPECT_RATIO:
        # Wider → extend Y range
        target_y_range = x_range / ASPECT_RATIO
        y_center = (min_y + max_y) / 2
        min_y = y_center - target_y_range / 2
        max_y = y_center + target_y_range / 2
        y_range = target_y_range
    else:
        # Taller → extend X range
        target_x_range = y_range * ASPECT_RATIO
        x_center = (min_x + max_x) / 2
        min_x = x_center - target_x_range / 2
        max_x = x_center + target_x_range / 2
        x_range = target_x_range

    # 3. Compute drawable screen area after padding
    draw_width = SCREEN_WIDTH - 2 * X_PAD
    draw_height = SCREEN_HEIGHT - 2 * Y_PAD

    # 4. Normalize and scale
    x_norm = (x - min_x) / x_range
    y_norm = (y - min_y) / y_range

    new_x = int(x_norm * draw_width + X_PAD)
    new_y = int(y_norm * draw_height + Y_PAD)

    # 5. Clamp to ensure no overflow
    new_x = max(X_PAD, min(SCREEN_WIDTH - X_PAD, new_x))
    new_y = max(Y_PAD, min(SCREEN_HEIGHT - Y_PAD, new_y))

    return new_x, new_y

XML_HEADER = ("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
              "<inkml:ink xmlns:emma=\"http://www.w3.org/2003/04/emma\" "
                 "xmlns:msink=\"http://schemas.microsoft.com/ink/2010/main\""
                 " xmlns:inkml=\"http://www.w3.org/2003/InkML\">\n")

def rm_to_inkml(rm_path: tp.Union[str, Path], inkml_path: tp.Union[str, Path]):
    _logger.info("Converting %s to %s", rm_path, inkml_path)
    with open(rm_path, "rb") as infile, open(inkml_path, "wt", encoding='utf-8') as outfile:
        tree = read_tree(infile)
        tree_to_xml(tree, outfile)
    _logger.info("Conversion complete.")


def tree_to_xml(tree: SceneTree, output):
    _logger.debug("Exporting %d items to InkML", len(list(tree.walk())))
    # XML header and root
    output.write(XML_HEADER)
    # Add pen configurations to file header
    configure_ink(tree, output)
    # Trace group - all ink data is placed here
    global min_x, max_x, min_y, max_y
    min_x, max_x, min_y, max_y = get_bounding_box(tree)
    output.write("  <inkml:traceGroup>\n")
    trace_id = 1
    for item in tree.walk():
        if isinstance(item, si.Line):
            draw_stroke(item, output, trace_id)
            trace_id += 1
    output.write("  </inkml:traceGroup>\n")
    print("minimas: ", min_x, min_y, "maximas:", max_x, max_y)
    output.write("</inkml:ink>\n")
    _logger.debug("Finished InkML export: %d traces", trace_id-1)

def configure_ink(tree: SceneTree, output):
    """
    Appends ink metadata to file header
    """
    output.write("  <inkml:definitions>")
    # Add context data. Channel F (optional) - stands for force (pressure).
    output.write("""
    <inkml:context xml:id="ctxCoordinatesWithPressure">
        <inkml:inkSource xml:id="inkSrcCoordinatesWithPressure">
            <inkml:traceFormat>
                <inkml:channel name="X" type="integer" max="32767" units="himetric" />
                <inkml:channel name="Y" type="integer" max="32767" units="himetric" />
                <inkml:channel name="F" type="integer" max="32767" units="dev" />
            </inkml:traceFormat>
            <inkml:channelProperties>
                <inkml:channelProperty channel="X" name="resolution" value="1" units="1/himetric" />
                <inkml:channelProperty channel="Y" name="resolution" value="1" units="1/himetric" />
                <inkml:channelProperty channel="F" name="resolution" value="1" units="1/dev" />
            </inkml:channelProperties>
        </inkml:inkSource>
    </inkml:context>
    """)
    # Add brush types
    pens_set = fetch_used_inks(tree)
    for pen in pens_set:
        output.write(f"""
    <inkml:brush xml:id="{generate_id_from_pen(pen)}">
        <inkml:brushProperty name="width" value="{int(pen.stroke_width * WIDTH_CONV_CONSTANT)}" units="himetric" />
        <inkml:brushProperty name="height" value="{int(pen.stroke_width * HEIGHT_CONV_CONSTANT)}" units="himetric" />
        <inkml:brushProperty name="color" value="{'#%02x%02x%02x' % RM_PALETTE[pen.stroke_color]}" />
        <inkml:brushProperty name="transparency" value="{1 - pen.stroke_opacity}" />
        <inkml:brushProperty name="tip" value="ellipse" />
        <inkml:brushProperty name="rasterOp" value="{'maskPen' if pen.name == 'Highlighter' else 'copyPen'}" />
        <inkml:brushProperty name="ignorePressure" value="false" />
        <inkml:brushProperty name="antiAliased" value="true" />
        <inkml:brushProperty name="fitToCurve" value="false" />
    </inkml:brush>""")
    output.write("\n  </inkml:definitions>\n")

def generate_id_from_pen(pen: Pen):
    return (f"name_{pen.name}_cap_{pen.stroke_linecap}_op_{pen.stroke_opacity}_w_"
            f"{pen.stroke_width}_clr_{pen.stroke_color}")

def fetch_used_inks(tree: SceneTree) -> List[Pen]:
    pens = []
    ink_ids = []
    for item in tree.walk():
        if isinstance(item, si.Line):
            # TODO - temporary fix until rmscene supports highlighter/shader colors
            color = item.color.value if item.color.value != 9 else PenColor.YELLOW.value
            pen = Pen.create(item.tool.value, color, item.thickness_scale)
            gen_id = generate_id_from_pen(pen)
            if gen_id not in ink_ids:
                ink_ids.append(gen_id)
                pens.append(pen)
    return pens

def draw_stroke(item: si.Line, output, trace_id: int) -> None:
    if _logger.root.level == logging.DEBUG:
        _logger.debug("Drawing stroke %d from node %s with %d points", trace_id, item.node_id, len(item.points))
    tid = str(trace_id)
    coord = []
    global min_x, min_y, max_x, max_y
    for pt in item.points:
        if pt.x < min_x:
            min_x = pt.x
        elif pt.x > max_x:
            max_x = pt.x
        if pt.y < min_y:
            min_y = pt.y
        elif pt.y > max_y:
            max_y = pt.y
        scaled_x, scaled_y = scale(pt.x, pt.y)
        scaled_pressure = int(pt.pressure * PRESSURE_CONV_CONSTANT)
        if scaled_x >= SCREEN_WIDTH or scaled_y >= SCREEN_HEIGHT or scaled_pressure >= SCREEN_HEIGHT or (scaled_x * scaled_y * scaled_pressure < 0):
            print("AAAH AAH", scaled_x, scaled_y, scaled_pressure)
        coord.append(f"{scaled_x} {scaled_y} {scaled_pressure}")
    coord_str = ",".join(coord)
    # TODO - temporary fix until rmscene supports highlighter/shader colors
    color = item.color.value if item.color.value != 9 else PenColor.YELLOW.value
    pen = Pen.create(item.tool.value, color, item.thickness_scale)
    brush_id = generate_id_from_pen(pen)
    output.write(
        f"    <inkml:trace xml:id=\"{tid}\" contextRef=\"#ctxCoordinatesWithPressure\" "
        f"brushRef=\"#{brush_id}\">{coord_str}</inkml:trace>\n"
    )


def draw_text(item: si.Text, output) -> None:
    _logger.debug("Drawing text from node %s", item.node_id)
    doc = TextDocument.from_scene_item(item)
    # Render text runs as annotations or ignore for InkML
    pass

def get_bounding_box(tree: SceneTree,
                     default: tp.Tuple[int, int, int, int] = (0,0,0,0)) \
        -> tp.Tuple[int, int, int, int]:
    """
    Get the bounding box of the given item.
    The minimum size is the default size of the screen.

    :return: x_min, x_max, y_min, y_max: the bounding box in screen units (need to be scalded using xx and yy functions)
    """
    x_min, x_max, y_min, y_max = default

    for item in tree.walk():
        if isinstance(item, si.Line):
            x_min = min([x_min] + [p.x for p in item.points])
            x_max = max([x_max] + [p.x for p in item.points])
            y_min = min([y_min] + [p.y for p in item.points])
            y_max = max([y_max] + [p.y for p in item.points])

    return x_min, x_max, y_min, y_max

def get_anchor(item: si.Group, anchor_pos):
    anchor_x = 0.0
    anchor_y = 0.0
    if item.anchor_id is not None:
        assert item.anchor_origin_x is not None
        anchor_x = item.anchor_origin_x.value
        if item.anchor_id.value in anchor_pos:
            anchor_y = anchor_pos[item.anchor_id.value]
        else:
            _logger.warning("Group anchor: %s is unknown!", item.anchor_id.value)

    return anchor_x, anchor_y
