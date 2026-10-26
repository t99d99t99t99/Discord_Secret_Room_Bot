FROM python:3.13.13-slim

# pg_dump and pg_restore are external PostgreSQL client tools used by the
# repository backup CLI and the bot's audit-channel backup commands.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
