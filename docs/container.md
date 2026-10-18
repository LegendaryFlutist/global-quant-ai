# DGX Spark 容器环境

项目容器继承本机 `nvcr.io/nvidia/pytorch:25.11-py3` 的 ARM64 环境，基础镜像固定为本地已验证的 digest `sha256:417cbf33f87b5378849df37983552cd1f8bc8b62fe1ceabe004de816a55dff21`。

实测基础镜像为 Python 3.12.3、NVIDIA PyTorch `2.10.0a0+b558c986e8.nv25.11`、CUDA 13.0，GPU 为 NVIDIA GB10。这个镜像提供的是 **PyTorch**，本机镜像没有安装 TensorFlow；TensorRT 和 TensorFlow 是不同的组件。未来可直接在 PyTorch 上开发深度学习模型；如确实需要 TensorFlow，应单独验证适配 ARM64/GB10/CUDA 的官方环境，避免覆盖当前 NVIDIA 库。

当前逻辑回归、随机森林、ExtraTrees、HistGradient、sklearn MLP 仍在 CPU 上训练；GPU 检查通过不表示这些模型会自动使用 GPU；新增 `torch_gru` 是独立的PyTorch双层GRU，已实际用GB10训练。

## 首次构建、启动和检查

在项目根目录运行：

```bash
bash scripts/container.sh build
bash scripts/container.sh start
bash scripts/container.sh check --output runs/environment/container_runtime.json
```

Compose 项目名为 `quant-lab`，服务名为 `lab`，默认容器名为 `quant-lab-lab-1`。`start` 启动就绪环境；回测由下面的 `run` 明确启动。该配置不会修改电脑上其他项目的容器，也不需要停止它们。

## 日常使用

```bash
# 使用 config/swing.json 的默认研究设置执行回测
bash scripts/container.sh run

# 只检查数据和参数，不执行训练
bash scripts/container.sh run --check

# 使用 lab.py 子命令，例如重新导出已有报告
bash scripts/container.sh lab report --run runs/某次运行/某个标的目录

# 完整测试；进入容器；停止本项目容器
bash scripts/container.sh test
bash scripts/container.sh shell
bash scripts/container.sh stop
```

也可以直接调用 Compose（所有命令均可加 `--env-file /dev/null`，避免 Compose 读取项目邮件 `.env`）：

```bash
docker compose --env-file /dev/null -p quant-lab exec -T lab python run.py
docker compose --env-file /dev/null -p quant-lab exec -T lab python lab.py --help
```

## 文件、依赖和权限

- 工作目录为容器内 `/workspace/quant_lab`，整个项目从宿主机挂载。源代码修改立即生效，`data/` 和 `runs/` 保留在宿主机，不随容器删除。
- 容器以宿主用户 UID/GID `1000:1000` 运行，报告仍由宿主用户拥有。用于其他 UID 的电脑时，同时调整 Dockerfile 构建参数和 Compose `user`。
- CUDA GPU 全部可见，私有共享内存为 8 GB，传统模型默认单线程以减少嵌套线程争用。
- `/opt/quant-venv` 使用 `--system-site-packages` 继承 NVIDIA 栈。`requirements-container.txt` 独立于宿主 `.venv` 的 `requirements.txt`；当前采用基础镜像已有版本，构建以 `--no-index --no-deps` 验证，不下载或覆盖 torch/CUDA/NumPy。
- 修改源码或配置后不必重建；修改容器依赖、Dockerfile 或镜像中脚本时，再执行 `build` 和 `start`。
- `.dockerignore` 排除数据、报告、宿主 `.venv`、`.env` 和密钥。运行时再用只读 `/dev/null` 遮蔽项目 `.env`，邮件模式固定为 `simulate`，不把 SMTP 密码传入容器。
- `scripts/check_runtime.py` 检查实际 GPU 矩阵乘法与 CPU 结果的一致性、加权 MLP 拟合、报告目录写权限，以及确切依赖版本。TensorFlow 未安装会如实记录，不会擅自安装。

NVIDIA 官方参考：[PyTorch 25.11 发布说明](https://docs.nvidia.com/deeplearning/frameworks/pytorch-release-notes/rel-25-11.html)、[DGX Spark PyTorch 示例](https://build.nvidia.com/spark/pytorch-fine-tune/instructions)。
