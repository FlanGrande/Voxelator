#!/usr/bin/env python3
"""Tiny ImGui viewer for Voxelator static stacked PNG spritesheets."""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from OpenGL import GL as gl
from PIL import Image
from imgui_bundle import imgui, hello_imgui, immapp


@dataclass
class TextureAtlas:
    rgba: np.ndarray
    tile_width: int
    tile_height: int
    layers: int
    cols: int
    texture_id: int | None = None

    @property
    def width(self) -> int:
        return int(self.rgba.shape[1])

    @property
    def height(self) -> int:
        return int(self.rgba.shape[0])


@dataclass
class Model:
    path: Path
    image_width: int
    image_height: int
    tile_size: int
    layers: int
    source_tiles: np.ndarray
    cell_count: int
    unique_colors: int
    non_empty_layers: int
    bounds: tuple[int, int, int] | None
    file_size: int
    sprite_atlas: TextureAtlas | None = None


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

    source_tiles = np.ascontiguousarray(arr.reshape(tile, layers, tile, 4).transpose(1, 0, 2, 3))

    alpha = source_tiles[:, :, :, 3] > alpha_threshold
    cell_count = int(alpha.sum())
    non_empty_layers = int(alpha.any(axis=(1, 2)).sum())
    unique_colors = 0
    if cell_count:
        rgb = source_tiles[:, :, :, :3]
        packed = (
            rgb[:, :, :, 0].astype(np.uint32)
            | (rgb[:, :, :, 1].astype(np.uint32) << 8)
            | (rgb[:, :, :, 2].astype(np.uint32) << 16)
        )
        unique_colors = int(np.unique(packed[alpha]).size)

    bounds = _bounds_for_mask(alpha)
    return Model(
        path=path,
        image_width=width,
        image_height=height,
        tile_size=tile,
        layers=layers,
        source_tiles=source_tiles,
        cell_count=cell_count,
        unique_colors=unique_colors,
        non_empty_layers=non_empty_layers,
        bounds=bounds,
        file_size=path.stat().st_size,
    )


def _axis_span(present: np.ndarray) -> int:
    idx = np.flatnonzero(present)
    if idx.size == 0:
        return 0
    return int(idx[-1] - idx[0] + 1)


def _bounds_for_mask(alpha: np.ndarray) -> tuple[int, int, int] | None:
    if not bool(alpha.any()):
        return None
    z_present = alpha.any(axis=(1, 2))
    row_present = alpha.any(axis=(0, 2))
    x_present = alpha.any(axis=(0, 1))
    return (_axis_span(x_present), _axis_span(row_present), _axis_span(z_present))


def _pack_tiles(tiles: np.ndarray) -> TextureAtlas:
    layers, tile_h, tile_w, _channels = tiles.shape
    cols = max(1, math.ceil(math.sqrt(layers)))
    rows = math.ceil(layers / cols)
    atlas = np.zeros((rows * tile_h, cols * tile_w, 4), dtype=np.uint8)
    for i in range(layers):
        row = i // cols
        col = i % cols
        atlas[row * tile_h : (row + 1) * tile_h, col * tile_w : (col + 1) * tile_w] = tiles[i]
    return TextureAtlas(np.ascontiguousarray(atlas), tile_w, tile_h, layers, cols)


def _sprite_atlas(model: Model) -> TextureAtlas:
    if model.sprite_atlas is None:
        model.sprite_atlas = _pack_tiles(model.source_tiles)
    return model.sprite_atlas


def _ensure_texture(atlas: TextureAtlas):
    if atlas.texture_id is None:
        tex = int(gl.glGenTextures(1))
        gl.glBindTexture(gl.GL_TEXTURE_2D, tex)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        gl.glTexImage2D(
            gl.GL_TEXTURE_2D,
            0,
            gl.GL_RGBA8,
            atlas.width,
            atlas.height,
            0,
            gl.GL_RGBA,
            gl.GL_UNSIGNED_BYTE,
            atlas.rgba,
        )
        gl.glBindTexture(gl.GL_TEXTURE_2D, 0)
        atlas.texture_id = tex
    return imgui.ImTextureRef(atlas.texture_id)


def _tile_uvs(atlas: TextureAtlas, index: int) -> tuple[imgui.ImVec2, imgui.ImVec2, imgui.ImVec2, imgui.ImVec2]:
    col = index % atlas.cols
    row = index // atlas.cols
    # Tiny inset keeps float rounding from sampling the neighboring atlas tile.
    eps_u = 0.02 / atlas.width
    eps_v = 0.02 / atlas.height
    u0 = (col * atlas.tile_width) / atlas.width + eps_u
    v0 = (row * atlas.tile_height) / atlas.height + eps_v
    u1 = ((col + 1) * atlas.tile_width) / atlas.width - eps_u
    v1 = ((row + 1) * atlas.tile_height) / atlas.height - eps_v
    return (imgui.ImVec2(u0, v0), imgui.ImVec2(u1, v0), imgui.ImVec2(u1, v1), imgui.ImVec2(u0, v1))


class PreviewApp:
    def __init__(self, model: Model):
        self.model = model
        self.angle = 0.0
        self.preview_bg_dark = True
        self.layer_offset = 1.0
        self.rotation_rate = math.radians(135.0)
        self.preview_size = min(640, max(320, model.tile_size * 7))

    def run(self) -> None:
        params = hello_imgui.RunnerParams()
        params.app_window_params.window_title = f"Voxelator Preview - {self.model.path.name}"
        params.app_window_params.window_geometry.size = (self.preview_size + 116, self.preview_size + 360)
        params.app_window_params.resizable = False
        params.imgui_window_params.show_menu_bar = False
        params.imgui_window_params.show_status_bar = False
        params.imgui_window_params.default_imgui_window_type = hello_imgui.DefaultImGuiWindowType.provide_full_screen_window
        try:
            params.ini_folder_type = hello_imgui.IniFolderType.temp_folder
        except Exception:
            pass
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
        rail_w = 42.0
        rail_gap = 12.0
        panel_w = self.preview_size + rail_gap + rail_w + margin * 2
        panel_h = self.preview_size + 320.0
        panel_bg = _u32(0, 0, 0)
        text_col = _u32(235, 235, 235)
        muted_col = _u32(165, 165, 165)

        draw.add_rect_filled(origin, imgui.ImVec2(origin.x + panel_w, origin.y + panel_h), panel_bg)

        canvas_min = imgui.ImVec2(origin.x + margin, origin.y + margin + 22.0)
        canvas_max = imgui.ImVec2(canvas_min.x + self.preview_size, canvas_min.y + self.preview_size)
        canvas_bg = _u32(8, 8, 8) if self.preview_bg_dark else _u32(245, 245, 245)
        draw.add_rect_filled(canvas_min, canvas_max, canvas_bg)

        self._draw_stacked_sprite(draw, canvas_min, canvas_max)

        imgui.set_cursor_screen_pos(canvas_min)
        imgui.invisible_button("preview_canvas", imgui.ImVec2(self.preview_size, self.preview_size))
        if imgui.is_item_hovered() and abs(io.mouse_wheel) > 0.001:
            self._rotate(io.mouse_wheel * 0.12)
        if imgui.is_item_active() and imgui.is_mouse_dragging(imgui.MouseButton_.left, 0.0):
            self._rotate(io.mouse_delta.x * 0.012)
            self._adjust_layer_offset(-io.mouse_delta.y * 0.01)

        dt = min(0.05, max(0.0, float(io.delta_time)))
        if imgui.is_key_down(imgui.Key.left_arrow):
            self._rotate(-self.rotation_rate * dt)
        if imgui.is_key_down(imgui.Key.right_arrow):
            self._rotate(self.rotation_rate * dt)

        rail_x = canvas_max.x + rail_gap
        rail_y = canvas_min.y
        if self._background_button(imgui.ImVec2(rail_x, rail_y), imgui.ImVec2(rail_w, rail_w)):
            self.preview_bg_dark = not self.preview_bg_dark
        if self._press_button("+", imgui.ImVec2(rail_x, rail_y + rail_w + 20.0), imgui.ImVec2(rail_w, rail_w)):
            self._snap_layer_offset(1)
        if self._press_button("-", imgui.ImVec2(rail_x, rail_y + (rail_w + 20.0) * 2.0), imgui.ImVec2(rail_w, rail_w)):
            self._snap_layer_offset(-1)

        button_y = canvas_max.y + 18.0
        if self._held_button("<", imgui.ImVec2(canvas_min.x, button_y), imgui.ImVec2(64, 34)):
            self._rotate(-self.rotation_rate * dt)

        if self._held_button(">", imgui.ImVec2(canvas_max.x - 64, button_y), imgui.ImVec2(64, 34)):
            self._rotate(self.rotation_rate * dt)

        stats_y = button_y + 52.0
        self._draw_stats(draw, imgui.ImVec2(canvas_min.x, stats_y), text_col, muted_col)

        imgui.set_cursor_screen_pos(imgui.ImVec2(origin.x + panel_w - 1, origin.y + panel_h - 1))
        imgui.dummy(imgui.ImVec2(1, 1))

    def _held_button(self, label: str, pos: imgui.ImVec2, size: imgui.ImVec2) -> bool:
        imgui.set_cursor_screen_pos(pos)
        imgui.button(label, size)
        return imgui.is_item_active()

    def _press_button(self, label: str, pos: imgui.ImVec2, size: imgui.ImVec2) -> bool:
        imgui.set_cursor_screen_pos(pos)
        imgui.button(label, size)
        return imgui.is_item_clicked()

    def _background_button(self, pos: imgui.ImVec2, size: imgui.ImVec2) -> bool:
        imgui.set_cursor_screen_pos(pos)
        clicked = imgui.button("##bg_toggle", size)
        draw = imgui.get_window_draw_list()
        fill = _u32(255, 255, 255) if self.preview_bg_dark else _u32(0, 0, 0)
        inset = 10.0
        draw.add_rect_filled(
            imgui.ImVec2(pos.x + inset, pos.y + inset),
            imgui.ImVec2(pos.x + size.x - inset, pos.y + size.y - inset),
            fill,
        )
        draw.add_rect(pos, imgui.ImVec2(pos.x + size.x, pos.y + size.y), _u32(180, 180, 180))
        return clicked

    def _rotate(self, delta: float) -> None:
        if abs(delta) <= 1.0e-9:
            return
        self.angle += delta

    def _adjust_layer_offset(self, delta: float) -> None:
        if abs(delta) <= 1.0e-9:
            return
        self.layer_offset = max(-1.0, min(1.0, self.layer_offset + delta))

    def _snap_layer_offset(self, direction: int) -> None:
        eps = 1.0e-6
        if direction > 0:
            value = (math.floor(self.layer_offset / 0.1 + eps) + 1) * 0.1
        else:
            value = (math.ceil(self.layer_offset / 0.1 - eps) - 1) * 0.1
        self.layer_offset = max(-1.0, min(1.0, round(value, 1)))

    def _draw_stacked_sprite(self, draw, canvas_min: imgui.ImVec2, canvas_max: imgui.ImVec2) -> None:
        model = self.model
        if model.cell_count <= 0:
            return
        atlas = _sprite_atlas(model)
        tex_id = _ensure_texture(atlas)
        w = canvas_max.x - canvas_min.x
        h = canvas_max.y - canvas_min.y
        cx = canvas_min.x + w * 0.5
        cy = canvas_min.y + h * 0.54
        center_z = (model.layers - 1) * 0.5
        stack_span = abs((model.layers - 1) * self.layer_offset)
        max_span = max(model.tile_size, model.tile_size + stack_span, 1.0)
        scale = min(w, h) * 0.72 / max_span
        ca = math.cos(self.angle)
        sa = math.sin(self.angle)

        half = model.tile_size * 0.5
        for z in range(model.layers):
            layer_y = -(z - center_z) * self.layer_offset
            pts = []
            for lx, ly in ((-half, -half), (half, -half), (half, half), (-half, half)):
                rx = ca * lx - sa * ly
                ry = sa * lx + ca * ly
                pts.append(imgui.ImVec2(cx + rx * scale, cy + (ry + layer_y) * scale))
            uv = _tile_uvs(atlas, z)
            draw.add_image_quad(tex_id, pts[0], pts[1], pts[2], pts[3], uv[0], uv[1], uv[2], uv[3])

    def _draw_stats(self, draw, pos: imgui.ImVec2, text_col: int, muted_col: int) -> None:
        model = self.model
        occupancy = (model.cell_count / max(1, model.tile_size * model.tile_size * model.layers)) * 100.0
        bounds = "x".join(str(v) for v in model.bounds) if model.bounds else "empty"
        lines = [
            ("Name", model.path.name),
            ("File", _format_bytes(model.file_size)),
            ("PNG", f"{model.image_width}x{model.image_height}"),
            ("Resolution", str(model.tile_size)),
            ("Layers", str(model.layers)),
            ("Cells", f"{model.cell_count} ({occupancy:.1f}%)"),
            ("Non-empty", str(model.non_empty_layers)),
            ("Bounds", bounds),
            ("Colors", str(model.unique_colors)),
            ("Layer Offset", f"{self.layer_offset:.2f}"),
            ("Angle", f"{math.degrees(self.angle) % 360:.0f} deg"),
        ]
        y = pos.y
        for key, value in lines:
            draw.add_text(imgui.ImVec2(pos.x, y), muted_col, f"{key}")
            draw.add_text(imgui.ImVec2(pos.x + 102.0, y), text_col, value)
            y += 18.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview a Voxelator static stacked PNG")
    parser.add_argument("--png", required=True, help="Static stacked PNG produced by Voxelator")
    parser.add_argument("--tile-size", type=int, default=0, help="Optional tile size override")
    parser.add_argument("--alpha-threshold", type=int, default=1, help="Minimum alpha value (0-255) to count a voxel")
    args = parser.parse_args()

    try:
        model = _load_model(Path(args.png).expanduser().resolve(), args.tile_size or None, args.alpha_threshold)
    except (OSError, ValueError) as exc:
        print(f"Voxelator preview error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    PreviewApp(model).run()


if __name__ == "__main__":
    main()
