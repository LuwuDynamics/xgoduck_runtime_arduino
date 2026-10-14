"""USB capture worker. Only launched when the preview is explicitly enabled."""

import base64
import glob
import json
import os
import time


def capture():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError('OpenCV is unavailable. Redeploy with python/requirements.txt installed.') from exc

    cv2.setNumThreads(1)
    # Prefer stable USB paths and avoid the SoC's internal video codecs.
    configured = os.environ.get('DUCK_CAMERA_DEVICE')
    if configured:
        devices = [configured]
    else:
        devices = sorted(glob.glob('/dev/v4l/by-id/*-video-index0'))
        if not devices:
            devices = [path for path in sorted(glob.glob('/dev/video*'))
                       if '/usb' in os.path.realpath('/sys/class/video4linux/' + os.path.basename(path) + '/device')]
    if not devices:
        raise RuntimeError('No USB camera found. Check the cable, USB hub power and app device access.')

    cap = None
    try:
        for device in devices:
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap.release()
                cap = None
                continue
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 10)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            if ok:
                break
            cap.release()
            cap = None
        if cap is None:
            raise RuntimeError('Cannot open USB camera. It may be in use or unavailable inside the app.')

        while True:
            tick = time.monotonic()
            if frame.shape[1] > 640 or frame.shape[0] > 480:
                scale = min(640 / frame.shape[1], 480 / frame.shape[0])
                frame = cv2.resize(frame, (int(frame.shape[1] * scale), int(frame.shape[0] * scale)))
            ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if not ok:
                raise RuntimeError('Could not encode the camera image.')
            print(json.dumps(dict(jpeg=base64.b64encode(jpeg).decode('ascii'),
                                  device=device, width=frame.shape[1], height=frame.shape[0])), flush=True)
            time.sleep(max(0, 0.1 - (time.monotonic() - tick)))
            ok, frame = cap.read()
            if not ok:
                raise RuntimeError('Camera disconnected or failed to return an image.')
    finally:
        if cap is not None:
            cap.release()


if __name__ == '__main__':
    try:
        capture()
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), flush=True)
