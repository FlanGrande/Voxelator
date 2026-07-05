#!/usr/bin/env python3
"""Tiny ImGui viewer for Voxelator static stacked PNG spritesheets."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from imgui_bundle import imgui, hello_imgui, immapp


FACE_DEFS = (
    ((1, 0, 0), ((1, -1, -1), (1, -1, 1), (1, 1, 1), (1, 1, -1))),
    ((-1, 0, 0), ((-1, -1, -1), (-1, 1, -1), (-1, 1, 1), (-1, -1, 1))),
    ((0, 1, 0), ((-1, 1, -1), (1, 1, -1), (1, 1, 1), (-1, 1, 1))),
    ((0, -1, 0), ((-1, -1, -1), (-1, -1, 1), (1, -1, 1), (1, -1, -1))),
    ((0, 0, 1), ((-1, -1, 1), (-1, 1, 1), (1, 1, 1), (1, -1, 1))),
    ((0, 0, -1), ((-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1))),
)


@dataclass(frozen=True)
class Face:
    normal: tuple[int, int, int]
    corners: tuple[tuple[float, float, float], ...]
    color: tuple[int, int, int, int]


@dataclass
class Model:
    path: Path
    image_width: int
    image_height: int
    tile_size: int
    layers: int
    cells: dict[tuple[int, int, int], tuple[int, int, int, int]]
    faces: list[Face]
    unique_colors: int
    non_empty_layers: int
    bounds: tuple[int, int, int] | None
    file_size: int


def _u32(r: int, g: int, b: int, a: int = 255) -> int:
    return imgui.get_color_u32(imgui.ImVec4(r / 255, g / 255, b / 255, a / 255))


def _format_bytes(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{size} B"


def _infer_static_layout(width: int, height: int, tile_size_arg: int | None) -> tuple[int, int]:
    tile = int(tile_size_arg) if tile_size_arg else int(height)
    if tile <= 0:
        raise ValueError("Tile size must be positive")
    if height != tile:
        raise ValueError(
            f"Expected static Voxelator PNG height to equal tile size ({tile}), got height={height}. "
            "Animation spritesheets are not supported by this preview."
        )
    if width % tile != 0:
        raise ValueError(f"Image width {width} is not divisible by tile size {tile}")
    return tile, width // tile


def _load_model(path: Path, tile_size_arg: int | None = None, alpha_threshold: int = 1) -> Model:
    img = Image.open(path).convert("RGBA")
    arr = np.asarray(img, dtype=np.uint8)
    height, width = arr.shape[:2]
    tile, layers = _infer_static_layout(width, height, tile_size_arg)

    cells: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
    colors = set()
    xs: list[int] = []
    ys: list[int] = []
    zs: list[int] = []

    for iz in range(layers):
        x0 = iz * tile
        tile_px = arr[:, x0 : x0 + tile, :]
        alpha = tile_px[:, :, 3]
        coords = np.argwhere(alpha > alpha_threshold)
        for row, col in coords:
            y = tile - 1 - int(row)
            x = int(col)
            rgba_np = tile_px[row, col]
            color = (int(rgba_np[0]), int(rgba_np[1]), int(rgba_np[2]), int(rgba_np[3]))
            cell = (x, y, iz)
            cells[cell] = color
            colors.add(color[:3])
            xs.append(x)
            ys.append(y)
            zs.append(iz)

    bounds = None
    if cells:
        bounds = (max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, max(zs) - min(zs) + 1)

    faces = _build_faces(cells, tile, layers)
    return Model(
        path=path,
        image_width=width,
        image_height=height,
        tile_size=tile,
        layers=layers,
        cells=cells,
        faces=faces,
        unique_colors=len(colors),
        non_empty_layers=len(set(zs)),
        bounds=bounds,
        file_size=path.stat().st_size,
    )


def _build_faces(cells: dict[tuple[int, int, int], tuple[int, int, int, int]], tile: int, layers: int) -> list[Face]:
    occupied = set(cells)
    if not occupied:
        return []
    xs = [cell[0] for cell in occupied]
    ys = [cell[1] for cell in occupied]
    zs = [cell[2] for cell in occupied]
    cx = (min(xs) + max(xs)) * 0.5
    cy = (min(ys) + max(ys)) * 0.5
    cz = (min(zs) + max(zs)) * 0.5

    faces: list[Face] = []
    for cell in sorted(occupied):
        ix, iy, iz = cell
        color = cells[cell]
        for normal, corners in FACE_DEFS:
            nx, ny, nz = normal
            if (ix + nx, iy + ny, iz + nz) in occupied:
                continue
            pts = tuple(
                ((ix - cx) + sx * 0.5, (iy - cy) + sy * 0.5, (iz - cz) + sz * 0.5)
                for sx, sy, sz in corners
            )
            faces.append(Face(normal=normal, corners=pts, color=color))
    return faces


def _rotate_project(point: tuple[float, float, float], angle: float, pitch: float, scale: float, cx: float, cy: float) -> tuple[float, float, float]:
    x, y, z = point
    ca = math.cos(angle)
    sa = math.sin(angle)
    xr = ca * x + sa * z
    zr = -sa * x + ca * z
    cp = math.cos(pitch)
    sp = math.sin(pitch)
    yp = cp * y - sp * zr
    zp = sp * y + cp * zr
    return (cx + xr * scale, cy - yp * scale, zp)


class PreviewApp:
    def __init__(self, model: Model):
        self.model = model
        self.angle = 0.0
        self.bg_dark = True
        self.pitch = math.radians(18.0)
        self.preview_size = min(640, max(320, model.tile_size * 7))

    def run(self) -> None:
        params = hello_imgui.RunnerParams()
        params.app_window_params.window_title = f"Voxelator Preview - {self.model.path.name}"
        params.app_window_params.window_geometry.size = (self.preview_size + 56, self.preview_size + 300)
        params.app_window_params.resizable = False
        params.imgui_window_params.show_menu_bar = False
        params.imgui_window_params.show_status_bar = False
        params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.provide_full_screen_window
        params.callbacks.setup_imgui_style = self._setup_style
        params.callbacks.show_gui = self._gui
        immapp.run(params)

    def _setup_style(self) -> None:
        style = imgui.get_style()
        style.window_rounding = 0.0
        style.frame_rounding = 0.0
        style.window_border_size = 0.0
        style.frame_border_size = 0.0
        style.item_spacing = imgui.ImVec2(8, 8)
        colors = imgui.Col_
        style.set_color_(colors.text, imgui.ImVec4(0.92, 0.92, 0.92, 1.0))
        style.set_color_(colors.window_bg, imgui.ImVec4(0.0, 0.0, 0.0, 1.0))
        style.set_color_(colors.button, imgui.ImVec4(0.12, 0.12, 0.12, 1.0))
        style.set_color_(colors.button_hovered, imgui.ImVec4(0.24, 0.24, 0.24, 1.0))
        style.set_color_(colors.button_active, imgui.ImVec4(0.35, 0.35, 0.35, 1.0))

    def _gui(self) -> None:
        io = imgui.get_io()
        draw = imgui.get_window_draw_list()
        origin = imgui.get_cursor_screen_pos()
        margin = 24.0
        panel_w = self.preview_size + margin * 2
        panel_h = self.preview_size + 250.0
        panel_bg = _u32(0, 0, 0) if self.bg_dark else _u32(255, 255, 255)
        text_col = _u32(235, 235, 235) if self.bg_dark else _u32(20, 20, 20)
        muted_col = _u32(165, 165, 165) if self.bg_dark else _u32(80, 80, 80)

        draw.add_rect_filled(origin, imgui.ImVec2(origin.x + panel_w, origin.y + panel_h), panel_bg)

        canvas_min = imgui.ImVec2(origin.x + margin, origin.y + margin + 22.0)
        canvas_max = imgui.ImVec2(canvas_min.x + self.preview_size, canvas_min.y + self.preview_size)
        canvas_bg = _u32(8, 8, 8) if self.bg_dark else _u32(245, 245, 245)
        draw.add_rect_filled(canvas_min, canvas_max, canvas_bg)

        self._draw_model(draw, canvas_min, canvas_max)

        imgui.set_cursor_screen_pos(canvas_min)
        imgui.invisible_button("preview_canvas", imgui.ImVec2(self.preview_size, self.preview_size))
        if imgui.is_item_hovered():
            if abs(io.mouse_wheel) > 0.001:
                self.angle += io.mouse_wheel * 0.12
            if imgui.is_mouse_dragging(imgui.MouseButton_.left, 0.0):
                self.angle += io.mouse_delta.x * 0.012

        if imgui.is_key_pressed(imgui.Key.left_arrow):
            self.angle -= 0.18
        if imgui.is_key_pressed(imgui.Key.right_arrow):
            self.angle += 0.18

        toggle_size = 22.0
        toggle_min = imgui.ImVec2(canvas_max.x - toggle_size - 10.0, canvas_min.y + 10.0)
        toggle_max = imgui.ImVec2(toggle_min.x + toggle_size, toggle_min.y + toggle_size)
        draw.add_rect_filled(toggle_min, toggle_max, _u32(255, 255, 255) if self.bg_dark else _u32(0, 0, 0))
        draw.add_rect(toggle_min, toggle_max, _u32(180, 180, 180))
        imgui.set_cursor_screen_pos(toggle_min)
        imgui.invisible_button("bg_toggle", imgui.ImVec2(toggle_size, toggle_size))
        if imgui.is_item_clicked():
            self.bg_dark = not self.bg_dark

        button_y = canvas_max.y + 18.0
        imgui.set_cursor_screen_pos(imgui.ImVec2(canvas_min.x, button_y))
        if imgui.button("<", imgui.ImVec2(92, 34)):
            self.angle -= math.pi / 8
        imgui.set_cursor_screen_pos(imgui.ImVec2(canvas_max.x - 92, button_y))
        if imgui.button(">", imgui.ImVec2(92, 34)):
            self.angle += math.pi / 8

        stats_y = button_y + 52.0
        self._draw_stats(draw, imgui.ImVec2(canvas_min.x, stats_y), text_col, muted_col)

        imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + panel_w - 1, origin.y + panel_h - 1))
        imgui.dummy(imgui.ImVec2(1, 1))

    def _draw_model(self, draw, canvas_min: imgui.ImVec2, canvas_max: imgui.ImVec2) -> None:
        model = self.model
        if not model.faces:
            return
        w = canvas_max.x - canvas_min.x
        h = canvas_max.y - canvas_min.y
        cx = canvas_min.x + w * 0.5
        cy = canvas_min.y + h * 0.54
        bounds = model.bounds or (model.tile_size, model.tile_size, model.layers)
        max_span = max(bounds[0], bounds[1], bounds[2], 1)
        scale = min(w, h) * 0.72 / max_span

        faces = []
        for face in model.faces:
            projected = [_rotate_project(p, self.angle, self.pitch, scale, cx, cy) for p in face.corners]
            depth = sum(p[2] for p in projected) / 4.0
            faces.append((depth, projected, face.color))

        for _depth, pts, color in sorted(faces, key=lambda item: item[0]):
            col = _u32(*color)
            p = [imgui.ImVec2(pts[i][0], pts[i][1]) for i in range(4)]
            draw.add_quad_filled(p[0], p[1], p[2], p[3], col)

    def _draw_stats(self, draw, pos: imgui.ImVec2, text_col: int, muted_col: int) -> None:
        model = self.model
        occupancy = (len(model.cells) / max(1, model.tile_size * model.tile_size * model.layers)) * 100.0
        bounds = "x".join(str(v) for v in model.bounds) if model.bounds else "empty"
        lines = [
            ("Name", model.path.name),
            ("File", _format_bytes(model.file_size)),
            ("PNG", f"{model.image_width}x{model.image_height}"),
            ("Resolution", str(model.tile_size)),
            ("Layers", str(model.layers)),
            ("Voxels", f"{len(model.cells)} ({occupancy:.1f}%)"),
            ("Non-empty", str(model.non_empty_layers)),
            ("Bounds", bounds),
            ("Colors", str(model.unique_colors)),
            ("Angle", f"{math.degrees(self.angle) % 360:.0f} deg"),
        ]
        y = pos.y
        for key, value in lines:
            draw.add_text(imgui.ImVec2(pos.x, y), muted_col, f"{key}")
            draw.add_text(imgui.ImVec2(pos.x + 102.0, y), text_col, value)
            y += 20.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview a Voxelator static stacked PNG")
    parser.add_argument("--png", required=True, help="Static stacked PNG produced by Voxelator")
    parser.add_argument("--tile-size", type=int, default=0, help="Optional tile size override")
    parser.add_argument("--alpha-threshold", type=int, default=1, help="Minimum alpha value (0-255) to count a voxel")
    args = parser.parse_args()

    model = _load_model(Path(args.png).expanduser().resolve(), args.tile_size or None, args.alpha_threshold)
    PreviewApp(model).run()


if __name__ == "__main__":
    main()
