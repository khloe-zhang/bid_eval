FROM python:3.13-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 创建虚拟环境并安装依赖
COPY pyproject.toml uv.lock ./
RUN pip install uv && \
    uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install crewai[tools]==1.14.2 streamlit>=1.45.0

# 复制代码（.dockerignore 排除不需要的文件）
COPY . .

# 设置环境
ENV PATH="/opt/venv/bin:$PATH"

# 暴露端口
EXPOSE 8501

# 启动命令
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0"]
