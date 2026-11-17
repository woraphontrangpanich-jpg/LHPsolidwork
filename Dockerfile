# SheetFlat web app - cloud container (Cloudflare Containers / Render / Cloud Run / any Docker host)
FROM python:3.12-slim

# OpenCASCADE (cadquery-ocp) needs a few system libraries even without a display
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libglib2.0-0 libxrender1 libxext6 libsm6 libfontconfig1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY sheetflat ./sheetflat
COPY run.py ./

ENV SHEETFLAT_CLOUD=1 \
    SHEETFLAT_DATA=/tmp/sheetflat \
    HOST=0.0.0.0 \
    PORT=8080 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
# Render / Cloud Run inject $PORT; Cloudflare uses 8080
CMD ["sh", "-c", "uvicorn sheetflat.app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
