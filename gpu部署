# GPU 部署（RTX 5060 Ti / Blackwell sm_120）

服务器：GeForce RTX 5060 Ti（Blackwell，compute capability sm_120） + i7-10700F。
目标：让向量计算（BGE-M3 嵌入 + BGE-reranker 重排）跑在 GPU 上。

容器里能不能用 GPU 有三道独立的门，缺一道就回退 CPU。下面三处改完才生效。
应用代码不用改 —— src/embed.py:_pick_device() 已经在 torch.cuda.is_available()
为真时自动选 "cuda"，reranker 也会自动用 GPU。

⚠️ 关键点：5060 Ti 是 Blackwell（sm_120）。默认 pip / cu126 的 torch 轮子
**没有为 sm_120 编译**，装上去会在运行时报
"no kernel image is available for execution on the device"。
必须用 cu128（CUDA 12.8）的轮子，对应 torch>=2.7（首个支持 sm_120 的版本）。
老笔记里的 cu118（给 1050 Ti 的）在这张卡上跑不起来。

------------------------------------------------------------------------

## 1. 安装 CUDA 版 PyTorch 轮子（Dockerfile）—— 已改

docker/Dockerfile.backend 里的 torch 安装行：

    # 之前（CPU-only）
    RUN pip install --index-url https://download.pytorch.org/whl/cpu  "torch>=2.6"

    # 现在（cu128 = CUDA 12.8，Blackwell 必需）
    RUN pip install --index-url https://download.pytorch.org/whl/cu128 "torch>=2.7"

为什么：PyTorch 按后端发不同轮子。CPU 轮子里根本不含 CUDA 库（cuBLAS、cuDNN、
GPU 算子内核），所以容器里 torch.cuda.is_available() 返回 False，
_pick_device() 就正确地回退到 "cpu"。换成 cu128 轮子才让 torch 会用 CUDA，
且这个轮子是为 sm_120 编译过的。

副作用：镜像里 torch 从 ~200MB 涨到 ~2GB（CUDA 运行时 + 内核被打进轮子）。

## 2. 主机装 NVIDIA Container Toolkit（一次性，仓库外）

在 Linux 主机上执行一次：

    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker

为什么：默认情况下容器看不到主机的 /dev/nvidia0 等设备。即使 torch 会用 CUDA，
容器命名空间里也没有可见的 GPU 设备。Container Toolkit 是个 Docker 运行时垫片，
当容器请求 GPU 时，把主机的设备文件 + 驱动库挂进容器。没装它，第 3 步的 deploy
块就是空操作，Docker 直接忽略 GPU 请求。

另外主机 NVIDIA 驱动要 >= 570（Blackwell 支持）。查：`nvidia-smi`，
看右上角 CUDA Version 应 >= 12.8。

## 3. 给 backend 服务预留 GPU（docker-compose.yml）—— 已改

docker/docker-compose.yml 的 backend: 服务下已加：

    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]

为什么：就算装了 toolkit，也得显式告诉 Docker "这个容器要 GPU"。这是 Compose v2
对应 `docker run --gpus all` 的写法。没有它，toolkit 不会为这个容器激活，
/dev/nvidia* 不挂进来。

------------------------------------------------------------------------

## 三道门一句话总结

| 层                        | 它回答的问题                       | 缺了会怎样                  |
|---------------------------|------------------------------------|-----------------------------|
| cu128 torch 轮子          | "我的 Python 代码会用 GPU 吗？"    | torch 静默跑在 CPU 上       |
| NVIDIA Container Toolkit  | "这台主机的容器看得到 GPU 吗？"    | deploy 块成为空操作         |
| compose deploy.devices    | "这个容器想要 GPU 吗？"            | 主机有 GPU 但容器拿不到     |

------------------------------------------------------------------------

## 应用 + 验证

    # 在仓库根目录
    docker compose -f docker/docker-compose.yml build backend
    docker compose -f docker/docker-compose.yml up -d
    docker compose -f docker/docker-compose.yml logs -f backend

启动日志里应看到：
    [embed] loading BAAI/bge-m3 on device=cuda fp16=True
而不是 device=cpu fp16=False。

直接确认容器内 GPU 可见：
    docker compose -f docker/docker-compose.yml exec backend \
      python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
应输出：True NVIDIA GeForce RTX 5060 Ti

跑起来后用 `nvidia-smi` 在主机上看一眼，回答问题或建索引时应有显存占用 + GPU 利用率。

## 回退到 CPU（比如换到无 GPU 的机器）

- Dockerfile 的 torch 行换回 `--index-url .../whl/cpu "torch>=2.6"`
- 删掉 compose backend 里的 deploy.resources 块
