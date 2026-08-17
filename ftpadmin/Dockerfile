# ftpadmin - vsftpd 网页管理后台 (Flask)
# 挂载宿主机 docker.sock 以重启 ftpserve 容器；挂载配置卷以读写 users.json
FROM python:3.11-slim
WORKDIR /app

# fnOS 出网走代理，构建时拉 pip 需要；如换网络请改/删 ARG
ARG HTTP_PROXY=http://192.168.20.2:7890
ARG HTTPS_PROXY=http://192.168.20.2:7890
ENV HTTP_PROXY=$HTTP_PROXY HTTPS_PROXY=$HTTPS_PROXY PIP_PROXY=$HTTPS_PROXY

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
EXPOSE 8080
CMD ["python", "app.py"]
