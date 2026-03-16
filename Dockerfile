FROM python:3.15-rc-slim

WORKDIR /usr/app/src

COPY . ./

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python3", "./main.py"]