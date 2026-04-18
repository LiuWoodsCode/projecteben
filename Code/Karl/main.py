import cv2
import time
import os
from datetime import datetime, timezone

# -----------------------------
# CONFIGURATION
# -----------------------------
OUTPUT_DIR = "recordings"
SEGMENT_DURATION = 600  # 10 minutes
FPS = 30
RESOLUTION = (1280, 720)

FONT = cv2.FONT_HERSHEY_COMPLEX

# -----------------------------
# HARDWARE INFO (REAL VALUES)
# -----------------------------
def get_info():
    model = "Unknown Model"
    serial = "Unknown Serial"

    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Model"):
                    model = line.split(":", 1)[1].strip()
                elif line.startswith("Serial"):
                    serial = line.split(":", 1)[1].strip()
    except Exception:
        pass

    return model, serial


DEVICE_MODEL, DEVICE_SERIAL = get_info()

# -----------------------------
# SETUP
# -----------------------------
os.makedirs(OUTPUT_DIR, exist_ok=True)

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, RESOLUTION[0])
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, RESOLUTION[1])
cap.set(cv2.CAP_PROP_FPS, FPS)

fourcc = cv2.VideoWriter_fourcc(*"mp4v")

# -----------------------------
# HELPERS
# -----------------------------
def create_writer(start_time):
    # Use segment START time for filename
    filename_time = start_time.strftime("%Y-%m-%d_%H-%M-%S%z")
    filename = os.path.join(OUTPUT_DIR, f"{filename_time}.mp4")

    writer = cv2.VideoWriter(filename, fourcc, FPS, RESOLUTION)
    return writer, filename


def overlay_metadata(frame):
    now = datetime.now(timezone.utc).astimezone()
    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S %z")

    lines = [
        timestamp_str,
        DEVICE_MODEL,
        DEVICE_SERIAL
    ]

    font_scale = 0.8
    thickness = 1
    margin = 10

    y = 30

    for line in lines:
        # Get text size
        (text_width, text_height), _ = cv2.getTextSize(
            line,
            FONT,
            font_scale,
            thickness
        )

        # Align right: frame width - text width - margin
        x = frame.shape[1] - text_width - margin

        cv2.putText(
            frame,
            line,
            (x, y),
            FONT,
            font_scale,
            (255, 255, 255),
            thickness,
            cv2.LINE_AA
        )

        y += text_height + 10

    return frame


# -----------------------------
# RECORDING LOOP
# -----------------------------
segment_start_time = datetime.now(timezone.utc).astimezone()
writer, current_file = create_writer(segment_start_time)
segment_start_monotonic = time.monotonic()
segment_frame_interval = 1.0 / FPS
segment_next_frame_time = segment_start_monotonic

print(f"Recording started → {current_file}")
print(f"Model: {DEVICE_MODEL}")
print(f"Serial: {DEVICE_SERIAL}")

try:
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Frame capture failed")
            break

        # Overlay metadata
        frame = overlay_metadata(frame)

        now = time.monotonic()

        # Write enough frames to match real elapsed time.
        while segment_next_frame_time <= now:
            writer.write(frame)
            segment_next_frame_time += segment_frame_interval

        # Rotate segment
        if now - segment_start_monotonic >= SEGMENT_DURATION:
            writer.release()
            print(f"Segment finalized → {current_file}")

            segment_start_time = datetime.now(timezone.utc).astimezone()
            writer, current_file = create_writer(segment_start_time)
            segment_start_monotonic = time.monotonic()
            segment_next_frame_time = segment_start_monotonic

            print(f"New segment started → {current_file}")

        # Optional preview
        cv2.imshow("Recording", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

except KeyboardInterrupt:
    print("Interrupted")

finally:
    writer.release()
    cap.release()
    cv2.destroyAllWindows()
    print("Stopped cleanly")