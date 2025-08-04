FROM python:3.9-slim-bullseye as builder

RUN apt-get update && apt-get install -y --no-install-recommends \
  build-essential \
  libssl-dev \
  libffi-dev \
  python3-dev \
  wget \
  && rm -rf /var/lib/apt/lists/*

RUN wget -O /tmp/ttyd https://github.com/tsl0922/ttyd/releases/download/1.7.7/ttyd.x86_64 \
  && chmod +x /tmp/ttyd 

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.9-slim-bullseye

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv 

COPY --from=builder /tmp/ttyd /usr/local/bin/

COPY app.py .

COPY templates/ templates/
COPY static/ static/

EXPOSE 5000
EXPOSE 7681
EXPOSE 2222

CMD ["/opt/venv/bin/python", "app.py"]