# 震线 EEW · 云端运行镜像
# 只用标准库，镜像很小；平台通过 $PORT 注入端口（Koyeb 默认 8000）
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先只拷依赖清单，让 pip 层可缓存（本项目无第三方依赖，这里几乎瞬间过）
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# 运行时需要写 data/（档案、用户库），确保目录存在
RUN mkdir -p /app/data

EXPOSE 8000

# 云端无桌面：必须 --no-browser；端口优先用平台注入的 $PORT
CMD ["sh", "-c", "python server.py --host 0.0.0.0 --port ${PORT:-8000} --no-browser"]
