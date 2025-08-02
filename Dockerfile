FROM python:3.9-slim-bullseye

# Instala o ttyd, zip, unzip e o CLIENTE DOCKER CLI (ainda útil se quiser testar localmente)
RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  libssl-dev \
  libffi-dev \
  python3-dev \
  sudo \
  curl \
  ca-certificates \
  gnupg \
  wget \
  vim \
  && rm -rf /var/lib/apt/lists/*

RUN wget -O /tmp/ttyd https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 \
  && chmod +x /tmp/ttyd \
  && mv /tmp/ttyd /usr/local/bin

WORKDIR /app

COPY app.py .
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY templates/ templates/
COPY static/ static/

EXPOSE 5000
EXPOSE 7681
EXPOSE 2222

CMD ["python", "app.py"]