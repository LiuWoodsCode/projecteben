#!/usr/bin/env python3
"""
Raspberry Pi Burn-In Test

Runs:
  - Fullscreen OpenGL 3D graphics stress test
    - Raspberry Pi logo texture on every cube face
  - 1000 Hz stereo speaker-test alternating between speakers
  - Default duration: 30 minutes

Dependencies from apt only:
  sudo apt install python3-pygame python3-opengl alsa-utils
"""

import argparse
import math
import os
import signal
import subprocess
import sys
import time
import threading

import pygame
from pygame.locals import DOUBLEBUF, FULLSCREEN, OPENGL, QUIT, KEYDOWN, K_ESCAPE

from OpenGL.GL import *
from OpenGL.GLU import *


DEFAULT_DURATION_SECONDS = 30 * 60
STATUS_OVERLAY_REFRESH_SECONDS = 0.2


def start_speaker_test_loop(stop_event: threading.Event) -> threading.Thread:
    """Run the exact speaker-test command repeatedly until stopped."""

    def speaker_loop() -> None:
        print("Starting speaker-test:")
        print(f"time is currently {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print("speaker-test -D default -r 48000 -c 2 -F S16_LE -t sine -f 1000 -l 1")

        while not stop_event.is_set():
            result = subprocess.run(
                "speaker-test -D default -r 48000 -c 2 -F S16_LE -t sine -f 1000 -l 1",
                shell=True,
                env=os.environ,
                capture_output=True,
                text=True
            )

            if stop_event.is_set():
                break

            if result.returncode != 0:
                print("ERROR: speaker-test exited with a non-zero status.")
                print(f"Stdout: {result.stdout if result.stdout else 'N/A'}")
                print(f"Stderr: {result.stderr if result.stderr else 'N/A'}")
                break

    speaker_thread = threading.Thread(target=speaker_loop, daemon=True)
    speaker_thread.start()
    return speaker_thread


def stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return

    if proc.poll() is not None:
        return

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=3)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


def create_texture_from_surface(surface: pygame.Surface) -> tuple[int, int, int]:
    texture_data = pygame.image.tostring(surface, "RGBA", True)
    width, height = surface.get_size()

    texture_id = glGenTextures(1)
    glBindTexture(GL_TEXTURE_2D, texture_id)
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
    glTexEnvf(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_DECAL)
    glTexImage2D(
        GL_TEXTURE_2D,
        0,
        GL_RGBA,
        width,
        height,
        0,
        GL_RGBA,
        GL_UNSIGNED_BYTE,
        texture_data,
    )
    glBindTexture(GL_TEXTURE_2D, 0)

    return texture_id, width, height


def load_logo_texture() -> int:
    image_path = os.path.join(os.path.dirname(__file__), "foundation.png")
    surface = pygame.image.load(image_path).convert_alpha()
    texture_id, _, _ = create_texture_from_surface(surface)

    return texture_id


def get_rpi_model() -> str:
    for path in (
        "/proc/device-tree/model",
        "/sys/firmware/devicetree/base/model",
    ):
        try:
            with open(path, "rb") as handle:
                model = handle.read().replace(b"\x00", b"").strip()
            if model:
                return model.decode("utf-8", errors="replace")
        except OSError:
            continue

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("Model"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass

    return "Unknown RPi model"


def get_rpi_temperature_c() -> float | None:
    for path in (
        "/sys/class/thermal/thermal_zone0/temp",
    ):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw_value = handle.read().strip()
            if raw_value:
                return float(raw_value) / 1000.0
        except (OSError, ValueError):
            continue

    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    prefix = "temp="
    suffix = "'C"
    output = result.stdout.strip()
    if output.startswith(prefix) and output.endswith(suffix):
        try:
            return float(output[len(prefix) : -len(suffix)])
        except ValueError:
            return None

    return None


def read_text_file(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip() or default
    except OSError:
        return default


def read_int_file(path: str) -> int | None:
    raw_value = read_text_file(path)
    if not raw_value:
        return None

    try:
        return int(raw_value)
    except ValueError:
        return None


def format_cooling_device_state(device_path: str) -> str:
    device_name = os.path.basename(device_path)
    device_type = read_text_file(os.path.join(device_path, "type"), "unknown")
    cur_state = read_int_file(os.path.join(device_path, "cur_state"))
    max_state = read_int_file(os.path.join(device_path, "max_state"))

    if cur_state is None and max_state is None:
        return f"{device_name} {device_type}: n/a"

    if cur_state is not None and max_state not in (None, 0):
        percent = 100.0 * cur_state / max_state
        status = "active" if cur_state > 0 else "idle"
        return f"{device_name} {device_type}: {status} {cur_state}/{max_state} ({percent:.0f}%)"

    if cur_state is not None:
        status = "active" if cur_state > 0 else "idle"
        return f"{device_name} {device_type}: {status} {cur_state}"

    return f"{device_name} {device_type}: max {max_state}"


def get_cooling_device_state() -> str | None:
    device_paths: list[str] = []

    try:
        with os.scandir("/sys/class/thermal") as entries:
            for entry in entries:
                if entry.name.startswith("cooling_device") and entry.is_dir(follow_symlinks=True):
                    device_paths.append(entry.path)
    except OSError:
        return None

    if not device_paths:
        return None

    device_paths.sort()
    summaries = [format_cooling_device_state(path) for path in device_paths]

    for summary in summaries:
        if ": active " in summary:
            return summary

    return summaries[0]


def draw_textured_quad(
    texture_id: int,
    x: int,
    y: int,
    width: int,
    height: int,
    window_width: int,
    window_height: int,
) -> None:
    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    glOrtho(0, window_width, window_height, 0, -1, 1)

    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()

    glDisable(GL_DEPTH_TEST)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glEnable(GL_TEXTURE_2D)
    glBindTexture(GL_TEXTURE_2D, texture_id)

    glBegin(GL_QUADS)
    glTexCoord2f(0.0, 0.0)
    glVertex2f(x, y + height)
    glTexCoord2f(1.0, 0.0)
    glVertex2f(x + width, y + height)
    glTexCoord2f(1.0, 1.0)
    glVertex2f(x + width, y)
    glTexCoord2f(0.0, 1.0)
    glVertex2f(x, y)
    glEnd()

    glBindTexture(GL_TEXTURE_2D, 0)
    glDisable(GL_TEXTURE_2D)
    glDisable(GL_BLEND)
    glEnable(GL_DEPTH_TEST)

    glMatrixMode(GL_MODELVIEW)
    glPopMatrix()

    glMatrixMode(GL_PROJECTION)
    glPopMatrix()

    glMatrixMode(GL_MODELVIEW)


def create_status_overlay_texture(
    font: pygame.font.Font,
    remaining_seconds: float,
    fps: float,
    model_name: str,
    temperature_c: float | None,
    cooling_state: str | None,
) -> tuple[int, int, int]:
    temperature_text = f"{temperature_c:.1f} C" if temperature_c is not None else "N/A"
    lines = [
        f"hostname {os.uname().nodename}",
        f"user {os.getlogin()}",
        f"eta {format_time(remaining_seconds)}",
        f"fps {fps:.1f}",
        f"{model_name}",
        f"cpu temp {temperature_text}",
        f"fan {cooling_state or 'N/A'}",
    ]

    padding_x = 14
    padding_y = 10
    line_gap = 4

    rendered_lines = [font.render(line, True, (255, 255, 255)) for line in lines]
    text_width = max(line.get_width() for line in rendered_lines)
    text_height = sum(line.get_height() for line in rendered_lines) + line_gap * (len(rendered_lines) - 1)

    surface = pygame.Surface((text_width + padding_x * 2, text_height + padding_y * 2), pygame.SRCALPHA)
    surface.fill((0, 0, 0, 140))

    current_y = padding_y
    for line_surface in rendered_lines:
        surface.blit(line_surface, (padding_x, current_y))
        current_y += line_surface.get_height() + line_gap

    return create_texture_from_surface(surface)


def render_status_overlay(
    texture_id: int,
    overlay_width: int,
    overlay_height: int,
    window_width: int,
    window_height: int,
) -> None:
    draw_textured_quad(texture_id, 16, 16, overlay_width, overlay_height, window_width, window_height)


def draw_cube(size: float = 1.0, logo_texture_id: int | None = None) -> None:
    s = size / 2.0

    vertices = [
        (-s, -s, -s),
        ( s, -s, -s),
        ( s,  s, -s),
        (-s,  s, -s),
        (-s, -s,  s),
        ( s, -s,  s),
        ( s,  s,  s),
        (-s,  s,  s),
    ]

    faces = [
        (0, 1, 2, 3),
        (4, 5, 6, 7),
        (0, 1, 5, 4),
        (2, 3, 7, 6),
        (1, 2, 6, 5),
        (0, 3, 7, 4),
    ]

    colors = [
        (1.0, 0.1, 0.2),
        (0.1, 1.0, 0.3),
        (0.2, 0.4, 1.0),
        (1.0, 0.8, 0.1),
        (1.0, 0.1, 1.0),
        (0.1, 1.0, 1.0),
    ]

    if logo_texture_id is not None:
        texcoords = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))

        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, logo_texture_id)

        glBegin(GL_QUADS)
        for face_index, face in enumerate(faces):
            glColor3fv(colors[face_index])
            for texcoord, vertex in zip(texcoords, face):
                glTexCoord2f(texcoord[0], texcoord[1])
                glVertex3fv(vertices[vertex])
        glEnd()

        glBindTexture(GL_TEXTURE_2D, 0)
        glDisable(GL_TEXTURE_2D)
    else:
        glBegin(GL_QUADS)
        for face_index, face in enumerate(faces):
            glColor3fv(colors[face_index])
            for vertex in face:
                glVertex3fv(vertices[vertex])
        glEnd()

    glColor3f(1.0, 1.0, 1.0)
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]

    glBegin(GL_LINES)
    for edge in edges:
        for vertex in edge:
            glVertex3fv(vertices[vertex])
    glEnd()


def init_graphics(width: int, height: int) -> int:
    pygame.display.set_mode((width, height), DOUBLEBUF | OPENGL | FULLSCREEN)
    pygame.display.set_caption("Raspberry Pi Burn-In Test")

    glViewport(0, 0, width, height)

    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    gluPerspective(60.0, width / float(height), 0.1, 200.0)

    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    glEnable(GL_DEPTH_TEST)
    glDepthFunc(GL_LEQUAL)

    glEnable(GL_CULL_FACE)
    glCullFace(GL_BACK)

    glShadeModel(GL_SMOOTH)

    glClearColor(0.02, 0.01, 0.04, 1.0)

    return load_logo_texture()


def render_scene(elapsed: float, cube_count: int, logo_texture_id: int | None) -> None:
    glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()

    # Camera wobble. Because apparently the cubes need to suffer too.
    cam_x = math.sin(elapsed * 0.33) * 4.0
    cam_y = math.cos(elapsed * 0.21) * 2.0
    cam_z = 24.0 + math.sin(elapsed * 0.17) * 4.0

    gluLookAt(
        cam_x, cam_y, cam_z,
        0.0, 0.0, 0.0,
        0.0, 1.0, 0.0,
    )

    grid = int(math.sqrt(cube_count))
    spacing = 3.0
    half = (grid - 1) * spacing / 2.0

    for i in range(cube_count):
        x_index = i % grid
        y_index = i // grid

        x = x_index * spacing - half
        y = y_index * spacing - half

        z = math.sin(elapsed * 1.7 + i * 0.37) * 3.5

        glPushMatrix()
        glTranslatef(x, y, z)

        glRotatef(elapsed * 55.0 + i * 7.0, 1.0, 0.3, 0.2)
        glRotatef(elapsed * 41.0 + i * 11.0, 0.1, 1.0, 0.4)
        glRotatef(elapsed * 73.0 + i * 5.0, 0.2, 0.2, 1.0)

        pulse = 0.75 + 0.25 * math.sin(elapsed * 3.0 + i)
        glScalef(pulse, pulse, pulse)

        draw_cube(1.4, logo_texture_id)

        glPopMatrix()

    # Extra spinning center object to keep the GPU chewing.
    glPushMatrix()
    glRotatef(elapsed * 95.0, 1.0, 1.0, 0.0)
    glRotatef(elapsed * 47.0, 0.0, 1.0, 1.0)
    glScalef(3.0, 3.0, 3.0)
    draw_cube(1.0, logo_texture_id)
    glPopMatrix()


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Raspberry Pi fullscreen 3D + speaker burn-in test")
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION_SECONDS,
        help="Duration in seconds. Default: 1800 / 30 minutes.",
    )
    parser.add_argument(
        "--frequency",
        type=int,
        default=1000,
        help="speaker-test sine frequency in Hz. Default: 1000.",
    )
    parser.add_argument(
        "--channels",
        type=int,
        default=2,
        help="Number of audio channels for speaker-test. Default: 2.",
    )
    parser.add_argument(
        "--device",
        type=str,
        default="default",
        help="ALSA device to use. Default: default.",
    )
    parser.add_argument(
        "--cubes",
        type=int,
        default=81,
        help="Number of cubes to render. Default: 81. Increase for more GPU load.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=0,
        help="FPS cap. 0 means uncapped. Default: 0.",
    )

    args = parser.parse_args()

    if args.duration <= 0:
        print("ERROR: duration must be greater than 0.")
        return 1

    speaker_stop_event = threading.Event()
    speaker_thread: threading.Thread | None = None

    try:
        pygame.display.init()
        pygame.font.init()

        info = pygame.display.Info()
        width = info.current_w
        height = info.current_h

        print(f"Display: {width}x{height}")
        print(f"Duration: {args.duration} seconds")
        print("Press ESC to stop early.")

        logo_texture_id = init_graphics(width, height)
        hud_font = pygame.font.Font(None, 44)
        model_name = get_rpi_model()
        temperature_c = get_rpi_temperature_c()
        cooling_state = get_cooling_device_state()

        clock = pygame.time.Clock()
        start_time = time.monotonic()
        last_status = 0.0
        last_temperature_sample = 0.0
        last_overlay_refresh = 0.0
        frames = 0
        frame_ms = 0
        overlay_texture_id: int | None = None
        overlay_width = 0
        overlay_height = 0
        speaker_thread = start_speaker_test_loop(speaker_stop_event)

        running = True

        while running:
            now = time.monotonic()
            elapsed = now - start_time
            remaining = max(0.0, args.duration - elapsed)

            if now - last_temperature_sample >= 1.0:
                temperature_c = get_rpi_temperature_c()
                cooling_state = get_cooling_device_state()
                last_temperature_sample = now

            if overlay_texture_id is None or now - last_overlay_refresh >= STATUS_OVERLAY_REFRESH_SECONDS:
                if overlay_texture_id is not None:
                    glDeleteTextures([overlay_texture_id])

                overlay_texture_id, overlay_width, overlay_height = create_status_overlay_texture(
                    hud_font,
                    remaining,
                    1000.0 / frame_ms if frame_ms > 0 else 0.0,
                    model_name,
                    temperature_c,
                    cooling_state,
                )
                last_overlay_refresh = now

            if elapsed >= args.duration:
                running = False

            for event in pygame.event.get():
                if event.type == QUIT:
                    running = False
                elif event.type == KEYDOWN and event.key == K_ESCAPE:
                    running = False

            render_scene(elapsed, args.cubes, logo_texture_id)
            render_status_overlay(overlay_texture_id, overlay_width, overlay_height, width, height)
            pygame.display.flip()

            frames += 1

            if now - last_status >= 5.0:
                avg_fps = frames / max(0.001, elapsed)
                print(
                    f"Elapsed {format_time(elapsed)} / "
                    f"Remaining {format_time(remaining)} / "
                    f"Average FPS {avg_fps:.1f}"
                )
                last_status = now

            if args.fps > 0:
                frame_ms = clock.tick(args.fps)
            else:
                frame_ms = clock.tick()

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        speaker_stop_event.set()

        if speaker_thread is not None:
            speaker_thread.join(timeout=5)

        if overlay_texture_id is not None:
            try:
                glDeleteTextures([overlay_texture_id])
            except Exception:
                pass

        print("Stopping speaker-test...")

        try:
            pygame.quit()
        except Exception:
            pass

        print("Burn-in test complete.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())