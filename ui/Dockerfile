FROM python:3.13-slim
WORKDIR /app

COPY . .
RUN pip install -r requirements.txt

RUN mkdir -p /.streamlit && mv config.toml /.streamlit/ && chown -R 1000:1000 /.streamlit

RUN chmod -R 755 /app/img

USER 1000
EXPOSE 8501

CMD ["streamlit", "run", "inputs.py"]
