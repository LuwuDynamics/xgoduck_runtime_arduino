FROM python:3.13-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
    libopus0 libportaudio2 alsa-utils v4l-utils dbus-daemon \
    python3-dbus python3-dbus-next python3-gi python3-evdev \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 1000 arduino && useradd --uid 1000 --gid arduino --create-home arduino
COPY python/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt fastapi==0.118.0
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
ENV PYTHONPATH=/usr/lib/python3/dist-packages
WORKDIR /app
CMD ["python", "tools/check-ai-sdk.py"]
