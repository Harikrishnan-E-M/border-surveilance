import os
import math
import subprocess
import numpy as np
import cv2

STORAGE_DIR = os.path.join("storage", "recordings")
os.makedirs(STORAGE_DIR, exist_ok=True)

def create_border_surveillance_clip(filename: str, title: str, recording_type: str = "breach", duration_sec: int = 8, fps: int = 24):
    width, height = 1280, 720
    temp_avi = os.path.join(STORAGE_DIR, f"temp_{filename}.avi")
    final_mp4 = os.path.join(STORAGE_DIR, filename)

    fourcc = cv2.VideoWriter_fourcc(*'MJPG')
    out = cv2.VideoWriter(temp_avi, fourcc, fps, (width, height))

    total_frames = duration_sec * fps

    for frame_idx in range(total_frames):
        t = frame_idx / fps
        progress = frame_idx / total_frames

        # Create base surveillance frame
        if recording_type == "breach":
            # Dark Night Vision Green tint background
            img = np.zeros((height, width, 3), dtype=np.uint8)
            img[:, :] = (15, 35, 20)  # Dark green tint

            # Add ground and sky gradient
            cv2.rectangle(img, (0, 420), (width, height), (20, 50, 25), -1)
            cv2.line(img, (0, 420), (width, 420), (40, 100, 50), 2)

            # Draw Fence posts and barbed wire
            for x in range(50, width, 120):
                cv2.line(img, (x, 260), (x, 460), (50, 120, 60), 3)
            # Barbed wires
            cv2.line(img, (0, 300), (width, 300), (60, 140, 70), 1)
            cv2.line(img, (0, 350), (width, 350), (60, 140, 70), 1)
            cv2.line(img, (0, 400), (width, 400), (60, 140, 70), 1)

            # Moving intruder target
            target_x = int(150 + progress * 800)
            target_y = int(320 + math.sin(t * 4) * 8)
            is_breaching = target_x > 500

            # Silhouette person
            color_person = (30, 80, 40)
            cv2.circle(img, (target_x, target_y - 45), 16, color_person, -1)  # Head
            cv2.ellipse(img, (target_x, target_y + 10), (20, 40), 0, 0, 360, color_person, -1)  # Body

            # AI Bounding Box
            box_color = (0, 0, 255) if is_breaching else (0, 220, 255)  # Red if breaching, Amber if approach
            cv2.rectangle(img, (target_x - 30, target_y - 70), (target_x + 30, target_y + 60), box_color, 2)
            
            label = "ALARM: BREACH BREACH!" if is_breaching else "TARGET INTRUDER (APPROACH)"
            cv2.putText(img, label, (target_x - 60, target_y - 80), cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)
            cv2.putText(img, f"CONF: {min(99.8, 85.0 + t * 2):.1f}%", (target_x - 40, target_y + 80), cv2.FONT_HERSHEY_SIMPLEX, 0.45, box_color, 1)

            # Flashing border on breach
            if is_breaching and (int(t * 4) % 2 == 0):
                cv2.rectangle(img, (5, 5), (width - 5, height - 5), (0, 0, 255), 6)
                cv2.putText(img, "!!! PERIMETER BREACH ALERT !!!", (width // 2 - 220, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 3)

        elif recording_type == "anpr":
            # Highway Checkpoint / Asphalt scene
            img = np.zeros((height, width, 3), dtype=np.uint8)
            img[:, :] = (35, 35, 40)  # Dark asphalt

            # Lane markings
            cv2.line(img, (200, 0), (350, height), (180, 180, 180), 3)
            cv2.line(img, (900, 0), (800, height), (180, 180, 180), 3)

            # Moving Vehicle
            v_x = int(380 + math.sin(t) * 20)
            v_y = int(180 + progress * 320)
            v_w = int(220 + progress * 80)
            v_h = int(120 + progress * 50)

            # Car body
            cv2.rectangle(img, (v_x, v_y), (v_x + v_w, v_y + v_h), (80, 80, 100), -1)
            # Windshield
            cv2.rectangle(img, (v_x + 20, v_y + 15), (v_x + v_w - 20, v_y + 45), (160, 180, 200), -1)

            # License Plate Box
            plate_x = v_x + v_w // 2 - 50
            plate_y = v_y + v_h - 30
            cv2.rectangle(img, (plate_x, plate_y), (plate_x + 100, plate_y + 25), (255, 255, 255), -1)
            cv2.putText(img, "IND-BP-04", (plate_x + 8, plate_y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)

            # ANPR Bounding Box
            cv2.rectangle(img, (plate_x - 15, plate_y - 15), (plate_x + 115, plate_y + 40), (0, 255, 255), 2)
            cv2.putText(img, "ANPR SCAN: IND-BP-04-X8921 [VALIDATED]", (v_x, v_y - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)

        else:  # Continuous or Thermal
            # Thermal Infrared Palettes (Cyan/Blue/Yellow/White)
            img = np.zeros((height, width, 3), dtype=np.uint8)
            img[:, :] = (80, 20, 10)  # Dark thermal blue

            # Thermal landscape outlines
            cv2.line(img, (0, 450), (width, 450), (160, 80, 20), 2)
            cv2.circle(img, (400, 300), 80, (120, 60, 30), -1)  # Hotspot tower

            # Sweeping Radar Circle
            angle = t * 2.0
            cx, cy = 640, 360
            rx = int(cx + 250 * math.cos(angle))
            ry = int(cy + 250 * math.sin(angle))
            cv2.line(img, (cx, cy), (rx, ry), (0, 255, 128), 2)
            cv2.circle(img, (cx, cy), 250, (0, 180, 100), 1)

            cv2.putText(img, "PATROL SWEEP: SECTOR 4 TOWER", (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 128), 2)

        # ── Overlay HUD Elements (Applies to all) ──────────────────────────
        # Corner Frame Brackets
        margin = 30
        length = 40
        c_color = (0, 220, 255)
        # Top-Left
        cv2.line(img, (margin, margin), (margin + length, margin), c_color, 2)
        cv2.line(img, (margin, margin), (margin, margin + length), c_color, 2)
        # Top-Right
        cv2.line(img, (width - margin, margin), (width - margin - length, margin), c_color, 2)
        cv2.line(img, (width - margin, margin), (width - margin, margin + length), c_color, 2)
        # Bottom-Left
        cv2.line(img, (margin, height - margin), (margin + length, height - margin), c_color, 2)
        cv2.line(img, (margin, height - margin), (margin, height - margin - length), c_color, 2)
        # Bottom-Right
        cv2.line(img, (width - margin, height - margin), (width - margin - length, height - margin), c_color, 2)
        cv2.line(img, (width - margin, height - margin), (width - margin, height - margin - length), c_color, 2)

        # REC Dot & Header
        if (int(t * 2) % 2 == 0):
            cv2.circle(img, (50, 45), 8, (0, 0, 255), -1)
        cv2.putText(img, "REC", (68, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(img, f"IBVAP SURVEILLANCE FEED | {title}", (130, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        # Timestamp Bottom Left
        sec_offset = int(t)
        timestamp_str = f"2026-09-12 10:15:{sec_offset:02d}.{int((t - sec_offset) * 1000):03d}"
        cv2.putText(img, timestamp_str, (50, height - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)
        cv2.putText(img, "FPS: 24.0 | RES: 1280x720 | LAT: 31.624 N  LON: 74.571 E", (width - 520, height - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)

        # Add subtle grain noise
        noise = np.random.randint(0, 12, (height, width, 3), dtype=np.uint8)
        img = cv2.add(img, noise)

        out.write(img)

    out.release()

    # Convert AVI to MP4 via FFmpeg with H.264 encoding for HTML5 browser compatibility
    cmd = [
        "ffmpeg",
        "-y",
        "-i", temp_avi,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        final_mp4,
    ]
    subprocess.run(cmd, capture_output=True)

    if os.path.exists(temp_avi):
        try:
            os.remove(temp_avi)
        except Exception:
            pass

    print(f"Generated border surveillance clip: {final_mp4} ({os.path.getsize(final_mp4)} bytes)")

if __name__ == "__main__":
    create_border_surveillance_clip("sample_border_recording.mp4", "BOP Sector 1 Perimeter", "breach")
    create_border_surveillance_clip("sample_anpr_recording.mp4", "Checkpoint 4 Vehicle Scan", "anpr")
    create_border_surveillance_clip("sample_patrol_recording.mp4", "Thermal Patrol Sector", "continuous")
