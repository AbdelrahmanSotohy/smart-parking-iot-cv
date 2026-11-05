"""
Smart Parking - Raspberry Pi gateway
------------------------------------
1. Reads spot occupancy from the Arduino over USB serial ("P:1,0,1,0" at 9600 baud)
   and drives one red/green LED per spot (GPIO, BCM numbering, active-low).
2. Runs camera-based occupancy detection with OpenCV: each spot ROI is compared to a
   saved "empty lot" baseline (Gaussian blur -> mean abs-diff -> EMA smoothing ->
   hysteresis thresholds + frame debounce) and drawn as a green/red overlay.

Keys in the video window:  C = calibrate on empty lot   S = save baseline
                           L = load baseline            Q = quit
ROIs are hard-coded for a 1280x720 camera frame (see ROI_BOXES).
Run:  python3 parking_gateway.py
"""

import serial
import RPi.GPIO as GPIO
import time
import cv2
import numpy as np
import os

CAM_INDEX = 0
SHOW_SCALE = 1.0
BLUR_KSIZE = 5
EMA_ALPHA = 0.2

TH_ENTER = 14.0
TH_EXIT = 9.0

ENTER_FRAMES = 4
EXIT_FRAMES = 6

BASELINE_FILE = "parking_baseline_four_spots.npz"

# Hard-coded ROIs for 1280x720 (x0, y0, x1, y1)
ROI_BOXES = [
    (11, 284, 92, 448),    # Spot 1
    (234, 279, 311, 448),  # Spot 2
    (340, 273, 440, 445),  # Spot 3
    (529, 279, 637, 437)   # Spot 4
]

led_pins = [
    {'R': 4,  'G': 17},
    {'R': 27, 'G': 22},
    {'R': 23, 'G': 24},
    {'R': 20, 'G': 21}
]

GPIO.setmode(GPIO.BCM)
for led in led_pins:
    GPIO.setup(led['R'], GPIO.OUT)
    GPIO.setup(led['G'], GPIO.OUT)
    GPIO.output(led['R'], GPIO.HIGH)
    GPIO.output(led['G'], GPIO.HIGH)

ser = serial.Serial('/dev/ttyACM0', 9600, timeout=1)
ser.flush()

def preprocess(gray):
    if BLUR_KSIZE > 0:
        gray = cv2.GaussianBlur(gray, (BLUR_KSIZE, BLUR_KSIZE), 0)
    return gray

def crop(img, box):
    x0, y0, x1, y1 = box
    return img[y0:y1, x0:x1]

def calc_score(curr_gray, ref_gray):
    diff = cv2.absdiff(curr_gray, ref_gray)
    return float(diff.mean())

def draw_overlay(frame, rois, states, scores):
    vis = frame.copy()
    for i, box in enumerate(rois):
        x0, y0, x1, y1 = box
        color = (0, 255, 0) if states[i] == 0 else (0, 0, 255)
        cv2.rectangle(vis, (x0, y0), (x1, y1), color, 2)
        label = f"S{i+1}:{'FREE' if states[i]==0 else 'OCC'}  s={scores[i]:.1f}"
        cv2.putText(vis, label, (x0+5, y0-8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    help_txt = "[C]=Calibrate empty  [S]=Save baseline  [L]=Load baseline  [Q]=Quit"
    cv2.putText(vis, help_txt, (12, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return vis

def save_baseline(path, rois, refs):
    data = {"rois": np.array(rois, dtype=np.int32)}
    for i, ref in enumerate(refs):
        data[f"ref_{i}"] = ref
    np.savez_compressed(path, **data)
    print(f"[saved] baseline -> {path}")

def load_baseline(path):
    z = np.load(path, allow_pickle=False)
    rois = z["rois"].tolist()
    refs = []
    i = 0
    while f"ref_{i}" in z.files:
        refs.append(z[f"ref_{i}"])
        i += 1
    print(f"[loaded] baseline from {path}")
    return rois, refs

def update_leds_from_arduino(states):
    for i, state in enumerate(states):
        if state == '1':
            GPIO.output(led_pins[i]['R'], GPIO.LOW)
            GPIO.output(led_pins[i]['G'], GPIO.HIGH)
        else:
            GPIO.output(led_pins[i]['R'], GPIO.HIGH)
            GPIO.output(led_pins[i]['G'], GPIO.LOW)

def main():
    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        print("Cannot open camera")
        return

    ok, frame = cap.read()
    if not ok:
        print("No frames from camera")
        return

    h, w = frame.shape[:2]
    if (w, h) != (1280, 720):
        print(f"Warning: expected 1280x720, got {w}x{h}. Boxes may not align.")

    rois = ROI_BOXES
    refs = [None] * 4
    ema_scores = [0.0] * 4
    states_cv = [0] * 4
    enter_cnt = [0] * 4
    exit_cnt = [0] * 4
    arduino_states = ['0', '0', '0', '0']

    def calibrate(reference_frame):
        gray = cv2.cvtColor(reference_frame, cv2.COLOR_BGR2GRAY)
        gray = preprocess(gray)
        for i, box in enumerate(rois):
            refs[i] = crop(gray, box).copy()
        print("[calibrated] baseline captured for all spots.")

    if os.path.exists(BASELINE_FILE):
        try:
            rois_loaded, refs_loaded = load_baseline(BASELINE_FILE)
            gray0 = preprocess(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
            ok_shapes = True
            for i, ref in enumerate(refs_loaded):
                if ref.shape != crop(gray0, rois[i]).shape:
                    ok_shapes = False
                    break
            if ok_shapes:
                refs = refs_loaded
            else:
                print("[warn] Saved baseline mismatch; press C to recalibrate.")
        except Exception as e:
            print(f"[warn] Could not load baseline: {e}")

    last_print = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = preprocess(gray)
        have_baseline = all(r is not None for r in refs)
        scores = []

        if have_baseline:
            for i, box in enumerate(rois):
                roi_now = crop(gray, box)
                score = calc_score(roi_now, refs[i])
                ema_scores[i] = EMA_ALPHA * score + (1 - EMA_ALPHA) * ema_scores[i]
                scores.append(ema_scores[i])

                if states_cv[i] == 0:
                    if ema_scores[i] > TH_ENTER:
                        enter_cnt[i] += 1
                        exit_cnt[i] = 0
                        if enter_cnt[i] >= ENTER_FRAMES:
                            states_cv[i] = 1
                            enter_cnt[i] = 0
                    else:
                        enter_cnt[i] = 0
                else:
                    if ema_scores[i] < TH_EXIT:
                        exit_cnt[i] += 1
                        enter_cnt[i] = 0
                        if exit_cnt[i] >= EXIT_FRAMES:
                            states_cv[i] = 0
                            exit_cnt[i] = 0
                    else:
                        exit_cnt[i] = 0
        else:
            scores = [0.0] * 4

        while ser.in_waiting > 0:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if line.startswith("P:"):
                s = line[2:].replace(',', '')
                if len(s) == 4 and all(c in '01' for c in s):
                    arduino_states = list(s)
                    update_leds_from_arduino(arduino_states)

        vis = draw_overlay(frame, rois, states_cv, scores)

        if SHOW_SCALE != 1.0:
            vis = cv2.resize(vis, None, fx=SHOW_SCALE, fy=SHOW_SCALE)

        cv2.imshow("Parking (4 spots) - Baseline compare", vis)
        key = cv2.waitKey(1) & 0xFF
        time.sleep(0.1)

        if key == ord('q'):
            break
        elif key == ord('c'):
            calibrate(frame)
        elif key == ord('s') and have_baseline:
            save_baseline(BASELINE_FILE, rois, refs)
        elif key == ord('l') and os.path.exists(BASELINE_FILE):
            try:
                rois, refs = load_baseline(BASELINE_FILE)
            except Exception as e:
                print(f"[warn] load failed: {e}")

        if time.time() - last_print > 1.0 and have_baseline:
            last_print = time.time()
            print(
                "Arduino:", "".join(arduino_states),
                "CV:", "".join(str(s) for s in states_cv),
                "Scores:", [round(s, 1) for s in scores]
            )

    cap.release()
    cv2.destroyAllWindows()
    GPIO.cleanup()
    ser.close()

if __name__ == "__main__":
    main()
