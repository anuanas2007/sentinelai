FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# --default-timeout/--retries: this environment's network has shown
# real, repeated ReadTimeoutError failures against PyPI on large
# packages (torch in particular) -- not a hang, a genuine connection
# reliability issue. Without this, one transient timeout kills the
# entire install instead of being retried.
RUN pip install --no-cache-dir --default-timeout=180 --retries 5 -r requirements.txt

COPY . .

EXPOSE 9000

CMD ["python", "log_collector.py"]
