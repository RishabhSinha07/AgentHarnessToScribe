"""Scribe -- a whiteboard an agent can draw on while it teaches.

The pieces:
  canvas   the drawing surface, the op language, and collision reporting
  session  one live lesson: a canvas plus the viewers watching it
  live     the web server that streams ops to a browser
  mcp      the adapter that lets an MCP client drive all of it
"""

from .canvas import GRID_H, GRID_W, PENS, Canvas, Element

__all__ = ["GRID_H", "GRID_W", "PENS", "Canvas", "Element", "__version__"]
__version__ = "0.1.0"
